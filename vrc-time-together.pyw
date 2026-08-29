from __future__ import annotations

import calendar
import hashlib
import logging
import sqlite3
import sys
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

from vrc_time_together.formatting import (
    format_duration as format_duration_modern,
    format_local_date,
    format_local_datetime,
)
from vrc_time_together.models import AppState, ComparisonData, DashboardData, FriendStat
from vrc_time_together.logging_utils import configure_logging
from vrc_time_together.repository import (
    VrcxDataError,
    VrcxRepository,
    aggregate_time_series as aggregate_series,
    find_database as locate_database,
)
from vrc_time_together.theme import (
    ACCENT,
    ACCENT_HOVER,
    BG,
    BORDER,
    FONT_BODY,
    FONT_LABEL,
    FONT_METRIC,
    FONT_SECTION,
    FONT_SMALL,
    FONT_TITLE,
    GRID,
    MUTED,
    PANEL,
    PANEL_ALT,
    PANEL_HOVER,
    SERIES_COLORS,
    SIDEBAR,
    SUCCESS,
    TEXT,
    WARNING,
)
from vrc_time_together.timezone_utils import LOCAL_TIMEZONE, LOCAL_TIMEZONE_NAME

APP_TITLE = "VRCX · Time Together"


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
            font=FONT_LABEL,
        ).pack(anchor="w", padx=13, pady=(9, 0))
        self.value = tk.Label(
            self, text="—", bg=PANEL, fg=TEXT,
            font=FONT_METRIC,
        )
        self.value.pack(anchor="w", padx=13, pady=(2, 0))
        self.detail = tk.Label(self, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 8))
        self.detail.pack(anchor="w", padx=13, pady=(0, 9))

    def set(self, value: str, detail: str) -> None:
        value_font = ("Segoe UI Semibold", 13) if len(value) > 18 else FONT_METRIC
        self.value.configure(text=value, font=value_font)
        self.detail.configure(text=detail)


