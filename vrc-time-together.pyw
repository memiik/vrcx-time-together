from __future__ import annotations

import calendar
import os
import sqlite3
import sys
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk


APP_TITLE = "VRCX · Friendship Analytics"
DB_NAME = "VRCX.sqlite3"

BG = "#0d1117"
PANEL = "#161b22"
PANEL_ALT = "#1d2430"
TEXT = "#f0f3f6"
MUTED = "#8b949e"
ACCENT = "#7c6cff"
ACCENT_HOVER = "#9185ff"
BORDER = "#30363d"
SUCCESS = "#56d364"
WARNING = "#f2cc60"
GRID = "#28313b"


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def open_database(database_path: Path) -> sqlite3.Connection:
    uri = database_path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def find_friend_table(connection: sqlite3.Connection) -> str:
    tables = [
        row[0]
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name LIKE 'usr%_friend_log_current'
            """
        )
    ]
    if not tables:
        raise RuntimeError("No VRCX current-friends table was found.")

    # A database can contain data for several accounts. The table with the
    # largest current friend list is the most likely active account.
    return max(
        tables,
        key=lambda table: connection.execute(
            f"SELECT COUNT(*) FROM {quote_identifier(table)}"
        ).fetchone()[0],
    )


def load_rankings(
    database_path: Path, start_date: date, end_date: date
) -> tuple[list[sqlite3.Row], str, int, list[tuple[date, int]]]:
    if end_date < start_date:
        raise ValueError("The end date must be on or after the start date.")

    start_at = start_date.isoformat() + "T00:00:00.000Z"
    end_exclusive = (end_date + timedelta(days=1)).isoformat() + "T00:00:00.000Z"

    with open_database(database_path) as connection:
        friend_table = find_friend_table(connection)
        friend_count = connection.execute(
            f"SELECT COUNT(*) FROM {quote_identifier(friend_table)}"
        ).fetchone()[0]
        latest_at = connection.execute(
            "SELECT MAX(created_at) FROM gamelog_join_leave"
        ).fetchone()[0]

        # created_at is the end of an encounter and time is its duration in ms.
        # MIN/MAX clip every encounter to the requested date interval.
        query = f"""
            SELECT
                f.user_id,
                f.display_name,
                COUNT(*) AS visits,
                CAST(ROUND(SUM(MAX(0.0,
                    (MIN(julianday(j.created_at), julianday(:end_at)) -
                     MAX(julianday(j.created_at) - (j.time / 86400000.0),
                         julianday(:start_at))) * 86400000.0
                ))) AS INTEGER) AS milliseconds
            FROM gamelog_join_leave AS j
            JOIN {quote_identifier(friend_table)} AS f ON f.user_id = j.user_id
            WHERE j.type = 'OnPlayerLeft'
              AND j.time > 0
              AND julianday(j.created_at) > julianday(:start_at)
              AND julianday(j.created_at) - (j.time / 86400000.0)
                    < julianday(:end_at)
            GROUP BY f.user_id, f.display_name
            HAVING milliseconds > 0
            ORDER BY milliseconds DESC, f.display_name COLLATE NOCASE
            LIMIT 25
        """
        rows = list(
            connection.execute(
                query, {"start_at": start_at, "end_at": end_exclusive}
            )
        )
        # Split encounters at UTC midnight so a session crossing two dates is
        # represented accurately on the daily chart. The recursive calendar
        # also preserves zero-activity days, which makes lows visible.
        daily_query = f"""
            WITH RECURSIVE days(day) AS (
                VALUES(date(:start_at))
                UNION ALL
                SELECT date(day, '+1 day') FROM days WHERE day < date(:last_day)
            ), friend_events AS (
                SELECT j.created_at, j.time
                FROM gamelog_join_leave AS j
                JOIN {quote_identifier(friend_table)} AS f ON f.user_id = j.user_id
                WHERE j.type = 'OnPlayerLeft' AND j.time > 0
                  AND julianday(j.created_at) > julianday(:start_at)
                  AND julianday(j.created_at) - (j.time / 86400000.0)
                        < julianday(:end_at)
            )
            SELECT d.day,
                   CAST(ROUND(COALESCE(SUM(MAX(0.0,
                       (MIN(julianday(e.created_at), julianday(d.day, '+1 day')) -
                        MAX(julianday(e.created_at) - (e.time / 86400000.0),
                            julianday(d.day))) * 86400000.0
                   )), 0)) AS INTEGER) AS milliseconds
            FROM days AS d
            LEFT JOIN friend_events AS e
              ON julianday(e.created_at) > julianday(d.day)
             AND julianday(e.created_at) - (e.time / 86400000.0)
                    < julianday(d.day, '+1 day')
            GROUP BY d.day
            ORDER BY d.day
        """
        daily = [
            (date.fromisoformat(row["day"]), row["milliseconds"])
            for row in connection.execute(
                daily_query,
                {
                    "start_at": start_at,
                    "end_at": end_exclusive,
                    "last_day": end_date.isoformat(),
                },
            )
        ]
    return rows, latest_at or "No activity", friend_count, daily


def format_duration(milliseconds: int) -> str:
    total_minutes = int(round(milliseconds / 60_000))
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes:02d}m"


def find_database() -> Path:
    candidates = [Path(__file__).resolve().with_name(DB_NAME)]
    if os.environ.get("APPDATA"):
        candidates.append(Path(os.environ["APPDATA"]) / "VRCX" / DB_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        f"Could not find {DB_NAME}.\n\nSearched:\n"
        + "\n".join(str(path) for path in candidates)
    )


class CalendarPopup(tk.Toplevel):
    def __init__(self, parent: tk.Widget, initial: date, on_select) -> None:
        super().__init__(parent)
        self.configure(bg=PANEL)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.on_select = on_select
        self.year = initial.year
        self.month = initial.month
        self.selected = initial
        self.title("Choose date")

        self.header = tk.Frame(self, bg=PANEL)
        self.header.pack(fill="x", padx=12, pady=(12, 6))
        self._nav_button("‹", -1).pack(side="left")
        self.month_label = tk.Label(
            self.header,
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI Semibold", 11),
            width=18,
        )
        self.month_label.pack(side="left", padx=6)
        self._nav_button("›", 1).pack(side="left")

        self.days_frame = tk.Frame(self, bg=PANEL)
        self.days_frame.pack(padx=12, pady=(0, 12))
        self.render_month()

        self.update_idletasks()
        root = parent.winfo_toplevel()
        x = root.winfo_rootx() + (root.winfo_width() - self.winfo_width()) // 2
        y = root.winfo_rooty() + (root.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _nav_button(self, text: str, direction: int) -> tk.Button:
        return tk.Button(
            self.header,
            text=text,
            command=lambda: self.change_month(direction),
            bg=PANEL_ALT,
            fg=TEXT,
            activebackground=ACCENT,
            activeforeground="white",
            relief="flat",
            bd=0,
            width=3,
            cursor="hand2",
            font=("Segoe UI", 12),
        )

    def change_month(self, direction: int) -> None:
        month_index = self.year * 12 + self.month - 1 + direction
        self.year, month_zero = divmod(month_index, 12)
        self.month = month_zero + 1
        self.render_month()

    def render_month(self) -> None:
        for child in self.days_frame.winfo_children():
            child.destroy()

        self.month_label.configure(
            text=f"{calendar.month_name[self.month]} {self.year}"
        )
        for column, weekday in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            tk.Label(
                self.days_frame,
                text=weekday,
                bg=PANEL,
                fg=MUTED,
                font=("Segoe UI Semibold", 9),
                width=4,
                pady=5,
            ).grid(row=0, column=column)

        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(
            self.year, self.month
        )
        today = date.today()
        for row_index, week in enumerate(weeks, start=1):
            for column, day_number in enumerate(week):
                if not day_number:
                    continue
                candidate = date(self.year, self.month, day_number)
                is_selected = candidate == self.selected
                is_today = candidate == today
                button = tk.Button(
                    self.days_frame,
                    text=str(day_number),
                    command=lambda value=candidate: self.choose(value),
                    bg=ACCENT if is_selected else PANEL,
                    fg="white" if is_selected else (SUCCESS if is_today else TEXT),
                    activebackground=ACCENT_HOVER,
                    activeforeground="white",
                    relief="flat",
                    bd=0,
                    width=4,
                    pady=5,
                    cursor="hand2",
                    font=("Segoe UI", 9),
                )
                button.grid(row=row_index, column=column, padx=1, pady=1)

    def choose(self, value: date) -> None:
        self.on_select(value)
        self.destroy()


class DateField(tk.Frame):
    def __init__(self, parent: tk.Widget, label: str, initial: date) -> None:
        super().__init__(parent, bg=PANEL)
        tk.Label(
            self,
            text=label.upper(),
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w", pady=(0, 5))

        row = tk.Frame(self, bg=PANEL)
        row.pack(fill="x")
        self.variable = tk.StringVar(value=initial.isoformat())
        self.entry = tk.Entry(
            row,
            textvariable=self.variable,
            bg=PANEL_ALT,
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground=ACCENT,
            relief="flat",
            bd=0,
            width=13,
            font=("Segoe UI", 11),
        )
        self.entry.pack(side="left", ipady=8, ipadx=8)
        tk.Button(
            row,
            text="▦",
            command=self.open_calendar,
            bg=PANEL_ALT,
            fg=TEXT,
            activebackground=ACCENT,
            activeforeground="white",
            relief="flat",
            bd=0,
            width=3,
            pady=6,
            cursor="hand2",
            font=("Segoe UI", 11),
        ).pack(side="left", padx=(4, 0))

    def get(self) -> date:
        try:
            return date.fromisoformat(self.variable.get().strip())
        except ValueError as error:
            raise ValueError("Dates must use YYYY-MM-DD format.") from error

    def set(self, value: date) -> None:
        self.variable.set(value.isoformat())

    def open_calendar(self) -> None:
        try:
            initial = self.get()
        except ValueError:
            initial = date.today()
        CalendarPopup(self, initial, self.set)


class MetricCard(tk.Frame):
    """Small Grafana-inspired stat panel."""

    def __init__(self, parent: tk.Widget, title: str, accent: str) -> None:
        super().__init__(parent, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        tk.Frame(self, bg=accent, height=3).pack(fill="x")
        tk.Label(
            self, text=title.upper(), bg=PANEL, fg=MUTED,
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w", padx=13, pady=(9, 0))
        self.value = tk.Label(
            self, text="—", bg=PANEL, fg=TEXT,
            font=("Segoe UI Semibold", 17),
        )
        self.value.pack(anchor="w", padx=13, pady=(2, 0))
        self.detail = tk.Label(self, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 8))
        self.detail.pack(anchor="w", padx=13, pady=(0, 9))

    def set(self, value: str, detail: str) -> None:
        self.value.configure(text=value)
        self.detail.configure(text=detail)


class DailyChart(tk.Canvas):
    """Dependency-free daily area chart with hover values."""

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, bg=PANEL, height=225, bd=0, highlightthickness=0)
        self.series: list[tuple[date, int]] = []
        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda _event: self.delete("hover"))

    def set_data(self, series: list[tuple[date, int]]) -> None:
        self.series = series
        self.redraw()

    @staticmethod
    def _axis_label(milliseconds: float) -> str:
        hours = milliseconds / 3_600_000
        return f"{hours:.0f}h" if hours >= 10 else f"{hours:.1f}h"

    def redraw(self) -> None:
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 200 or height < 130:
            return
        self.create_text(
            17, 14, text="DAILY PERSON-TIME", fill=TEXT, anchor="nw",
            font=("Segoe UI Semibold", 10),
        )
        self.create_text(
            width - 17, 14, text="UTC · hover for details", fill=MUTED,
            anchor="ne", font=("Segoe UI", 8),
        )
        left, top, right, bottom = 55, 43, width - 17, height - 30
        if not self.series:
            self.create_text(width / 2, height / 2, text="No activity", fill=MUTED)
            return
        maximum = max(value for _, value in self.series)
        scale = maximum * 1.1 if maximum else 3_600_000
        for index in range(4):
            y = top + (bottom - top) * index / 3
            value = scale * (1 - index / 3)
            self.create_line(left, y, right, y, fill=GRID)
            self.create_text(left - 7, y, text=self._axis_label(value), fill=MUTED, anchor="e", font=("Segoe UI", 8))
        count = len(self.series)
        points: list[float] = []
        for index, (_day, value) in enumerate(self.series):
            x = left if count == 1 else left + (right - left) * index / (count - 1)
            y = bottom - (bottom - top) * value / scale
            points += [x, y]
        if count > 1:
            self.create_polygon(left, bottom, *points, right, bottom, fill="#282451", outline="")
            self.create_line(*points, fill=ACCENT_HOVER, width=2, smooth=count < 45)
        else:
            self.create_oval(points[0] - 3, points[1] - 3, points[0] + 3, points[1] + 3, fill=ACCENT)
        for index in sorted({0, count // 2, count - 1}):
            x = left if count == 1 else left + (right - left) * index / (count - 1)
            self.create_text(x, bottom + 10, text=self.series[index][0].strftime("%d %b"), fill=MUTED, anchor="n", font=("Segoe UI", 8))
        peak = max(range(count), key=lambda item: self.series[item][1])
        x = left if count == 1 else left + (right - left) * peak / (count - 1)
        y = points[peak * 2 + 1]
        self.create_oval(x - 4, y - 4, x + 4, y + 4, fill=SUCCESS, outline=PANEL, width=2)

    def _hover(self, event: tk.Event) -> None:
        self.delete("hover")
        if not self.series:
            return
        left, right = 55, self.winfo_width() - 17
        if not left <= event.x <= right:
            return
        count = len(self.series)
        index = 0 if count == 1 else round((event.x - left) * (count - 1) / (right - left))
        index = max(0, min(count - 1, index))
        day, value = self.series[index]
        x = left if count == 1 else left + (right - left) * index / (count - 1)
        label = f"{day:%a, %d %b %Y}  ·  {format_duration(value)}"
        box_x = min(max(8, x - 91), self.winfo_width() - 190)
        self.create_line(x, 42, x, self.winfo_height() - 30, fill=MUTED, dash=(3, 3), tags="hover")
        self.create_rectangle(box_x, 47, box_x + 182, 74, fill=PANEL_ALT, outline=BORDER, tags="hover")
        self.create_text(box_x + 8, 60, text=label, fill=TEXT, anchor="w", font=("Segoe UI", 8), tags="hover")


class TopFriendsApp(tk.Tk):
    def __init__(self, database_path: Path) -> None:
        super().__init__()
        self.database_path = database_path
        self.title(APP_TITLE)
        self.geometry("1080x860")
        self.minsize(860, 720)
        self.configure(bg=BG)
        self._configure_styles()
        self._build_ui()
        self.after(100, self.refresh)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Friends.Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=TEXT,
            rowheight=42,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.map(
            "Friends.Treeview",
            background=[("selected", ACCENT)],
            foreground=[("selected", "white")],
        )
        style.configure(
            "Friends.Treeview.Heading",
            background=PANEL_ALT,
            foreground=MUTED,
            borderwidth=0,
            relief="flat",
            padding=(10, 10),
            font=("Segoe UI Semibold", 9),
        )
        style.map("Friends.Treeview.Heading", background=[("active", PANEL_ALT)])

    def _build_ui(self) -> None:
        container = tk.Frame(self, bg=BG)
        container.pack(fill="both", expand=True, padx=32, pady=26)

        heading = tk.Frame(container, bg=BG)
        heading.pack(fill="x", pady=(0, 20))
        tk.Label(
            heading,
            text="Friendship Analytics",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI Semibold", 24),
        ).pack(anchor="w")
        tk.Label(
            heading,
            text="Grafana-style person-time metrics from your VRCX history",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(3, 0))

        controls = tk.Frame(
            container, bg=PANEL, highlightthickness=1, highlightbackground=BORDER
        )
        controls.pack(fill="x", pady=(0, 18))
        inner = tk.Frame(controls, bg=PANEL)
        inner.pack(fill="x", padx=18, pady=16)

        today = date.today()
        self.start_field = DateField(inner, "Start date", today - timedelta(days=29))
        self.start_field.pack(side="left", padx=(0, 18))
        self.end_field = DateField(inner, "End date", today)
        self.end_field.pack(side="left")

        tk.Button(
            inner,
            text="Refresh dashboard",
            command=self.refresh,
            bg=ACCENT,
            fg="white",
            activebackground=ACCENT_HOVER,
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=20,
            pady=9,
            cursor="hand2",
            font=("Segoe UI Semibold", 10),
        ).pack(side="right", pady=(18, 0))

        presets = tk.Frame(container, bg=BG)
        presets.pack(fill="x", pady=(0, 12))
        tk.Label(
            presets, text="QUICK RANGE", bg=BG, fg=MUTED, font=("Segoe UI Semibold", 8)
        ).pack(side="left", padx=(0, 10))
        for label, days in (
            ("Today", 1),
            ("7 days", 7),
            ("14 days", 14),
            ("3 weeks", 21),
            ("30 days", 30),
            ("90 days", 90),
        ):
            tk.Button(
                presets,
                text=label,
                command=lambda count=days: self.set_range(count),
                bg=PANEL_ALT,
                fg=TEXT,
                activebackground=ACCENT,
                activeforeground="white",
                relief="flat",
                bd=0,
                padx=12,
                pady=5,
                cursor="hand2",
                font=("Segoe UI", 9),
            ).pack(side="left", padx=(0, 7))
        tk.Button(
            presets,
            text="All time",
            command=self.set_all_time,
            bg=PANEL_ALT,
            fg=TEXT,
            activebackground=ACCENT,
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=12,
            pady=5,
            cursor="hand2",
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(0, 7))

        metrics = tk.Frame(container, bg=BG)
        metrics.pack(fill="x", pady=(0, 12))
        self.total_card = MetricCard(metrics, "Total person-time", ACCENT)
        self.average_card = MetricCard(metrics, "Daily average", "#58a6ff")
        self.peak_card = MetricCard(metrics, "Peak day", SUCCESS)
        self.low_card = MetricCard(metrics, "Quietest day", WARNING)
        cards = (self.total_card, self.average_card, self.peak_card, self.low_card)
        for index, card in enumerate(cards):
            card.grid(
                row=0, column=index, sticky="nsew",
                padx=(0 if index == 0 else 5, 0 if index == len(cards) - 1 else 5),
            )
            metrics.grid_columnconfigure(index, weight=1, uniform="metric")

        table_frame = tk.Frame(
            container, bg=PANEL, highlightthickness=1, highlightbackground=BORDER
        )
        table_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            table_frame,
            columns=("rank", "friend", "duration", "visits"),
            show="headings",
            style="Friends.Treeview",
            selectmode="browse",
            height=4,
        )
        self.tree.heading("rank", text="#", anchor="center")
        self.tree.heading("friend", text="FRIEND", anchor="w")
        self.tree.heading("duration", text="TIME TOGETHER", anchor="e")
        self.tree.heading("visits", text="ENCOUNTERS", anchor="e")
        self.tree.column("rank", width=55, minwidth=45, stretch=False, anchor="center")
        self.tree.column("friend", width=410, minwidth=180, anchor="w")
        self.tree.column("duration", width=170, minwidth=130, anchor="e")
        self.tree.column("visits", width=130, minwidth=105, stretch=False, anchor="e")
        table_scrollbar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=table_scrollbar.set)
        table_scrollbar.pack(side="right", fill="y", padx=(0, 1), pady=1)
        self.tree.pack(fill="both", expand=True, padx=(1, 0), pady=1)

        chart_frame = tk.Frame(
            container, bg=PANEL, highlightthickness=1, highlightbackground=BORDER
        )
        chart_frame.pack(fill="x", pady=(12, 0))
        self.chart = DailyChart(chart_frame)
        self.chart.pack(fill="x", padx=1, pady=1)

        footer = tk.Frame(container, bg=BG)
        footer.pack(fill="x", pady=(12, 0))
        self.status = tk.Label(
            footer, text="Loading…", bg=BG, fg=MUTED, font=("Segoe UI", 9)
        )
        self.status.pack(side="left")
        tk.Label(
            footer,
            text="Dates use VRCX's UTC event timestamps",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="right")

        self.bind("<Return>", lambda _event: self.refresh())
        self.bind("<F5>", lambda _event: self.refresh())

    def set_range(self, days: int) -> None:
        end = date.today()
        self.start_field.set(end - timedelta(days=days - 1))
        self.end_field.set(end)
        self.refresh()

    def set_all_time(self) -> None:
        try:
            with open_database(self.database_path) as connection:
                earliest_at = connection.execute(
                    "SELECT MIN(created_at) FROM gamelog_join_leave"
                ).fetchone()[0]
            if not earliest_at:
                raise RuntimeError("No activity was found in the database.")
            self.start_field.set(date.fromisoformat(earliest_at[:10]))
            self.end_field.set(date.today())
            self.refresh()
        except (ValueError, RuntimeError, sqlite3.Error, OSError) as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)

    def refresh(self) -> None:
        try:
            start = self.start_field.get()
            end = self.end_field.get()
            rows, latest_at, friend_count, daily = load_rankings(
                self.database_path, start, end
            )
        except (ValueError, RuntimeError, sqlite3.Error, OSError) as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)
            return

        for item in self.tree.get_children():
            self.tree.delete(item)
        for rank, row in enumerate(rows, start=1):
            self.tree.insert(
                "",
                "end",
                values=(
                    rank,
                    row["display_name"],
                    format_duration(row["milliseconds"]),
                    row["visits"],
                ),
            )

        total = sum(milliseconds for _day, milliseconds in daily)
        average = round(total / max(1, len(daily)))
        peak_day, peak_value = max(daily, key=lambda item: item[1]) if daily else (None, 0)
        low_day, low_value = min(daily, key=lambda item: item[1]) if daily else (None, 0)
        encounters = sum(row["visits"] for row in rows)
        self.total_card.set(format_duration(total), "all current friends in range")
        self.average_card.set(format_duration(average), f"across {len(daily)} calendar days")
        self.peak_card.set(
            format_duration(peak_value), peak_day.strftime("%a, %d %b %Y") if peak_day else "No data"
        )
        self.low_card.set(
            format_duration(low_value), low_day.strftime("%a, %d %b %Y") if low_day else "No data"
        )
        self.chart.set_data(daily)

        if rows:
            summary = f"{len(rows)} shown · {encounters:,} encounters among shown friends · {friend_count} current friends · Latest data {latest_at[:10]}"
        else:
            summary = f"No completed encounters in this range · Latest data {latest_at[:10]}"
        self.status.configure(text=summary)


def run_check(database_path: Path) -> int:
    end = date.today()
    rows, latest_at, friend_count, daily = load_rankings(
        database_path, end - timedelta(days=6), end
    )
    total = sum(value for _day, value in daily)
    peak_day, peak_value = max(daily, key=lambda item: item[1])
    print(
        f"OK: {len(rows)} rankings, {friend_count} current friends, "
        f"{len(daily)} daily points, {format_duration(total)} person-time, "
        f"peak {peak_day} ({format_duration(peak_value)}), latest activity {latest_at}"
    )
    return 0


def main() -> int:
    try:
        database_path = find_database()
    except FileNotFoundError as error:
        if "--check" in sys.argv:
            print(f"ERROR: {error}", file=sys.stderr)
        else:
            messagebox.showerror(APP_TITLE, str(error))
        return 1
    if "--check" in sys.argv:
        return run_check(database_path)
    app = TopFriendsApp(database_path)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
