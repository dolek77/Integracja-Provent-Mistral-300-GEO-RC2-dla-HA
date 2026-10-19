"""Stałe integracji Pro-Vent Mistral (podsłuch RS485)."""
from __future__ import annotations

DOMAIN = "provent_mistral"

DEFAULT_HOST = "192.168.2.57"
DEFAULT_PORT = 8899

CONNECT_TIMEOUT = 5.0   # s, nawiązanie połączenia TCP
DATA_TIMEOUT = 30.0     # s, brak jakichkolwiek danych => ponowne połączenie
RECONNECT_MIN = 2.0
RECONNECT_MAX = 60.0

WEEKDAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]
