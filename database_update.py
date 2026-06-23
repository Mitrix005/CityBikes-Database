import time

from import_citybikes import run_import, utc_now

INTERVAL_SECONDS = 30 * 60

while True:
    try:
        stats = run_import()
        print(f"{utc_now()} — odebrano {stats.records_received}, zapisano {stats.records_saved}")
    except Exception as exc:
        print(f"{utc_now()} BŁĄD — {exc}")
    time.sleep(INTERVAL_SECONDS)