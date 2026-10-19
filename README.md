# Pro-Vent Mistral 300 GEO – integracja Home Assistant (pasywny podsłuch RS485)

Niestandardowa integracja (HACS) czytająca stan rekuperatora **Pro-Vent Mistral 300 GEO**
z magistrali RS485 przez konwerter **RS485-LAN** (serwer TCP, domyślnie port 8899).
Integracja jest wyłącznie pasywnym odbiornikiem: interpretuje ramki `44` (centrala → RC2).

## Encje (sensory)

| Encja | Wartość | Źródło w ramce `44` |
|---|---|---|
| Czas | `HH:MM` (zegar); w trybie wietrzenia `MM:SS` do końca wietrzenia (atrybuty: `mode`, `clock`, `clock_set`, `ventilation_active`, `seconds_remaining`, `source`) | bajty 1–4 (7-seg), napis „wietrzenie” (b9, bit 7) |
| Dzień tygodnia | poniedziałek … niedziela | bajt 9 (`0x40 + n`) |
| Aktywny panel | zegar / bypass / gwc / t2 / wietrzenie (patrz niżej) | bajty 0, 7, 8 (reguły w `PANEL_RULES`) |
| Temperatura | liczba całkowita, °C, tylko gdy aktywny jest panel BYPASS / GWC / T2 (atrybuty: `panel`, `last_value`, `last_panel`, `last_update`) | bajty 3–4 (dwie prawe cyfry 7-seg) aktywnego panelu |
| Bieg | 0–4 | bajt 13 (bit 4 + bity 6–5) |
| Bypass | on / off | bajt 12, bit 0 |
| GWC | on / off | bajt 14, bit 3 |

### Sensor „Aktywny panel”

Panele RC2 rozpoznawane są z bajtów 0, 7 i 8 ramki `44` (klawisz WIDOK na RC2 przełącza je cyklicznie):

| Stan sensora | Panel RC2 | Rozpoznanie (bajty) |
|---|---|---|
| `clock` (Zegar) | symbol zegara, godzina | b0=`00`, b7=`C1`, b8=`00` |
| `bypass` (BYPASS) | napis BYPASS, wartość | b0=`64`, b7=`10` |
| `gwc` (GWC) | napis GWC, wartość | b0=`69`, b7=`10` |
| `t2` (T2) | symbol T2, wartość, litera „A” | b7=`13`, b8=`77` |
| `ventilation` (Wietrzenie) | widok odliczania mm:ss (istnieje tylko podczas wietrzenia) | b7 ma bit 3 (`C9`) |

Nierozpoznana kombinacja daje stan „nieznany”. Napis „wietrzenie” (bit 7 bajtu 9) jest ustawiony na
wszystkich panelach przez cały czas wietrzenia, dlatego trafia do atrybutu `ventilation_active`
sensora panelu (oraz sensora Czas), a nie do stanu panelu. Atrybuty `byte_0`, `byte_5`,
`byte_7`, `byte_8`, `byte_9`, `byte_10`, `byte_11`, `byte_12`, `byte_14` to surowe wartości HEX do diagnostyki.

Czas i dzień tygodnia są aktualizowane **tylko w panelu zegara**, bo w pozostałych panelach cyfry
pokazują inne wartości (BYPASS, GWC, T2, odliczanie) – wtedy sensory zachowują ostatnią znaną wartość.
Atrybut `clock_set: false` oznacza zegar niewstawiony (bit 7 cyfr po starcie).
Reguły rozpoznawania można zmienić w `PANEL_RULES` w `protocol.py`.

### Sensor „Temperatura”

Na panelach BYPASS, GWC i T2 centrala przesyła wyświetlaną wartość jako **dwie prawe cyfry 7-segmentowe**
(bajty 3 i 4 ramki `44`). W danej chwili wyświetlany jest tylko jeden panel i tylko dla niego centrala
wysyła temperaturę, dlatego jest **jeden sensor Temperatura**:

- gdy na RC2 jest panel BYPASS, GWC lub T2: sensor pokazuje wartość tego panelu, a atrybut `panel`
  mówi, którego (`bypass` / `gwc` / `t2`);
- na panelu zegara i w widoku odliczania wietrzenia sensor jest „nieznany” (`panel: null`);
- ostatnia odebrana wartość zostaje w atrybutach `last_value`, `last_panel` i `last_update` (UTC).

Integracja jest pasywna i nie przełącza paneli, więc sensor ma wartość tylko wtedy, gdy ktoś wyświetli
odpowiedni panel na RC2. Dokumentacja nie opisuje jednostki, znaku ani kropki dziesiętnej: przyjęto
liczby całkowite w °C (wartości ujemne i ułamki nie są interpretowane).

