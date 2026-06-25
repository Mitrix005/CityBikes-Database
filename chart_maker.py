import tkinter as tk
from tkinter import messagebox, ttk

import matplotlib

matplotlib.use("TkAgg")

import matplotlib.dates as mdates
import pandas as pd
from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg, NavigationToolbar2Tk)
from matplotlib.figure import Figure

import psycopg
from psycopg.rows import dict_row


DB_CONFIG = {
	"host": "localhost",
	"port": 5432,
	"dbname": "citybikes",
	"user": "postgres",
	"password": "root",
}


def get_connection() -> psycopg.Connection:
	return psycopg.connect(**DB_CONFIG, row_factory=dict_row)


def query_to_dataframe(query: str, params: dict | tuple | None = None) -> pd.DataFrame:
	with get_connection() as connection:
		with connection.cursor() as cursor:
			cursor.execute(query, params)
			rows = cursor.fetchall()
	return pd.DataFrame(rows)


def test_database_connection() -> tuple[str, str]:
	query = """
		SELECT
			current_database() AS database_name,
			current_user AS database_user;
	"""
	with get_connection() as connection:
		with connection.cursor() as cursor:
			cursor.execute(query)
			result = cursor.fetchone()
	if result is None:
		raise RuntimeError("Błąd połączenia.")
	return (str(result["database_name"]), str(result["database_user"]))


def load_city_names() -> list[str]:
	query = """
		SELECT DISTINCT c.name AS city_name
		FROM cities AS c
		INNER JOIN networks AS n ON n.city_id = c.city_id
		ORDER BY c.name;
	"""
	data = query_to_dataframe(query)
	if data.empty:
		return []
	return data["city_name"].tolist()


def load_network_names() -> list[str]:
	query = """
		SELECT name FROM networks ORDER BY name;
	"""
	data = query_to_dataframe(query)
	if data.empty:
		return []
	return data["name"].tolist()


def load_latest_station_data(
	city_filter: str | None = None,
	network_filter: str | None = None,
	status_filter: str = "wszystkie",
	min_total_slots: int = 0,
) -> pd.DataFrame:
	query = """
		SELECT DISTINCT ON (s.station_id)
			s.station_id,
			s.name AS station_name,
			s.latitude,
			s.longitude,
			s.total_slots,
			s.network_id,
			n.name AS network_name,
			c.name AS city_name,
			co.name AS country_name,
			h.free_bikes,
			h.empty_slots,
			h.ebikes,
			h.timestamp
		FROM stations AS s
		INNER JOIN station_history AS h ON h.station_id = s.station_id
		LEFT JOIN networks AS n ON n.network_id = s.network_id
		LEFT JOIN cities AS c ON c.city_id = n.city_id
		LEFT JOIN countries AS co ON co.country_code = c.country_code
		WHERE
			s.latitude IS NOT NULL
			AND s.longitude IS NOT NULL
			AND h.timestamp IS NOT NULL
			AND (%(city)s::text IS NULL OR c.name = %(city)s)
			AND (%(network)s::text IS NULL OR n.name = %(network)s)
			AND (s.total_slots IS NULL OR s.total_slots >= %(min_slots)s)
		ORDER BY
			s.station_id,
			h.timestamp DESC,
			h.record_id DESC;
	"""
	params = {
		"city": city_filter,
		"network": network_filter,
		"min_slots": min_total_slots,
	}
	data = query_to_dataframe(query, params)
	if not data.empty:
		data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
		data["free_bikes"] = pd.to_numeric(data["free_bikes"], errors="coerce")
		if status_filter == "dostępne":
			data = data[data["free_bikes"] > 3]
		elif status_filter == "mało":
			data = data[(data["free_bikes"] >= 1) & (data["free_bikes"] <= 3)]
		elif status_filter == "puste":
			data = data[(data["free_bikes"].isna()) | (data["free_bikes"] <= 0)]
	return data.reset_index(drop=True)


