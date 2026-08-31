from __future__ import annotations

import hashlib
import logging
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from PySide6.QtCore import (
    QAbstractTableModel,
    QDate,
    QEvent,
    QLocale,
    QModelIndex,
    QObject,
    QPoint,
    QRect,
    QRunnable,
    QSettings,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QBoxLayout,
    QButtonGroup,
    QComboBox,
    QCompleter,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableView,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .formatting import (
    ENGLISH_MONTHS,
    format_duration,
    format_english_day,
    format_local_date,
    format_local_datetime,
)
from .logging_utils import configure_logging
from .models import (
    AppState,
    ComparisonData,
    DashboardData,
    FriendIdentity,
    FriendInsightsData,
    FriendMapData,
    FriendStat,
)
from .qt_chart import TimeSeriesChart
from .qt_friend_map import (
    FriendMapWidget,
    SegmentedControl,
    TopFriendsBarChart,
    activity_rank_legend_html,
    friend_group_color,
)
from .qt_insights import (
    CalendarHeatmap,
    CompanyContextChart,
    CoPresenceChart,
    WeekHourHeatmap,
)
from .qt_theme import (
    ACCENT,
    SERIES_COLORS,
    SUCCESS,
    WARNING,
    apply_theme,
)
from .repository import (
    VrcxDataError,
    VrcxRepository,
    aggregate_time_series,
    find_database,
    find_friend_table,
    open_database,
)
from .timezone_utils import LOCAL_TIMEZONE_NAME

LOGGER = logging.getLogger(__name__)
ENGLISH_LOCALE = QLocale(QLocale.Language.English, QLocale.Country.UnitedKingdom)
SETTINGS_ORGANIZATION = "VRCX Time Together"
SETTINGS_APPLICATION = "Desktop"
DATABASE_PATH_KEY = "databasePath"
PAGE_OVERVIEW = 0
PAGE_FRIENDS = 1
PAGE_FRIEND_MAP = 2
PAGE_SHARED_TIME = 3
PAGE_INSIGHTS = 4


def resolve_database_path(script_path: Path) -> Path:
    """Use a valid saved path, otherwise retain the existing automatic lookup."""
    settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
    stored = settings.value(DATABASE_PATH_KEY, "", type=str).strip()
    if stored:
        candidate = Path(stored)
        if candidate.is_file():
            return candidate
        settings.remove(DATABASE_PATH_KEY)
    return find_database(script_path)


class WorkerSignals(QObject):
    result = Signal(int, object)
    error = Signal(int, str, str)
    finished = Signal(object)


class RepositoryWorker(QRunnable):
    def __init__(self, generation: int, function: Callable[[], object]) -> None:
        super().__init__()
        self.generation = generation
        self.function = function
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function()
        except Exception as error:  # Worker boundary logs technical context.
            LOGGER.exception("Background repository operation failed")
            self.signals.error.emit(
                self.generation,
                str(error),
                error.__class__.__name__,
            )
        else:
            self.signals.result.emit(self.generation, result)
        finally:
            self.signals.finished.emit(self)


class KpiInfoLabel(QLabel):
    """Information marker that shows help immediately and reliably."""

    def __init__(self, title: str, explanation: str) -> None:
        super().__init__("i")
        self.help_text = (
            "<div style='width:280px; white-space:normal'>"
            f"<b>{title}</b><br><br>{explanation}</div>"
        )
        self.setObjectName("KpiInfo")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setToolTip(self.help_text)
        self.setAccessibleName(f"About {title}")
        self.setAccessibleDescription(explanation)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedSize(18, 18)

    def show_help(self) -> None:
        position = self.mapToGlobal(QPoint(-18, self.height() + 7))
        QToolTip.showText(position, self.help_text, self, QRect(), 12_000)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.show_help()
        super().enterEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.show_help()
        super().focusInEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.show_help()
        super().mousePressEvent(event)


class MetricCard(QFrame):
    def __init__(self, title: str, accent: str, explanation: str) -> None:
        super().__init__()
        self.setObjectName("Card")
        self.setAccessibleName(f"{title} KPI")
        self.setAccessibleDescription(explanation)
        self.setMinimumHeight(108)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        stripe = QFrame()
        stripe.setFixedWidth(3)
        stripe.setStyleSheet(
            f"background:{accent}; border-top-left-radius:9px; border-bottom-left-radius:9px;"
        )
        layout.addWidget(stripe)
        content = QVBoxLayout()
        content.setContentsMargins(15, 12, 14, 12)
        content.setSpacing(2)
        self.title = QLabel(title.upper())
        self.title.setObjectName("MetricLabel")
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        title_row.addWidget(self.title)
        title_row.addStretch(1)
        self.info = KpiInfoLabel(title, explanation)
        title_row.addWidget(self.info)
        self.value = QLabel("—")
        self.value.setObjectName("MetricValue")
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail = QLabel("Loading activity…")
        self.detail.setObjectName("MetricDetail")
        self.detail.setWordWrap(True)
        content.addLayout(title_row)
        content.addWidget(self.value)
        content.addWidget(self.detail)
        content.addStretch(1)
        layout.addLayout(content, 1)

    def set_value(self, value: str, detail: str) -> None:
        self.value.setText(value)
        font = self.value.font()
        font.setPointSize(17 if len(value) <= 18 else 12)
        self.value.setFont(font)
        self.detail.setText(detail)


class FriendsTableModel(QAbstractTableModel):
    COLUMNS = (
        ("Friend", "display_name"),
        ("Time together", "milliseconds"),
        ("Sessions", "sessions"),
        ("Average", "average_milliseconds"),
        ("Longest", "longest_milliseconds"),
        ("Active days", "active_days"),
        ("First seen", "first_seen"),
        ("Last seen", "last_seen"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.friends: list[FriendStat] = []

    def set_friends(self, friends: list[FriendStat]) -> None:
        self.beginResetModel()
        self.friends = list(friends)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self.friends)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self.COLUMNS)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.friends):
            return None
        friend = self.friends[index.row()]
        attribute = self.COLUMNS[index.column()][1]
        value = getattr(friend, attribute)
        if role == Qt.ItemDataRole.UserRole:
            if hasattr(value, "timestamp"):
                return value.timestamp() if value is not None else 0
            return value.casefold() if isinstance(value, str) else value
        if role == Qt.ItemDataRole.DisplayRole:
            if attribute in {
                "milliseconds",
                "average_milliseconds",
                "longest_milliseconds",
            }:
                return format_duration(value)
            if attribute in {"first_seen", "last_seen"}:
                return format_local_date(value)
            return value
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if index.column() == 0:
                return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(friend)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section][0]
        return None

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder) -> None:
        if not 0 <= column < len(self.COLUMNS):
            return
        attribute = self.COLUMNS[column][1]

        def key(friend: FriendStat):
            value = getattr(friend, attribute)
            if value is None:
                return (0, 0)
            if isinstance(value, str):
                value = value.casefold()
            return (1, value)

        self.layoutAboutToBeChanged.emit()
        self.friends.sort(
            key=key,
            reverse=order == Qt.SortOrder.DescendingOrder,
        )
        self.layoutChanged.emit()

    @staticmethod
    def _tooltip(friend: FriendStat) -> str:
        return (
            f"{friend.display_name}\n"
            f"{format_duration(friend.milliseconds)} across {friend.sessions} sessions\n"
            f"Last seen {format_local_datetime(friend.last_seen)}"
        )


