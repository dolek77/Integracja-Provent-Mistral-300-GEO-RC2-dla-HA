"""Sensory Pro-Vent Mistral: czas, dzień tygodnia, bieg, bypass, GWC."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, WEEKDAYS
from .hub import ProVentHub
from .protocol import DIAGNOSTIC_BYTES, PANELS, ProVentState


@dataclass(frozen=True, kw_only=True)
class ProVentSensorDescription(SensorEntityDescription):
    value_fn: Callable[[ProVentState], str | int | None]
    attrs_fn: Callable[[ProVentState], dict[str, Any]] | None = None


def _on_off(value: bool | None) -> str | None:
    if value is None:
        return None
    return "on" if value else "off"


def _panel_attrs(s: ProVentState) -> dict[str, Any]:
    """Wietrzenie aktywne + surowe bajty ikon/napisów (diagnostyka)."""
    if len(s.raw) < max(DIAGNOSTIC_BYTES) + 1:
        return {}
    attrs: dict[str, Any] = {"ventilation_active": s.ventilation}
    attrs.update({f"byte_{i}": f"{s.raw[i]:02X}" for i in DIAGNOSTIC_BYTES})
    return attrs


SENSORS: tuple[ProVentSensorDescription, ...] = (
    ProVentSensorDescription(
        key="time",
        translation_key="time",
        icon="mdi:clock-outline",
        value_fn=lambda s: s.time,
        attrs_fn=lambda s: {"clock_set": s.clock_set, "ventilation": s.ventilation},
    ),
    ProVentSensorDescription(
        key="weekday",
        translation_key="weekday",
        icon="mdi:calendar-today",
        device_class=SensorDeviceClass.ENUM,
        options=WEEKDAYS,
        value_fn=lambda s: WEEKDAYS[s.weekday - 1] if s.weekday else None,
    ),
    ProVentSensorDescription(
        key="panel",
        translation_key="panel",
        icon="mdi:monitor-dashboard",
        device_class=SensorDeviceClass.ENUM,
        options=PANELS,
        value_fn=lambda s: s.panel if s.raw else None,
        attrs_fn=_panel_attrs,
    ),
    ProVentSensorDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.temperature,
    ),
    ProVentSensorDescription(
        key="gear",
        translation_key="gear",
        icon="mdi:fan",
        value_fn=lambda s: s.gear,
    ),
    ProVentSensorDescription(
        key="bypass",
        translation_key="bypass",
        icon="mdi:swap-horizontal-bold",
        device_class=SensorDeviceClass.ENUM,
        options=["on", "off"],
        value_fn=lambda s: _on_off(s.bypass),
    ),
    ProVentSensorDescription(
        key="gwc",
        translation_key="gwc",
        icon="mdi:snowflake-thermometer",
        device_class=SensorDeviceClass.ENUM,
        options=["on", "off"],
        value_fn=lambda s: _on_off(s.gwc),
    ),
)


def _sensor_class(desc: ProVentSensorDescription) -> type[ProVentSensor]:
    if desc.key == "time":
        return ProVentTimeSensor
    if desc.key == "temperature":
        return ProVentTemperatureSensor
    return ProVentSensor


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: ProVentHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        _sensor_class(desc)(hub, entry, desc) for desc in SENSORS
    )


class ProVentSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: ProVentSensorDescription

    def __init__(
        self, hub: ProVentHub, entry: ConfigEntry, description: ProVentSensorDescription
    ) -> None:
        self._hub = hub
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Pro-Vent Mistral 300 GEO",
            manufacturer="Pro-Vent",
            model="Mistral 300 GEO (podsłuch RS485)",
        )

    @property
    def available(self) -> bool:
        return self._hub.connected

    @property
    def native_value(self) -> str | int | None:
        return self.entity_description.value_fn(self._hub.state)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self._hub.state) if fn else None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._hub.signal, self.async_write_ha_state
            )
        )


class ProVentTimeSensor(ProVentSensor):
    """Czas: zegar, a w trybie wietrzenia pozostały czas do jego końca (MM:SS).

    W trybie wietrzenia zegar nie jest aktualizowany, więc ten sam sensor pokazuje
    odliczanie (odczytane z widoku odliczania lub szacowane lokalnie, patrz
    atrybut `source`). Ostatni znany zegar jest dostępny w atrybucie `clock`.
    """

    @property
    def native_value(self) -> str | None:
        if not self._hub.state.ventilation:
            return self._hub.state.time
        seconds = self._hub.ventilation_remaining()
        if seconds is None:
            return None  # start wietrzenia nieznany (połączono w jego trakcie)
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._hub.state
        attrs: dict[str, Any] = {
            "mode": "ventilation_countdown" if state.ventilation else "clock",
            "clock": state.time,
            "clock_set": state.clock_set,
            "ventilation_active": state.ventilation,
        }
        if state.ventilation:
            attrs["seconds_remaining"] = self._hub.ventilation_remaining()
            attrs["source"] = self._hub.timer.source
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._hub.tick_signal, self.async_write_ha_state
            )
        )


class ProVentTemperatureSensor(ProVentSensor):
    """Temperatura aktywnego panelu BYPASS / GWC / T2.

    Centrala wysyła wartość tylko dla panelu aktualnie wyświetlanego na RC2, więc
    sensor ma wartość tylko wtedy, gdy aktywny jest jeden z tych paneli; na panelu
    zegara i w widoku odliczania jest „nieznany". Ostatnia odebrana wartość jest
    w atrybutach `last_value`, `last_panel` i `last_update`.
    """

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._hub.state
        return {
            "panel": state.panel if state.temperature is not None else None,
            "last_value": state.last_temperature,
            "last_panel": state.last_temperature_panel,
            "last_update": self._hub.temp_updated,
        }
