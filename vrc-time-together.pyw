from __future__ import annotations

import calendar
import os
import sqlite3
import sys
import tkinter as tk
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from tkinter import messagebox, ttk

try:
    from tzlocal import get_localzone
except ImportError:
    get_localzone = None


APP_TITLE = "VRCX · Time Together"
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
LOCAL_TIMEZONE = (
    get_localzone() if get_localzone is not None else datetime.now().astimezone().tzinfo
)
_local_now = datetime.now(LOCAL_TIMEZONE)
_local_offset = _local_now.strftime("%z")
_local_offset = _local_offset[:3] + ":" + _local_offset[3:]
LOCAL_TIMEZONE_NAME = f"{_local_now.tzname()} · UTC{_local_offset}"
SERIES_COLORS = (
    "#9185ff", "#58a6ff", "#56d364", "#f2cc60", "#ff7b72",
    "#d2a8ff", "#79c0ff", "#7ee787", "#ffa657", "#ff9bce",
)


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def open_database(database_path: Path) -> sqlite3.Connection:
    uri = database_path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def local_range_utc(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(start_date, time.min, tzinfo=LOCAL_TIMEZONE)
    end = datetime.combine(
        end_date + timedelta(days=1), time.min, tzinfo=LOCAL_TIMEZONE
    )
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def sqlite_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


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


def load_local_daily_series(
    database_path: Path,
    start_date: date,
    end_date: date,
    user_ids: list[str] | None = None,
) -> tuple[dict[str, list[tuple[date, int]]], list[tuple[date, int]]]:
    """Split friend encounters at local midnight and merge social overlaps."""
    if end_date < start_date:
        raise ValueError("The end date must be on or after the start date.")
    days = [
        start_date + timedelta(days=index)
        for index in range((end_date - start_date).days + 1)
    ]
    range_start, range_end = local_range_utc(start_date, end_date)
    requested_ids = list(dict.fromkeys(user_ids or []))
    totals: dict[str, dict[date, int]] = {
        user_id: {day: 0 for day in days} for user_id in requested_ids
    }
    social_intervals: dict[date, list[tuple[datetime, datetime]]] = {
        day: [] for day in days
    }

    with open_database(database_path) as connection:
        friend_table = find_friend_table(connection)
        parameters: dict[str, object] = {
            "start_at": sqlite_timestamp(range_start),
            "end_at": sqlite_timestamp(range_end),
        }
        user_filter = ""
        if requested_ids:
            placeholders = []
            for index, user_id in enumerate(requested_ids):
                key = f"user_{index}"
                placeholders.append(f":{key}")
                parameters[key] = user_id
            user_filter = f"AND f.user_id IN ({', '.join(placeholders)})"
        query = f"""
            SELECT f.user_id, j.created_at, j.time
            FROM gamelog_join_leave AS j
            JOIN {quote_identifier(friend_table)} AS f ON f.user_id = j.user_id
            WHERE j.type = 'OnPlayerLeft'
              AND j.time > 0
              AND julianday(j.created_at) > julianday(:start_at)
              AND julianday(j.created_at) - (j.time / 86400000.0)
                    < julianday(:end_at)
              {user_filter}
            ORDER BY j.created_at
        """
        for row in connection.execute(query, parameters):
            user_id = row["user_id"]
            totals.setdefault(user_id, {day: 0 for day in days})
            event_end = datetime.fromisoformat(
                row["created_at"].replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            event_start = event_end - timedelta(milliseconds=row["time"])
            cursor = max(event_start, range_start)
            clipped_end = min(event_end, range_end)
            while cursor < clipped_end:
                local_cursor = cursor.astimezone(LOCAL_TIMEZONE)
                day = local_cursor.date()
                next_midnight_local = datetime.combine(
                    day + timedelta(days=1), time.min, tzinfo=LOCAL_TIMEZONE
                )
                segment_end = min(
                    clipped_end, next_midnight_local.astimezone(timezone.utc)
                )
                milliseconds = round((segment_end - cursor).total_seconds() * 1000)
                totals[user_id][day] += milliseconds
                social_intervals[day].append((cursor, segment_end))
                cursor = segment_end

    per_friend = {
        user_id: [(day, daily[day]) for day in days]
        for user_id, daily in totals.items()
    }
    social: list[tuple[date, int]] = []
    for day in days:
        merged = 0
        current_start: datetime | None = None
        current_end: datetime | None = None
        for interval_start, interval_end in sorted(social_intervals[day]):
            if current_end is None or interval_start > current_end:
                if current_start is not None and current_end is not None:
                    merged += round((current_end - current_start).total_seconds() * 1000)
                current_start, current_end = interval_start, interval_end
            elif interval_end > current_end:
                current_end = interval_end
        if current_start is not None and current_end is not None:
            merged += round((current_end - current_start).total_seconds() * 1000)
        social.append((day, merged))
    return per_friend, social


def load_overview_daily_series(
    database_path: Path, start_date: date, end_date: date
) -> tuple[list[tuple[date, int]], list[tuple[date, int]]]:
    per_friend, social = load_local_daily_series(database_path, start_date, end_date)
    person_time = []
    for index, day in enumerate(
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    ):
        person_time.append(
            (day, sum(series[index][1] for series in per_friend.values()))
        )
    return person_time, social


def load_rankings(
    database_path: Path,
    start_date: date,
    end_date: date,
    friend_search: str = "",
    minimum_minutes: int = 0,
    result_limit: int | None = 50,
) -> tuple[
    list[sqlite3.Row], int, str, int, list[tuple[date, int]], list[tuple[date, int]]
]:
    if end_date < start_date:
        raise ValueError("The end date must be on or after the start date.")

    range_start, range_end = local_range_utc(start_date, end_date)
    start_at = sqlite_timestamp(range_start)
    end_exclusive = sqlite_timestamp(range_end)

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
            WITH rankings AS (
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
            )
            SELECT user_id, display_name, visits, milliseconds
            FROM rankings
            WHERE milliseconds > 0
              AND milliseconds >= :minimum_ms
              AND display_name LIKE :friend_search ESCAPE '\\'
            ORDER BY milliseconds DESC, display_name COLLATE NOCASE
        """
        search_pattern = (
            "%"
            + friend_search.strip()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
            + "%"
        )
        all_rows = list(
            connection.execute(
                query,
                {
                    "start_at": start_at,
                    "end_at": end_exclusive,
                    "minimum_ms": max(0, minimum_minutes) * 60_000,
                    "friend_search": search_pattern,
                },
            )
        )
        matching_count = len(all_rows)
        rows = all_rows if result_limit is None else all_rows[:result_limit]
    daily, social_daily = load_overview_daily_series(
        database_path, start_date, end_date
    )
    return (
        rows,
        matching_count,
        latest_at or "No activity",
        friend_count,
        daily,
        social_daily,
    )


def load_friend_daily_series(
    database_path: Path, start_date: date, end_date: date, user_id: str
) -> list[tuple[date, int]]:
    series, _social = load_local_daily_series(
        database_path, start_date, end_date, [user_id]
    )
    return series[user_id]


def load_friends_daily_series(
    database_path: Path, start_date: date, end_date: date, user_ids: list[str]
) -> dict[str, list[tuple[date, int]]]:
    series, _social = load_local_daily_series(
        database_path, start_date, end_date, user_ids
    )
    return series


def load_social_daily_series(
    database_path: Path, start_date: date, end_date: date
) -> list[tuple[date, int]]:
    _per_friend, social = load_local_daily_series(
        database_path, start_date, end_date
    )
    return social


def aggregate_time_series(
    daily: list[tuple[date, int]], granularity: str
) -> list[tuple[date, int]]:
    """Aggregate daily points while preserving a chronological series."""
    if granularity == "Daily":
        return daily
    totals: dict[date, int] = {}
    for day, milliseconds in daily:
        if granularity == "Weekly":
            bucket = day - timedelta(days=day.weekday())
        else:
            bucket = day.replace(day=1)
        totals[bucket] = totals.get(bucket, 0) + milliseconds
    return list(totals.items())


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


class TimeSeriesChart(tk.Canvas):
    """Interactive multi-series comparison chart with shared hover values."""

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, bg=PANEL, height=240, bd=0, highlightthickness=0)
        self.series_list: list[tuple[str, list[tuple[date, int]], str]] = []
        self.series: list[tuple[date, int]] = []
        self.granularity = "Daily"
        self.metric_label = "Time with friends"
        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda _event: self.delete("hover"))

    def set_series(
        self,
        series_list: list[tuple[str, list[tuple[date, int]], str]],
        granularity: str,
        metric_label: str,
    ) -> None:
        self.series_list = series_list
        self.series = series_list[0][1] if series_list else []
        self.granularity = granularity
        self.metric_label = metric_label
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
            17, 14,
            text=f"{self.granularity.upper()} {self.metric_label.upper()}",
            fill=TEXT, anchor="nw",
            font=("Segoe UI Semibold", 10),
        )
        self.create_text(
            width - 17, 14,
            text=f"Local time · {LOCAL_TIMEZONE_NAME} · hover for details",
            fill=MUTED,
            anchor="ne", font=("Segoe UI", 8),
        )
        left, top, right, bottom = 55, 61, width - 17, height - 30
        if not self.series_list or not self.series:
            self.create_text(width / 2, height / 2, text="No activity", fill=MUTED)
            return
        legend_x = left
        for index, (name, _series, color) in enumerate(self.series_list):
            label = name if len(name) <= 15 else name[:14] + "…"
            needed = 18 + len(label) * 6
            if legend_x + needed > right:
                remaining = len(self.series_list) - index
                self.create_text(
                    legend_x, 40, text=f"+{remaining} more", fill=MUTED,
                    anchor="w", font=("Segoe UI", 8),
                )
                break
            self.create_oval(
                legend_x, 36, legend_x + 7, 43, fill=color, outline=""
            )
            self.create_text(
                legend_x + 11, 40, text=label, fill=TEXT,
                anchor="w", font=("Segoe UI", 8),
            )
            legend_x += needed

        maximum = max(
            value
            for _name, series, _color in self.series_list
            for _day, value in series
        )
        scale = maximum * 1.1 if maximum else 3_600_000
        for index in range(4):
            y = top + (bottom - top) * index / 3
            value = scale * (1 - index / 3)
            self.create_line(left, y, right, y, fill=GRID)
            self.create_text(left - 7, y, text=self._axis_label(value), fill=MUTED, anchor="e", font=("Segoe UI", 8))
        count = len(self.series)
        for series_index, (_name, series, color) in enumerate(self.series_list):
            points: list[float] = []
            for index, (_day, value) in enumerate(series):
                x = left if count == 1 else left + (right - left) * index / (count - 1)
                y = bottom - (bottom - top) * value / scale
                points += [x, y]
            if count > 1:
                if len(self.series_list) == 1:
                    self.create_polygon(
                        left, bottom, *points, right, bottom,
                        fill="#282451", outline="",
                    )
                self.create_line(*points, fill=GRID, width=6, smooth=count < 45)
                self.create_line(*points, fill=color, width=2, smooth=count < 45)
            elif points:
                self.create_oval(
                    points[0] - 3, points[1] - 3,
                    points[0] + 3, points[1] + 3,
                    fill=color, outline="",
                )
        for index in sorted({0, count // 2, count - 1}):
            x = left if count == 1 else left + (right - left) * index / (count - 1)
            axis_format = "%b %Y" if self.granularity == "Monthly" else "%d %b"
            self.create_text(x, bottom + 10, text=self.series[index][0].strftime(axis_format), fill=MUTED, anchor="n", font=("Segoe UI", 8))

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
        day, _value = self.series[index]
        x = left if count == 1 else left + (right - left) * index / (count - 1)
        if self.granularity == "Weekly":
            period = f"Week of {day:%d %b %Y}"
        elif self.granularity == "Monthly":
            period = f"{day:%B %Y}"
        else:
            period = f"{day:%a, %d %b %Y}"
        values = [
            (name, series[index][1], color)
            for name, series, color in self.series_list
        ]
        longest = max([len(period)] + [len(name) + 12 for name, _value, _color in values])
        box_width = max(205, min(310, longest * 6 + 24))
        box_height = 29 + len(values) * 17
        box_x = min(max(8, x - box_width / 2), self.winfo_width() - box_width - 8)
        box_y = min(65, max(4, self.winfo_height() - 30 - box_height))
        self.create_line(x, 60, x, self.winfo_height() - 30, fill=MUTED, dash=(3, 3), tags="hover")
        self.create_rectangle(
            box_x, box_y, box_x + box_width, box_y + box_height,
            fill=PANEL_ALT, outline=BORDER, tags="hover",
        )
        self.create_text(
            box_x + 9, box_y + 9, text=period, fill=TEXT,
            anchor="nw", font=("Segoe UI Semibold", 8), tags="hover",
        )
        for row, (name, value, color) in enumerate(values):
            y = box_y + 27 + row * 17
            self.create_oval(box_x + 9, y, box_x + 16, y + 7, fill=color, outline="", tags="hover")
            self.create_text(
                box_x + 21, y + 3, text=f"{name}: {format_duration(value)}",
                fill=TEXT, anchor="w", font=("Segoe UI", 8), tags="hover",
            )


class TopFriendsApp(tk.Tk):
    def __init__(self, database_path: Path) -> None:
        super().__init__()
        self.database_path = database_path
        self._refresh_job: str | None = None
        self._selected_user_ids: list[str] = []
        self._selected_friend_names: dict[str, str] = {}
        self._friend_series: dict[str, list[tuple[date, int]]] = {}
        self._raw_series: list[tuple[date, int]] = []
        self._overview_daily: list[tuple[date, int]] = []
        self._social_daily: list[tuple[date, int]] = []
        self._series_range: tuple[date, date] | None = None
        self._suppress_selection_event = False
        self.title(APP_TITLE)
        self.geometry("1080x860")
        self.minsize(960, 760)
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
        style.configure(
            "Filter.TCombobox",
            fieldbackground=PANEL_ALT,
            background=PANEL_ALT,
            foreground=TEXT,
            arrowcolor=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=7,
        )
        style.map(
            "Filter.TCombobox",
            fieldbackground=[("readonly", PANEL_ALT)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", PANEL_ALT)],
            selectforeground=[("readonly", TEXT)],
        )

    def _build_ui(self) -> None:
        container = tk.Frame(self, bg=BG)
        container.pack(fill="both", expand=True, padx=32, pady=26)

        heading = tk.Frame(container, bg=BG)
        heading.pack(fill="x", pady=(0, 20))
        tk.Label(
            heading,
            text="Time Together",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI Semibold", 24),
        ).pack(anchor="w")
        tk.Label(
            heading,
            text="A private, local view of the time you've shared in VRChat",
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

        tk.Frame(controls, bg=BORDER, height=1).pack(fill="x")
        filters = tk.Frame(controls, bg=PANEL)
        filters.pack(fill="x", padx=18, pady=(12, 15))

        search_group = tk.Frame(filters, bg=PANEL)
        search_group.pack(side="left", padx=(0, 18))
        tk.Label(
            search_group,
            text="FIND A FRIEND",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w", pady=(0, 5))
        self.search_variable = tk.StringVar()
        self.search_entry = tk.Entry(
            search_group,
            textvariable=self.search_variable,
            bg=PANEL_ALT,
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground=ACCENT,
            relief="flat",
            bd=0,
            width=31,
            font=("Segoe UI", 10),
        )
        self.search_entry.pack(ipady=8, ipadx=9)
        self.search_entry.bind("<KeyRelease>", self.schedule_refresh)

        minimum_group = tk.Frame(filters, bg=PANEL)
        minimum_group.pack(side="left", padx=(0, 18))
        tk.Label(
            minimum_group,
            text="MINIMUM TIME",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w", pady=(0, 5))
        self.minimum_variable = tk.StringVar(value="Any time")
        minimum_box = ttk.Combobox(
            minimum_group,
            textvariable=self.minimum_variable,
            values=("Any time", "15 minutes", "1 hour", "5 hours", "10 hours"),
            state="readonly",
            width=14,
            style="Filter.TCombobox",
        )
        minimum_box.pack()
        minimum_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        limit_group = tk.Frame(filters, bg=PANEL)
        limit_group.pack(side="left")
        tk.Label(
            limit_group,
            text="SHOW",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w", pady=(0, 5))
        self.limit_variable = tk.StringVar(value="Top 50")
        limit_box = ttk.Combobox(
            limit_group,
            textvariable=self.limit_variable,
            values=("Top 25", "Top 50", "Top 100", "All matches"),
            state="readonly",
            width=13,
            style="Filter.TCombobox",
        )
        limit_box.pack()
        limit_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        tk.Button(
            filters,
            text="Clear filters",
            command=self.clear_filters,
            bg=PANEL,
            fg=MUTED,
            activebackground=PANEL_ALT,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=10,
            pady=8,
            cursor="hand2",
            font=("Segoe UI", 9),
        ).pack(side="right", pady=(17, 0))

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

        dashboard_body = tk.Frame(container, bg=BG)
        dashboard_body.pack(fill="both", expand=True, pady=(0, 28))
        dashboard_body.grid_rowconfigure(0, weight=1)
        dashboard_body.grid_columnconfigure(0, weight=1, uniform="dashboard")
        dashboard_body.grid_columnconfigure(1, weight=1, uniform="dashboard")

        table_frame = tk.Frame(
            dashboard_body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER
        )
        table_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        table_header = tk.Frame(table_frame, bg=PANEL)
        table_header.pack(fill="x", padx=14, pady=(11, 9))
        tk.Label(
            table_header,
            text="FRIENDS BY SHARED TIME",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left")
        self.ranking_summary = tk.Label(
            table_header,
            text="",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
        )
        self.ranking_summary.pack(side="right")
        self.tree = ttk.Treeview(
            table_frame,
            columns=("rank", "friend", "duration", "visits"),
            show="headings",
            style="Friends.Treeview",
            selectmode="extended",
        )
        self.tree.heading("rank", text="#", anchor="center")
        self.tree.heading("friend", text="FRIEND", anchor="w")
        self.tree.heading("duration", text="TIME TOGETHER", anchor="e")
        self.tree.heading("visits", text="SHARED SESSIONS", anchor="e")
        self.tree.column("rank", width=55, minwidth=45, stretch=False, anchor="center")
        self.tree.column("friend", width=410, minwidth=180, anchor="w")
        self.tree.column("duration", width=170, minwidth=130, anchor="e")
        self.tree.column("visits", width=130, minwidth=105, stretch=False, anchor="e")
        table_scrollbar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=table_scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", self.show_selected_friends)
        table_scrollbar.pack(side="right", fill="y", padx=(0, 1), pady=(0, 1))
        self.tree.pack(fill="both", expand=True, padx=(1, 0), pady=(0, 1))
        self.empty_state = tk.Label(
            table_frame,
            text="No friends match these filters\nTry a longer date range or clear the list filters.",
            bg=PANEL,
            fg=MUTED,
            justify="center",
            font=("Segoe UI", 10),
        )

        chart_frame = tk.Frame(
            dashboard_body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER
        )
        chart_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        series_header = tk.Frame(chart_frame, bg=PANEL)
        series_header.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(
            series_header,
            text="TIME SERIES",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left")
        self.series_context = tk.Label(
            series_header,
            text="Social time · loading…",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
        )
        self.series_context.pack(side="right")
        series_controls = tk.Frame(chart_frame, bg=PANEL)
        series_controls.pack(fill="x", padx=14, pady=(0, 3))
        self.granularity_variable = tk.StringVar(value="Daily")
        granularity_box = ttk.Combobox(
            series_controls,
            textvariable=self.granularity_variable,
            values=("Daily", "Weekly", "Monthly"),
            state="readonly",
            width=10,
            style="Filter.TCombobox",
        )
        granularity_box.pack(side="right")
        granularity_box.bind(
            "<<ComboboxSelected>>", lambda _event: self.render_time_series()
        )
        self.metric_variable = tk.StringVar(value="Time with friends")
        self.metric_box = ttk.Combobox(
            series_controls,
            textvariable=self.metric_variable,
            values=("Time with friends", "Person-time"),
            state="readonly",
            width=17,
            style="Filter.TCombobox",
        )
        self.metric_box.pack(side="right", padx=(0, 8))
        self.metric_box.bind(
            "<<ComboboxSelected>>", lambda _event: self.show_overview_series()
        )
        self.overview_button = tk.Button(
            series_controls,
            text="All friends",
            command=self.show_overview_series,
            bg=PANEL_ALT,
            fg=TEXT,
            activebackground=ACCENT,
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=11,
            pady=6,
            cursor="hand2",
            font=("Segoe UI", 9),
            state="disabled",
        )
        self.overview_button.pack(side="right", padx=(0, 8))
        self.chart = TimeSeriesChart(chart_frame)
        self.chart.pack(fill="both", expand=True, padx=1, pady=1)

        footer = tk.Frame(container, bg=BG)
        footer.place(relx=0, rely=1, relwidth=1, anchor="sw")
        self.status = tk.Label(
            footer, text="Loading…", bg=BG, fg=MUTED, font=("Segoe UI", 9)
        )
        self.status.pack(side="left")
        tk.Label(
            footer,
            text=f"Dates use local time · {LOCAL_TIMEZONE_NAME}",
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

    def schedule_refresh(self, _event: tk.Event | None = None) -> None:
        """Debounce text filtering so typing stays responsive."""
        if self._refresh_job is not None:
            self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(300, self.refresh)

    def clear_filters(self) -> None:
        self.search_variable.set("")
        self.minimum_variable.set("Any time")
        self.limit_variable.set("Top 50")
        self.search_entry.focus_set()
        self.refresh()

    def ranking_filters(self) -> tuple[int, int | None]:
        minimums = {
            "Any time": 0,
            "15 minutes": 15,
            "1 hour": 60,
            "5 hours": 300,
            "10 hours": 600,
        }
        limits = {
            "Top 25": 25,
            "Top 50": 50,
            "Top 100": 100,
            "All matches": None,
        }
        return (
            minimums.get(self.minimum_variable.get(), 0),
            limits.get(self.limit_variable.get(), 50),
        )

    def render_time_series(self) -> None:
        granularity = self.granularity_variable.get()
        if self._selected_user_ids:
            series_list = [
                (
                    self._selected_friend_names.get(user_id, "Friend"),
                    aggregate_time_series(self._friend_series[user_id], granularity),
                    SERIES_COLORS[index % len(SERIES_COLORS)],
                )
                for index, user_id in enumerate(self._selected_user_ids)
                if user_id in self._friend_series
            ]
            metric_label = "Time together"
            count = len(series_list)
            context = (
                f"Comparing {count} friend{'s' if count != 1 else ''} · "
                "Ctrl-click rows to add or remove"
            )
        elif self.metric_variable.get() == "Person-time":
            total = sum(value for _day, value in self._raw_series)
            series_list = [
                (
                    "All current friends",
                    aggregate_time_series(self._raw_series, granularity),
                    ACCENT_HOVER,
                )
            ]
            metric_label = "Person-time"
            context = f"Total person-time · {format_duration(total)} · overlaps count per friend"
        else:
            total = sum(value for _day, value in self._raw_series)
            series_list = [
                (
                    "All current friends",
                    aggregate_time_series(self._raw_series, granularity),
                    ACCENT_HOVER,
                )
            ]
            metric_label = "Time with friends"
            context = f"Social time · {format_duration(total)} · overlaps counted once"
        self.series_context.configure(text=context)
        self.chart.set_series(series_list, granularity, metric_label)

    def show_overview_series(self) -> None:
        self._selected_user_ids = []
        self._selected_friend_names = {}
        self._friend_series = {}
        self._series_range = None
        self._raw_series = (
            self._overview_daily
            if self.metric_variable.get() == "Person-time"
            else self._social_daily
        )
        selection = self.tree.selection()
        if selection:
            self._suppress_selection_event = True
            self.tree.selection_remove(*selection)
            self._suppress_selection_event = False
        self.overview_button.configure(state="disabled")
        self.metric_box.configure(state="readonly")
        self.render_time_series()

    def show_selected_friends(self, _event: tk.Event | None = None) -> None:
        if self._suppress_selection_event:
            return
        selection = list(self.tree.selection())
        if not selection:
            self.show_overview_series()
            return
        friend_names = {
            user_id: str(self.tree.item(user_id, "values")[1])
            for user_id in selection
            if len(self.tree.item(user_id, "values")) >= 2
        }
        selection = [user_id for user_id in selection if user_id in friend_names]
        try:
            start = self.start_field.get()
            end = self.end_field.get()
            if self._selected_user_ids == selection and self._series_range == (start, end):
                return
            series = load_friends_daily_series(
                self.database_path, start, end, selection
            )
        except (ValueError, RuntimeError, sqlite3.Error, OSError) as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)
            return
        self._selected_user_ids = selection
        self._selected_friend_names = friend_names
        self._series_range = (start, end)
        self._friend_series = series
        self.overview_button.configure(state="normal")
        self.metric_box.configure(state="disabled")
        self.render_time_series()

    def set_all_time(self) -> None:
        try:
            with open_database(self.database_path) as connection:
                earliest_at = connection.execute(
                    "SELECT MIN(created_at) FROM gamelog_join_leave"
                ).fetchone()[0]
            if not earliest_at:
                raise RuntimeError("No activity was found in the database.")
            earliest_local = datetime.fromisoformat(
                earliest_at.replace("Z", "+00:00")
            ).astimezone(LOCAL_TIMEZONE)
            self.start_field.set(earliest_local.date())
            self.end_field.set(date.today())
            self.refresh()
        except (ValueError, RuntimeError, sqlite3.Error, OSError) as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)

    def refresh(self) -> None:
        self._refresh_job = None
        self.status.configure(text="Updating dashboard…")
        self.update_idletasks()
        try:
            start = self.start_field.get()
            end = self.end_field.get()
            minimum_minutes, result_limit = self.ranking_filters()
            (
                rows,
                matching_count,
                latest_at,
                friend_count,
                daily,
                social_daily,
            ) = load_rankings(
                self.database_path,
                start,
                end,
                friend_search=self.search_variable.get(),
                minimum_minutes=minimum_minutes,
                result_limit=result_limit,
            )
        except (ValueError, RuntimeError, sqlite3.Error, OSError) as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)
            return

        selected_user_ids = list(self._selected_user_ids)
        self._suppress_selection_event = True
        for item in self.tree.get_children():
            self.tree.delete(item)
        for rank, row in enumerate(rows, start=1):
            self.tree.insert(
                "",
                "end",
                iid=row["user_id"],
                values=(
                    rank,
                    row["display_name"],
                    format_duration(row["milliseconds"]),
                    row["visits"],
                ),
            )
        self._suppress_selection_event = False
        if rows:
            self.empty_state.place_forget()
        else:
            self.empty_state.place(relx=0.5, rely=0.56, anchor="center")
            self.empty_state.lift()

        if len(rows) < matching_count:
            ranking_text = f"Showing {len(rows)} of {matching_count} · Ctrl-click to compare"
        else:
            ranking_text = (
                f"{matching_count} match{'es' if matching_count != 1 else ''}"
                " · Ctrl-click to compare"
            )
        self.ranking_summary.configure(text=ranking_text)

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
        self._overview_daily = daily
        self._social_daily = social_daily
        visible_ids = {row["user_id"] for row in rows}
        visible_selection = [
            user_id for user_id in selected_user_ids if user_id in visible_ids
        ]
        if visible_selection:
            self._series_range = None
            self._suppress_selection_event = True
            self.tree.selection_set(visible_selection)
            self.tree.focus(visible_selection[0])
            self._suppress_selection_event = False
            self.show_selected_friends()
        else:
            self.show_overview_series()

        if latest_at == "No activity":
            latest_label = latest_at
        else:
            latest_local = datetime.fromisoformat(
                latest_at.replace("Z", "+00:00")
            ).astimezone(LOCAL_TIMEZONE)
            latest_label = latest_local.strftime("%Y-%m-%d %H:%M")
        if rows:
            summary = (
                f"{start:%d %b %Y} – {end:%d %b %Y} · "
                f"{encounters:,} shared sessions in visible list · "
                f"{friend_count} current friends · Latest data {latest_label}"
            )
        elif matching_count == 0:
            summary = (
                f"No friends match · {friend_count} current friends · "
                f"Latest data {latest_label}"
            )
        else:
            summary = f"No friends shown · Latest data {latest_label}"
        self.status.configure(text=summary)


def run_check(database_path: Path) -> int:
    end = date.today()
    rows, matching_count, latest_at, friend_count, daily, social_daily = load_rankings(
        database_path, end - timedelta(days=6), end
    )
    total = sum(value for _day, value in daily)
    social_total = sum(value for _day, value in social_daily)
    if social_total > total:
        raise RuntimeError("Social time cannot exceed total person-time.")
    peak_day, peak_value = max(daily, key=lambda item: item[1])
    print(
        f"OK: {len(rows)} of {matching_count} friends shown, {friend_count} current friends, "
        f"{len(daily)} daily points, {format_duration(social_total)} social time, "
        f"{format_duration(total)} person-time, "
        f"peak {peak_day} ({format_duration(peak_value)}), "
        f"timezone {LOCAL_TIMEZONE_NAME}, latest activity {latest_at}"
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