class TimeSeriesChart(tk.Canvas):
    """Interactive multi-series comparison chart with shared hover values."""

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, bg=PANEL, height=240, bd=0, highlightthickness=0)
        self.series_list: list[tuple[str, list[tuple[date, int]], str]] = []
        self.series: list[tuple[date, int]] = []
        self.granularity = "Daily"
        self.metric_label = "Time with friends"
        self._view_start = 0.0
        self._view_end = 1.0
        self._drag_origin: tuple[int, float, float] | None = None
        self._resize_job: str | None = None
        self.bind("<Configure>", self._schedule_redraw)
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda _event: self.delete("hover"))
        self.bind("<MouseWheel>", self._zoom)
        self.bind("<ButtonPress-1>", self._start_pan)
        self.bind("<B1-Motion>", self._pan)
        self.bind("<Double-Button-1>", lambda _event: self.reset_zoom())

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
        self._view_start = 0.0
        self._view_end = 1.0
        self.redraw()

    def _schedule_redraw(self, _event: tk.Event | None = None) -> None:
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(45, self.redraw)

    def reset_zoom(self) -> None:
        self._view_start = 0.0
        self._view_end = 1.0
        self.redraw()

    def _visible_series(
        self,
    ) -> list[tuple[str, list[tuple[date, int]], str]]:
        if not self.series_list or not self.series:
            return []
        count = len(self.series)
        first = max(0, min(count - 1, round(self._view_start * (count - 1))))
        last = max(first + 1, min(count, round(self._view_end * (count - 1)) + 1))
        return [
            (name, series[first:last], color)
            for name, series, color in self.series_list
        ]

    def _zoom(self, event: tk.Event) -> str:
        if len(self.series) < 3:
            return "break"
        left, right = 55, self.winfo_width() - 17
        if right <= left:
            return "break"
        cursor = max(0.0, min(1.0, (event.x - left) / (right - left)))
        span = self._view_end - self._view_start
        factor = 0.78 if event.delta > 0 else 1.28
        new_span = max(2 / max(2, len(self.series) - 1), min(1.0, span * factor))
        absolute = self._view_start + cursor * span
        start = absolute - cursor * new_span
        start = max(0.0, min(1.0 - new_span, start))
        self._view_start, self._view_end = start, start + new_span
        self.redraw()
        return "break"

    def _start_pan(self, event: tk.Event) -> None:
        self._drag_origin = (event.x, self._view_start, self._view_end)

    def _pan(self, event: tk.Event) -> None:
        if self._drag_origin is None or self._view_end - self._view_start >= 0.999:
            return
        width = max(1, self.winfo_width() - 72)
        origin_x, origin_start, origin_end = self._drag_origin
        span = origin_end - origin_start
        shift = -(event.x - origin_x) / width * span
        start = max(0.0, min(1.0 - span, origin_start + shift))
        self._view_start, self._view_end = start, start + span
        self.redraw()

    @staticmethod
    def _axis_label(milliseconds: float) -> str:
        hours = milliseconds / 3_600_000
        return f"{hours:.0f}h" if hours >= 10 else f"{hours:.1f}h"

    def redraw(self) -> None:
        self._resize_job = None
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
        visible_series = self._visible_series()
        if not visible_series:
            self.create_text(width / 2, height / 2, text="No activity", fill=MUTED)
            return
        legend_x = left
        for index, (name, _series, color) in enumerate(visible_series):
            label = name if len(name) <= 15 else name[:14] + "…"
            needed = 18 + len(label) * 6
            if legend_x + needed > right:
                remaining = len(visible_series) - index
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
            for _name, series, _color in visible_series
            for _day, value in series
        )
        scale = maximum * 1.1 if maximum else 3_600_000
        for index in range(4):
            y = top + (bottom - top) * index / 3
            value = scale * (1 - index / 3)
            self.create_line(left, y, right, y, fill=GRID)
            self.create_text(left - 7, y, text=self._axis_label(value), fill=MUTED, anchor="e", font=("Segoe UI", 8))
        primary_series = visible_series[0][1]
        count = len(primary_series)
        for series_index, (_name, series, color) in enumerate(visible_series):
            points: list[float] = []
            for index, (_day, value) in enumerate(series):
                x = left if count == 1 else left + (right - left) * index / (count - 1)
                y = bottom - (bottom - top) * value / scale
                points += [x, y]
            if count > 1:
                if len(visible_series) == 1:
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
            self.create_text(x, bottom + 10, text=primary_series[index][0].strftime(axis_format), fill=MUTED, anchor="n", font=("Segoe UI", 8))

    def _hover(self, event: tk.Event) -> None:
        self.delete("hover")
        visible_series = self._visible_series()
        if not visible_series:
            return
        left, right = 55, self.winfo_width() - 17
        if not left <= event.x <= right:
            return
        primary_series = visible_series[0][1]
        count = len(primary_series)
        index = 0 if count == 1 else round((event.x - left) * (count - 1) / (right - left))
        index = max(0, min(count - 1, index))
        day, _value = primary_series[index]
        x = left if count == 1 else left + (right - left) * index / (count - 1)
        if self.granularity == "Weekly":
            period = f"Week of {day:%d %b %Y}"
        elif self.granularity == "Monthly":
            period = f"{day:%B %Y}"
        else:
            period = f"{day:%a, %d %b %Y}"
        values = [
            (name, series[index][1], color)
            for name, series, color in visible_series
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
                box_x + 21, y + 3, text=f"{name}: {format_duration_modern(value)}",
                fill=TEXT, anchor="w", font=("Segoe UI", 8), tags="hover",
            )


