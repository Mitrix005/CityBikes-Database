import tkinter as tk
from tkinter import messagebox, ttk

#from pathlib import Path
import matplotlib

matplotlib.use("TkAgg")

import matplotlib.dates as mdates
import pandas as pd
from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg, NavigationToolbar2Tk)
#import matplotlib.image as mpimg
from matplotlib.figure import Figure

import psycopg
from psycopg.rows import dict_row


DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "citybikes",
    "user": "postgres",
    "password": "postgres",
}

#MAP_IMAGE_PATH = Path("mapa_polski2.jpg")

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
        raise RuntimeError("PostgreSQL nie zwrócił danych połączenia.")

    return (str(result["database_name"]), str(result["database_user"]))

def load_latest_station_data() -> pd.DataFrame:

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
        INNER JOIN station_history AS h
            ON h.station_id = s.station_id
        LEFT JOIN networks AS n
            ON n.network_id = s.network_id
        LEFT JOIN cities AS c
            ON c.city_id = n.city_id
        LEFT JOIN countries AS co
            ON co.country_code = c.country_code
        WHERE
            s.latitude IS NOT NULL
            AND s.longitude IS NOT NULL
            AND h.timestamp IS NOT NULL
        ORDER BY
            s.station_id,
            h.timestamp DESC,
            h.record_id DESC;
    """

    data = query_to_dataframe(query)
    if not data.empty:
        data["timestamp"] = pd.to_datetime(data["timestamp"],errors="coerce")

    return data

def load_bikes_time_series() -> pd.DataFrame:

    query = """
        SELECT
            DATE_TRUNC('hour',h.timestamp) AS measurement_hour,
            ROUND(AVG(h.free_bikes)::numeric,2) AS average_available_bikes,
            SUM(h.free_bikes) AS total_available_bikes,
            SUM(h.ebikes) AS total_ebikes,
            COUNT(*) AS measurements_count,
            COUNT(DISTINCT h.station_id) AS stations_count
        FROM station_history AS h
        WHERE
            h.timestamp IS NOT NULL
            AND h.free_bikes IS NOT NULL
            AND h.timestamp >= (DATE_TRUNC('day', NOW()) - INTERVAL '1 day')
        GROUP BY
            DATE_TRUNC('hour', h.timestamp)
        ORDER BY
            measurement_hour;
    """

    data = query_to_dataframe(query)

    if data.empty:
        return data

    data["measurement_hour"] = pd.to_datetime(data["measurement_hour"], errors="coerce")
    data["average_available_bikes"] = pd.to_numeric(data["average_available_bikes"],errors="coerce")
    data["total_available_bikes"] = pd.to_numeric(data["total_available_bikes"],errors="coerce")
    data["total_ebikes"] = pd.to_numeric(data["total_ebikes"],errors="coerce")
    data = data.dropna(subset=["measurement_hour","average_available_bikes"])

    return data.sort_values("measurement_hour").reset_index(drop=True)

def load_most_available_stations(limit: int = 20) -> pd.DataFrame:

    if not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError("limit musi być liczbą całkowitą.")

    if limit <= 0:
        raise ValueError("limit musi być większy od zera.")

    query = """
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

            INNER JOIN station_history AS h
                ON h.station_id = s.station_id

            LEFT JOIN networks AS n
                ON n.network_id = s.network_id

            LEFT JOIN cities AS c
                ON c.city_id = n.city_id

            WHERE
                h.free_bikes IS NOT NULL
                AND h.timestamp IS NOT NULL

            ORDER BY
                s.station_id,
                h.timestamp DESC,
                h.record_id DESC
        ) AS latest

        ORDER BY
            latest.free_bikes DESC,
            latest.station_name ASC

        LIMIT %(limit)s;
    """

    data = query_to_dataframe(
        query,
        {"limit": limit},
    )

    if not data.empty:
        data["free_bikes"] = pd.to_numeric(data["free_bikes"],errors="coerce")
        data["empty_slots"] = pd.to_numeric(data["empty_slots"],errors="coerce")
        data["total_slots"] = pd.to_numeric(data["total_slots"],errors="coerce")
        data["timestamp"] = pd.to_datetime(data["timestamp"],errors="coerce")

    return data

def get_station_color(free_bikes: int | float) -> str:
    """
    Dobiera kolor punktu na podstawie liczby dostępnych rowerów.
    Czerwony: 0 rowerów
    Pomarańczowy: 1 - 3 rowerów
    Zielony: Więcej niż 3 rowery
    """

    if pd.isna(free_bikes) or free_bikes <= 0:
        return "red"

    if free_bikes <= 3:
        return "orange"

    return "green"


def create_empty_figure(message: str) -> Figure:

    figure = Figure(figsize=(10, 7), dpi=100)
    axis = figure.add_subplot(111)

    axis.text(0.5,0.5,message,horizontalalignment="center",verticalalignment="center",transform=axis.transAxes,fontsize=13)
    axis.set_axis_off()
    figure.tight_layout()

    return figure

def create_station_map(data: pd.DataFrame) -> Figure:

    if data.empty:
        return create_empty_figure("Brak danych do wyświetlenia mapy stacji.")

    required_columns = {"station_name","longitude","latitude","free_bikes"}

    if not required_columns.issubset(data.columns):
        return create_empty_figure("W danych brakuje wymaganych kolumn.")

    #if not MAP_IMAGE_PATH.exists():
    #    return create_empty_figure(f"Nie znaleziono pliku mapy:\n{MAP_IMAGE_PATH.resolve()}")

    plot_data = data.dropna(subset=["station_name","longitude","latitude","free_bikes"]).copy()
    plot_data = plot_data.reset_index(drop=True)

    if plot_data.empty:
        return create_empty_figure("Brak poprawnych danych stacji.")

    plot_data["longitude"] = pd.to_numeric(plot_data["longitude"],errors="coerce")
    plot_data["latitude"] = pd.to_numeric(plot_data["latitude"],errors="coerce")
    plot_data["free_bikes"] = pd.to_numeric(plot_data["free_bikes"],errors="coerce")

    plot_data = plot_data.dropna(subset=["longitude","latitude","free_bikes"])

    figure = Figure(figsize=(11, 8),dpi=100)

    axis = figure.add_subplot(111)

    #map_image = mpimg.imread(MAP_IMAGE_PATH)
    #map_extent = [14.0,   # zachodnia granica 24.2,   # wschodnia granica 49.0,   # południowa granica 54.9    # północna granica]

    #axis.imshow(map_image,extent=map_extent,origin="upper",aspect="auto",alpha=0.75,zorder=0)
    
    colors = plot_data["free_bikes"].apply(get_station_color)

    station_points = axis.scatter(plot_data["longitude"],plot_data["latitude"],c=colors,
        s=22,
        alpha=0.8,
        edgecolors="black",
        linewidths=0.25,
        picker=True,
        pickradius=7,
        zorder=2
    )

    axis.set_title("Aktualny stan stacji rowerowych",fontsize=15,pad=15)
    axis.set_xlabel("Długość geograficzna")
    axis.set_ylabel("Szerokość geograficzna")
    #axis.set_xlim(map_extent[0],map_extent[1])
    #axis.set_ylim(map_extent[2],map_extent[3])
    axis.grid(True,alpha=0.25,zorder=1)

    axis.scatter([],[],c="green",s=40,edgecolors="black",label="Więcej niż 3 rowery")
    axis.scatter([],[],c="orange",s=40,edgecolors="black",label="Od 1 do 3 rowerów")
    axis.scatter([],[],c="red",s=40,edgecolors="black",label="Brak rowerów")

    axis.legend(title="Status stacji",loc="lower left")

    annotation = axis.annotate("",xy=(0, 0),xytext=(15, 15),textcoords="offset points",
        bbox={
            "boxstyle": "round,pad=0.5",
            "facecolor": "white",
            "edgecolor": "black",
            "alpha": 0.95
        },
        arrowprops={
            "arrowstyle": "->"
        },
        zorder=3
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

        annotation.xy = (longitude,latitude)

        annotation.set_text(
            f"Nazwa: {station['station_name']}\n"
            f"Szerokość: {latitude:.6f}\n"
            f"Długość: {longitude:.6f}\n"
            f"Dostępne rowery: {free_bikes}"
        )

        annotation.set_visible(True)

        figure.canvas.draw_idle()

    figure.canvas.mpl_connect("pick_event",on_station_click)
    figure.tight_layout()

    return figure

def create_time_series_chart(data: pd.DataFrame) -> Figure:

    if data.empty:
        return create_empty_figure("Brak danych historycznych do utworzenia wykresu.")

    required_columns = {"measurement_hour","average_available_bikes"}

    if not required_columns.issubset(data.columns):
        return create_empty_figure("W pobranych danych brakuje wymaganych kolumn.")

    plot_data = data.copy()

    plot_data["average_available_bikes"] = pd.to_numeric(plot_data["average_available_bikes"],errors="coerce")
    plot_data = plot_data.dropna(subset=["measurement_hour","average_available_bikes"])
    plot_data = plot_data.sort_values("measurement_hour")

    if plot_data.empty:
        return create_empty_figure("Nie znaleziono poprawnych danych czasowych.")

    figure = Figure(figsize=(10, 7), dpi=100)
    axis = figure.add_subplot(111)
    axis.plot(plot_data["measurement_hour"],plot_data["average_available_bikes"],linewidth=1.8,marker="o",markersize=3)
    axis.set_title("Średnia liczba dostępnych rowerów w czasie",fontsize=15,pad=15,)
    axis.set_xlabel("Data i godzina")
    axis.set_ylabel("Średnia liczba rowerów na stację")
    axis.grid(True, alpha=0.3)
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m\n%H:%M"))

    figure.autofmt_xdate()
    figure.tight_layout()

    return figure

def create_most_available_stations_chart(data: pd.DataFrame) -> Figure:

    if data.empty:
        return create_empty_figure("Brak danych do utworzenia rankingu dostępności.")

    required_columns = {"station_name","free_bikes"}

    if not required_columns.issubset(data.columns):
        return create_empty_figure("W danych brakuje wymaganych kolumn.")

    plot_data = data.copy()
    plot_data["free_bikes"] = pd.to_numeric(plot_data["free_bikes"],errors="coerce")
    plot_data = plot_data.dropna(subset=["station_name", "free_bikes"])
    plot_data = plot_data.sort_values("free_bikes",ascending=True,)

    if plot_data.empty:
        return create_empty_figure("Brak poprawnych danych do utworzenia wykresu.")

    figure_height = max(7,len(plot_data) * 0.42)
    figure = Figure(figsize=(10, figure_height),dpi=100)

    axis = figure.add_subplot(111)
    bars = axis.barh(plot_data["station_name"], plot_data["free_bikes"],)
    axis.set_title("Stacje z największą liczbą dostępnych rowerów", fontsize=15, pad=15,)
    axis.set_xlabel("Liczba dostępnych rowerów")
    axis.set_ylabel("Stacja")
    axis.grid(True, axis="x", alpha=0.3)

    for bar, free_bikes in zip(bars, plot_data["free_bikes"],):
        axis.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2, f"{int(free_bikes)}",verticalalignment="center",)

    maximum = float(plot_data["free_bikes"].max())

    axis.set_xlim(0, maximum + max(2, maximum * 0.1),)

    figure.tight_layout()

    return figure


class CityBikesChartsApplication:

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("CityBikes — wykresy danych z PostgreSQL")
        self.root.geometry("1200x800")
        self.root.minsize(900, 600)
        self.time_series_data = pd.DataFrame()
        self.time_canvas = None
        self.create_interface()
        self.refresh_charts()

    def create_interface(self) -> None:

        top_frame = ttk.Frame(self.root,padding=10)
        top_frame.pack(fill=tk.X)

        title_label = ttk.Label(top_frame,text="Analiza danych CityBikes",font=("Arial", 18, "bold"))
        title_label.pack(side=tk.LEFT)

        refresh_button = ttk.Button(top_frame,text="Odśwież dane",command=self.refresh_charts)
        refresh_button.pack(side=tk.RIGHT)

        self.status_label = ttk.Label(self.root,text="",padding=(10, 0, 10, 5))
        self.status_label.pack(fill=tk.X)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH,expand=True, padx=10, pady=10)

        self.map_tab = ttk.Frame(self.notebook)
        self.time_tab = ttk.Frame(self.notebook)
        self.ranking_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.map_tab,text="Rozmieszczenie stacji")
        self.notebook.add(self.time_tab,text="Dostępność w czasie")
        self.notebook.add(self.ranking_tab,text="Najwięcej dostępnych rowerów")

        self.time_controls_frame = ttk.Frame(self.time_tab,padding=10)
        self.time_controls_frame.pack(side=tk.TOP,fill=tk.X)
       
        self.time_chart_frame = ttk.Frame(self.time_tab)
        self.time_chart_frame.pack(side=tk.TOP,fill=tk.BOTH,expand=True)
       
        self.time_slider_label = ttk.Label(self.time_controls_frame,text="Data końcowa:")
        self.time_slider_label.pack(side=tk.LEFT,padx=(0, 10))
        
        self.time_slider_value_label = ttk.Label(self.time_controls_frame,text="Brak danych",width=20)
        self.time_slider_value_label.pack(side=tk.RIGHT,padx=(10, 0))
        
        self.time_slider = ttk.Scale(self.time_controls_frame,from_=0,to=0,orient=tk.HORIZONTAL,command=self.on_time_slider_change)
        self.time_slider.pack(side=tk.LEFT,fill=tk.X,expand=True)

    def on_time_slider_change(self, value: str) -> None:
        
        if self.time_series_data.empty:
            return

        end_index = int(round(float(value)))

        end_index = max(0,min(end_index,len(self.time_series_data) - 1))

        end_time = self.time_series_data.loc[end_index,"measurement_hour"]
        self.time_slider_value_label.config(text=end_time.strftime("%d.%m.%Y %H:%M"))
        start_index = max(0,end_index - 9)
        visible_data = self.time_series_data.iloc[start_index:end_index + 1].copy()

        self.update_time_series_chart(visible_data)

    def update_time_series_chart(self,data: pd.DataFrame) -> None:
        self.clear_tab(self.time_chart_frame)
        figure = create_time_series_chart(data)

        self.time_canvas = self.display_figure(self.time_chart_frame,figure)

    @staticmethod
    def clear_tab(tab: ttk.Frame) -> None:
        for widget in tab.winfo_children():
            widget.destroy()

    @staticmethod
    def display_figure(tab: ttk.Frame,figure: Figure) -> FigureCanvasTkAgg:

        canvas = FigureCanvasTkAgg(figure,master=tab)
        canvas.draw()

        toolbar = NavigationToolbar2Tk(canvas,tab,pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side=tk.BOTTOM,fill=tk.X)

        canvas.get_tk_widget().pack(side=tk.TOP,fill=tk.BOTH, expand=True)

        return canvas

    def refresh_charts(self) -> None:

        try:
            self.status_label.config(text="Łączenie z bazą citybikes...")
            self.root.update_idletasks()
            database_name, database_user = (test_database_connection())

            station_data = load_latest_station_data()
            self.time_series_data = load_bikes_time_series()
            ranking_data = load_most_available_stations(limit=10)

            self.clear_tab(self.map_tab)
            self.clear_tab(self.time_chart_frame)
            self.clear_tab(self.ranking_tab)

            if self.time_series_data.empty:
                self.time_slider.configure(from_=0,to=0)
                self.time_slider.set(0)
                self.time_slider_value_label.config(text="Brak danych")
                self.update_time_series_chart(self.time_series_data)

            else:
                last_index = len(self.time_series_data) - 1

                self.time_slider.configure(from_=0,to=last_index)
                self.time_slider.set(last_index)
                self.on_time_slider_change(str(last_index))

            self.map_canvas = self.display_figure(self.map_tab,create_station_map(station_data))
            self.ranking_canvas = self.display_figure(self.ranking_tab,create_most_available_stations_chart(ranking_data))

            self.status_label.config(
                text=(
                    f"Połączono z bazą: {database_name} | "
                    f"Użytkownik: {database_user} | "
                    f"Liczba stacji: {len(station_data)} | "
                    f"Liczba punktów czasowych: "
                    f"{len(self.time_series_data)}"
                )
            )

        except Exception as error:
            self.status_label.config(text="Nie udało się pobrać danych.")

            messagebox.showerror("Błąd",(
                    "Nie udało się połączyć z bazą citybikes "
                    "lub utworzyć wykresów.\n\n"
                    f"Szczegóły błędu:\n{error}"
                )
            )


def main() -> None:
    """
    Uruchamia okno aplikacji.
    """

    root = tk.Tk()

    CityBikesChartsApplication(root)

    root.mainloop()


if __name__ == "__main__":
    main()