def load_bikes_time_series(
	hours_back: int = 24,
	city_filter: str | None = None,
	granularity: str = "hour",
) -> pd.DataFrame:
	if granularity not in ("hour", "day"):
		granularity = "hour"

	query = f"""
		SELECT
			DATE_TRUNC('{granularity}', h.timestamp) AS measurement_hour,
			ROUND(AVG(h.free_bikes)::numeric, 2) AS average_available_bikes,
			SUM(h.free_bikes) AS total_available_bikes,
			SUM(h.ebikes) AS total_ebikes,
			COUNT(*) AS measurements_count,
			COUNT(DISTINCT h.station_id) AS stations_count
		FROM station_history AS h
		LEFT JOIN stations AS s ON s.station_id = h.station_id
		LEFT JOIN networks AS n ON n.network_id = s.network_id
		LEFT JOIN cities AS c ON c.city_id = n.city_id
		WHERE
			h.timestamp IS NOT NULL
			AND h.free_bikes IS NOT NULL
			AND h.timestamp >= NOW() - (%(hours)s * INTERVAL '1 hour')
			AND (%(city)s::text IS NULL OR c.name = %(city)s)
		GROUP BY DATE_TRUNC('{granularity}', h.timestamp)
		ORDER BY measurement_hour;
	"""
	params = {
		"hours": hours_back,
		"city": city_filter,
	}
	data = query_to_dataframe(query, params)
	if data.empty:
		return data
	data["measurement_hour"] = pd.to_datetime(data["measurement_hour"], errors="coerce")
	data["average_available_bikes"] = pd.to_numeric(data["average_available_bikes"], errors="coerce")
	data["total_available_bikes"] = pd.to_numeric(data["total_available_bikes"], errors="coerce")
	data["total_ebikes"] = pd.to_numeric(data["total_ebikes"], errors="coerce")
	data = data.dropna(subset=["measurement_hour", "average_available_bikes"])
	return data.sort_values("measurement_hour").reset_index(drop=True)


def load_most_available_stations(
	limit: int = 10,
	city_filter: str | None = None,
	sort_mode: str = "najwięcej rowerów",
) -> pd.DataFrame:
	if not isinstance(limit, int) or isinstance(limit, bool):
		raise TypeError("limit musi być liczbą całkowitą.")
	if limit <= 0:
		raise ValueError("limit musi być większy od zera.")

	order_clause = "latest.free_bikes DESC, latest.station_name ASC"
	if sort_mode == "najmniej rowerów":
		order_clause = "latest.free_bikes ASC, latest.station_name ASC"
	elif sort_mode == "najwięcej wolnych miejsc":
		order_clause = "latest.empty_slots DESC NULLS LAST, latest.station_name ASC"

	query = f"""
		SELECT
			latest.station_id,
			latest.station_name,
			latest.city_name,
			latest.network_name,
			latest.free_bikes,
			latest.empty_slots,
			latest.ebikes,
			latest.total_slots,
			latest.timestamp
		FROM (
			SELECT DISTINCT ON (s.station_id)
				s.station_id,
				s.name AS station_name,
				c.name AS city_name,
				n.name AS network_name,
				s.total_slots,
				h.free_bikes,
				h.empty_slots,
				h.ebikes,
				h.timestamp
			FROM stations AS s
			INNER JOIN station_history AS h ON h.station_id = s.station_id
			LEFT JOIN networks AS n ON n.network_id = s.network_id
			LEFT JOIN cities AS c ON c.city_id = n.city_id
			WHERE
				h.free_bikes IS NOT NULL
				AND h.timestamp IS NOT NULL
				AND (%(city)s::text IS NULL OR c.name = %(city)s)
			ORDER BY
				s.station_id,
				h.timestamp DESC,
				h.record_id DESC
		) AS latest
		ORDER BY {order_clause}
		LIMIT %(limit)s;
	"""
	params = {
		"limit": limit,
		"city": city_filter,
	}
	data = query_to_dataframe(query, params)
	if not data.empty:
		data["free_bikes"] = pd.to_numeric(data["free_bikes"], errors="coerce")
		data["empty_slots"] = pd.to_numeric(data["empty_slots"], errors="coerce")
		data["total_slots"] = pd.to_numeric(data["total_slots"], errors="coerce")
		data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
	return data


