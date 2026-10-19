"""Protokół RS485 Pro-Vent Mistral 300 GEO <-> RC2 (wyłącznie odbiór i dekodowanie).

Moduł nie zawiera kodu nadawczego i nie zależy od Home Assistant.

Ramka:  FF FF 68 | A | B | CMD | dane | CRC16 (LE) | 16
Bajtowanie unikowe: 80 oznacza, że następny bajt jest zanegowany bitowo
(80 -> 80 7F, 16 -> 80 E9, 68 -> 80 97). CRC-16/MODBUS liczone po
rozpakowaniu od bajtu A do końca danych.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

BYTE_START = 0x68
BYTE_END = 0x16
BYTE_ESC = 0x80
PREAMBLE = b"\xff\xff"

ADDR_CENTRAL = 0xFE
CMD_DISPLAY = 0x44   # centrala -> RC2
PAYLOAD_LEN = 15

_MAX_BODY_LEN = 64

PANELS = ["clock", "bypass", "gwc", "t2", "ventilation"]

# Rozpoznawanie aktywnego panelu RC2 (dokumentacja pkt 5 i 7). Każda reguła:
# (panel, ((indeks bajtu ładunku, maska, wartość po zamaskowaniu), ...)),
# wszystkie warunki reguły muszą być spełnione; pierwsza pasująca reguła wygrywa.
# Gdy żadna nie pasuje, panel jest nieznany (None).
#   panel 1 (zegar):     b0=00, b7=C1, b8=00
#   panel 2 (BYPASS):    b0=64, b7=10
#   panel 3 (GWC):       b0=69, b7=10
#   panel 4 (T2):        b7=13, b8=77
#   panel 5 (odliczanie wietrzenia): b7=C9 (bit 3) - widok istnieje tylko podczas wietrzenia
PANEL_RULES: tuple[tuple[str, tuple[tuple[int, int, int], ...]], ...] = (
    ("ventilation", ((7, 0x08, 0x08),)),
    ("bypass", ((0, 0xFF, 0x64), (7, 0xFF, 0x10))),
    ("gwc", ((0, 0xFF, 0x69), (7, 0xFF, 0x10))),
    ("t2", ((7, 0xFF, 0x13), (8, 0xFF, 0x77))),
    ("clock", ((0, 0xFF, 0x00), (7, 0xFF, 0xC1), (8, 0xFF, 0x00))),
)

# Panele pokazujące wartość (temperaturę) na dwóch prawych cyfrach (bajty 3 i 4):
# Centrala wysyła wartość tylko dla panelu aktualnie wyświetlanego.
PANEL_VALUE_PANELS = ("bypass", "gwc", "t2")

# Bajty pokazywane jako atrybuty diagnostyczne (surowe wartości HEX).
DIAGNOSTIC_BYTES = (0, 5, 7, 8, 9, 10, 11, 12, 14)

_SEGMENTS = {
    0x3F: 0, 0x06: 1, 0x5B: 2, 0x4F: 3, 0x66: 4,
    0x6D: 5, 0x7D: 6, 0x07: 7, 0x7F: 8, 0x6F: 9,
}


def crc16_modbus(data: bytes) -> int:
    """CRC-16/MODBUS (0xA001 odbity, init 0xFFFF, bez końcowego XOR)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


@dataclass(frozen=True)
class Frame:
    dst: int
    src: int
    cmd: int
    data: bytes


def parse_frame(body: bytes) -> Frame | None:
    """Sprawdź CRC rozpakowanej ramki (od A do CRC) i rozbij na pola."""
    if len(body) < 5:
        return None
    if crc16_modbus(body[:-2]) != int.from_bytes(body[-2:], "little"):
        return None
    return Frame(body[0], body[1], body[2], bytes(body[3:-2]))


class FrameParser:
    """Strumieniowy parser ramek; zwraca rozpakowane treści (od A do CRC)."""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self._in_frame = False
        self._escape = False
        self._ff = 0
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        frames: list[bytes] = []
        for byte in data:
            if not self._in_frame:
                if byte == 0xFF:
                    self._ff += 1
                elif byte == BYTE_START and self._ff >= 2:
                    self._in_frame = True
                    self._escape = False
                    self._buf = bytearray()
                    self._ff = 0
                else:
                    self._ff = 0
                continue

            if self._escape:
                self._buf.append(byte ^ 0xFF)
                self._escape = False
            elif byte == BYTE_ESC:
                self._escape = True
            elif byte == BYTE_END:
                frames.append(bytes(self._buf))
                self._reset()
                continue
            elif byte == BYTE_START:
                # Surowe 68 w środku ramki jest niedozwolone: ucięta ramka.
                if bytes(self._buf[-2:]) == PREAMBLE:
                    self._buf = bytearray()  # zaczyna się nowa ramka
                else:
                    self._reset()
                continue
            else:
                self._buf.append(byte)

            if len(self._buf) > _MAX_BODY_LEN:
                self._reset()
        return frames


@dataclass(frozen=True)
class ProVentState:
    """Stan odczytany z ramek 44 (centrala -> RC2)."""

    time: str | None = None          # "HH:MM"
    clock_set: bool | None = None    # False: zegar niewstawiony (bit 7 cyfr po starcie)
    weekday: int | None = None       # 1 = poniedziałek ... 7 = niedziela
    gear: int | None = None          # 0..4
    bypass: bool | None = None
    gwc: bool | None = None
    ventilation: bool = False       # napis "wietrzenie" (bit 7 bajtu 9), na każdym panelu
    panel: str | None = None        # patrz PANELS; None = nierozpoznany
    temperature: int | None = None            # wartość aktywnego panelu BYPASS/GWC/T2, poza nimi None
    last_temperature: int | None = None       # ostatnia odebrana wartość
    last_temperature_panel: str | None = None # panel, z którego pochodzi
    raw: bytes = b""