def configure_table(table: QTableView, model: FriendsTableModel) -> None:
    table.setModel(model)
    table.setSortingEnabled(True)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(40)
    table.horizontalHeader().setStretchLastSection(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    table.setMinimumHeight(180)


class DateRangePanel(QFrame):
    """Expandable in-app English local-date controls."""

    range_selected = Signal(object, object, str)
    close_requested = Signal()

    def __init__(
        self,
        start: date,
        end: date,
        earliest: date,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("InlineDatePanel")
        self.earliest = earliest
        self.preset_label = "Custom range"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("Date range")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        context = QLabel(f"English · local date & time · {LOCAL_TIMEZONE_NAME}")
        context.setObjectName("Muted")
        header.addWidget(context)
        header.addStretch(1)
        close = QPushButton("Close")
        close.setObjectName("QuietButton")
        close.clicked.connect(self.close_requested.emit)
        header.addWidget(close)
        layout.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(14)
        presets = QGridLayout()
        presets.setHorizontalSpacing(7)
        presets.setVerticalSpacing(7)
        today = date.today()
        last_month_end = today.replace(day=1) - timedelta(days=1)
        values = (
            ("Today", today, today),
            ("Last 7 days", today - timedelta(days=6), today),
            ("Last 30 days", today - timedelta(days=29), today),
            ("Last 90 days", today - timedelta(days=89), today),
            ("This month", today.replace(day=1), today),
            ("Last month", last_month_end.replace(day=1), last_month_end),
            ("This year", today.replace(month=1, day=1), today),
            ("All time", earliest, today),
        )
        for index, (label, preset_start, preset_end) in enumerate(values):
            button = QPushButton(label)
            button.setObjectName("PresetButton")
            button.clicked.connect(
                lambda _checked=False, name=label, first=preset_start, last=preset_end:
                self.set_preset(name, first, last)
            )
            presets.addWidget(button, index // 4, index % 4)
        content.addLayout(presets, 3)

        exact = QGridLayout()
        exact.setHorizontalSpacing(8)
        exact.setVerticalSpacing(7)
        from_label = QLabel("FROM")
        from_label.setObjectName("MetricLabel")
        to_label = QLabel("TO")
        to_label.setObjectName("MetricLabel")
        self.start_edit = self._date_edit(start)
        self.end_edit = self._date_edit(end)
        self.start_edit.dateChanged.connect(self._mark_custom)
        self.end_edit.dateChanged.connect(self._mark_custom)
        exact.addWidget(from_label, 0, 0)
        exact.addWidget(self.start_edit, 0, 1)
        exact.addWidget(to_label, 1, 0)
        exact.addWidget(self.end_edit, 1, 1)
        apply_button = QPushButton("Apply")
        apply_button.setObjectName("PrimaryButton")
        apply_button.clicked.connect(self.apply_custom_range)
        apply_button.setMinimumWidth(74)
        exact.addWidget(apply_button, 0, 2, 2, 1)
        content.addLayout(exact, 2)
        layout.addLayout(content)

    @staticmethod
    def _date_edit(value: date) -> QDateEdit:
        editor = QDateEdit(QDate(value.year, value.month, value.day))
        editor.setCalendarPopup(True)
        editor.setDisplayFormat("dd MMMM yyyy")
        editor.setLocale(ENGLISH_LOCALE)
        editor.calendarWidget().setLocale(ENGLISH_LOCALE)
        editor.setMinimumWidth(170)
        return editor

    @staticmethod
    def _python_date(value: QDate) -> date:
        return date(value.year(), value.month(), value.day())

    def set_preset(self, label: str, start: date, end: date) -> None:
        if label == "All time":
            start = self.earliest
        self.start_edit.blockSignals(True)
        self.end_edit.blockSignals(True)
        self.start_edit.setDate(QDate(start.year, start.month, start.day))
        self.end_edit.setDate(QDate(end.year, end.month, end.day))
        self.start_edit.blockSignals(False)
        self.end_edit.blockSignals(False)
        self.preset_label = label
        self.range_selected.emit(start, end, label)

    def set_earliest_date(self, value: date) -> None:
        self.earliest = value

    def _mark_custom(self) -> None:
        self.preset_label = "Custom range"

    def selected_range(self) -> tuple[date, date, str]:
        return (
            self._python_date(self.start_edit.date()),
            self._python_date(self.end_edit.date()),
            self.preset_label,
        )

    def set_current_range(self, start: date, end: date) -> None:
        self.start_edit.blockSignals(True)
        self.end_edit.blockSignals(True)
        self.start_edit.setDate(QDate(start.year, start.month, start.day))
        self.end_edit.setDate(QDate(end.year, end.month, end.day))
        self.start_edit.blockSignals(False)
        self.end_edit.blockSignals(False)

    def apply_custom_range(self) -> None:
        start, end, _label = self.selected_range()
        if end < start:
            QMessageBox.warning(
                self,
                "Invalid date range",
                "The end date must be on or after the start date.",
            )
            return
        self.range_selected.emit(start, end, "Custom range")


class SearchableComboBox(QComboBox):
    """Editable combo that opens all options on click and filters while typing."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMaxVisibleItems(18)
        editor = self.lineEdit()
        if editor is not None:
            editor.setPlaceholderText("Click or type to filter friends…")
            editor.installEventFilter(self)
            editor.textEdited.connect(self._filter_options)
        completer = self.completer()
        if completer is not None:
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            completer.setMaxVisibleItems(18)
            completer.activated[str].connect(self._select_completion)

    def _filter_options(self, value: str) -> None:
        completer = self.completer()
        if completer is None:
            return
        completer.setCompletionPrefix(value)
        completer.complete()

    def _select_completion(self, value: str) -> None:
        index = self.findText(value, Qt.MatchFlag.MatchExactly)
        if index >= 0:
            self.setCurrentIndex(index)

    def show_all_options(self) -> None:
        editor = self.lineEdit()
        completer = self.completer()
        if editor is None or completer is None:
            self.showPopup()
            return
        editor.setFocus()
        editor.selectAll()
        completer.setCompletionPrefix("")
        completer.complete()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched is self.lineEdit() and event.type() == QEvent.Type.MouseButtonPress:
            QTimer.singleShot(0, self.show_all_options)
        return super().eventFilter(watched, event)


class MainWindow(QMainWindow):
    def __init__(
        self,
        repository: VrcxRepository,
        script_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.setLocale(ENGLISH_LOCALE)
        self.repository = repository
        self.script_path = script_path or Path(__file__)
        today = date.today()
        self.state = AppState(today - timedelta(days=29), today, result_limit=None)
        self._start_date = self.state.start_date
        self._end_date = self.state.end_date
        self._range_label = "Last 30 days"
        self.dashboard: DashboardData | None = None
        self.thread_pool = QThreadPool.globalInstance()
        self._workers: set[RepositoryWorker] = set()
        self._dashboard_generation = 0
        self._comparison_generation = 0
        self._insights_generation = 0
        self._map_generation = 0
        self._selected_friend_ids: list[str] = []
        self._selected_insight_friend_id: str | None = None
        self._current_friend_id: str | None = None
        self._friend_by_id: dict[str, FriendStat] = {}
        self._friend_option_by_id: dict[str, FriendIdentity] = {}
        self._friend_insights_data: FriendInsightsData | None = None
        self._friend_map_data: FriendMapData | None = None
        self._selected_map_friend_id: str | None = None
        self._selected_map_group_id = 0
        self._settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
        self._dashboard_debounce = QTimer(self)
        self._dashboard_debounce.setSingleShot(True)
        self._dashboard_debounce.timeout.connect(self.refresh_dashboard)
        self._comparison_debounce = QTimer(self)
        self._comparison_debounce.setSingleShot(True)
        self._comparison_debounce.timeout.connect(self.refresh_comparison)

        self.setWindowTitle("VRCX · Time Together")
        self.setMinimumSize(940, 700)
        self.resize(1280, 860)
        saved_geometry = self._settings.value("windowGeometry")
        if saved_geometry:
            self.restoreGeometry(saved_geometry)
        self._build_shell()
        self._connect_shortcuts()
        QTimer.singleShot(0, self.refresh_dashboard)

    def _build_shell(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_sidebar())

        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self._build_topbar())
        try:
            earliest = self.repository.earliest_local_date()
        except (VrcxDataError, OSError):
            earliest = self._start_date
        self.date_range_panel = DateRangePanel(
            self._start_date,
            self._end_date,
            earliest,
        )
        self.date_range_panel.range_selected.connect(self.set_date_range)
        self.date_range_panel.close_requested.connect(self.close_date_range_panel)
        self.date_range_panel.hide()
        main_layout.addWidget(self.date_range_panel)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        main_layout.addWidget(self.progress)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_overview_page())
        self.pages.addWidget(self._build_friends_page())
        self.pages.addWidget(self._build_friend_map_page())
        self.pages.addWidget(self._build_compare_page())
        self.pages.addWidget(self._build_insights_page())
        main_layout.addWidget(self.pages, 1)
        root_layout.addWidget(main, 1)
        self.setCentralWidget(root)

        status = QStatusBar()
        status.setSizeGripEnabled(False)
        self.setStatusBar(status)
        self.status_label = QLabel("Loading local VRCX activity…")
        self.status_label.setObjectName("Muted")
        status.addWidget(self.status_label, 1)
        timezone = QLabel(f"Local date & time · {LOCAL_TIMEZONE_NAME}")
        timezone.setObjectName("Muted")
        status.addPermanentWidget(timezone)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(206)
        self.sidebar = sidebar
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(15, 18, 15, 16)
        layout.setSpacing(6)

        brand = QLabel("VRCX  Time Together")
        brand.setObjectName("Brand")
        layout.addWidget(brand)
        layout.addSpacing(18)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[QPushButton] = []
        for index, (label, description) in enumerate(
            (
                ("Overview", "Summary and trends"),
                ("Friends", "Search and details"),
                ("Friend Map", "Interactive co-presence network"),
                ("Shared Time", "Multi-friend timeline"),
                ("Insights", "Selected-friend patterns"),
            )
        ):
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setToolTip(description)
            button.clicked.connect(lambda checked=False, value=index: self.set_page(value))
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            layout.addWidget(button)
        self.nav_buttons[PAGE_OVERVIEW].setChecked(True)
        layout.addStretch(1)

        privacy = QLabel("●  LOCAL & READ-ONLY")
        privacy.setObjectName("PrivacyChip")
        privacy.setToolTip("No telemetry, uploads, login, or VRCX database writes")
        privacy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(privacy)
        self.db_label = QLabel(self.repository.database_path.name)
        self.db_label.setObjectName("Subtle")
        self.db_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.db_label.setToolTip(str(self.repository.database_path))
        layout.addWidget(self.db_label)
        choose_database = QPushButton("Change database")
        choose_database.setObjectName("QuietButton")
        choose_database.setToolTip("Choose another VRCX SQLite database in read-only mode")
        choose_database.clicked.connect(self.choose_database)
        layout.addWidget(choose_database)
        self.reset_database_button = QPushButton("Use automatic path")
        self.reset_database_button.setObjectName("QuietButton")
        self.reset_database_button.setToolTip(
            "Return to the database beside this app or the standard VRCX AppData location"
        )
        self.reset_database_button.clicked.connect(self.reset_database_path)
        self.reset_database_button.setVisible(
            bool(self._settings.value(DATABASE_PATH_KEY, "", type=str).strip())
        )
        layout.addWidget(self.reset_database_button)
        return sidebar

    def _build_topbar(self) -> QWidget:
        topbar = QWidget()
        topbar.setObjectName("TopBar")
        layout = QHBoxLayout(topbar)
        layout.setContentsMargins(24, 13, 24, 13)
        layout.setSpacing(9)
        self.page_context = QLabel("OVERVIEW")
        self.page_context.setObjectName("MetricLabel")
        layout.addWidget(self.page_context)
        layout.addStretch(1)

        self.range_button = QPushButton()
        self.range_button.setObjectName("RangeButton")
        self.range_button.setMaximumWidth(520)
        self.range_button.setCheckable(True)
        self.range_button.setToolTip("Choose a quick range or exact local dates")
        self.range_button.clicked.connect(self.toggle_date_range_panel)
        self._update_range_button()
        layout.addWidget(self.range_button)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("PrimaryButton")
        self.refresh_button.clicked.connect(lambda: self.refresh_dashboard(force=True))
        layout.addWidget(self.refresh_button)
        return topbar

    def choose_database(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose VRCX database",
            str(self.repository.database_path.parent),
            "SQLite databases (*.sqlite3 *.sqlite *.db);;All files (*)",
        )
        if not selected:
            return
        self.change_database(Path(selected), remember=True)

    def reset_database_path(self) -> None:
        try:
            database_path = find_database(self.script_path)
        except FileNotFoundError as error:
            self.show_error(str(error))
            return
        self.change_database(database_path, remember=False)

    @staticmethod
    def validate_database(database_path: Path) -> None:
        if not database_path.is_file():
            raise VrcxDataError(f"Database file does not exist: {database_path}")
        with closing(open_database(database_path)) as connection:
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='gamelog_join_leave'"
            ).fetchone() is None:
                raise VrcxDataError(
                    "The selected file is not a compatible VRCX database "
                    "(gamelog_join_leave is missing)."
                )
            find_friend_table(connection)

    def change_database(self, database_path: Path, *, remember: bool) -> None:
        try:
            database_path = database_path.resolve()
            self.validate_database(database_path)
            candidate = VrcxRepository(database_path)
            earliest = candidate.earliest_local_date()
        except (VrcxDataError, OSError, sqlite3.Error) as error:
            self.show_error(f"The selected database could not be used.\n\n{error}")
            return

        self._dashboard_debounce.stop()
        self._comparison_debounce.stop()
        self._dashboard_generation += 1
        self._comparison_generation += 1
        self._insights_generation += 1
        self._map_generation += 1
        self.repository = candidate
        self.dashboard = None
        self._friend_by_id = {}
        self._friend_option_by_id = {}
        self._selected_friend_ids = []
        self._selected_insight_friend_id = None
        self._current_friend_id = None
        self._comparison_data = None
        self._friend_insights_data = None
        self._friend_map_data = None
        self._selected_map_friend_id = None
        self.clear_comparison()
        self.clear_friend_insights()
        self.clear_friend_map()
        self.date_range_panel.set_earliest_date(earliest)
        self.db_label.setText(database_path.name)
        self.db_label.setToolTip(str(database_path))
        if remember:
            self._settings.setValue(DATABASE_PATH_KEY, str(database_path))
        else:
            self._settings.remove(DATABASE_PATH_KEY)
        self.reset_database_button.setVisible(remember)
        self.refresh_dashboard(force=True)

    def _page_container(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 21, 24, 20)
        layout.setSpacing(14)
        title_label = QLabel(title)
        title_label.setObjectName("PageTitle")
        layout.addWidget(title_label)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("Muted")
        layout.addWidget(subtitle_label)
        return page, layout

    def _build_overview_page(self) -> QWidget:
        page, layout = self._page_container(
            "Overview",
            "A quick view of how social your selected period was and who shaped it.",
        )
        self.metrics_host = QWidget()
        self.metrics_layout = QGridLayout(self.metrics_host)
        self.metrics_layout.setContentsMargins(0, 0, 0, 0)
        self.metrics_layout.setHorizontalSpacing(11)
        self.metrics_layout.setVerticalSpacing(11)
        self.metric_cards = [
            MetricCard(
                "Social time",
                ACCENT,
                "Total wall-clock time spent with at least one current friend. "
                "Overlapping friend sessions are merged, so the same minute is counted once.",
            ),
            MetricCard(
                "Friends in range",
                "#5ba7ff",
                "Current VRCX friends with shared activity matching the selected local-date "
                "range and the active friend filters.",
            ),
            MetricCard(
                "Shared sessions",
                SUCCESS,
                "Completed join-to-leave sessions represented by the currently visible friend "
                "results. Each friend's session is counted separately.",
            ),
            MetricCard(
                "Most social day",
                WARNING,
                "The local calendar day with the most social wall-clock time in this period. "
                "Overlapping friend sessions are merged before the peak is calculated.",
            ),
            MetricCard(
                "Top friend",
                "#e887b7",
                "The current friend with the greatest total shared-session time inside the "
                "selected local-date range and active filters.",
            ),
        ]
        for index, card in enumerate(self.metric_cards):
            self.metrics_layout.addWidget(card, 0, index)
        layout.addWidget(self.metrics_host)

        chart_panel = QFrame()
        chart_panel.setObjectName("Panel")
        chart_layout = QVBoxLayout(chart_panel)
        chart_layout.setContentsMargins(16, 14, 16, 14)
        header = QHBoxLayout()
        title = QLabel("Social activity")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.overview_metric = QComboBox()
        self.overview_metric.addItems(["Time with friends", "Person-time"])
        self.overview_metric.currentTextChanged.connect(self.render_overview_chart)
        header.addWidget(self.overview_metric)
        self.overview_aggregation = QComboBox()
        self.overview_aggregation.addItems(["Daily", "Weekly", "Monthly"])
        self.overview_aggregation.currentTextChanged.connect(self.render_overview_chart)
        header.addWidget(self.overview_aggregation)
        reset = QPushButton("Reset view")
        reset.clicked.connect(lambda: self.overview_chart.reset_view())
        header.addWidget(reset)
        chart_layout.addLayout(header)
        self.overview_chart = TimeSeriesChart()
        chart_layout.addWidget(self.overview_chart, 1)
        layout.addWidget(chart_panel, 3)

        top_panel = QFrame()
        top_panel.setObjectName("Panel")
        top_panel.setMaximumHeight(210)
        top_layout = QVBoxLayout(top_panel)
        top_layout.setContentsMargins(14, 12, 14, 12)
        top_header = QHBoxLayout()
        top_title = QLabel("Top friends")
        top_title.setObjectName("SectionTitle")
        top_header.addWidget(top_title)
        top_header.addStretch(1)
        open_friends = QPushButton("View all friends")
        open_friends.clicked.connect(lambda: self.set_page(PAGE_FRIENDS))
        top_header.addWidget(open_friends)
        top_layout.addLayout(top_header)
        self.top_friends_host = QWidget()
        self.top_friends_host.setMinimumHeight(136)
        self.top_friends_layout = QVBoxLayout(self.top_friends_host)
        self.top_friends_layout.setContentsMargins(0, 0, 0, 0)
        self.top_friends_layout.setSpacing(5)
        top_layout.addWidget(self.top_friends_host)
        layout.addWidget(top_panel)
        return page

    def _build_friends_page(self) -> QWidget:
        page, layout = self._page_container(
            "Friends",
            "Sort the raw numbers, search by display name, and inspect one relationship at a time.",
        )
        filters = QFrame()
        filters.setObjectName("Panel")
        filter_layout = QHBoxLayout(filters)
        filter_layout.setContentsMargins(12, 10, 12, 10)
        self.friend_search = QLineEdit()
        self.friend_search.setPlaceholderText("Search friends…  Ctrl+F")
        self.friend_search.setClearButtonEnabled(True)
        self.friend_search.textChanged.connect(self._friend_search_changed)
        filter_layout.addWidget(self.friend_search, 1)
        self.minimum_time = QComboBox()
        self.minimum_time.addItems(["Any time", "15 minutes", "1 hour", "5 hours", "10 hours"])
        self.minimum_time.currentTextChanged.connect(self.schedule_dashboard_refresh)
        filter_layout.addWidget(self.minimum_time)
        self.result_limit = QComboBox()
        self.result_limit.addItems(["All friends", "Top 25", "Top 50", "Top 100"])
        self.result_limit.currentTextChanged.connect(self.schedule_dashboard_refresh)
        filter_layout.addWidget(self.result_limit)
        clear = QPushButton("Clear filters")
        clear.clicked.connect(self.clear_friend_filters)
        filter_layout.addWidget(clear)
        layout.addWidget(filters)

        panel = QFrame()
        panel.setObjectName("Panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        self.friends_model = FriendsTableModel()
        self.friends_table = QTableView()
        configure_table(self.friends_table, self.friends_model)
        self.friends_table.selectionModel().selectionChanged.connect(self.show_friend_detail)
        self.friends_table.doubleClicked.connect(self.compare_current_friend)
        panel_layout.addWidget(self.friends_table, 1)
        layout.addWidget(panel, 1)

        self.friend_detail = QFrame()
        self.friend_detail.setObjectName("DetailStrip")
        detail_layout = QVBoxLayout(self.friend_detail)
        detail_layout.setContentsMargins(14, 10, 14, 10)
        self.friend_detail_title = QLabel("Select a friend to see focused details")
        self.friend_detail_title.setObjectName("SectionTitle")
        self.friend_detail_text = QLabel("Total time, session quality, active days, and recency appear here.")
        self.friend_detail_text.setObjectName("Muted")
        self.friend_detail_text.setWordWrap(True)
        detail_layout.addWidget(self.friend_detail_title)
        detail_layout.addWidget(self.friend_detail_text)
        detail_actions = QHBoxLayout()
        detail_actions.addStretch(1)
        self.friend_insights_button = QPushButton("Open friend insights")
        self.friend_insights_button.setObjectName("PrimaryButton")
        self.friend_insights_button.setEnabled(False)
        self.friend_insights_button.clicked.connect(self.open_current_friend_insights)
        detail_actions.addWidget(self.friend_insights_button)
        detail_layout.addLayout(detail_actions)
        layout.addWidget(self.friend_detail)
        return page

    def _build_compare_page(self) -> QWidget:
        page, layout = self._page_container(
            "Shared Time",
            "Choose several friends and view their shared-time trends on one interactive chart.",
        )
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        left = QFrame()
        left.setObjectName("Panel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_header = QHBoxLayout()
        left_title = QLabel("Choose friends")
        left_title.setObjectName("SectionTitle")
        left_header.addWidget(left_title)
        left_header.addStretch(1)
        self.compare_count = QLabel("0 selected")
        self.compare_count.setObjectName("Muted")
        left_header.addWidget(self.compare_count)
        left_layout.addLayout(left_header)
        self.compare_search = QLineEdit()
        self.compare_search.setPlaceholderText("Filter this list…")
        self.compare_search.setClearButtonEnabled(True)
        self.compare_search.textChanged.connect(self.filter_compare_list)
        left_layout.addWidget(self.compare_search)
        self.compare_list = QListWidget()
        self.compare_list.itemChanged.connect(self.schedule_comparison_refresh)
        left_layout.addWidget(self.compare_list, 1)
        clear = QPushButton("Clear selection")
        clear.clicked.connect(self.clear_comparison)
        left_layout.addWidget(clear)
        splitter.addWidget(left)

        right = QFrame()
        right.setObjectName("Panel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 14, 16, 14)
        controls = QHBoxLayout()
        self.compare_context = QLabel("Select at least one friend")
        self.compare_context.setObjectName("SectionTitle")
        controls.addWidget(self.compare_context)
        controls.addStretch(1)
        self.compare_mode = QComboBox()
        self.compare_mode.addItems(["Period total", "Cumulative"])
        self.compare_mode.currentTextChanged.connect(self.render_comparison_chart)
        controls.addWidget(self.compare_mode)
        self.compare_aggregation = QComboBox()
        self.compare_aggregation.addItems(["Daily", "Weekly", "Monthly"])
        self.compare_aggregation.currentTextChanged.connect(self.render_comparison_chart)
        controls.addWidget(self.compare_aggregation)
        reset = QPushButton("Reset view")
        reset.clicked.connect(lambda: self.compare_chart.reset_view())
        controls.addWidget(reset)
        right_layout.addLayout(controls)
        self.compare_chart = TimeSeriesChart()
        right_layout.addWidget(self.compare_chart, 1)
        splitter.addWidget(right)
        splitter.setSizes([270, 780])
        layout.addWidget(splitter, 1)
        return page

    def _build_insights_page(self) -> QWidget:
        page, layout = self._page_container(
            "Friend insights",
            "Choose one friend to explore when you meet, the company context, and who is usually there.",
        )

        controls = QFrame()
        controls.setObjectName("Panel")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(12, 10, 12, 10)
        friend_label = QLabel("FRIEND")
        friend_label.setObjectName("MetricLabel")
        controls_layout.addWidget(friend_label)
        self.insight_friend = SearchableComboBox()
        self.insight_friend.setMinimumWidth(280)
        self.insight_friend.setToolTip(
            "Click to browse all current friends, or type to filter the list"
        )
        self.insight_friend.currentIndexChanged.connect(
            self.insight_friend_changed
        )
        controls_layout.addWidget(self.insight_friend)
        controls_layout.addStretch(1)
        self.insight_scope = QLabel("Known current friends · same recorded instance")
        self.insight_scope.setObjectName("Muted")
        self.insight_scope.setToolTip(
            "Group size counts overlapping current-friend records, not every person "
            "who may have been in the instance."
        )
        controls_layout.addWidget(self.insight_scope)
        layout.addWidget(controls)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 4, 4)
        content_layout.setSpacing(12)

        metrics_host = QWidget()
        self.insight_metrics_layout = QGridLayout(metrics_host)
        self.insight_metrics_layout.setContentsMargins(0, 0, 0, 0)
        self.insight_metrics_layout.setHorizontalSpacing(11)
        self.insight_metrics_layout.setVerticalSpacing(11)
        self.insight_metric_cards = [
            MetricCard(
                "Time together",
                ACCENT,
                "Completed selected-friend session time clipped to this date range.",
            ),
            MetricCard(
                "Active days",
                "#5ba7ff",
                "Local calendar days with recorded time together in this range.",
            ),
            MetricCard(
                "Encounters",
                SUCCESS,
                "Completed selected-friend OnPlayerLeft sessions overlapping this range.",
            ),
        ]
        for index, card in enumerate(self.insight_metric_cards):
            self.insight_metrics_layout.addWidget(card, 0, index)
        content_layout.addWidget(metrics_host)

        calendar_panel = QFrame()
        calendar_panel.setObjectName("Panel")
        calendar_layout = QVBoxLayout(calendar_panel)
        calendar_layout.setContentsMargins(16, 13, 16, 13)
        calendar_header = QHBoxLayout()
        calendar_title = QLabel("Days together")
        calendar_title.setObjectName("SectionTitle")
        calendar_header.addWidget(calendar_title)
        calendar_header.addStretch(1)
        calendar_note = QLabel("Darker cells mean more recorded time · click a day")
        calendar_note.setObjectName("Muted")
        calendar_header.addWidget(calendar_note)
        calendar_layout.addLayout(calendar_header)
        self.insight_calendar = CalendarHeatmap()
        self.insight_calendar.day_selected.connect(self.show_insight_day)
        self.insight_calendar_scroll = QScrollArea()
        self.insight_calendar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.insight_calendar_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.insight_calendar_scroll.setWidgetResizable(False)
        self.insight_calendar_scroll.setFixedHeight(192)
        self.insight_calendar_scroll.setWidget(self.insight_calendar)
        calendar_layout.addWidget(self.insight_calendar_scroll)
        self.insight_day_detail = QLabel("Select a day for its exact value")
        self.insight_day_detail.setObjectName("Muted")
        calendar_layout.addWidget(self.insight_day_detail)
        content_layout.addWidget(calendar_panel)

        rhythm_panel = QFrame()
        rhythm_panel.setObjectName("Panel")
        rhythm_layout = QVBoxLayout(rhythm_panel)
        rhythm_layout.setContentsMargins(16, 13, 16, 13)
        rhythm_header = QHBoxLayout()
        rhythm_title = QLabel("Typical time of week")
        rhythm_title.setObjectName("SectionTitle")
        rhythm_header.addWidget(rhythm_title)
        rhythm_header.addStretch(1)
        self.insight_heatmap_mode = QComboBox()
        self.insight_heatmap_mode.addItems(["Average per weekday", "Total in range"])
        self.insight_heatmap_mode.currentTextChanged.connect(
            self.render_insight_week_heatmap
        )
        rhythm_header.addWidget(self.insight_heatmap_mode)
        rhythm_layout.addLayout(rhythm_header)
        self.insight_week_heatmap = WeekHourHeatmap()
        rhythm_layout.addWidget(self.insight_week_heatmap)
        content_layout.addWidget(rhythm_panel)

        company_panel = QFrame()
        company_panel.setObjectName("Panel")
        company_layout = QVBoxLayout(company_panel)
        company_layout.setContentsMargins(16, 13, 16, 13)
        company_title = QLabel("Known-friend company context")
        company_title.setObjectName("SectionTitle")
        company_layout.addWidget(company_title)
        company_explanation = QLabel(
            "Only this friend = no other current-friend record overlaps · "
            "small = 1–3 others · larger = 4+ others. Encounter counts use "
            "the context covering most of that encounter."
        )
        company_explanation.setObjectName("Muted")
        company_explanation.setWordWrap(True)
        company_layout.addWidget(company_explanation)
        self.insight_company_chart = CompanyContextChart()
        company_layout.addWidget(self.insight_company_chart)
        content_layout.addWidget(company_panel)

        presence_panel = QFrame()
        presence_panel.setObjectName("Panel")
        presence_layout = QVBoxLayout(presence_panel)
        presence_layout.setContentsMargins(16, 13, 16, 13)
        presence_header = QHBoxLayout()
        presence_title = QLabel("Who is usually there?")
        presence_title.setObjectName("SectionTitle")
        presence_header.addWidget(presence_title)
        presence_header.addStretch(1)
        self.insight_presence_mode = QComboBox()
        self.insight_presence_mode.addItems(["Time overlap", "Encounter overlap"])
        self.insight_presence_mode.currentTextChanged.connect(
            self.render_insight_co_presence
        )
        presence_header.addWidget(self.insight_presence_mode)
        presence_layout.addLayout(presence_header)
        presence_note = QLabel(
            "Percentages do not add to 100% because several friends can be present at once."
        )
        presence_note.setObjectName("Muted")
        presence_layout.addWidget(presence_note)
        self.insight_presence_chart = CoPresenceChart()
        presence_layout.addWidget(self.insight_presence_chart)
        content_layout.addWidget(presence_panel)
        content_layout.addStretch(1)

        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        return page

    def _build_friend_map_page(self) -> QWidget:
        page, layout = self._page_container(
            "Friend Map",
            "See who has been around you most and which current friends tend to overlap in the same recorded instance.",
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.map_page_content = QWidget()
        self.map_page_content.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        content_layout = QVBoxLayout(self.map_page_content)
        content_layout.setContentsMargins(0, 0, 4, 4)
        content_layout.setSpacing(10)

        controls = self._build_map_toolbar()
        content_layout.addWidget(controls)

        self.map_visuals_host = QWidget()
        self.map_visuals_host.setMinimumWidth(0)
        self.map_visuals_layout = QBoxLayout(
            QBoxLayout.Direction.LeftToRight, self.map_visuals_host
        )
        self.map_visuals_layout.setContentsMargins(0, 0, 0, 0)
        self.map_visuals_layout.setSpacing(10)
        self.map_visuals_layout.addWidget(self._build_map_network_panel(), 4)
        self.map_visuals_layout.addWidget(self._build_map_ranking_panel(), 1)
        content_layout.addWidget(self.map_visuals_host, 1)
        content_layout.addWidget(self._build_map_inspector())

        scroll.setWidget(self.map_page_content)
        layout.addWidget(scroll, 1)
        return page

    def _build_map_toolbar(self) -> QFrame:
        controls = QFrame()
        controls.setObjectName("Panel")
        self.map_toolbar_layout = QGridLayout(controls)
        self.map_toolbar_layout.setContentsMargins(12, 8, 12, 8)
        self.map_toolbar_layout.setHorizontalSpacing(12)
        self.map_toolbar_layout.setVerticalSpacing(7)

        def control_group(label: str, control: QWidget) -> QWidget:
            group = QWidget()
            group_layout = QHBoxLayout(group)
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(7)
            caption = QLabel(label.upper())
            caption.setObjectName("MetricLabel")
            group_layout.addWidget(caption)
            group_layout.addWidget(control)
            return group

        self.map_friend_count = SegmentedControl(
            ("12", "20", "30", "40", "All"), "20"
        )
        self.map_friend_count.value_changed.connect(self.render_friend_map)
        self.map_friends_control = control_group("Friends", self.map_friend_count)
        self.map_connection_detail = SegmentedControl(
            ("Focused", "Balanced", "All"), "Focused"
        )
        self.map_connection_detail.setToolTip(
            "Focused keeps the strongest useful links. Balanced adds context. "
            "All shows every measured connection."
        )
        self.map_connection_detail.value_changed.connect(self.render_friend_map)
        self.map_connections_control = control_group(
            "Connections", self.map_connection_detail
        )
        self.map_connection_metric = QComboBox()
        self.map_connection_metric.addItems(
            ["Time overlap", "Co-appearance likelihood"]
        )
        self.map_connection_metric.setMinimumWidth(190)
        self.map_connection_metric.setToolTip(
            "Time overlap favors total shared hours. Co-appearance likelihood "
            "is overlap divided by the less-frequent friend's total time, so it "
            "describes historical consistency rather than a future prediction."
        )
        self.map_connection_metric.currentTextChanged.connect(self.render_friend_map)
        self.map_metric_control = control_group("Metric", self.map_connection_metric)
        self.map_view_mode = SegmentedControl(("Activity", "Groups"), "Activity")
        self.map_view_mode.setToolTip(
            "Activity colors nodes by time rank. Groups detects communities from repeated "
            "same-instance overlap; it does not assume those people are friends."
        )
        self.map_view_mode.value_changed.connect(self.render_friend_map)
        self.map_view_control = control_group("View", self.map_view_mode)
        self.map_reset_button = QPushButton("Reset view")
        self.map_reset_button.clicked.connect(lambda: self.friend_map.reset_view())
        self._map_toolbar_compact: bool | None = None
        self._layout_map_toolbar(compact=False)
        return controls

    def _build_map_network_panel(self) -> QFrame:
        self.map_panel = QFrame()
        self.map_panel.setObjectName("Panel")
        self.map_panel.setMinimumWidth(0)
        map_layout = QVBoxLayout(self.map_panel)
        map_layout.setContentsMargins(12, 10, 12, 9)
        map_header = QHBoxLayout()
        self.map_context = QLabel("Open the map to load this date range")
        self.map_context.setObjectName("SectionTitle")
        self.map_context.setWordWrap(True)
        self.map_context.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        map_header.addWidget(self.map_context, 1)
        gesture_hint = QLabel("Drag to pan · Wheel to zoom")
        gesture_hint.setObjectName("Subtle")
        map_header.addWidget(gesture_hint)
        map_layout.addLayout(map_header)
        self.friend_map = FriendMapWidget()
        self.friend_map.friend_selected.connect(self.show_map_friend)
        self.friend_map.friend_activated.connect(self.open_map_friend_insights)
        self.friend_map.group_selected.connect(self.show_map_group)
        self.friend_map.selection_cleared.connect(self.clear_map_selection)
        map_layout.addWidget(self.friend_map, 1)
        legend_row = QHBoxLayout()
        self.map_legend = QLabel()
        self.map_legend.setObjectName("Muted")
        self.map_legend.setWordWrap(True)
        self.map_legend.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        legend_row.addWidget(self.map_legend, 1)
        help_button = QPushButton("?")
        help_button.setObjectName("QuietButton")
        help_button.setFixedSize(26, 26)
        help_button.setToolTip(
            "How to read the Friend Map\n\n"
            "• The number inside each node is that friend's activity rank for the selected period.\n"
            "• Node color groups ranks: purple is top 5, cyan is 6–10, blue is 11–20, "
            "and green is 21+.\n"
            "• Groups view colors and positions inferred communities using repeated, measured "
            "same-instance overlap. It cannot know whether two people are actually friends.\n"
            "• Merely appearing in the same date range adds no group strength. Actual overlap "
            "in the same known instance is the clustering evidence.\n"
            "• Larger nodes mean more recorded time around you.\n"
            "• Position is determined by the relationship network: friends with stronger "
            "measured overlap tend to sit closer together.\n"
            "• A brighter or thicker connection means a stronger value for the selected metric.\n"
            "• A gold line marks the strongest overlap for the selected friend under the "
            "current metric; a connection also turns gold while you hover over it.\n"
            "• Selecting a friend fades unrelated nodes and connections.\n\n"
            "Connections require both friends to be recorded in the same known VRChat instance. "
            "Missing location data is excluded rather than inferred. Co-appearance likelihood "
            "describes historical overlap only and does not predict future behavior."
        )
        legend_row.addWidget(help_button)
        map_layout.addLayout(legend_row)
        return self.map_panel

    def _build_map_ranking_panel(self) -> QFrame:
        self.map_ranking_panel = QFrame()
        self.map_ranking_panel.setObjectName("Panel")
        ranking_layout = QVBoxLayout(self.map_ranking_panel)
        ranking_layout.setContentsMargins(12, 10, 12, 10)
        ranking_layout.setSpacing(8)
        self.map_group_explorer = QWidget()
        explorer_layout = QVBoxLayout(self.map_group_explorer)
        explorer_layout.setContentsMargins(0, 0, 0, 4)
        explorer_layout.setSpacing(5)
        explorer_label = QLabel("EXPLORE GROUP")
        explorer_label.setObjectName("MetricLabel")
        explorer_layout.addWidget(explorer_label)
        self.map_group_filter = QComboBox()
        self.map_group_filter.setToolTip(
            "Focus one inferred group to make large maps easier to read."
        )
        self.map_group_filter.currentIndexChanged.connect(
            self.select_map_group_from_filter
        )
        explorer_layout.addWidget(self.map_group_filter)
        self.map_group_summary = QLabel()
        self.map_group_summary.setObjectName("Subtle")
        self.map_group_summary.setWordWrap(True)
        self.map_group_summary.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        explorer_layout.addWidget(self.map_group_summary)
        self.map_group_explorer.hide()
        ranking_layout.addWidget(self.map_group_explorer)
        self.map_ranking_title = QLabel("Top friends")
        self.map_ranking_title.setObjectName("SectionTitle")
        ranking_layout.addWidget(self.map_ranking_title)
        self.map_ranking_note = QLabel("Click to select · Double-click for insights")
        self.map_ranking_note.setObjectName("Muted")
        self.map_ranking_note.setWordWrap(True)
        self.map_ranking_note.setFixedHeight(36)
        self.map_ranking_note.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.map_ranking_note.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        ranking_layout.addWidget(self.map_ranking_note)
        self.map_top_friends = TopFriendsBarChart()
        self.map_top_friends.friend_selected.connect(self.select_map_friend)
        self.map_top_friends.friend_activated.connect(self.open_map_friend_insights)
        self.map_top_friends.friend_hovered.connect(self.show_map_ranking_hover)
        ranking_scroll = QScrollArea()
        ranking_scroll.setWidgetResizable(True)
        ranking_scroll.setFrameShape(QFrame.Shape.NoFrame)
        ranking_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        ranking_scroll.setWidget(self.map_top_friends)
        ranking_layout.addWidget(ranking_scroll, 1)
        return self.map_ranking_panel

    def show_map_ranking_hover(self, details: str) -> None:
        self.map_ranking_note.setText(
            details or "Click to select · Double-click for insights"
        )

    def _build_map_inspector(self) -> QFrame:
        self.map_detail = QFrame()
        self.map_detail.setObjectName("DetailStrip")
        detail_layout = QVBoxLayout(self.map_detail)
        detail_layout.setContentsMargins(14, 10, 14, 10)
        detail_layout.setSpacing(8)
        detail_header = QHBoxLayout()
        self.map_detail_title = QLabel("Select a friend")
        self.map_detail_title.setObjectName("SectionTitle")
        detail_header.addWidget(self.map_detail_title)
        self.map_detail_rank = QLabel()
        self.map_detail_rank.setObjectName("Muted")
        detail_header.addWidget(self.map_detail_rank)
        detail_header.addStretch(1)
        self.map_insights_button = QPushButton("Open friend insights")
        self.map_insights_button.setObjectName("PrimaryButton")
        self.map_insights_button.setEnabled(False)
        self.map_insights_button.clicked.connect(self.open_selected_map_friend_insights)
        detail_header.addWidget(self.map_insights_button)
        detail_layout.addLayout(detail_header)
        self.map_inspector_idle = QLabel(
            "Click a node to explore their relationships. Double-click to open detailed insights."
        )
        self.map_inspector_idle.setObjectName("Muted")
        detail_layout.addWidget(self.map_inspector_idle)

        self.map_inspector_content = QWidget()
        inspector_layout = QHBoxLayout(self.map_inspector_content)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(24)

        def inspector_metric() -> tuple[QWidget, QLabel, QLabel]:
            host = QWidget()
            host_layout = QVBoxLayout(host)
            host_layout.setContentsMargins(0, 0, 0, 0)
            host_layout.setSpacing(1)
            value = QLabel("—")
            value.setObjectName("MapInspectorValue")
            caption = QLabel()
            caption.setObjectName("MapInspectorLabel")
            host_layout.addWidget(value)
            host_layout.addWidget(caption)
            return host, value, caption

        time_host, self.map_time_value, self.map_time_caption = inspector_metric()
        sessions_host, self.map_sessions_value, self.map_sessions_caption = inspector_metric()
        relationships_host, self.map_relationships_value, self.map_relationships_caption = inspector_metric()
        strongest_host, self.map_strongest_value, self.map_strongest_caption = inspector_metric()
        inspector_layout.addWidget(time_host)
        inspector_layout.addWidget(sessions_host)
        inspector_layout.addWidget(relationships_host)
        inspector_layout.addWidget(strongest_host, 1)
        self.map_inspector_content.hide()
        detail_layout.addWidget(self.map_inspector_content)
        return self.map_detail

    def _layout_map_toolbar(self, *, compact: bool) -> None:
        if self._map_toolbar_compact == compact:
            return
        self._map_toolbar_compact = compact
        widgets = (
            self.map_friends_control,
            self.map_connections_control,
            self.map_metric_control,
            self.map_view_control,
            self.map_reset_button,
        )
        for widget in widgets:
            self.map_toolbar_layout.removeWidget(widget)
        if compact:
            self.map_toolbar_layout.addWidget(self.map_friends_control, 0, 0)
            self.map_toolbar_layout.addWidget(self.map_connections_control, 0, 1)
            self.map_toolbar_layout.addWidget(self.map_metric_control, 1, 0)
            self.map_toolbar_layout.addWidget(self.map_view_control, 1, 1)
            self.map_toolbar_layout.addWidget(
                self.map_reset_button,
                1,
                2,
                Qt.AlignmentFlag.AlignRight,
            )
        else:
            self.map_toolbar_layout.addWidget(self.map_friends_control, 0, 0)
            self.map_toolbar_layout.addWidget(self.map_connections_control, 0, 1)
            self.map_toolbar_layout.addWidget(self.map_metric_control, 0, 2)
            self.map_toolbar_layout.addWidget(self.map_view_control, 0, 3)
            self.map_toolbar_layout.addWidget(self.map_reset_button, 0, 4)
            self.map_toolbar_layout.setColumnStretch(2, 1)

    def _connect_shortcuts(self) -> None:
        refresh = QAction(self)
        refresh.setShortcut(QKeySequence("F5"))
        refresh.triggered.connect(lambda: self.refresh_dashboard(force=True))
        self.addAction(refresh)
        focus_search = QAction(self)
        focus_search.setShortcut(QKeySequence("Ctrl+F"))
        focus_search.triggered.connect(self.focus_search)
        self.addAction(focus_search)
        escape = QAction(self)
        escape.setShortcut(QKeySequence("Escape"))
        escape.triggered.connect(self.clear_contextual_input)
        self.addAction(escape)
        for index, key in enumerate(
            ("Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5")
        ):
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(lambda checked=False, value=index: self.set_page(value))
            self.addAction(action)

    def set_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        self.page_context.setText(
            ("OVERVIEW", "FRIENDS", "FRIEND MAP", "SHARED TIME", "INSIGHTS")[index]
        )
        if index == PAGE_FRIENDS:
            QTimer.singleShot(0, self.friend_search.setFocus)
        elif index == PAGE_INSIGHTS:
            QTimer.singleShot(0, self.insight_friend.setFocus)
        elif (
            index == PAGE_FRIEND_MAP
            and self.dashboard is not None
            and self._friend_map_data is None
        ):
            self.refresh_friend_map()

    def focus_search(self) -> None:
        if self.pages.currentIndex() == PAGE_SHARED_TIME:
            self.compare_search.setFocus()
        elif self.pages.currentIndex() == PAGE_INSIGHTS:
            self.insight_friend.setFocus()
            self.insight_friend.show_all_options()
        elif self.pages.currentIndex() == PAGE_FRIEND_MAP:
            return
        else:
            self.set_page(PAGE_FRIENDS)
            self.friend_search.setFocus()

    def clear_contextual_input(self) -> None:
        if self.pages.currentIndex() == PAGE_SHARED_TIME:
            self.compare_search.clear()
        elif self.pages.currentIndex() == PAGE_INSIGHTS:
            self.insight_calendar.clear_selection()
            self.insight_day_detail.setText("Select a day for its exact value")
        elif self.pages.currentIndex() == PAGE_FRIEND_MAP:
            if self._selected_map_group_id:
                self.show_map_group(0)
            else:
                self.friend_map.reset_view()
        elif self.pages.currentIndex() == PAGE_FRIENDS:
            self.clear_friend_filters()

    def toggle_date_range_panel(self, visible: bool) -> None:
        if visible:
            self.date_range_panel.set_current_range(self._start_date, self._end_date)
        self.date_range_panel.setVisible(visible)

    def close_date_range_panel(self) -> None:
        self.date_range_panel.hide()
        self.range_button.setChecked(False)

    def set_date_range(self, start: date, end: date, label: str = "Custom range") -> None:
        self._start_date = start
        self._end_date = end
        self._range_label = label
        self._update_range_button()
        self.close_date_range_panel()
        self.refresh_dashboard()

    def _update_range_button(self) -> None:
        start_month = ENGLISH_MONTHS[self._start_date.month][:3]
        end_month = ENGLISH_MONTHS[self._end_date.month][:3]
        if self._start_date.year == self._end_date.year:
            dates = (
                f"{self._start_date.day:02d} {start_month} – "
                f"{self._end_date.day:02d} {end_month} {self._end_date.year}"
            )
        else:
            dates = (
                f"{self._start_date.day:02d} {start_month} {self._start_date.year} – "
                f"{self._end_date.day:02d} {end_month} {self._end_date.year}"
            )
        self.range_button.setText(f"{self._range_label}   ·   {dates}   ▾")

    def collect_state(self) -> AppState:
        minimums = {
            "Any time": 0,
            "15 minutes": 15,
            "1 hour": 60,
            "5 hours": 300,
            "10 hours": 600,
        }
        limits = {
            "All friends": None,
            "Top 25": 25,
            "Top 50": 50,
            "Top 100": 100,
        }
        return AppState(
            start_date=self._start_date,
            end_date=self._end_date,
            search_term=self.friend_search.text(),
            minimum_minutes=minimums.get(self.minimum_time.currentText(), 0),
            result_limit=limits.get(self.result_limit.currentText()),
            selected_friend_ids=list(self._selected_friend_ids),
            aggregation=self.compare_aggregation.currentText(),
            overview_metric=self.overview_metric.currentText(),
        )

    def _friend_search_changed(self, _value: str) -> None:
        self.schedule_dashboard_refresh()

    def clear_friend_filters(self) -> None:
        self.friend_search.clear()
        self.minimum_time.setCurrentIndex(0)
        self.result_limit.setCurrentIndex(0)
        self.schedule_dashboard_refresh()

    def schedule_dashboard_refresh(self) -> None:
        self._dashboard_debounce.start(280)

    def refresh_dashboard(self, force: bool = False) -> None:
        state = self.collect_state()
        if state.end_date < state.start_date:
            self.show_error("The end date must be on or after the start date.")
            return
        if force:
            self.repository.invalidate()
        self.state = state
        if self._selected_friend_ids:
            self._comparison_generation += 1
            self._comparison_data = None
            self.compare_context.setText("Updating shared time for the new date range…")
        if self._selected_insight_friend_id:
            self._insights_generation += 1
            self._friend_insights_data = None
        if (
            self._friend_map_data is not None
            or self.pages.currentIndex() == PAGE_FRIEND_MAP
        ):
            self._map_generation += 1
            self._friend_map_data = None
        self._dashboard_generation += 1
        generation = self._dashboard_generation
        self.set_loading(True, "Loading local VRCX activity…")
        worker = RepositoryWorker(
            generation,
            lambda: self.repository.load_dashboard(state),
        )
        worker.signals.result.connect(self.dashboard_loaded)
        worker.signals.error.connect(self.worker_error)
        worker.signals.finished.connect(self.release_worker)
        self._workers.add(worker)
        self.thread_pool.start(worker)

    @Slot(int, object)
    def dashboard_loaded(self, generation: int, result: object) -> None:
        if generation != self._dashboard_generation:
            return
        data = result
        if not isinstance(data, DashboardData):
            return
        self.dashboard = data
        self._friend_by_id = {friend.user_id: friend for friend in data.friends}
        self._friend_option_by_id = {
            friend.user_id: friend for friend in data.friend_options
        }
        self.friends_model.set_friends(list(data.friends))
        self._current_friend_id = None
        self.friend_insights_button.setEnabled(False)
        self.friends_table.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        self.update_top_friends()
        self.populate_compare_list()
        self.populate_insight_friends()
        self.update_metrics()
        self.render_overview_chart()
        self.set_loading(False)
        self.status_label.setText(
            f"{data.matching_count} friends in range · {data.visible_sessions:,} shared sessions · "
            f"latest data {format_local_datetime(data.latest_activity)}"
        )
        if self._selected_friend_ids:
            self.refresh_comparison()
        if self._selected_insight_friend_id:
            self.refresh_friend_insights()
        if self.pages.currentIndex() == PAGE_FRIEND_MAP:
            self.refresh_friend_map()

    def update_top_friends(self) -> None:
        while self.top_friends_layout.count():
            item = self.top_friends_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        friends = list(self.dashboard.friends[:3]) if self.dashboard else []
        if not friends:
            empty = QLabel("No shared activity in this range")
            empty.setObjectName("Muted")
            self.top_friends_layout.addWidget(empty)
            return
        for rank, friend in enumerate(friends, start=1):
            row = QFrame()
            row.setObjectName("TopFriendRow")
            row.setMinimumHeight(42)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 5, 10, 5)
            row_layout.setSpacing(10)
            badge = QLabel(str(rank))
            badge.setObjectName("RankBadge")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(25, 25)
            row_layout.addWidget(badge)
            name = QLabel(friend.display_name)
            name.setObjectName("TopFriendName")
            name.setMinimumWidth(160)
            name.setToolTip(friend.display_name)
            detail = QLabel(
                f"{friend.sessions} sessions · last seen {format_local_date(friend.last_seen)}"
            )
            detail.setObjectName("Subtle")
            row_layout.addWidget(name)
            row_layout.addWidget(detail, 1)
            duration = QLabel(format_duration(friend.milliseconds))
            duration.setObjectName("TopFriendDuration")
            row_layout.addWidget(duration)
            self.top_friends_layout.addWidget(row)

    def update_metrics(self) -> None:
        if self.dashboard is None:
            return
        data = self.dashboard
        social_peak = max(data.social_daily, key=lambda item: item[1]) if data.social_daily else (None, 0)
        self.metric_cards[0].set_value(
            format_duration(data.total_social_milliseconds),
            "Actual wall-clock time with at least one friend",
        )
        self.metric_cards[1].set_value(
            str(data.matching_count),
            f"of {data.current_friend_count} current friends",
        )
        self.metric_cards[2].set_value(
            f"{data.visible_sessions:,}",
            "Completed shared sessions in the visible list",
        )
        peak_day, peak_value = social_peak
        self.metric_cards[3].set_value(
            format_duration(peak_value),
            format_english_day(peak_day) if peak_day else "No activity",
        )
        top_friend = data.friends[0] if data.friends else None
        self.metric_cards[4].set_value(
            top_friend.display_name if top_friend else "—",
            (
                f"{format_duration(top_friend.milliseconds)} in this period"
                if top_friend
                else "No shared activity"
            ),
        )

    def render_overview_chart(self) -> None:
        if self.dashboard is None:
            return
        granularity = self.overview_aggregation.currentText()
        metric = self.overview_metric.currentText()
        raw = (
            self.dashboard.person_daily
            if metric == "Person-time"
            else self.dashboard.social_daily
        )
        series = aggregate_time_series(raw, granularity)
        self.overview_chart.set_series(
            [(metric, series, ACCENT)], granularity, metric
        )

    def populate_insight_friends(self) -> None:
        if self.dashboard is None:
            return
        selected = self._selected_insight_friend_id
        self.insight_friend.blockSignals(True)
        self.insight_friend.clear()
        for friend in self.dashboard.friend_options:
            self.insight_friend.addItem(friend.display_name, friend.user_id)
            item_index = self.insight_friend.count() - 1
            self.insight_friend.setItemData(
                item_index,
                f"{format_duration(friend.milliseconds)} together in this range",
                Qt.ItemDataRole.ToolTipRole,
            )
        selected_index = self.insight_friend.findData(selected) if selected else -1
        if selected and selected_index < 0:
            self._selected_insight_friend_id = None
            self.clear_friend_insights()
        self.insight_friend.setCurrentIndex(selected_index)
        if selected_index < 0 and self.insight_friend.lineEdit() is not None:
            self.insight_friend.lineEdit().clear()
        self.insight_friend.blockSignals(False)

    def insight_friend_changed(self, index: int) -> None:
        user_id = self.insight_friend.itemData(index) if index >= 0 else None
        if not user_id:
            self._selected_insight_friend_id = None
            self._insights_generation += 1
            self.clear_friend_insights()
            return
        if user_id == self._selected_insight_friend_id and self._friend_insights_data:
            return
        self._selected_insight_friend_id = user_id
        self.refresh_friend_insights()

    def open_current_friend_insights(self) -> None:
        if not self._current_friend_id:
            return
        index = self.insight_friend.findData(self._current_friend_id)
        selection_changed = index >= 0 and self.insight_friend.currentIndex() != index
        if selection_changed:
            self.insight_friend.setCurrentIndex(index)
        else:
            self._selected_insight_friend_id = self._current_friend_id
        self.set_page(PAGE_INSIGHTS)
        if not selection_changed and (
            self._friend_insights_data is None
            or self._friend_insights_data.friend.user_id != self._current_friend_id
        ):
            self.refresh_friend_insights()

    def refresh_friend_insights(self) -> None:
        user_id = self._selected_insight_friend_id
        if not user_id:
            return
        state = self.collect_state()
        self._insights_generation += 1
        generation = self._insights_generation
        friend = self._friend_option_by_id.get(user_id)
        name = friend.display_name if friend else "selected friend"
        self.set_loading(True, f"Loading insights for {name}…")
        worker = RepositoryWorker(
            generation,
            lambda: self.repository.load_friend_insights(state, user_id),
        )
        worker.signals.result.connect(self.friend_insights_loaded)
        worker.signals.error.connect(self.insights_error)
        worker.signals.finished.connect(self.release_worker)
        self._workers.add(worker)
        self.thread_pool.start(worker)

    @Slot(int, object)
    def friend_insights_loaded(self, generation: int, result: object) -> None:
        if generation != self._insights_generation:
            return
        data = result
        if not isinstance(data, FriendInsightsData):
            return
        if data.friend.user_id != self._selected_insight_friend_id:
            return
        self._friend_insights_data = data
        self.set_loading(False)
        range_days = len(data.daily)
        self.insight_metric_cards[0].set_value(
            format_duration(data.total_milliseconds),
            "Completed time in the selected range",
        )
        self.insight_metric_cards[1].set_value(
            str(data.active_days),
            f"of {range_days} local day{'s' if range_days != 1 else ''}",
        )
        self.insight_metric_cards[2].set_value(
            f"{data.sessions:,}",
            "Completed overlapping sessions",
        )
        self.insight_calendar.set_data(list(data.daily))
        self.insight_day_detail.setText("Select a day for its exact value")
        self.render_insight_week_heatmap()
        self.insight_company_chart.set_data(
            data.context_milliseconds,
            data.context_encounters,
        )
        self.render_insight_co_presence()
        QTimer.singleShot(
            0,
            lambda: self.insight_calendar_scroll.horizontalScrollBar().setValue(
                self.insight_calendar_scroll.horizontalScrollBar().maximum()
            ),
        )
        self.status_label.setText(
            f"Insights for {data.friend.display_name} · {data.sessions:,} encounters · "
            "known-current-friend company context"
        )

    def render_insight_week_heatmap(self, _value: str = "") -> None:
        data = self._friend_insights_data
        if data is None:
            self.insight_week_heatmap.set_data(tuple(), tuple(), average=True)
            return
        self.insight_week_heatmap.set_data(
            data.weekday_hourly_milliseconds,
            data.weekday_occurrences,
            average=self.insight_heatmap_mode.currentText() == "Average per weekday",
        )

    def render_insight_co_presence(self, _value: str = "") -> None:
        data = self._friend_insights_data
        if data is None:
            self.insight_presence_chart.set_data(tuple(), 0, 0, "Time overlap")
            return
        self.insight_presence_chart.set_data(
            data.co_presence,
            data.total_milliseconds,
            data.sessions,
            self.insight_presence_mode.currentText(),
        )

    def show_insight_day(self, selected_day: date) -> None:
        data = self._friend_insights_data
        if data is None:
            return
        value = dict(data.daily).get(selected_day, 0)
        self.insight_day_detail.setText(
            f"{format_english_day(selected_day, include_year=True)} · "
            f"{format_duration(value)} together"
        )

    def clear_friend_insights(self) -> None:
        self._friend_insights_data = None
        for card in getattr(self, "insight_metric_cards", ()):
            card.set_value("—", "Select a friend")
        if not hasattr(self, "insight_calendar"):
            return
        self.insight_calendar.set_data([])
        self.insight_week_heatmap.set_data(tuple(), tuple(), average=True)
        self.insight_company_chart.set_data((0, 0, 0), (0, 0, 0))
        self.insight_presence_chart.set_data(tuple(), 0, 0, "Time overlap")
        self.insight_day_detail.setText("Select a day for its exact value")

    def refresh_friend_map(self) -> None:
        state = self.collect_state()
        self._map_generation += 1
        generation = self._map_generation
        self.set_loading(True, "Building the friend co-presence map…")
        worker = RepositoryWorker(
            generation,
            lambda: self.repository.load_friend_map(state, max_nodes=None),
        )
        worker.signals.result.connect(self.friend_map_loaded)
        worker.signals.error.connect(self.map_error)
        worker.signals.finished.connect(self.release_worker)
        self._workers.add(worker)
        self.thread_pool.start(worker)

    @Slot(int, object)
    def friend_map_loaded(self, generation: int, result: object) -> None:
        if generation != self._map_generation:
            return
        data = result
        if not isinstance(data, FriendMapData):
            return
        self._friend_map_data = data
        self.set_loading(False)
        self.render_friend_map()
        self.status_label.setText(
            f"Friend Map ready · {len(data.nodes)} active friends considered · "
            f"{len(data.links)} same-instance connections"
        )

    def render_friend_map(self, _value: str = "") -> None:
        data = self._friend_map_data
        if data is None:
            self.friend_map.set_data(FriendMapData(tuple(), tuple()))
            self.map_top_friends.set_nodes(tuple())
            return
        count_value = self.map_friend_count.value()
        node_limit = None if count_value == "All" else int(count_value)
        detail = self.map_connection_detail.value()
        metric = self.map_connection_metric.currentText()
        color_mode = (
            "Friend groups" if self.map_view_mode.value() == "Groups" else "Activity"
        )
        self.friend_map.set_data(
            data,
            node_limit=node_limit,
            connection_detail=detail,
            connection_metric=metric,
            color_mode=color_mode,
        )
        if (
            color_mode != "Friend groups"
            or self._selected_map_group_id > self.friend_map.group_count()
        ):
            self._selected_map_group_id = 0
        self.friend_map.set_selected_group(self._selected_map_group_id)
        self._populate_map_group_filter(color_mode == "Friend groups")
        node_count, link_count = self.friend_map.visible_counts()
        measured_count = self.friend_map.measured_connection_count()
        group_context = (
            f" · {self.friend_map.group_count()} inferred group"
            f"{'s' if self.friend_map.group_count() != 1 else ''}"
            if color_mode == "Friend groups"
            else ""
        )
        focus_context = (
            f" · Group {self._selected_map_group_id} focused"
            if self._selected_map_group_id
            else ""
        )
        self._map_context_base = (
            f"{node_count} friend{'s' if node_count != 1 else ''} · "
            f"{link_count} of {measured_count} measured connections shown · {metric}"
            f"{group_context}"
        )
        self.map_context.setText(f"{self._map_context_base}{focus_context}")
        link_legend = (
            "Stronger links = more consistent co-appearance"
            if metric == "Co-appearance likelihood"
            else "Stronger links = more shared-instance time"
        )
        color_legend = (
            self.friend_map.group_legend_html()
            if color_mode == "Friend groups"
            else activity_rank_legend_html()
        )
        color_meaning = (
            "Color + position = inferred groups · Click a cluster to explore"
            if color_mode == "Friend groups"
            else "Color = activity rank"
        )
        self.map_legend.setText(
            f"{color_legend} &nbsp; · &nbsp; {color_meaning} &nbsp; · &nbsp; "
            f"Larger node = more time &nbsp; · &nbsp; {link_legend} &nbsp; · &nbsp; "
            '<span style="color:#e6b85c">━</span> Gold = strongest overlap or hovered link'
        )
        visible_nodes = self.friend_map.visible_nodes()
        self._update_map_group_panel()
        visible_ids = {node.user_id for node in visible_nodes}
        if self._selected_map_friend_id in visible_ids:
            self.friend_map.set_selected_friend(self._selected_map_friend_id)
            self.show_map_friend(self._selected_map_friend_id)
        elif self._selected_map_friend_id:
            self.clear_map_selection()

    def _populate_map_group_filter(self, groups_visible: bool) -> None:
        self.map_group_explorer.setVisible(
            groups_visible and self.friend_map.group_count() > 0
        )
        self.map_group_filter.blockSignals(True)
        self.map_group_filter.clear()
        visible_count = len(self.friend_map.visible_nodes())
        self.map_group_filter.addItem(f"All groups · {visible_count} people", 0)
        selected_index = 0
        for group_id in range(1, self.friend_map.group_count() + 1):
            members = self.friend_map.group_members(group_id)
            swatch = QPixmap(12, 12)
            swatch.fill(friend_group_color(group_id))
            self.map_group_filter.addItem(
                QIcon(swatch),
                f"Group {group_id} · {len(members)} people",
                group_id,
            )
            if group_id == self._selected_map_group_id:
                selected_index = self.map_group_filter.count() - 1
        self.map_group_filter.setCurrentIndex(selected_index)
        self.map_group_filter.blockSignals(False)

    def select_map_group_from_filter(self, index: int) -> None:
        group_id = self.map_group_filter.itemData(index)
        self.show_map_group(group_id if isinstance(group_id, int) else 0)

    def show_map_group(self, group_id: int) -> None:
        available = range(1, self.friend_map.group_count() + 1)
        self._selected_map_group_id = group_id if group_id in available else 0
        self.friend_map.set_selected_group(self._selected_map_group_id)
        self.clear_map_selection()
        focus_context = (
            f" · Group {self._selected_map_group_id} focused"
            if self._selected_map_group_id
            else ""
        )
        self.map_context.setText(
            f"{getattr(self, '_map_context_base', '')}{focus_context}"
        )
        selected_index = self.map_group_filter.findData(self._selected_map_group_id)
        if selected_index >= 0 and selected_index != self.map_group_filter.currentIndex():
            self.map_group_filter.blockSignals(True)
            self.map_group_filter.setCurrentIndex(selected_index)
            self.map_group_filter.blockSignals(False)
        self._update_map_group_panel()

    def _update_map_group_panel(self) -> None:
        visible_nodes = self.friend_map.visible_nodes()
        group_id = self._selected_map_group_id
        members = self.friend_map.group_members(group_id)
        colors = self.friend_map.node_colors()
        if group_id <= 0:
            self.map_ranking_title.setText(
                f"Top {len(visible_nodes)} friend"
                f"{'s' if len(visible_nodes) != 1 else ''}"
            )
            self.map_group_summary.setText(
                "Click a colored cluster on the map, or choose one here, "
                "to isolate its people and internal connections."
            )
            self.map_top_friends.set_nodes(visible_nodes, colors=colors)
            return

        member_ids = {node.user_id for node in members}
        internal_links = tuple(
            link
            for link in (
                self._friend_map_data.links if self._friend_map_data is not None else ()
            )
            if link.source_user_id in member_ids
            and link.target_user_id in member_ids
        )
        total_time = sum(node.milliseconds for node in members)
        strongest = max(
            internal_links,
            key=(
                (lambda link: link.likelihood)
                if self.map_connection_metric.currentText()
                == "Co-appearance likelihood"
                else (lambda link: link.milliseconds)
            ),
            default=None,
        )
        strongest_text = "No internal overlap measured"
        if strongest is not None:
            names = {node.user_id: node.display_name for node in members}
            value = (
                f"{strongest.likelihood:.0%} likelihood"
                if self.map_connection_metric.currentText()
                == "Co-appearance likelihood"
                else format_duration(strongest.milliseconds)
            )
            strongest_text = (
                f"Strongest pair: {names[strongest.source_user_id]} ↔ "
                f"{names[strongest.target_user_id]} · {value}"
            )
        self.map_ranking_title.setText(f"Group {group_id} · {len(members)} people")
        self.map_group_summary.setText(
            f"{len(internal_links)} internal connections · "
            f"{format_duration(total_time)} combined around-you time\n"
            f"{strongest_text}"
        )
        self.map_top_friends.set_nodes(members, colors=colors)

    def show_map_friend(self, user_id: str) -> None:
        data = self._friend_map_data
        if data is None:
            return
        node = next((item for item in data.nodes if item.user_id == user_id), None)
        if node is None:
            return
        node_group = self.friend_map.group_for(user_id)
        if (
            self._selected_map_group_id
            and node_group != self._selected_map_group_id
        ):
            self.show_map_group(node_group)
        self._selected_map_friend_id = user_id
        self.friend_map.set_selected_friend(user_id)
        rank = self.friend_map.rank_for(user_id)
        relationships = self.friend_map.measured_relationship_count(user_id)
        visible_ids = {item.user_id for item in self.friend_map.visible_nodes()}
        candidates = [
            link
            for link in data.links
            if user_id in (link.source_user_id, link.target_user_id)
            and link.source_user_id in visible_ids
            and link.target_user_id in visible_ids
        ]
        metric = self.map_connection_metric.currentText()
        strongest = max(
            candidates,
            key=(
                (lambda link: link.likelihood)
                if metric == "Co-appearance likelihood"
                else (lambda link: link.milliseconds)
            ),
            default=None,
        )
        strongest_value = "No measured relationship"
        strongest_caption = (
            "Most consistent co-appearance"
            if metric == "Co-appearance likelihood"
            else "Strongest overlap"
        )
        if strongest is not None:
            other_id = (
                strongest.target_user_id
                if strongest.source_user_id == user_id
                else strongest.source_user_id
            )
            other = next(item for item in data.nodes if item.user_id == other_id)
            if metric == "Co-appearance likelihood":
                strongest_value = f"{other.display_name} · {strongest.likelihood:.0%}"
            else:
                strongest_value = (
                    f"{other.display_name} · {format_duration(strongest.milliseconds)}"
                )
        self.map_detail_title.setText(node.display_name)
        self.map_top_friends.set_selected_friend(user_id)
        group_id = self.friend_map.group_for(user_id)
        rank_text = f"Rank #{rank}" if rank else ""
        if self.map_view_mode.value() == "Groups":
            group_text = f"Group {group_id}" if group_id else "Unclustered"
            rank_text = f"{rank_text} · {group_text}" if rank_text else group_text
        self.map_detail_rank.setText(rank_text)
        self.map_time_value.setText(format_duration(node.milliseconds))
        self.map_time_caption.setText("Around you")
        self.map_sessions_value.setText(f"{node.sessions:,}")
        self.map_sessions_caption.setText("Shared sessions")
        self.map_relationships_value.setText(f"{relationships:,}")
        self.map_relationships_caption.setText("Measured relationships")
        self.map_strongest_value.setText(strongest_value)
        self.map_strongest_value.setToolTip(strongest_value)
        self.map_strongest_caption.setText(strongest_caption)
        self.map_inspector_idle.hide()
        self.map_inspector_content.show()
        self.map_insights_button.setEnabled(True)

    def select_map_friend(self, user_id: str) -> None:
        self.show_map_friend(user_id)

    def clear_map_selection(self) -> None:
        self._selected_map_friend_id = None
        if not hasattr(self, "friend_map"):
            return
        self.friend_map.set_selected_friend(None)
        self.map_top_friends.set_selected_friend(None)
        self.map_detail_title.setText("Select a friend")
        self.map_detail_rank.clear()
        self.map_inspector_content.hide()
        self.map_inspector_idle.show()
        self.map_insights_button.setEnabled(False)

    def open_map_friend_insights(self, user_id: str) -> None:
        self._selected_map_friend_id = user_id
        self._current_friend_id = user_id
        self.open_current_friend_insights()

    def open_selected_map_friend_insights(self) -> None:
        if self._selected_map_friend_id:
            self.open_map_friend_insights(self._selected_map_friend_id)

    def clear_friend_map(self) -> None:
        self._friend_map_data = None
        self._selected_map_friend_id = None
        self._selected_map_group_id = 0
        if not hasattr(self, "friend_map"):
            return
        self.friend_map.set_data(FriendMapData(tuple(), tuple()))
        self.map_top_friends.set_nodes(tuple())
        self.map_group_explorer.hide()
        self.map_group_filter.blockSignals(True)
        self.map_group_filter.clear()
        self.map_group_filter.blockSignals(False)
        self.map_context.setText("Open the map to load this date range")
        self.map_legend.clear()
        self.clear_map_selection()

    def show_friend_detail(self) -> None:
        rows = self.friends_table.selectionModel().selectedRows()
        if not rows:
            return
        friend = self.friends_model.friends[rows[0].row()]
        self._current_friend_id = friend.user_id
        self.friend_insights_button.setEnabled(True)
        self.friend_detail_title.setText(friend.display_name)
        self.friend_detail_text.setText(
            f"<b>{format_duration(friend.milliseconds)}</b> together across "
            f"<b>{friend.sessions}</b> sessions&nbsp;&nbsp; · &nbsp;&nbsp;"
            f"{format_duration(friend.average_milliseconds)} average&nbsp;&nbsp; · &nbsp;&nbsp;"
            f"{format_duration(friend.longest_milliseconds)} longest&nbsp;&nbsp; · &nbsp;&nbsp;"
            f"{friend.active_days} active days&nbsp;&nbsp; · &nbsp;&nbsp;"
            f"last seen {format_local_datetime(friend.last_seen)}"
        )

    def compare_current_friend(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        friend = self.friends_model.friends[index.row()]
        self._selected_friend_ids = [friend.user_id]
        self.populate_compare_list()
        self.set_page(PAGE_SHARED_TIME)
        self.refresh_comparison()

    def populate_compare_list(self) -> None:
        if self.dashboard is None:
            return
        selected = set(self._selected_friend_ids)
        self.compare_list.blockSignals(True)
        self.compare_list.clear()
        for friend in self.dashboard.friends:
            item = QListWidgetItem(
                f"{friend.display_name}\n{format_duration(friend.milliseconds)} · {friend.sessions} sessions"
            )
            item.setData(Qt.ItemDataRole.UserRole, friend.user_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if friend.user_id in selected else Qt.CheckState.Unchecked
            )
            item.setSizeHint(QSize(0, 48))
            self.compare_list.addItem(item)
        self.compare_list.blockSignals(False)
        self.filter_compare_list(self.compare_search.text())
        self.update_compare_count()

    def filter_compare_list(self, value: str) -> None:
        query = value.strip().casefold()
        for index in range(self.compare_list.count()):
            item = self.compare_list.item(index)
            item.setHidden(query not in item.text().casefold())

    def selected_compare_ids(self) -> list[str]:
        return [
            self.compare_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.compare_list.count())
            if self.compare_list.item(index).checkState() == Qt.CheckState.Checked
        ]

    def update_compare_count(self) -> None:
        count = len(self._selected_friend_ids)
        self.compare_count.setText(f"{count} selected")

    def schedule_comparison_refresh(self) -> None:
        self._selected_friend_ids = self.selected_compare_ids()
        self.update_compare_count()
        self._comparison_debounce.start(180)

    def refresh_comparison(self) -> None:
        if not self._selected_friend_ids:
            self.compare_context.setText("Select at least one friend")
            self.compare_chart.set_series([], "Daily", "Time together")
            return
        state = self.collect_state()
        state.selected_friend_ids = list(self._selected_friend_ids)
        self._comparison_generation += 1
        generation = self._comparison_generation
        self.set_loading(True, f"Loading shared time for {len(self._selected_friend_ids)} friends…")
        worker = RepositoryWorker(
            generation,
            lambda: self.repository.load_comparison(state),
        )
        worker.signals.result.connect(self.comparison_loaded)
        worker.signals.error.connect(self.comparison_error)
        worker.signals.finished.connect(self.release_worker)
        self._workers.add(worker)
        self.thread_pool.start(worker)

    @Slot(int, object)
    def comparison_loaded(self, generation: int, result: object) -> None:
        if generation != self._comparison_generation:
            return
        data = result
        if not isinstance(data, ComparisonData):
            return
        self._comparison_data = data
        self.set_loading(False)
        self.render_comparison_chart()
        self.status_label.setText(
            f"Comparing {len(data.series_by_user)} friends · local date & time {LOCAL_TIMEZONE_NAME}"
        )

    def render_comparison_chart(self) -> None:
        data = getattr(self, "_comparison_data", None)
        if data is None or not self._selected_friend_ids:
            return
        granularity = self.compare_aggregation.currentText()
        cumulative = self.compare_mode.currentText() == "Cumulative"
        colors = self.assign_colors(self._selected_friend_ids)
        series_list = []
        for user_id in self._selected_friend_ids:
            raw = data.series_by_user.get(user_id)
            friend = self._friend_by_id.get(user_id)
            if raw is None or friend is None:
                continue
            series = aggregate_time_series(raw, granularity)
            if cumulative:
                running = 0
                accumulated = []
                for day, value in series:
                    running += value
                    accumulated.append((day, running))
                series = accumulated
            series_list.append((friend.display_name, series, colors[user_id]))
        label = "Cumulative time together" if cumulative else "Time together"
        self.compare_context.setText(
            f"{len(series_list)} friend{'s' if len(series_list) != 1 else ''} · {label.lower()}"
        )
        self.compare_chart.set_series(series_list, granularity, label)

    @staticmethod
    def assign_colors(user_ids: list[str]) -> dict[str, str]:
        assigned: dict[str, str] = {}
        used: set[int] = set()
        for user_id in sorted(user_ids):
            digest = hashlib.blake2b(user_id.encode("utf-8"), digest_size=2).digest()
            index = int.from_bytes(digest, "big") % len(SERIES_COLORS)
            for _attempt in range(len(SERIES_COLORS)):
                if index not in used:
                    break
                index = (index + 1) % len(SERIES_COLORS)
            assigned[user_id] = SERIES_COLORS[index]
            used.add(index)
        return assigned

    def clear_comparison(self) -> None:
        self.compare_list.blockSignals(True)
        for index in range(self.compare_list.count()):
            self.compare_list.item(index).setCheckState(Qt.CheckState.Unchecked)
        self.compare_list.blockSignals(False)
        self._selected_friend_ids = []
        self._comparison_data = None
        self.update_compare_count()
        self.compare_context.setText("Select at least one friend")
        self.compare_chart.set_series([], "Daily", "Time together")

    @Slot(int, str, str)
    def worker_error(self, generation: int, message: str, _kind: str) -> None:
        if generation != self._dashboard_generation:
            return
        self.set_loading(False)
        self.show_error(message)

    @Slot(int, str, str)
    def comparison_error(self, generation: int, message: str, _kind: str) -> None:
        if generation != self._comparison_generation:
            return
        self.set_loading(False)
        self.show_error(message)

    @Slot(int, str, str)
    def insights_error(self, generation: int, message: str, _kind: str) -> None:
        if generation != self._insights_generation:
            return
        self.set_loading(False)
        self.show_error(message)

    @Slot(int, str, str)
    def map_error(self, generation: int, message: str, _kind: str) -> None:
        if generation != self._map_generation:
            return
        self.set_loading(False)
        self.show_error(message)

    @Slot(object)
    def release_worker(self, worker: RepositoryWorker) -> None:
        self._workers.discard(worker)

    def set_loading(self, loading: bool, message: str = "Loading…") -> None:
        self.progress.setVisible(loading)
        self.refresh_button.setEnabled(not loading)
        if loading:
            self.status_label.setText(message)

    def show_error(self, message: str) -> None:
        self.status_label.setText("VRCX activity could not be loaded")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("VRCX Time Together")
        box.setText(message)
        box.setInformativeText("The VRCX database was not modified. Technical details were written to the local application log.")
        box.exec()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if not hasattr(self, "sidebar") or not hasattr(self, "metric_cards"):
            return
        compact = self.width() < 1100
        self.sidebar.setFixedWidth(174 if compact else 206)
        columns = 5
        for card in self.metric_cards:
            self.metrics_layout.removeWidget(card)
        for index, card in enumerate(self.metric_cards):
            self.metrics_layout.addWidget(card, index // columns, index % columns)
        if not hasattr(self, "insight_metric_cards"):
            return
        insight_columns = 2 if compact else 3
        self.insight_scope.setVisible(not compact)
        for card in self.insight_metric_cards:
            self.insight_metrics_layout.removeWidget(card)
        for index, card in enumerate(self.insight_metric_cards):
            self.insight_metrics_layout.addWidget(
                card,
                index // insight_columns,
                index % insight_columns,
            )
        if not hasattr(self, "map_visuals_layout"):
            return
        map_side_by_side = self.width() >= 1180
        self.map_visuals_layout.setDirection(
            QBoxLayout.Direction.LeftToRight
            if map_side_by_side
            else QBoxLayout.Direction.TopToBottom
        )
        self.map_visuals_layout.setStretch(0, 4 if map_side_by_side else 3)
        self.map_visuals_layout.setStretch(1, 1)
        self.map_ranking_panel.setMinimumWidth(230 if map_side_by_side else 0)
        self.map_ranking_panel.setMaximumWidth(285 if map_side_by_side else 16_777_215)
        self.map_ranking_panel.setMinimumHeight(180)
        toolbar_stacked = self.width() < 1040
        self._layout_map_toolbar(compact=toolbar_stacked)
        self.map_page_content.setMinimumHeight(
            900 if toolbar_stacked else 760 if not map_side_by_side else 0
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self._settings.setValue("windowGeometry", self.saveGeometry())
        self.thread_pool.waitForDone(1500)
        super().closeEvent(event)


def run_check(database_path: Path) -> int:
    end = date.today()
    data = VrcxRepository(database_path).load_dashboard(
        AppState(end - timedelta(days=6), end)
    )
    if data.total_social_milliseconds > data.total_person_milliseconds:
        raise RuntimeError("Social time cannot exceed total person-time.")
    peak_day, peak_value = max(data.social_daily, key=lambda item: item[1])
    message = (
        f"OK: {len(data.friends)} of {data.matching_count} friends shown, "
        f"{data.current_friend_count} current friends, {len(data.social_daily)} daily points, "
        f"{format_duration(data.total_social_milliseconds)} social time, "
        f"{format_duration(data.total_person_milliseconds)} person-time, "
        f"peak {peak_day} ({format_duration(peak_value)}), timezone {LOCAL_TIMEZONE_NAME}, "
        f"latest activity {format_local_datetime(data.latest_activity)}"
    )
    if sys.stdout is not None:
        print(message)
    else:
        LOGGER.info(message)
    return 0


def run_ui_check(app: QApplication, window: MainWindow) -> int:
    """Exercise the frozen Qt shell and first background database load."""
    poll = QTimer(window)
    poll.setInterval(40)
    timeout = QTimer(window)
    timeout.setSingleShot(True)

    def complete() -> None:
        if window.dashboard is None:
            return
        if window.pages.currentIndex() != PAGE_FRIEND_MAP:
            window.set_page(PAGE_FRIEND_MAP)
            return
        if window._friend_map_data is None:
            return
        if window.map_view_mode.value() != "Groups":
            window.map_view_mode.set_value("Groups")
            return
        if window.map_friend_count.value() != "All":
            window.map_friend_count.set_value("All")
            return
        if (
            window.friend_map.group_count()
            and window.friend_map.selected_group() == 0
        ):
            window.show_map_group(1)
            return
        ranking_right = window.map_ranking_panel.mapTo(
            window.map_visuals_host, window.map_ranking_panel.rect().bottomRight()
        ).x()
        if ranking_right > window.map_visuals_host.width():
            LOGGER.error("Friend Map ranking panel extends beyond its viewport")
            fail()
            return
        LOGGER.info("Packaged UI check completed successfully")
        poll.stop()
        timeout.stop()
        window.close()
        app.exit(0)

    def fail() -> None:
        LOGGER.error("Packaged UI check timed out")
        poll.stop()
        window.close()
        app.exit(1)

    poll.timeout.connect(complete)
    timeout.timeout.connect(fail)
    window.show()
    poll.start()
    timeout.start(15_000)
    return app.exec()


def main(script_path: Path) -> int:
    QLocale.setDefault(ENGLISH_LOCALE)
    configure_logging()
    LOGGER.info("Starting VRCX Time Together")
    try:
        database_path = resolve_database_path(script_path)
    except FileNotFoundError as error:
        if "--check" in sys.argv:
            if sys.stderr is not None:
                print(f"ERROR: {error}", file=sys.stderr)
            else:
                LOGGER.error("%s", error)
            return 1
        app = QApplication(sys.argv)
        apply_theme(app)
        QMessageBox.critical(None, "VRCX Time Together", str(error))
        return 1
    LOGGER.info("Using read-only VRCX database at %s", database_path)
    if "--check" in sys.argv:
        return run_check(database_path)
    app = QApplication(sys.argv)
    app.setApplicationName("VRCX Time Together")
    app.setOrganizationName("VRCX Time Together")
    apply_theme(app)
    window = MainWindow(VrcxRepository(database_path), script_path)
    if "--ui-check" in sys.argv:
        return run_ui_check(app, window)
    window.show()
    return app.exec()