def get_station_color(free_bikes: int | float) -> str:
	if pd.isna(free_bikes) or free_bikes <= 0:
		return "red"
	if free_bikes <= 3:
		return "orange"
	return "green"


def create_empty_figure(message: str) -> Figure:
	figure = Figure(figsize=(10, 7), dpi=100)
	axis = figure.add_subplot(111)
	axis.text(0.5, 0.5, message, horizontalalignment="center", verticalalignment="center", transform=axis.transAxes, fontsize=13)
	axis.set_axis_off()
	figure.tight_layout()
	return figure


def create_station_map(data: pd.DataFrame) -> Figure:
	if data.empty:
		return create_empty_figure("Brak danych do wyświetlenia mapy stacji.")

	required_columns = {"station_name", "longitude", "latitude", "free_bikes"}
	if not required_columns.issubset(data.columns):
		return create_empty_figure("W danych brakuje wymaganych kolumn.")

	plot_data = data.dropna(subset=["station_name", "longitude", "latitude", "free_bikes"]).copy()
	plot_data = plot_data.reset_index(drop=True)

	if plot_data.empty:
		return create_empty_figure("Brak poprawnych danych stacji.")

	plot_data["longitude"] = pd.to_numeric(plot_data["longitude"], errors="coerce")
	plot_data["latitude"] = pd.to_numeric(plot_data["latitude"], errors="coerce")
	plot_data["free_bikes"] = pd.to_numeric(plot_data["free_bikes"], errors="coerce")
	plot_data = plot_data.dropna(subset=["longitude", "latitude", "free_bikes"])

	figure = Figure(figsize=(11, 8), dpi=100)
	axis = figure.add_subplot(111)

	colors = plot_data["free_bikes"].apply(get_station_color)

	station_points = axis.scatter(
		plot_data["longitude"],
		plot_data["latitude"],
		c=colors,
		s=22,
		alpha=0.8,
		edgecolors="black",
		linewidths=0.25,
		picker=True,
		pickradius=7,
		zorder=2,
	)

	axis.set_title("Aktualny stan stacji rowerowych", fontsize=15, pad=15)
	axis.set_xlabel("Długość geograficzna")
	axis.set_ylabel("Szerokość geograficzna")
	axis.grid(True, alpha=0.25, zorder=1)

	axis.scatter([], [], c="green", s=40, edgecolors="black", label="Więcej niż 3 rowery")
	axis.scatter([], [], c="orange", s=40, edgecolors="black", label="Od 1 do 3 rowerów")
	axis.scatter([], [], c="red", s=40, edgecolors="black", label="Brak rowerów")
	axis.legend(title="Status stacji", loc="lower left")

	annotation = axis.annotate(
		"",
		xy=(0, 0),
		xytext=(15, 15),
		textcoords="offset points",
		bbox={"boxstyle": "round,pad=0.5", "facecolor": "white", "edgecolor": "black", "alpha": 0.95},
		arrowprops={"arrowstyle": "->"},
		zorder=3,
	)
	annotation.set_visible(False)

	def on_station_click(event) -> None:
		if event.artist is not station_points:
			return
		if len(event.ind) == 0:
			return
		station_index = int(event.ind[0])
		station = plot_data.iloc[station_index]
		longitude = float(station["longitude"])
		latitude = float(station["latitude"])
		free_bikes = int(station["free_bikes"])
		annotation.xy = (longitude, latitude)
		annotation.set_text(
			f"Nazwa: {station['station_name']}\n"
			f"Szerokość: {latitude:.6f}\n"
			f"Długość: {longitude:.6f}\n"
			f"Dostępne rowery: {free_bikes}"
		)
		annotation.set_visible(True)
		figure.canvas.draw_idle()

	figure.canvas.mpl_connect("pick_event", on_station_click)
	figure.tight_layout()
	return figure


