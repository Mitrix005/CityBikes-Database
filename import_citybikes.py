import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import psycopg
import requests
from psycopg import Connection

API_URL = "http://api.citybik.es/v2/networks"
SCHEMA_PATH = "schema.sql"
DB_CONFIG = {
	"host": "localhost",
	"port": 5432,
	"dbname": "citybikes",
	"user": "postgres",
	"password": "postgres",
}
TIMEOUT_SECONDS = 30
COUNTRY_CODE = "PL"


@dataclass
class ImportStats:
	records_received: int
	records_saved: int


def utc_now() -> datetime:
	return datetime.now(timezone.utc).replace(microsecond=0)


def create_schema(conn: Connection) -> None:
	with open(SCHEMA_PATH, encoding="utf-8") as f:
		conn.execute(f.read())
	conn.commit()


def start_import_log(conn: Connection, imported_at: datetime) -> int:
	cur = conn.execute(
		"""
		INSERT INTO import_log (
			imported_at,
			api_name,
			endpoint,
			records_received,
			records_saved,
			status
		)
		VALUES (%s, %s, %s, 0, 0, 'RUNNING')
		RETURNING import_id;
		""",
		(imported_at, "Citybikes API", API_URL),
	)

	import_id = cur.fetchone()[0]
	conn.commit()

	return import_id


def finish_import_log(
	conn: Connection,
	import_id: int,
	*,
	records_received: int,
	records_saved: int,
	status: str,
	error_message: str | None = None,
) -> None:
	conn.execute(
		"""
		UPDATE import_log
		SET records_received = %s,
			records_saved = %s,
			status = %s,
			error_message = %s
		WHERE import_id = %s;
		""",
		(
			records_received,
			records_saved,
			status,
			error_message,
			import_id,
		),
	)

	conn.commit()


def fetch_networks() -> list[dict[str, Any]]:
	response = requests.get(API_URL, timeout=TIMEOUT_SECONDS)
	response.raise_for_status()
	return response.json().get("networks", [])


def fetch_network_detail(network_id: str) -> dict[str, Any]:
	url = f"{API_URL}/{network_id}"
	response = requests.get(url, timeout=TIMEOUT_SECONDS)
	response.raise_for_status()
	return response.json().get("network", {})


def save_country(
	conn: Connection,
	country_code: str,
	name: str,
) -> int:
	if not country_code:
		return 0

	conn.execute(
		"""
		INSERT INTO countries (country_code, name)
		VALUES (%s, %s)
		ON CONFLICT(country_code) DO UPDATE SET
			name = EXCLUDED.name;
		""",
		(country_code, name),
	)

	return 1


def save_city(
	conn: Connection,
	city_id: str,
	location: dict[str, Any],
) -> int:
	if not city_id or not location:
		return 0

	conn.execute(
		"""
		INSERT INTO cities (
			city_id,
			name,
			country_code,
			latitude,
			longitude
		)
		VALUES (%s, %s, %s, %s, %s)
		ON CONFLICT(city_id) DO UPDATE SET
			name = EXCLUDED.name,
			country_code = EXCLUDED.country_code,
			latitude = EXCLUDED.latitude,
			longitude = EXCLUDED.longitude;
		""",
		(
			city_id,
			location.get("city", "brak nazwy"),
			location.get("country"),
			location.get("latitude"),
			location.get("longitude"),
		),
	)

	return 1


def save_network(
	conn: Connection,
	network: dict[str, Any],
	city_id: str,
	timestamp: datetime,
) -> int:
	if not network or not network.get("id"):
		return 0

	company = network.get("company")

	if isinstance(company, list):
		company = ", ".join(company)

	location = network.get("location") or {}

	conn.execute(
		"""
		INSERT INTO networks (
			network_id,
			name,
			company,
			href,
			city_id,
			latitude,
			longitude,
			raw_json,
			first_seen_at
		)
		VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
		ON CONFLICT(network_id) DO UPDATE SET
			name = EXCLUDED.name,
			company = EXCLUDED.company,
			href = EXCLUDED.href,
			city_id = EXCLUDED.city_id,
			latitude = EXCLUDED.latitude,
			longitude = EXCLUDED.longitude,
			raw_json = EXCLUDED.raw_json;
		""",
		(
			network["id"],
			network.get("name", "brak nazwy"),
			company,
			network.get("href"),
			city_id,
			location.get("latitude"),
			location.get("longitude"),
			json.dumps(network, ensure_ascii=False),
			timestamp,
		),
	)

	return 1


