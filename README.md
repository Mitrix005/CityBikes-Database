# CityBikes API 

Projekt semestralny z przedmiotu *Wprowadzenie do baz danych*. Cykliczny import danych z otwartego API CityBikes do bazy PostgreSQL, z zapisem historii zmian.

## Zespół

Dwuosobowy

## Wymagania

- Python 3.10+
- PostgreSQL 14+ (lokalnie)
- biblioteki Pythona: `requests`, `psycopg`, `pandas`

## Struktura projektu

```
projekt-citybikes/
├── README.md
├── schema.sql             skrypt SQL tworzący tabele i indeksy
├── import_citybikes.py    logika importu, odpalana cyklicznie
├── queries.sql            zapytania analityczne
├── raport.pdf             raport projektowy
└── Screenshots/           zrzuty ekranu z działania importu i wizualizacji
```

## Konfiguracja bazy danych

### 1. Utworzenie bazy

Na początku trzeba utworzyć bazę `citybikes`. Na przykład z konsoli:

```bash
psql -U postgres -c "CREATE DATABASE citybikes;"
```

Albo w pgAdmin: „Databases" → Create → Database, nazwa `citybikes`.

### 2. Parametry połączenia

Domyślne ustawienia w `import_citybikes.py`:

```python
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "citybikes",
    "user": "postgres",
    "password": "root",
}
```

### 3. Tabele

Tabele tworzą się automatycznie przy pierwszym uruchomieniu importu, skrypt czyta `schema.sql` i wykonuje wszystkie `CREATE TABLE IF NOT EXISTS`.

## Instalacja zależności

```bash
pip install -r requirements.txt
```

## Uruchomienie

Z katalogu projektu:

```bash
python import_citybikes.py
```

Skrypt:

1. łączy się z bazą `citybikes` w PostgreSQL,
2. tworzy tabele i indeksy ze schematu `schema.sql` (jeśli jeszcze nie istnieją),
3. zakłada wiersz w `import_log` ze statusem `RUNNING`,
4. pobiera listę sieci rowerowych z `api.citybik.es/v2/networks`,
5. filtruje sieci polskie (`country == "PL"`),
6. dla każdej sieci dociąga szczegóły wraz ze stacjami (`/v2/networks/{id}`),
7. zapisuje stałe dane (kraj, miasto, sieć, stacja) z UPSERT (`ON CONFLICT DO UPDATE`),
8. dodaje pomiary do `station_history` z deduplikacją (`UNIQUE(station_id, timestamp)` + `DO NOTHING`),
9. dodaje wiersz w `import_log` finalnym statusem i licznikami.

Na koniec wypisuje krótkie podsumowanie: ile pomiarów odebrał i ile zapisał.

## Uruchomienie cykliczne (co 30 min)

Zgodnie z wymaganiami import ma chodzić w ustalonym odstępie czasowym. Zostało to zaimplementowane za pomocą pliku `database_update.py`, zawierajacy pętle `while True` z `time.sleep()` na 30 minut.

## Model danych

6 tabel: 4 merytoryczne, 1 słownikowa, 1 techniczna.

| Tabela | Rola | Zapis |
|---|---|---|
| `countries` | słownikowa — kody państw ISO | UPSERT |
| `cities` | merytoryczna — miasta | UPSERT |
| `networks` | merytoryczna — systemy rowerowe | UPSERT |
| `stations` | merytoryczna — stałe dane stacji | UPSERT |
| `station_history` | merytoryczna — pomiary w czasie | INSERT + DO NOTHING |
| `import_log` | techniczna — dziennik przebiegów | INSERT na starcie, UPDATE na końcu |

Relacje:

```
countries 1—N cities 1—N networks 1—N stations 1—N station_history
                                                            N—1 import_log
```

Pełna struktura tabeli w pliku `schema.sql`

## Deduplikacja

Klucz `UNIQUE(station_id, timestamp)` na `station_history` zabezpiecza przed duplikacją, przed pobraniem danych z tej samej stacji w tym samym momencie pomiaru. `INSERT ... ON CONFLICT DO NOTHING` pomija powtórki, gdy przy kolejnym przebiegu stacja zwróciła ten sam `timestamp` co poprzednio, nic się nie zapisuje. W `import_log`: `records_received - records_saved > 0` oznacza pominięte duplikaty.

Klucz pochodzi z `timestamp` zwracanego przez API (moment ważności pomiaru), nie z czasu uruchomienia skryptu.

## Logowanie importu

Każdy przebieg zostawia log w `import_log`:

- `imported_at` — kiedy się zaczął,
- `records_received` / `records_saved` — ile pomiarów otrzymano / ile faktycznie zapisano,
- `status` — `SUCCESS` przy powodzeniu, `ERROR` przy wyjątku (z treścią błędu w `error_message`),
- `RUNNING` widoczne w trakcie przebiegu — jeśli zostało po zakończeniu, znaczy że skrypt został przerwany.

Wiersz logu zakłada się na samym początku (z osobnym commitem), a kończy aktualizacją na końcu. Dzięki temu nawet przy błędzie, ślad po przebiegu zostaje.

## Konfiguracja

W `import_citybikes.py`:

- `API_URL` — endpoint CityBikes
- `DB_CONFIG` — parametry połączenia z PostgreSQL
- `SCHEMA_PATH` — ścieżka do pliku `schema.sql`
- `COUNTRY_CODE` — kod kraju do filtrowania sieci (domyślnie `"PL"`)
- `TIMEOUT_SECONDS` — timeout dla zapytań do API