def create_time_series_chart(data: pd.DataFrame) -> Figure:
	if data.empty:
		return create_empty_figure("Brak danych historycznych do utworzenia wykresu.")

	required_columns = {"measurement_hour", "average_available_bikes"}
	if not required_columns.issubset(data.columns):
		return create_empty_figure("W pobranych danych brakuje wymaganych kolumn.")

	plot_data = data.copy()
	plot_data["average_available_bikes"] = pd.to_numeric(plot_data["average_available_bikes"], errors="coerce")
	plot_data = plot_data.dropna(subset=["measurement_hour", "average_available_bikes"])
	plot_data = plot_data.sort_values("measurement_hour")

	if plot_data.empty:
		return create_empty_figure("Nie znaleziono poprawnych danych czasowych.")

	figure = Figure(figsize=(10, 7), dpi=100)
	axis = figure.add_subplot(111)
	axis.plot(plot_data["measurement_hour"], plot_data["average_available_bikes"], linewidth=1.8, marker="o", markersize=3)
	axis.set_title("Średnia liczba dostępnych rowerów w czasie", fontsize=15, pad=15)
	axis.set_xlabel("Data i godzina")
	axis.set_ylabel("Średnia liczba rowerów na stację")
	axis.grid(True, alpha=0.3)
	axis.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m\n%H:%M"))
	figure.autofmt_xdate()
	figure.tight_layout()
	return figure


def create_most_available_stations_chart(data: pd.DataFrame, sort_mode: str = "najwięcej rowerów") -> Figure:
	if data.empty:
		return create_empty_figure("Brak danych do utworzenia rankingu dostępności.")

	value_column = "free_bikes"
	x_label = "Liczba dostępnych rowerów"
	title = "Stacje z największą liczbą dostępnych rowerów"
	ascending_bars = True

	if sort_mode == "najmniej rowerów":
		title = "Stacje z najmniejszą liczbą dostępnych rowerów"
		ascending_bars = False
	elif sort_mode == "najwięcej wolnych miejsc":
		value_column = "empty_slots"
		x_label = "Liczba wolnych miejsc"
		title = "Stacje z największą liczbą wolnych miejsc"

	required_columns = {"station_name", value_column}
	if not required_columns.issubset(data.columns):
		return create_empty_figure("W danych brakuje wymaganych kolumn.")

	plot_data = data.copy()
	plot_data[value_column] = pd.to_numeric(plot_data[value_column], errors="coerce")
	plot_data = plot_data.dropna(subset=["station_name", value_column])
	plot_data = plot_data.sort_values(value_column, ascending=ascending_bars)

	if plot_data.empty:
		return create_empty_figure("Brak poprawnych danych do utworzenia wykresu.")

	figure_height = max(7, len(plot_data) * 0.42)
	figure = Figure(figsize=(10, figure_height), dpi=100)
	axis = figure.add_subplot(111)
	bars = axis.barh(plot_data["station_name"], plot_data[value_column])
	axis.set_title(title, fontsize=15, pad=15)
	axis.set_xlabel(x_label)
	axis.set_ylabel("Stacja")
	axis.grid(True, axis="x", alpha=0.3)

	for bar, value in zip(bars, plot_data[value_column]):
		axis.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2, f"{int(value)}", verticalalignment="center")

	maximum = float(plot_data[value_column].max())
	axis.set_xlim(0, maximum + max(2, maximum * 0.1))
	figure.tight_layout()
	return figure


