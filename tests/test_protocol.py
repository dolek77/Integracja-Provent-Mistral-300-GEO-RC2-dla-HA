"""Testy dekodera (bez Home Assistant). Uruchom: python tests/test_protocol.py"""
import importlib.util
import pathlib
import sys

path = pathlib.Path(__file__).parent.parent / "custom_components/provent_mistral/protocol.py"
spec = importlib.util.spec_from_file_location("provent_protocol", path)
p = importlib.util.module_from_spec(spec)
sys.modules["provent_protocol"] = p
spec.loader.exec_module(p)


def stuff(body: bytes) -> bytes:
    out = bytearray()
    for b in body:
        out += bytes((0x80, b ^ 0xFF)) if b in (0x80, 0x16, 0x68) else bytes((b,))
    return bytes(out)


def build(dst, src, cmd, data=b""):
    body = bytes((dst, src, cmd)) + data
    body += p.crc16_modbus(body).to_bytes(2, "little")
    return b"\xff\xff\x68" + stuff(body) + b"\x16"


def H(s):
    return bytes.fromhex(s.replace(" ", ""))


# Ładunki z dokumentacji
P1 = H("00 06 66 5B 6F 49 00 C1 00 45 03 00 00 10 08")   # panel 1, pt 14:29, GWC on
P1_SAT = H("00 06 7D 6D 4F 49 00 C1 00 46 03 00 00 10 08")  # sobota 16:53
P2 = H("64 00 00 5B 4F 49 00 10 00 40 03 00 00 10 00")   # BYPASS "23"
P3 = H("69 00 00 06 66 49 00 10 00 40 03 00 00 10 00")   # GWC "14"
P4 = H("00 00 00 06 7F 49 00 13 77 40 03 00 00 10 00")   # T2 "18"
P5 = H("00 06 6D 3F 3F 49 00 C9 00 C0 03 00 00 F0 08")   # odliczanie 15:00


def test_doc_crc_examples():
    assert build(1, 0xFE, 0x44, P1)[-3:-1].hex().upper() == "0FE1"
    assert build(1, 0xFE, 0x44, P2)[-3:-1].hex().upper() == "8369"


def test_stream_parse_and_decode():
    frame = build(1, 0xFE, 0x44, P1)
    data = b"\x00\x12" + frame + frame
    parser, out = p.FrameParser(), []
    for i in range(0, len(data), 7):
        out += parser.feed(data[i:i + 7])
    assert len(out) == 2
    st = p.decode_status(p.parse_frame(out[0]).data, p.ProVentState())
    assert (st.time, st.weekday, st.gear, st.bypass, st.gwc, st.ventilation, st.panel) == (
        "14:29", 5, 1, False, True, False, "clock")


def test_gears():
    for val, g in [(0x00, 0), (0x10, 1), (0x30, 2), (0x50, 3), (0x70, 4),
                   (0x90, 1), (0xB0, 2), (0xD0, 3), (0xF0, 4)]:
        pl = bytearray(P1); pl[13] = val
        assert p.decode_status(bytes(pl), p.ProVentState()).gear == g


def test_startup_bits():
    pl = bytearray(P1); pl[1:5] = H("86 BF BF FD"); pl[9] = 0x47; pl[13] = 0x90
    s = p.decode_status(bytes(pl), p.ProVentState())
    assert (s.time, s.clock_set, s.weekday, s.panel) == ("10:06", False, 7, "clock")


def test_panels():
    clock = p.decode_status(P1_SAT, p.ProVentState())
    assert (clock.panel, clock.time, clock.weekday) == ("clock", "16:53", 6)
    for payload, name in [(P2, "bypass"), (P3, "gwc"), (P4, "t2"), (P5, "ventilation")]:
        s = p.decode_status(payload, clock)
        assert s.panel == name, name
        assert (s.time, s.weekday) == ("16:53", 6)   # czas i dzień zachowane
    assert p.decode_status(P5, clock).gear == 4
    pl = bytearray(P1); pl[0] = 0x55
    assert p.decode_status(bytes(pl), clock).panel is None


