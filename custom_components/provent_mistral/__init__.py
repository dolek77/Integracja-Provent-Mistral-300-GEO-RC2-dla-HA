"""Integracja Pro-Vent Mistral 300 GEO (pasywny podsłuch ramek centrala -> RC2)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .hub import ProVentHub

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub = ProVentHub(hass, entry.entry_id, entry.data[CONF_HOST], entry.data[CONF_PORT])
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    hub.start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hub: ProVentHub = hass.data[DOMAIN].pop(entry.entry_id)
        await hub.async_stop()
    return unload_ok