class CityBikesChartsApplication:

	def __init__(self, root: tk.Tk) -> None:
		self.root = root
		self.root.title("CityBikes — wykresy danych z PostgreSQL")
		self.root.geometry("1300x900")
		self.root.minsize(900, 600)
		self.time_series_data = pd.DataFrame()
		self.time_canvas = None
		self.map_canvas = None
		self.ranking_canvas = None
		self.create_interface()
		self.load_filter_options()
		self.refresh_all()

	def create_interface(self) -> None:
		top_frame = ttk.Frame(self.root, padding=10)
		top_frame.pack(fill=tk.X)

		title_label = ttk.Label(top_frame, text="Analiza danych CityBikes", font=("Arial", 18, "bold"))
		title_label.pack(side=tk.LEFT)

		refresh_button = ttk.Button(top_frame, text="Odśwież dane", command=self.refresh_all)
		refresh_button.pack(side=tk.RIGHT)

		self.status_label = ttk.Label(self.root, text="", padding=(10, 0, 10, 5))
		self.status_label.pack(fill=tk.X)

		self.notebook = ttk.Notebook(self.root)
		self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

		self.map_tab = ttk.Frame(self.notebook)
		self.time_tab = ttk.Frame(self.notebook)
		self.ranking_tab = ttk.Frame(self.notebook)

		self.notebook.add(self.map_tab, text="Rozmieszczenie stacji")
		self.notebook.add(self.time_tab, text="Dostępność w czasie")
		self.notebook.add(self.ranking_tab, text="Ranking stacji")

		self.build_map_controls()
		self.build_time_controls()
		self.build_ranking_controls()

	def build_map_controls(self) -> None:
		controls = ttk.LabelFrame(self.map_tab, text="Filtry mapy", padding=10)
		controls.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

		ttk.Label(controls, text="Miasto:").grid(row=0, column=0, sticky="w", padx=5)
		self.map_city = ttk.Combobox(controls, values=["wszystkie"], state="readonly", width=20)
		self.map_city.set("wszystkie")
		self.map_city.grid(row=0, column=1, padx=5)
		self.map_city.bind("<<ComboboxSelected>>", lambda e: self.refresh_map())

		ttk.Label(controls, text="Sieć:").grid(row=0, column=2, sticky="w", padx=5)
		self.map_network = ttk.Combobox(controls, values=["wszystkie"], state="readonly", width=20)
		self.map_network.set("wszystkie")
		self.map_network.grid(row=0, column=3, padx=5)
		self.map_network.bind("<<ComboboxSelected>>", lambda e: self.refresh_map())

		ttk.Label(controls, text="Status:").grid(row=0, column=4, sticky="w", padx=5)
		self.map_status = ttk.Combobox(controls, values=["wszystkie", "dostępne", "mało", "puste"], state="readonly", width=15)
		self.map_status.set("wszystkie")
		self.map_status.grid(row=0, column=5, padx=5)
		self.map_status.bind("<<ComboboxSelected>>", lambda e: self.refresh_map())

		ttk.Label(controls, text="Min. pojemność:").grid(row=0, column=6, sticky="w", padx=5)
		self.map_min_slots = tk.IntVar(value=0)
		self.map_min_slots_label = ttk.Label(controls, text="0")
		self.map_min_slots_label.grid(row=0, column=8, padx=5)
		self.map_min_slots_scale = ttk.Scale(
			controls,
			from_=0,
			to=40,
			orient=tk.HORIZONTAL,
			length=150,
		)
		self.map_min_slots_scale.set(0)
		self.map_min_slots_scale.grid(row=0, column=7, padx=5)

		self.map_chart_frame = ttk.Frame(self.map_tab)
		self.map_chart_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

		self.map_min_slots_scale.configure(command=self.on_min_slots_change)

	def on_min_slots_change(self, value: str) -> None:
		new_value = int(round(float(value)))
		self.map_min_slots.set(new_value)
		self.map_min_slots_label.config(text=str(new_value))
		self.refresh_map()

	def build_time_controls(self) -> None:
		controls = ttk.LabelFrame(self.time_tab, text="Filtry szeregu czasowego", padding=10)
		controls.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

		ttk.Label(controls, text="Okres:").grid(row=0, column=0, sticky="w", padx=5)
		self.time_period = ttk.Combobox(controls, values=["24 h", "3 dni", "7 dni", "30 dni"], state="readonly", width=10)
		self.time_period.set("24 h")
		self.time_period.grid(row=0, column=1, padx=5)
		self.time_period.bind("<<ComboboxSelected>>", lambda e: self.refresh_time_series())

		ttk.Label(controls, text="Miasto:").grid(row=0, column=2, sticky="w", padx=5)
		self.time_city = ttk.Combobox(controls, values=["wszystkie"], state="readonly", width=20)
		self.time_city.set("wszystkie")
		self.time_city.grid(row=0, column=3, padx=5)
		self.time_city.bind("<<ComboboxSelected>>", lambda e: self.refresh_time_series())

		ttk.Label(controls, text="Granularność:").grid(row=0, column=4, sticky="w", padx=5)
		self.time_granularity = ttk.Combobox(controls, values=["godzina", "dzień"], state="readonly", width=10)
		self.time_granularity.set("godzina")
		self.time_granularity.grid(row=0, column=5, padx=5)
		self.time_granularity.bind("<<ComboboxSelected>>", lambda e: self.refresh_time_series())

		slider_frame = ttk.Frame(self.time_tab, padding=10)
		slider_frame.pack(side=tk.TOP, fill=tk.X)

		ttk.Label(slider_frame, text="Data końcowa:").pack(side=tk.LEFT, padx=(0, 10))
		self.time_slider_value_label = ttk.Label(slider_frame, text="Brak danych", width=20)
		self.time_slider_value_label.pack(side=tk.RIGHT, padx=(10, 0))
		self.time_slider = ttk.Scale(slider_frame, from_=0, to=0, orient=tk.HORIZONTAL, command=self.on_time_slider_change)
		self.time_slider.pack(side=tk.LEFT, fill=tk.X, expand=True)

		self.time_chart_frame = ttk.Frame(self.time_tab)
		self.time_chart_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

	def build_ranking_controls(self) -> None:
		controls = ttk.LabelFrame(self.ranking_tab, text="Filtry rankingu", padding=10)
		controls.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

		ttk.Label(controls, text="Liczba stacji:").grid(row=0, column=0, sticky="w", padx=5)
		self.ranking_limit_label = ttk.Label(controls, text="10")
		self.ranking_limit_label.grid(row=0, column=2, padx=5)
		self.ranking_limit_scale = ttk.Scale(
			controls,
			from_=5,
			to=50,
			orient=tk.HORIZONTAL,
			length=150,
		)
		self.ranking_limit_scale.set(10)
		self.ranking_limit_scale.grid(row=0, column=1, padx=5)

		ttk.Label(controls, text="Miasto:").grid(row=0, column=3, sticky="w", padx=5)
		self.ranking_city = ttk.Combobox(controls, values=["wszystkie"], state="readonly", width=20)
		self.ranking_city.set("wszystkie")
		self.ranking_city.grid(row=0, column=4, padx=5)
		self.ranking_city.bind("<<ComboboxSelected>>", lambda e: self.refresh_ranking())

		ttk.Label(controls, text="Sortowanie:").grid(row=0, column=5, sticky="w", padx=5)
		self.ranking_sort = ttk.Combobox(
			controls,
			values=["najwięcej rowerów", "najmniej rowerów", "najwięcej wolnych miejsc"],
			state="readonly",
			width=25,
		)
		self.ranking_sort.set("najwięcej rowerów")
		self.ranking_sort.grid(row=0, column=6, padx=5)
		self.ranking_sort.bind("<<ComboboxSelected>>", lambda e: self.refresh_ranking())

		self.ranking_chart_frame = ttk.Frame(self.ranking_tab)
		self.ranking_chart_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

		self.ranking_limit_scale.configure(command=self.on_ranking_limit_change)

	def on_ranking_limit_change(self, value: str) -> None:
		new_value = int(round(float(value)))
		self.ranking_limit_label.config(text=str(new_value))
		self.refresh_ranking()

	def load_filter_options(self) -> None:
		try:
			cities = load_city_names()
			networks = load_network_names()
		except Exception:
			cities = []
			networks = []

		city_values = ["wszystkie"] + cities
		network_values = ["wszystkie"] + networks

		self.map_city["values"] = city_values
		self.time_city["values"] = city_values
		self.ranking_city["values"] = city_values
		self.map_network["values"] = network_values

	def on_time_slider_change(self, value: str) -> None:
		if self.time_series_data.empty:
			return
		end_index = int(round(float(value)))
		end_index = max(0, min(end_index, len(self.time_series_data) - 1))
		end_time = self.time_series_data.loc[end_index, "measurement_hour"]
		self.time_slider_value_label.config(text=end_time.strftime("%d.%m.%Y %H:%M"))
		start_index = max(0, end_index - 9)
		visible_data = self.time_series_data.iloc[start_index:end_index + 1].copy()
		self.update_time_series_chart(visible_data)

	def update_time_series_chart(self, data: pd.DataFrame) -> None:
		self.clear_tab(self.time_chart_frame)
		figure = create_time_series_chart(data)
		self.time_canvas = self.display_figure(self.time_chart_frame, figure)

	@staticmethod
	def clear_tab(tab: ttk.Frame) -> None:
		for widget in tab.winfo_children():
			widget.destroy()

	@staticmethod
	def display_figure(tab: ttk.Frame, figure: Figure) -> FigureCanvasTkAgg:
		canvas = FigureCanvasTkAgg(figure, master=tab)
		canvas.draw()
		toolbar = NavigationToolbar2Tk(canvas, tab, pack_toolbar=False)
		toolbar.update()
		toolbar.pack(side=tk.BOTTOM, fill=tk.X)
		canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
		return canvas

	def refresh_map(self) -> None:
		try:
			city = self.map_city.get()
			network = self.map_network.get()
			status = self.map_status.get()
			min_slots = self.map_min_slots.get()

			station_data = load_latest_station_data(
				city_filter=None if city == "wszystkie" else city,
				network_filter=None if network == "wszystkie" else network,
				status_filter=status,
				min_total_slots=min_slots,
			)

			self.clear_tab(self.map_chart_frame)
			self.map_canvas = self.display_figure(self.map_chart_frame, create_station_map(station_data))
		except Exception as error:
			messagebox.showerror("Błąd", f"Nie udało się odświeżyć mapy.\n\n{error}")

	def refresh_time_series(self) -> None:
		try:
			period_map = {"24 h": 24, "3 dni": 72, "7 dni": 168, "30 dni": 720}
			hours = period_map.get(self.time_period.get(), 24)
			city = self.time_city.get()
			granularity_map = {"godzina": "hour", "dzień": "day"}
			granularity = granularity_map.get(self.time_granularity.get(), "hour")

			self.time_series_data = load_bikes_time_series(
				hours_back=hours,
				city_filter=None if city == "wszystkie" else city,
				granularity=granularity,
			)

			if self.time_series_data.empty:
				self.time_slider.configure(from_=0, to=0)
				self.time_slider.set(0)
				self.time_slider_value_label.config(text="Brak danych")
				self.update_time_series_chart(self.time_series_data)
			else:
				last_index = len(self.time_series_data) - 1
				self.time_slider.configure(from_=0, to=last_index)
				self.time_slider.set(last_index)
				self.on_time_slider_change(str(last_index))
		except Exception as error:
			messagebox.showerror("Błąd", f"Nie udało się odświeżyć wykresu czasowego.\n\n{error}")

	def refresh_ranking(self) -> None:
		try:
			limit = int(round(float(self.ranking_limit_scale.get())))
			city = self.ranking_city.get()
			sort_mode = self.ranking_sort.get()

			ranking_data = load_most_available_stations(
				limit=limit,
				city_filter=None if city == "wszystkie" else city,
				sort_mode=sort_mode,
			)

			self.clear_tab(self.ranking_chart_frame)
			self.ranking_canvas = self.display_figure(
				self.ranking_chart_frame,
				create_most_available_stations_chart(ranking_data, sort_mode=sort_mode),
			)
		except Exception as error:
			messagebox.showerror("Błąd", f"Nie udało się odświeżyć rankingu.\n\n{error}")

	def refresh_all(self) -> None:
		try:
			self.status_label.config(text="Łączenie z bazą citybikes...")
			self.root.update_idletasks()
			database_name, database_user = test_database_connection()

			self.refresh_map()
			self.refresh_time_series()
			self.refresh_ranking()

			self.status_label.config(
				text=(
					f"Połączono z bazą: {database_name} | "
					f"Użytkownik: {database_user} | "
					f"Punktów czasowych: {len(self.time_series_data)}"
				)
			)
		except Exception as error:
			self.status_label.config(text="Nie udało się pobrać danych.")
			messagebox.showerror("Błąd", f"Nie udało się połączyć z bazą citybikes.\n\n{error}")


def main() -> None:
	root = tk.Tk()
	CityBikesChartsApplication(root)
	root.mainloop()


if __name__ == "__main__":
	main()