class TopFriendsApp(tk.Tk):
    def __init__(self, database_path: Path) -> None:
        super().__init__()
        self.database_path = database_path
        today = date.today()
        self.repository = VrcxRepository(database_path)
        self.state = AppState(today - timedelta(days=29), today)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vrcx-data")
        self._dashboard_future: Future[DashboardData] | None = None
        self._comparison_future: Future[ComparisonData] | None = None
        self._request_generation = 0
        self._dashboard_data: DashboardData | None = None
        self._visible_friends: list[FriendStat] = []
        self._sort_column = "milliseconds"
        self._sort_reverse = True
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
        window_width = min(1240, max(960, self.winfo_screenwidth() - 120))
        window_height = min(900, max(760, self.winfo_screenheight() - 120))
        window_x = max(0, (self.winfo_screenwidth() - window_width) // 2)
        window_y = max(0, (self.winfo_screenheight() - window_height) // 2)
        self.geometry(f"{window_width}x{window_height}+{window_x}+{window_y}")
        self.minsize(960, 760)
        self.configure(bg=BG)
        self._configure_styles()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self.refresh)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()

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
        style.configure(
            "Dashboard.Horizontal.TProgressbar",
            troughcolor=PANEL_ALT,
            background=ACCENT,
            bordercolor=BG,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
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

        self.refresh_button = tk.Button(
            inner,
            text="Refresh dashboard",
            command=lambda: self.refresh(force=True),
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
        )
        self.refresh_button.pack(side="right", pady=(18, 0))

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
        for label, period in (
            ("This month", "this_month"),
            ("Last month", "last_month"),
            ("This year", "this_year"),
        ):
            tk.Button(
                presets,
                text=label,
                command=lambda value=period: self.set_calendar_range(value),
                bg=PANEL_ALT,
                fg=TEXT,
                activebackground=ACCENT,
                activeforeground="white",
                relief="flat",
                bd=0,
                padx=12,
                pady=5,
                cursor="hand2",
                font=FONT_SMALL,
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
        self.total_card = MetricCard(metrics, "Social time", ACCENT)
        self.average_card = MetricCard(metrics, "Friends in range", "#58a6ff")
        self.peak_card = MetricCard(metrics, "Most social day", SUCCESS)
        self.low_card = MetricCard(metrics, "Top friend", WARNING)
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
        self.selection_summary = tk.Label(
            table_frame,
            text="Select a friend for details · Ctrl-click to compare",
            bg=PANEL_ALT,
            fg=MUTED,
            anchor="w",
            padx=12,
            pady=7,
            font=FONT_SMALL,
        )
        self.selection_summary.pack(fill="x", padx=1)
        self.tree = ttk.Treeview(
            table_frame,
            columns=(
                "rank", "friend", "duration", "sessions", "average",
                "longest", "active_days", "last_seen",
            ),
            show="headings",
            style="Friends.Treeview",
            selectmode="extended",
        )
        self.tree.heading("rank", text="#", anchor="center")
        self.tree.heading(
            "friend", text="FRIEND", anchor="w",
            command=lambda: self.sort_friends("display_name"),
        )
        self.tree.heading(
            "duration", text="TIME TOGETHER", anchor="e",
            command=lambda: self.sort_friends("milliseconds"),
        )
        self.tree.heading(
            "sessions", text="SESSIONS", anchor="e",
            command=lambda: self.sort_friends("sessions"),
        )
        self.tree.heading(
            "average", text="AVERAGE", anchor="e",
            command=lambda: self.sort_friends("average_milliseconds"),
        )
        self.tree.heading(
            "longest", text="LONGEST", anchor="e",
            command=lambda: self.sort_friends("longest_milliseconds"),
        )
        self.tree.heading(
            "active_days", text="ACTIVE DAYS", anchor="e",
            command=lambda: self.sort_friends("active_days"),
        )
        self.tree.heading(
            "last_seen", text="LAST SEEN", anchor="e",
            command=lambda: self.sort_friends("last_seen"),
        )
        self.tree.column("rank", width=55, minwidth=45, stretch=False, anchor="center")
        self.tree.column("friend", width=220, minwidth=160, anchor="w")
        self.tree.column("duration", width=125, minwidth=105, anchor="e")
        self.tree.column("sessions", width=90, minwidth=80, anchor="e")
        self.tree.column("average", width=100, minwidth=85, anchor="e")
        self.tree.column("longest", width=100, minwidth=85, anchor="e")
        self.tree.column("active_days", width=95, minwidth=85, anchor="e")
        self.tree.column("last_seen", width=115, minwidth=100, anchor="e")
        table_scrollbar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.tree.yview
        )
        table_horizontal = ttk.Scrollbar(
            table_frame, orient="horizontal", command=self.tree.xview
        )
        self.tree.configure(
            yscrollcommand=table_scrollbar.set,
            xscrollcommand=table_horizontal.set,
        )
        self.tree.bind("<<TreeviewSelect>>", self.show_selected_friends)
        table_scrollbar.pack(side="right", fill="y", padx=(0, 1), pady=(0, 1))
        table_horizontal.pack(side="bottom", fill="x", padx=1, pady=(0, 1))
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
        self.comparison_mode_variable = tk.StringVar(value="Period total")
        self.comparison_mode_box = ttk.Combobox(
            series_controls,
            textvariable=self.comparison_mode_variable,
            values=("Period total", "Cumulative"),
            state="disabled",
            width=12,
            style="Filter.TCombobox",
        )
        self.comparison_mode_box.pack(side="left", padx=(8, 0))
        self.comparison_mode_box.bind(
            "<<ComboboxSelected>>", lambda _event: self.render_time_series()
        )
        tk.Button(
            series_controls,
            text="Reset zoom",
            command=lambda: self.chart.reset_zoom(),
            bg=PANEL,
            fg=MUTED,
            activebackground=PANEL_ALT,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=8,
            pady=6,
            cursor="hand2",
            font=FONT_SMALL,
        ).pack(side="left")
        self.chart = TimeSeriesChart(chart_frame)
        self.chart.pack(fill="both", expand=True, padx=1, pady=1)

        footer = tk.Frame(container, bg=BG)
        footer.place(relx=0, rely=1, relwidth=1, anchor="sw")
        self.status = tk.Label(
            footer, text="Loading…", bg=BG, fg=MUTED, font=("Segoe UI", 9)
        )
        self.status.pack(side="left")
        self.loading_bar = ttk.Progressbar(
            footer,
            mode="indeterminate",
            length=90,
            style="Dashboard.Horizontal.TProgressbar",
        )
        tk.Label(
            footer,
            text=f"Dates use local time · {LOCAL_TIMEZONE_NAME}",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="right")

        self.bind("<Return>", lambda _event: self.refresh())
        self.bind("<F5>", lambda _event: self.refresh(force=True))
        self.bind("<Control-f>", lambda _event: self.search_entry.focus_set())
        self.bind("<Escape>", lambda _event: self.clear_filters())

    def set_range(self, days: int) -> None:
        end = date.today()
        self.start_field.set(end - timedelta(days=days - 1))
        self.end_field.set(end)
        self.refresh()

    def set_calendar_range(self, period: str) -> None:
        today = date.today()
        if period == "this_month":
            start, end = today.replace(day=1), today
        elif period == "last_month":
            end = today.replace(day=1) - timedelta(days=1)
            start = end.replace(day=1)
        else:
            start, end = today.replace(month=1, day=1), today
        self.start_field.set(start)
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
            colors = self.assign_series_colors(self._selected_user_ids)
            series_list = [
                (
                    self._selected_friend_names.get(user_id, "Friend"),
                    self.prepare_comparison_series(
                        self._friend_series[user_id], granularity
                    ),
                    colors[user_id],
                )
                for user_id in self._selected_user_ids
                if user_id in self._friend_series
            ]
            metric_label = (
                "Cumulative time together"
                if self.comparison_mode_variable.get() == "Cumulative"
                else "Time together"
            )
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
                    aggregate_series(self._raw_series, granularity),
                    ACCENT_HOVER,
                )
            ]
            metric_label = "Person-time"
            context = f"Total person-time · {format_duration_modern(total)} · overlaps count per friend"
        else:
            total = sum(value for _day, value in self._raw_series)
            series_list = [
                (
                    "All current friends",
                    aggregate_series(self._raw_series, granularity),
                    ACCENT_HOVER,
                )
            ]
            metric_label = "Time with friends"
            context = f"Social time · {format_duration_modern(total)} · overlaps counted once"
        self.series_context.configure(text=context)
        self.chart.set_series(series_list, granularity, metric_label)

    def prepare_comparison_series(
        self, daily: list[tuple[date, int]], granularity: str
    ) -> list[tuple[date, int]]:
        series = aggregate_series(daily, granularity)
        if self.comparison_mode_variable.get() != "Cumulative":
            return series
        running = 0
        cumulative = []
        for day, value in series:
            running += value
            cumulative.append((day, running))
        return cumulative

    @staticmethod
    def series_color(user_id: str) -> str:
        digest = hashlib.blake2b(user_id.encode("utf-8"), digest_size=2).digest()
        return SERIES_COLORS[int.from_bytes(digest, "big") % len(SERIES_COLORS)]

    @classmethod
    def assign_series_colors(cls, user_ids: list[str]) -> dict[str, str]:
        assigned: dict[str, str] = {}
        used: set[int] = set()
        for user_id in sorted(user_ids):
            preferred = SERIES_COLORS.index(cls.series_color(user_id))
            index = preferred
            for _attempt in range(len(SERIES_COLORS)):
                if index not in used:
                    break
                index = (index + 1) % len(SERIES_COLORS)
            assigned[user_id] = SERIES_COLORS[index]
            used.add(index)
        return assigned

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
        self.comparison_mode_box.configure(state="disabled")
        self.selection_summary.configure(
            text="Select a friend for details · Ctrl-click to compare"
        )
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
        self._selected_user_ids = selection
        self._selected_friend_names = friend_names
        selected_stats = [
            friend for friend in self._visible_friends if friend.user_id in selection
        ]
        if len(selected_stats) == 1:
            friend = selected_stats[0]
            self.selection_summary.configure(
                text=(
                    f"{friend.display_name}  ·  {format_duration_modern(friend.milliseconds)} total  ·  "
                    f"{friend.sessions} sessions  ·  {format_duration_modern(friend.average_milliseconds)} average  ·  "
                    f"{format_duration_modern(friend.longest_milliseconds)} longest  ·  "
                    f"last seen {format_local_datetime(friend.last_seen)}"
                )
            )
        else:
            self.selection_summary.configure(
                text=f"Comparing {len(selected_stats)} friends across the selected date range"
            )
        self.state.selected_friend_ids = selection
        self.overview_button.configure(state="normal")
        self.metric_box.configure(state="disabled")
        self.comparison_mode_box.configure(state="readonly")
        comparison_state = self.collect_state()
        comparison_state.selected_friend_ids = selection
        self._comparison_future = self._executor.submit(
            self.repository.load_comparison, comparison_state
        )
        self.status.configure(
            text=f"Loading comparison for {len(selection)} friend{'s' if len(selection) != 1 else ''}…"
        )
        self.after(40, self.poll_comparison, self._comparison_future, comparison_state)

    def poll_comparison(
        self, future: Future[ComparisonData], comparison_state: AppState
    ) -> None:
        if future is not self._comparison_future:
            return
        if not future.done():
            self.after(40, self.poll_comparison, future, comparison_state)
            return
        try:
            result = future.result()
        except (VrcxDataError, ValueError, sqlite3.Error, OSError) as error:
            logging.exception("Comparison query failed")
            self.status.configure(text="Could not load the selected comparison.")
            messagebox.showerror(APP_TITLE, str(error), parent=self)
            return
        self._friend_series = {
            user_id: list(series)
            for user_id, series in result.series_by_user.items()
        }
        self._series_range = (comparison_state.start_date, comparison_state.end_date)
        self.render_time_series()
        self.status.configure(
            text=f"Comparing {len(self._friend_series)} friends · {LOCAL_TIMEZONE_NAME}"
        )

    def set_all_time(self) -> None:
        try:
            self.start_field.set(self.repository.earliest_local_date())
            self.end_field.set(date.today())
            self.refresh()
        except (VrcxDataError, ValueError, sqlite3.Error, OSError) as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)

    def collect_state(self) -> AppState:
        minimum_minutes, result_limit = self.ranking_filters()
        return AppState(
            start_date=self.start_field.get(),
            end_date=self.end_field.get(),
            search_term=self.search_variable.get(),
            minimum_minutes=minimum_minutes,
            result_limit=result_limit,
            selected_friend_ids=list(self._selected_user_ids),
            aggregation=self.granularity_variable.get(),
            overview_metric=self.metric_variable.get(),
        )

    def set_loading(self, loading: bool, message: str = "Loading activity…") -> None:
        self.configure(cursor="watch" if loading else "")
        self.refresh_button.configure(state="disabled" if loading else "normal")
        if loading:
            self.status.configure(text=message)
            self.loading_bar.pack(side="left", padx=(10, 0))
            self.loading_bar.start(12)
        else:
            self.loading_bar.stop()
            self.loading_bar.pack_forget()

    def refresh(self, force: bool = False) -> None:
        self._refresh_job = None
        try:
            state = self.collect_state()
        except ValueError as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)
            return
        if force:
            self.repository.invalidate()
        self.state = state
        self._request_generation += 1
        generation = self._request_generation
        if self._dashboard_future is not None and not self._dashboard_future.done():
            self._dashboard_future.cancel()
        self.set_loading(True)
        self._dashboard_future = self._executor.submit(
            self.repository.load_dashboard, state
        )
        self.after(40, self.poll_dashboard, self._dashboard_future, generation, state)

    def poll_dashboard(
        self,
        future: Future[DashboardData],
        generation: int,
        state: AppState,
    ) -> None:
        if generation != self._request_generation:
            return
        if not future.done():
            self.after(40, self.poll_dashboard, future, generation, state)
            return
        try:
            data = future.result()
        except (VrcxDataError, ValueError, sqlite3.Error, OSError) as error:
            logging.exception("Dashboard refresh failed")
            self.set_loading(False)
            self.status.configure(text="Could not refresh VRCX activity.")
            messagebox.showerror(APP_TITLE, str(error), parent=self)
            return
        self._dashboard_data = data
        self.render_dashboard(data, state)
        self.set_loading(False)

    def sort_friends(self, column: str) -> None:
        if column == self._sort_column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = column != "display_name"
        self.populate_friend_table(self._visible_friends)

    def populate_friend_table(self, friends: list[FriendStat]) -> None:
        selected = set(self._selected_user_ids)
        if self._sort_column == "display_name":
            key = lambda friend: friend.display_name.casefold()
        elif self._sort_column == "last_seen":
            key = lambda friend: (
                friend.last_seen is not None,
                friend.last_seen.timestamp() if friend.last_seen else 0,
            )
        else:
            key = lambda friend: getattr(friend, self._sort_column)
        ordered = sorted(friends, key=key, reverse=self._sort_reverse)

        headings = {
            "friend": "FRIEND",
            "duration": "TIME TOGETHER",
            "sessions": "SESSIONS",
            "average": "AVERAGE",
            "longest": "LONGEST",
            "active_days": "ACTIVE DAYS",
            "last_seen": "LAST SEEN",
        }
        attribute_to_column = {
            "display_name": "friend",
            "milliseconds": "duration",
            "sessions": "sessions",
            "average_milliseconds": "average",
            "longest_milliseconds": "longest",
            "active_days": "active_days",
            "last_seen": "last_seen",
        }
        active_column = attribute_to_column.get(self._sort_column)
        for column, label in headings.items():
            arrow = " ▼" if self._sort_reverse else " ▲"
            self.tree.heading(column, text=label + (arrow if column == active_column else ""))

        self._suppress_selection_event = True
        for item in self.tree.get_children():
            self.tree.delete(item)
        for rank, friend in enumerate(ordered, start=1):
            self.tree.insert(
                "",
                "end",
                iid=friend.user_id,
                values=(
                    rank,
                    friend.display_name,
                    format_duration_modern(friend.milliseconds),
                    friend.sessions,
                    format_duration_modern(friend.average_milliseconds),
                    format_duration_modern(friend.longest_milliseconds),
                    friend.active_days,
                    format_local_date(friend.last_seen),
                ),
            )
        visible_selection = [friend.user_id for friend in ordered if friend.user_id in selected]
        if visible_selection:
            self.tree.selection_set(visible_selection)
        self._suppress_selection_event = False

    def render_dashboard(self, data: DashboardData, state: AppState) -> None:
        selected_user_ids = list(self._selected_user_ids)
        rows = list(data.friends)
        self._visible_friends = rows
        self.populate_friend_table(rows)
        if rows:
            self.empty_state.place_forget()
        else:
            self.empty_state.place(relx=0.5, rely=0.56, anchor="center")
            self.empty_state.lift()

        if len(rows) < data.matching_count:
            ranking_text = f"Showing {len(rows)} of {data.matching_count} · Ctrl-click to compare"
        else:
            ranking_text = (
                f"{data.matching_count} match{'es' if data.matching_count != 1 else ''}"
                " · Ctrl-click to compare"
            )
        self.ranking_summary.configure(text=ranking_text)

        social_daily = list(data.social_daily)
        peak_day, peak_value = (
            max(social_daily, key=lambda item: item[1])
            if social_daily else (None, 0)
        )
        top_friend = rows[0] if rows else None
        self.total_card.set(
            format_duration_modern(data.total_social_milliseconds),
            "wall-clock time with at least one friend",
        )
        self.average_card.set(
            f"{data.matching_count}",
            f"of {data.current_friend_count} current friends in range",
        )
        self.peak_card.set(
            format_duration_modern(peak_value),
            peak_day.strftime("%a, %d %b %Y") if peak_day else "No data",
        )
        self.low_card.set(
            top_friend.display_name if top_friend else "—",
            format_duration_modern(top_friend.milliseconds) if top_friend else "No activity",
        )
        self._overview_daily = list(data.person_daily)
        self._social_daily = social_daily
        visible_ids = {friend.user_id for friend in rows}
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

        latest_label = format_local_datetime(data.latest_activity)
        if rows:
            summary = (
                f"{state.start_date:%d %b %Y} – {state.end_date:%d %b %Y} · "
                f"{data.visible_sessions:,} shared sessions in visible list · "
                f"Latest data {latest_label}"
            )
        elif data.matching_count == 0:
            summary = (
                f"No friends match · {data.current_friend_count} current friends · "
                f"Latest data {latest_label}"
            )
        else:
            summary = f"No friends shown · Latest data {latest_label}"
        self.status.configure(text=summary)