### Sensor „Czas” w trybie wietrzenia

Poza wietrzeniem sensor **Czas** pokazuje zegar (`HH:MM`, `mode: clock`). Gdy trwa wietrzenie, zegar
nie jest aktualizowany, więc ten sam sensor pokazuje **czas pozostały do końca wietrzenia** w formacie
`MM:SS` (`mode: ventilation_countdown`), odświeżany co sekundę. Ostatni znany zegar jest wtedy dostępny
w atrybucie `clock`. Po zakończeniu wietrzenia sensor wraca do zegara.

Centrala przesyła odliczanie mm:ss **tylko wtedy, gdy na RC2 wyświetlany jest widok odliczania**
(panel 5); na pozostałych panelach w ramkach jest wyłącznie napis „wietrzenie”. Dlatego atrybut `source` to:

- `countdown_view` – wartość odczytana z cyfr widoku odliczania (dokładna);
- `estimated` – szacunek: start 15:00 w chwili pojawienia się napisu „wietrzenie”, odliczany
  lokalnie co 1 s i korygowany do wartości z centrali przy każdym wejściu RC2 w widok odliczania.

Jeśli integracja połączy się w trakcie trwającego wietrzenia, sensor jest „nieznany”, dopóki ktoś nie
wyświetli widoku odliczania na RC2. Wietrzenie kończy się samoczynnie po 15 min albo po naciśnięciu
„+” / „−” na RC2.

### Wymagania wobec konwertera RS485-LAN

Port szeregowy konwertera: **115200 bit/s, 8N1, bez sterowania przepływem (RTS/CTS wyłączone)**,
tryb przezroczysty (transparent), serwer TCP na porcie 8899.

## Logo

Ikona integracji (logo Pro-Vent) leży w `custom_components/provent_mistral/brand/` (`icon.png` 256×256,
`icon@2x.png` 512×512). Lokalne obrazy marki są obsługiwane od Home Assistant 2026.3 i mają pierwszeństwo
przed obrazami z zewnętrznego repozytorium marek; po aktualizacji może być potrzebne odświeżenie
pamięci podręcznej przeglądarki. Ponieważ logo jest kwadratowe, dodano tylko pliki `icon`.

## Instalacja przez HACS

1. Wgraj zawartość tego katalogu do repozytorium GitHub (np. `ha-provent-mistral`)
   i w `manifest.json` zamień `TWOJ_LOGIN` na swój login.
2. HACS → menu (⋮) → **Niestandardowe repozytoria** → adres repozytorium, kategoria **Integracja**.
3. Zainstaluj „Pro-Vent Mistral (RC2)” i uruchom ponownie Home Assistant.
4. **Ustawienia → Urządzenia i usługi → Dodaj integrację → Pro-Vent Mistral (RC2)**.

Pola konfiguracji: adres IP (domyślnie `192.168.2.57`) i port (`8899`).

## Jak to działa

- **Tylko podsłuch.** Integracja łączy się z konwerterem RS485-LAN jako klient TCP i wyłącznie
  odbiera dane. Niczego nie wysyła (ani odpowiedzi `C4`, ani klawiszy, ani poleceń), więc nie wpływa
  na komunikację centrala ↔ RC2. W kodzie nie ma żadnej ścieżki nadawczej.
- **Interpretowane są tylko ramki `44`** (centrala `FE` → RC2, adres `00`–`0F`) z pełnym 15-bajtowym,
  niezerowym ładunkiem. Wszystkie RC2 dostają to samo „zdjęcie” stanu, dlatego adres odbiorcy
  nie ma znaczenia, a numer wirtualnego manipulatora nie jest już potrzebny. Ramki `C4` i ramki skanu
  adresów są pomijane.
- Ramki są walidowane (preambuła, bajtowanie unikowe, CRC-16/MODBUS); uszkodzone są odrzucane.
- Po 30 s bez danych połączenie jest ponawiane, a sensory pokazują „niedostępny”.
- Wiele konwerterów RS485-LAN dopuszcza tylko jedno–kilka połączeń TCP naraz.
- Konwerter musi widzieć cały ruch na magistrali.

## Struktura

```
custom_components/provent_mistral/
├── protocol.py      # ramki, bajtowanie unikowe, CRC16, dekodowanie (bez HA)
├── hub.py           # klient TCP (tylko odbiór), reconnect, push do encji
├── config_flow.py   # formularz: IP, port, numer RC2
├── sensor.py        # 5 sensorów
├── brand/           # icon.png, icon@2x.png (logo Pro-Vent)
└── translations/    # pl, en
tests/test_protocol.py   # testy dekodera: python tests/test_protocol.py
```
