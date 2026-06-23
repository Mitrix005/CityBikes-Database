CREATE TABLE IF NOT EXISTS countries (
	country_code TEXT PRIMARY KEY,
	name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cities (
	city_id TEXT PRIMARY KEY,
	name TEXT NOT NULL,
	country_code TEXT NOT NULL,
	latitude DOUBLE PRECISION,
	longitude DOUBLE PRECISION,
	FOREIGN KEY (country_code) REFERENCES countries (country_code)
);

CREATE TABLE IF NOT EXISTS networks (
	network_id TEXT PRIMARY KEY,
	name TEXT NOT NULL,
	company TEXT,
	href TEXT,
	city_id TEXT,
	latitude DOUBLE PRECISION,
	longitude DOUBLE PRECISION,
	raw_json TEXT,
	first_seen_at TIMESTAMPTZ NOT NULL,
	FOREIGN KEY (city_id) REFERENCES cities (city_id)
);

CREATE TABLE IF NOT EXISTS stations (
	station_id TEXT PRIMARY KEY,
	network_id TEXT NOT NULL,
	external_id TEXT NOT NULL,
	name TEXT NOT NULL,
	latitude DOUBLE PRECISION,
	longitude DOUBLE PRECISION,
	raw_json TEXT,
	total_slots INTEGER,
	first_seen_at TIMESTAMPTZ NOT NULL,
	last_seen_at TIMESTAMPTZ NOT NULL,
	FOREIGN KEY (network_id) REFERENCES networks (network_id)
);

CREATE TABLE IF NOT EXISTS import_log (
	import_id SERIAL PRIMARY KEY,
	imported_at TIMESTAMPTZ NOT NULL,
	api_name TEXT NOT NULL,
	endpoint TEXT NOT NULL,
	records_received INTEGER NOT NULL,
	records_saved INTEGER NOT NULL,
	status TEXT NOT NULL,
	error_message TEXT
);

CREATE TABLE IF NOT EXISTS station_history (
	record_id SERIAL PRIMARY KEY,
	station_id TEXT NOT NULL,
	free_bikes INTEGER,
	empty_slots INTEGER,
	ebikes INTEGER,
	timestamp TIMESTAMPTZ NOT NULL,
	imported_at TIMESTAMPTZ NOT NULL,
	import_id INTEGER NOT NULL,
	UNIQUE (station_id, timestamp),
	FOREIGN KEY (station_id) REFERENCES stations (station_id),
	FOREIGN KEY (import_id) REFERENCES import_log (import_id)
);

CREATE INDEX IF NOT EXISTS idx_history_timestamp ON station_history (timestamp);
CREATE INDEX IF NOT EXISTS idx_stations_network ON stations (network_id);
CREATE INDEX IF NOT EXISTS idx_networks_city ON networks (city_id);