def save_station(
	conn: Connection,
	station_id: str,
	network_id: str,
	station: dict[str, Any],
	timestamp: datetime,
) -> int:
	if not station or not station.get("id"):
		return 0

	extra = station.get("extra") or {}

	conn.execute(
		"""
		INSERT INTO stations (
			station_id,
			network_id,
			external_id,
			name,
			latitude,
			longitude,
			raw_json,
			total_slots,
			first_seen_at,
			last_seen_at
		)
		VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
		ON CONFLICT(station_id) DO UPDATE SET
			name = EXCLUDED.name,
			latitude = EXCLUDED.latitude,
			longitude = EXCLUDED.longitude,
			raw_json = EXCLUDED.raw_json,
			total_slots = EXCLUDED.total_slots,
			last_seen_at = EXCLUDED.last_seen_at;
		""",
		(
			station_id,
			network_id,
			station["id"],
			station.get("name", "brak nazwy"),
			station.get("latitude"),
			station.get("longitude"),
			json.dumps(station, ensure_ascii=False),
			extra.get("slots"),
			timestamp,
			timestamp,
		),
	)

	return 1


def save_station_history(
	conn: Connection,
	station_id: str,
	station: dict[str, Any],
	*,
	import_id: int,
	imported_at: datetime,
) -> int:
	if not station or not station.get("id"):
		return 0

	reported_at = station.get("timestamp") or imported_at

	if isinstance(reported_at, str) and reported_at.endswith("Z") and "+" in reported_at:
		reported_at = reported_at[:-1]

	extra = station.get("extra") or {}

	cur = conn.execute(
		"""
		INSERT INTO station_history (
			station_id,
			free_bikes,
			empty_slots,
			ebikes,
			timestamp,
			imported_at,
			import_id
		)
		VALUES (%s, %s, %s, %s, %s, %s, %s)
		ON CONFLICT(station_id, timestamp) DO NOTHING;
		""",
		(
			station_id,
			station.get("free_bikes"),
			station.get("empty_slots"),
			extra.get("ebikes"),
			reported_at,
			imported_at,
			import_id,
		),
	)

	return cur.rowcount


def run_import() -> ImportStats:
	timestamp = utc_now()

	with psycopg.connect(**DB_CONFIG) as conn:
		create_schema(conn)
		import_id = start_import_log(conn, imported_at=timestamp)

		received = 0
		saved = 0

		try:
			networks = fetch_networks()

			country_networks = [
				network
				for network in networks
				if (network.get("location") or {}).get("country")
				== COUNTRY_CODE
			]

			for network in country_networks:
				network_id = network.get("id")

				if not network_id:
					continue

				detail = fetch_network_detail(network_id)

				if not detail:
					continue

				location = detail.get("location") or {}

				if not location.get("country") or not location.get("city"):
					continue

				country_code = location["country"]
				city_id = f'{country_code}:{location["city"]}'
				stations = detail.get("stations") or []

				save_country(
					conn,
					country_code,
					country_code,
				)

				save_city(
					conn,
					city_id,
					location,
				)

				save_network(
					conn,
					detail,
					city_id,
					timestamp,
				)

				for station in stations:
					external_station_id = station.get("id")

					if not external_station_id:
						continue

					station_id = (
						f"{network_id}:{external_station_id}"
					)

					save_station(
						conn,
						station_id,
						network_id,
						station,
						timestamp,
					)

					received += 1

					saved += save_station_history(
						conn,
						station_id,
						station,
						import_id=import_id,
						imported_at=timestamp,
					)

			conn.commit()

			finish_import_log(
				conn,
				import_id=import_id,
				records_received=received,
				records_saved=saved,
				status="SUCCESS",
			)

			return ImportStats(
				records_received=received,
				records_saved=saved,
			)

		except Exception as exc:
			conn.rollback()

			finish_import_log(
				conn,
				import_id=import_id,
				records_received=received,
				records_saved=saved,
				status="ERROR",
				error_message=str(exc),
			)

			raise


if __name__ == "__main__":
	stats = run_import()
	print(f"Received {stats.records_received}, saved {stats.records_saved}")