def run_check(database_path: Path) -> int:
    end = date.today()
    data = VrcxRepository(database_path).load_dashboard(
        AppState(end - timedelta(days=6), end)
    )
    total = data.total_person_milliseconds
    social_total = data.total_social_milliseconds
    if social_total > total:
        raise RuntimeError("Social time cannot exceed total person-time.")
    peak_day, peak_value = max(data.social_daily, key=lambda item: item[1])
    print(
        f"OK: {len(data.friends)} of {data.matching_count} friends shown, "
        f"{data.current_friend_count} current friends, {len(data.social_daily)} daily points, "
        f"{format_duration_modern(social_total)} social time, "
        f"{format_duration_modern(total)} person-time, "
        f"peak {peak_day} ({format_duration_modern(peak_value)}), "
        f"timezone {LOCAL_TIMEZONE_NAME}, "
        f"latest activity {format_local_datetime(data.latest_activity)}"
    )
    return 0


def main() -> int:
    log_path = configure_logging()
    logging.info("Starting VRCX Time Together")
    try:
        database_path = locate_database(Path(__file__))
    except FileNotFoundError as error:
        if "--check" in sys.argv:
            print(f"ERROR: {error}", file=sys.stderr)
        else:
            messagebox.showerror(APP_TITLE, str(error))
        return 1
    logging.info("Using read-only VRCX database at %s", database_path)
    if "--check" in sys.argv:
        return run_check(database_path)
    app = TopFriendsApp(database_path)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