def test_ventilation_text_on_all_panels():
    clock = p.decode_status(P1_SAT, p.ProVentState())
    assert clock.ventilation is False
    pl = bytearray(P1_SAT); pl[9] = 0xC6          # zegar + napis "wietrzenie"
    s = p.decode_status(bytes(pl), clock)
    assert (s.panel, s.weekday, s.ventilation) == ("clock", 6, True)
    pl = bytearray(P2); pl[9] = 0xC0              # BYPASS + napis "wietrzenie"
    s = p.decode_status(bytes(pl), s)
    assert (s.panel, s.ventilation) == ("bypass", True)
    assert p.decode_status(P5, clock).ventilation is True


def test_stuffed_crc_bytes():
    f = build(1, 0xFE, 0x44, bytes([0x68, 0x16, 0x80]) + bytes(12))
    out = p.FrameParser().feed(f)
    assert p.parse_frame(out[0]).data[:3] == bytes([0x68, 0x16, 0x80])


def test_countdown_and_timer():
    assert p.parse_countdown(P5) == 900
    pl = bytearray(P5); pl[1:5] = H("06 66 6F 6F")           # 14:99 -> format ok
    assert p.parse_countdown(bytes(pl)) == 14 * 60 + 99
    pl[1:5] = H("00 00 06 6D")                               # 00:15
    assert p.parse_countdown(bytes(pl)) == 15

    tm = p.VentilationTimer()
    assert tm.remaining(0) is None
    tm.update(None, True, None, 100.0)                       # start nieznany
    assert tm.remaining(100.0) is None
    tm.update(False, True, None, 100.0)                      # nowy start, szacunek
    assert tm.source == "estimated" and tm.remaining(100.0) == 900
    assert tm.remaining(160.0) == 840
    tm.update(True, True, None, 200.0)                       # trwa: bez zmian
    assert tm.remaining(200.0) == 800
    tm.update(True, True, 770, 200.0)                        # korekta z widoku odliczania
    assert tm.source == "countdown_view" and tm.remaining(230.0) == 740
    assert tm.remaining(2000.0) == 0                         # nie schodzi poniżej 0
    tm.update(True, False, None, 300.0)                      # koniec / przerwanie
    assert tm.remaining(300.0) is None and tm.source is None
    tm.update(None, True, 600, 400.0)                        # dołączenie w widoku odliczania
    assert tm.remaining(400.0) == 600


def test_temperature():
    s = p.decode_status(P1_SAT, p.ProVentState())
    assert (s.temperature, s.last_temperature, s.last_temperature_panel) == (None, None, None)
    s = p.decode_status(P2, s)
    assert (s.panel, s.temperature, s.last_temperature_panel) == ("bypass", 23, "bypass")
    s = p.decode_status(P3, s)
    assert (s.panel, s.temperature, s.last_temperature_panel) == ("gwc", 14, "gwc")
    s = p.decode_status(P4, s)
    assert (s.panel, s.temperature, s.last_temperature_panel) == ("t2", 18, "t2")
    s = p.decode_status(P1_SAT, s)                    # zegar: brak temperatury, ostatnia zostaje
    assert (s.temperature, s.last_temperature, s.last_temperature_panel) == (None, 18, "t2")
    s = p.decode_status(P5, s)                        # odliczanie nie jest temperaturą
    assert (s.temperature, s.last_temperature) == (None, 18)
    pl = bytearray(P2); pl[3:5] = H("00 6D")          # " 5" -> 5
    assert p.decode_status(bytes(pl), p.ProVentState()).temperature == 5
    pl[3:5] = H("5B 55")                              # niepoprawna cyfra -> None, ostatnia bez zmian
    s2 = p.decode_status(bytes(pl), s)
    assert (s2.temperature, s2.last_temperature) == (None, 18)
    assert p.parse_panel_value(P4) == 18


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