def parse_panel_value(payload: bytes) -> int | None:
    """Wartość z dwóch prawych cyfr (bajty 3 i 4) paneli BYPASS / GWC / T2.

    Puste cyfry dziesiątek (00) traktujemy jak 0. Znak ani kropka dziesiętna
    nie są opisane w dokumentacji, więc nie są interpretowane.
    """
    if len(payload) != PAYLOAD_LEN:
        return None
    tens, units = payload[3] & 0x7F, payload[4] & 0x7F
    t = 0 if tens == 0 else _SEGMENTS.get(tens)
    u = _SEGMENTS.get(units)
    if t is None or u is None:
        return None
    return t * 10 + u


def detect_panel(payload: bytes) -> str | None:
    """Rozpoznaj aktywny panel RC2 według PANEL_RULES."""
    for panel, conditions in PANEL_RULES:
        if all(payload[idx] & mask == value for idx, mask, value in conditions):
            return panel
    return None


def decode_status(payload: bytes, previous: ProVentState) -> ProVentState:
    """Zdekoduj 15-bajtowy ładunek ramki 44.

    Bajty: 0, 7, 8 ikony/napisy panelu, 1-4 cyfry 7-seg, 9 dzień tygodnia
    (0x40 + n) z bitem 7 = napis "wietrzenie", 12 bypass (bit 0),
    13 wiatraczek/bieg, 14 GWC (bit 3).
    Czas i dzień są aktualizowane tylko, gdy aktywny jest panel zegara; w innych
    panelach cyfry pokazują inne wartości, więc czas i dzień zachowują ostatnią
    znaną wartość.
    """
    if len(payload) != PAYLOAD_LEN:
        return previous

    panel = detect_panel(payload)
    time_str = previous.time
    clock_set = previous.clock_set
    weekday = previous.weekday

    if panel == "clock":
        digits = [_SEGMENTS.get(b & 0x7F) for b in payload[1:5]]
        if None not in digits:
            time_str = f"{digits[0]}{digits[1]}:{digits[2]}{digits[3]}"
            clock_set = not any(b & 0x80 for b in payload[1:5])
        day = payload[9] & 0x7F  # bit 7 = napis "wietrzenie"
        if day & 0xF8 == 0x40 and 1 <= (day & 0x07) <= 7:
            weekday = day & 0x07

    fan = payload[13]
    gear = ((fan >> 5) & 0x03) + 1 if fan & 0x10 else 0

    temperature = None
    last_temperature = previous.last_temperature
    last_temperature_panel = previous.last_temperature_panel
    if panel in PANEL_VALUE_PANELS:
        temperature = parse_panel_value(payload)
        if temperature is not None:
            last_temperature = temperature
            last_temperature_panel = panel

    return replace(
        previous,
        time=time_str,
        clock_set=clock_set,
        weekday=weekday,
        gear=gear,
        bypass=bool(payload[12] & 0x01),
        gwc=bool(payload[14] & 0x08),
        ventilation=bool(payload[9] & 0x80),
        panel=panel,
        raw=bytes(payload),
        temperature=temperature,
        last_temperature=last_temperature,
        last_temperature_panel=last_temperature_panel,
    )


VENTILATION_SECONDS = 900  # wietrzenie (MAX) trwa 15 minut


def parse_countdown(payload: bytes) -> int | None:
    """Odczytaj pozostałe sekundy z widoku odliczania (cyfry mm:ss).

    Wywołuj tylko, gdy aktywny jest panel "ventilation" (widok odliczania).
    """
    if len(payload) != PAYLOAD_LEN:
        return None
    # puste cyfry (00) traktujemy jak 0
    digits = [0 if b & 0x7F == 0 else _SEGMENTS.get(b & 0x7F) for b in payload[1:5]]
    if None in digits:
        return None
    return (digits[0] * 10 + digits[1]) * 60 + digits[2] * 10 + digits[3]


class VentilationTimer:
    """Czas pozostały do końca wietrzenia.

    Centrala podaje odliczanie mm:ss tylko w ramkach widoku odliczania (panel 5).
    Na pozostałych panelach w ramkach jest tylko napis "wietrzenie", więc czas jest
    szacowany lokalnie: start = 15:00 w chwili pojawienia się napisu, a przy każdej
    ramce z widoku odliczania szacunek jest korygowany do wartości z centrali.
    """

    def __init__(self) -> None:
        self.deadline: float | None = None
        self.source: str | None = None  # "countdown_view" / "estimated"

    def update(
        self, was_active: bool | None, active: bool, countdown: int | None, now: float
    ) -> None:
        """was_active: poprzedni stan napisu (None = pierwsza ramka po połączeniu)."""
        if not active:
            self.deadline = None
            self.source = None
        elif countdown is not None:
            self.deadline = now + countdown
            self.source = "countdown_view"
        elif was_active is False:
            self.deadline = now + VENTILATION_SECONDS
            self.source = "estimated"
        # was_active None + brak odliczania: start nieznany, czekamy na widok odliczania

    def remaining(self, now: float) -> int | None:
        if self.deadline is None:
            return None
        return max(0, math.ceil(self.deadline - now))
