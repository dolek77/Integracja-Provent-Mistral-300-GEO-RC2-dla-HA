"""Pasywny podsłuch magistrali RS485 przez konwerter RS485-LAN (serwer TCP).

Integracja tylko odbiera dane: nic nie wysyła do konwertera, więc nie wpływa
na komunikację centrala <-> RC2.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONNECT_TIMEOUT,
    DATA_TIMEOUT,
    DOMAIN,
    RECONNECT_MAX,
    RECONNECT_MIN,
)
from .protocol import (
    ADDR_CENTRAL,
    CMD_DISPLAY,
    PAYLOAD_LEN,
    Frame,
    FrameParser,
    ProVentState,
    VentilationTimer,
    decode_status,
    parse_countdown,
    parse_frame,
)

_LOGGER = logging.getLogger(__name__)


class ProVentHub:
    """Utrzymuje połączenie TCP i publikuje stan do encji (push)."""

    def __init__(self, hass: HomeAssistant, entry_id: str, host: str, port: int) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self.host = host
        self.port = port
        self.state = ProVentState()
        self.connected = False
        self.timer = VentilationTimer()
        self.temp_updated: str | None = None  # czas ostatniej zmiany odebranej temperatury
        self._task: asyncio.Task | None = None
        self._unsub_tick = None

    @property
    def signal(self) -> str:
        return f"{DOMAIN}_update_{self._entry_id}"

    @property
    def tick_signal(self) -> str:
        return f"{DOMAIN}_tick_{self._entry_id}"

    def ventilation_remaining(self) -> int | None:
        """Pozostałe sekundy wietrzenia (0 = brak wietrzenia, None = nieznane)."""
        if not self.state.ventilation:
            return 0
        return self.timer.remaining(time.monotonic())

    @callback
    def _tick(self, _now) -> None:
        if self.state.ventilation:
            async_dispatcher_send(self._hass, self.tick_signal)

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self._hass, self.signal)

    @callback
    def _set_connected(self, value: bool) -> None:
        if self.connected != value:
            self.connected = value
            self._notify()

    def start(self) -> None:
        self._task = self._hass.async_create_background_task(
            self._run(), name=f"{DOMAIN} {self.host}:{self.port}"
        )
        self._unsub_tick = async_track_time_interval(
            self._hass, self._tick, timedelta(seconds=1)
        )

    async def async_stop(self) -> None:
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        backoff = RECONNECT_MIN
        while True:
            writer = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT
                )
                _LOGGER.info("Podsłuch: połączono z %s:%s", self.host, self.port)
                backoff = RECONNECT_MIN
                self._set_connected(True)
                await self._read_loop(reader)
            except (OSError, asyncio.TimeoutError, ConnectionError) as err:
                _LOGGER.warning(
                    "Błąd połączenia z %s:%s: %s (ponowna próba za %.0f s)",
                    self.host, self.port, err or type(err).__name__, backoff,
                )
            finally:
                self._set_connected(False)
                if writer is not None:
                    writer.close()
                    with contextlib.suppress(OSError, asyncio.TimeoutError):
                        await asyncio.wait_for(writer.wait_closed(), 2)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        parser = FrameParser()
        while True:
            data = await asyncio.wait_for(reader.read(512), DATA_TIMEOUT)
            if not data:
                raise ConnectionError("połączenie zamknięte przez konwerter")
            for body in parser.feed(data):
                frame = parse_frame(body)
                if frame is not None:
                    self._handle_frame(frame)

    @callback
    def _handle_frame(self, frame: Frame) -> None:
        """Interpretuj wyłącznie ramki 44 centrala -> RC2 (adres 00-0F) z danymi.

        Wszystkie RC2 dostają to samo "zdjęcie" stanu, więc adres odbiorcy
        nie ma znaczenia. Ramki C4 (RC2 -> centrala) są ignorowane.
        """
        if frame.cmd != CMD_DISPLAY or frame.src != ADDR_CENTRAL or frame.dst > 0x0F:
            return
        # Ramki skanu adresów mają pusty / zerowy ładunek - pomijamy.
        if len(frame.data) != PAYLOAD_LEN or not any(frame.data):
            return
        previous = self.state
        new_state = decode_status(frame.data, previous)
        countdown = parse_countdown(frame.data) if new_state.panel == "ventilation" else None
        self.timer.update(
            previous.ventilation if previous.raw else None,
            new_state.ventilation,
            countdown,
            time.monotonic(),
        )
        if new_state.temperature is not None and (
            previous.temperature != new_state.temperature
            or previous.panel != new_state.panel
        ):
            self.temp_updated = dt_util.utcnow().isoformat(timespec="seconds")

        if new_state != previous:
            self.state = new_state
            self._notify()
