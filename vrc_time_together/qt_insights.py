from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from .formatting import ENGLISH_MONTHS, ENGLISH_WEEKDAYS, format_duration
from .models import CoPresenceStat
from .qt_theme import (
    ACCENT,
    ACCENT_HOVER,
    BORDER,
    SERIES_COLORS,
    SUCCESS,
    SURFACE_RAISED,
    TEXT,
    TEXT_MUTED,
    TEXT_SUBTLE,
    WARNING,
)


def _heat_color(value: float, maximum: float) -> QColor:
    if value <= 0 or maximum <= 0:
        return QColor(SURFACE_RAISED)
    ratio = min(1.0, max(0.0, value / maximum))
    ratio = 0.18 + ratio * 0.82
    low = QColor("#252444")
    high = QColor(ACCENT_HOVER)
    return QColor(
        round(low.red() + (high.red() - low.red()) * ratio),
        round(low.green() + (high.green() - low.green()) * ratio),
        round(low.blue() + (high.blue() - low.blue()) * ratio),
    )


class CalendarHeatmap(QWidget):
    day_selected = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._daily: list[tuple[date, int]] = []
        self._cell_rects: list[tuple[QRectF, date, int]] = []
        self._selected_day: date | None = None
        self.setMouseTracking(True)
        self.setFixedHeight(174)
        self.setMinimumWidth(620)

    def set_data(self, daily: list[tuple[date, int]]) -> None:
        self._daily = list(daily)
        self._selected_day = None
        if self._daily:
            first = self._daily[0][0]
            last = self._daily[-1][0]
            first_monday = first - timedelta(days=first.weekday())
            weeks = (last - first_monday).days // 7 + 1
            self.setMinimumWidth(max(620, 72 + weeks * 17))
            self.resize(self.minimumWidth(), self.height())
        self.update()

    def clear_selection(self) -> None:
        self._selected_day = None
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._cell_rects = []
        if not self._daily:
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No activity in this range")
            return

        first_monday = self._daily[0][0] - timedelta(
            days=self._daily[0][0].weekday()
        )
        left = 54.0
        top = 28.0
        cell = 13.0
        gap = 4.0
        maximum = max(value for _day, value in self._daily)
        painter.setPen(QColor(TEXT_SUBTLE))
        for weekday, label in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            painter.drawText(
                QRectF(0, top + weekday * (cell + gap) - 1, 44, cell + 2),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )

        last_month: tuple[int, int] | None = None
        painter.setPen(QColor(TEXT_MUTED))
        for day, value in self._daily:
            offset = (day - first_monday).days
            column = offset // 7
            row = day.weekday()
            x = left + column * (cell + gap)
            y = top + row * (cell + gap)
            rect = QRectF(x, y, cell, cell)
            painter.setPen(QPen(QColor(BORDER), 0.7))
            painter.setBrush(_heat_color(value, maximum))
            painter.drawRoundedRect(rect, 2.2, 2.2)
            if day == self._selected_day:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(TEXT), 2))
                painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 3, 3)
            self._cell_rects.append((rect, day, value))

            month = (day.year, day.month)
            if month != last_month:
                painter.setPen(QColor(TEXT_MUTED))
                painter.drawText(
                    QRectF(x, 2, 72, 20),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    f"{ENGLISH_MONTHS[day.month][:3]}"
                    + (f" {day.year}" if day.month == 1 else ""),
                )
                last_month = month

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        for rect, day, value in self._cell_rects:
            if rect.contains(event.position()):
                label = (
                    f"{ENGLISH_WEEKDAYS[day.weekday()]}, {day.day:02d} "
                    f"{ENGLISH_MONTHS[day.month]} {day.year}"
                )
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    f"<b>{label}</b><br>{format_duration(value)} together",
                    self,
                )
                return
        QToolTip.hideText()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        for rect, day, _value in self._cell_rects:
            if rect.contains(event.position()):
                self._selected_day = day
                self.update()
                self.day_selected.emit(day)
                return
        super().mousePressEvent(event)


class WeekHourHeatmap(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: tuple[tuple[int, ...], ...] = tuple()
        self._occurrences: tuple[int, ...] = tuple()
        self._average = True
        self._cell_rects: list[tuple[QRectF, int, int, float]] = []
        self.setMouseTracking(True)
        self.setMinimumHeight(230)

    def set_data(
        self,
        values: tuple[tuple[int, ...], ...],
        occurrences: tuple[int, ...],
        *,
        average: bool,
    ) -> None:
        self._values = values
        self._occurrences = occurrences
        self._average = average
        self.update()

    def _display_value(self, weekday: int, hour: int) -> float:
        value = self._values[weekday][hour]
        if not self._average:
            return float(value)
        occurrences = self._occurrences[weekday]
        return value / occurrences if occurrences else 0.0

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._cell_rects = []
        if not self._values:
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Select a friend to see their rhythm")
            return

        left = 72.0
        top = 30.0
        gap = 3.0
        available = max(240.0, self.width() - left - 8)
        cell_width = (available - gap * 23) / 24
        cell_height = 21.0
        display_values = [
            self._display_value(weekday, hour)
            for weekday in range(7)
            for hour in range(24)
        ]
        maximum = max(display_values, default=0.0)

        painter.setPen(QColor(TEXT_MUTED))
        for hour in range(0, 24, 3):
            x = left + hour * (cell_width + gap)
            painter.drawText(
                QRectF(x - 6, 2, 40, 20),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{hour:02d}:00",
            )
        for weekday, name in enumerate(ENGLISH_WEEKDAYS):
            y = top + weekday * (cell_height + gap)
            painter.drawText(
                QRectF(0, y, 62, cell_height),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                name[:3],
            )
            for hour in range(24):
                value = self._display_value(weekday, hour)
                x = left + hour * (cell_width + gap)
                rect = QRectF(x, y, cell_width, cell_height)
                painter.setPen(QPen(QColor(BORDER), 0.7))
                painter.setBrush(_heat_color(value, maximum))
                painter.drawRoundedRect(rect, 2, 2)
                self._cell_rects.append((rect, weekday, hour, value))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        for rect, weekday, hour, value in self._cell_rects:
            if rect.contains(event.position()):
                qualifier = "average for this weekday" if self._average else "total in range"
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    f"<b>{ENGLISH_WEEKDAYS[weekday]} {hour:02d}:00–{(hour + 1) % 24:02d}:00</b>"
                    f"<br>{format_duration(round(value))} · {qualifier}",
                    self,
                )
                return
        QToolTip.hideText()


class CompanyContextChart(QWidget):
    LABELS = ("Only this friend", "Small friend group", "Larger friend group")
    COLORS = (ACCENT_HOVER, SUCCESS, WARNING)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._milliseconds = (0, 0, 0)
        self._encounters = (0, 0, 0)
        self.setMinimumHeight(112)

    def set_data(
        self,
        milliseconds: tuple[int, int, int],
        encounters: tuple[int, int, int],
    ) -> None:
        self._milliseconds = milliseconds
        self._encounters = encounters
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        total = sum(self._milliseconds)
        bar = QRectF(0, 7, self.width(), 25)
        painter.setPen(QPen(QColor(BORDER), 1))
        painter.setBrush(QColor(SURFACE_RAISED))
        painter.drawRoundedRect(bar, 6, 6)
        if total:
            cursor = 0.0
            for value, color in zip(self._milliseconds, self.COLORS):
                width = bar.width() * value / total
                if width > 0:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(color))
                    painter.drawRect(QRectF(cursor, bar.y(), width, bar.height()))
                cursor += width

        column_width = self.width() / 3
        for index, (label, color, milliseconds, encounters) in enumerate(
            zip(self.LABELS, self.COLORS, self._milliseconds, self._encounters)
        ):
            left = index * column_width
            percent = milliseconds / total * 100 if total else 0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(QRectF(left, 53, 8, 8))
            painter.setPen(QColor(TEXT))
            painter.drawText(
                QRectF(left + 14, 43, column_width - 16, 24),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{label}  {percent:.0f}%",
            )
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(
                QRectF(left + 14, 67, column_width - 16, 28),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{format_duration(milliseconds)} · {encounters} encounter"
                f"{'s' if encounters != 1 else ''}",
            )


class CoPresenceChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stats: tuple[CoPresenceStat, ...] = tuple()
        self._total_milliseconds = 0
        self._total_encounters = 0
        self._mode = "Time overlap"
        self._row_rects: list[tuple[QRectF, CoPresenceStat, float]] = []
        self.setMouseTracking(True)
        self.setMinimumHeight(180)

    def set_data(
        self,
        stats: tuple[CoPresenceStat, ...],
        total_milliseconds: int,
        total_encounters: int,
        mode: str,
    ) -> None:
        self._stats = stats
        self._total_milliseconds = total_milliseconds
        self._total_encounters = total_encounters
        self._mode = mode
        self.setMinimumHeight(max(180, min(10, len(stats)) * 38 + 16))
        self.updateGeometry()
        self.update()

    def _ratio(self, stat: CoPresenceStat) -> float:
        if self._mode == "Encounter overlap":
            return stat.encounters / self._total_encounters if self._total_encounters else 0
        return stat.milliseconds / self._total_milliseconds if self._total_milliseconds else 0

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._row_rects = []
        if not self._stats:
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "No other current friends overlapped in this range",
            )
            return

        stats = sorted(
            self._stats,
            key=lambda stat: (-self._ratio(stat), stat.display_name.casefold()),
        )[:10]
        label_width = min(190.0, max(130.0, self.width() * 0.22))
        detail_width = min(245.0, max(185.0, self.width() * 0.28))
        bar_left = label_width + 10
        bar_width = max(80.0, self.width() - bar_left - detail_width - 14)
        metrics = QFontMetrics(painter.font())
        for index, stat in enumerate(stats):
            y = 8.0 + index * 38
            ratio = self._ratio(stat)
            row_rect = QRectF(0, y, self.width(), 30)
            self._row_rects.append((row_rect, stat, ratio))
            painter.setPen(QColor(TEXT))
            name = metrics.elidedText(
                stat.display_name,
                Qt.TextElideMode.ElideRight,
                round(label_width - 8),
            )
            painter.drawText(
                QRectF(0, y, label_width, 28),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                name,
            )
            track = QRectF(bar_left, y + 6, bar_width, 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(SURFACE_RAISED))
            painter.drawRoundedRect(track, 5, 5)
            fill = QRectF(track.x(), track.y(), track.width() * ratio, track.height())
            painter.setBrush(QColor(SERIES_COLORS[index % len(SERIES_COLORS)]))
            if fill.width() > 0:
                painter.drawRoundedRect(fill, 5, 5)
            percent = ratio * 100
            percent_text = f"{percent:.0f}%" if percent >= 10 else f"{percent:.1f}%"
            detail = (
                f"{percent_text} · {format_duration(stat.milliseconds)} · "
                f"{stat.encounters}/{self._total_encounters} encounters"
            )
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(
                QRectF(bar_left + bar_width + 10, y, detail_width, 28),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                detail,
            )

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        for rect, stat, ratio in self._row_rects:
            if rect.contains(event.position()):
                percent = ratio * 100
                explanation = (
                    "encounters with this person present"
                    if self._mode == "Encounter overlap"
                    else "selected-friend time with this person present"
                )
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    f"<b>{stat.display_name}</b><br>{percent:.1f}% of {explanation}"
                    f"<br>{format_duration(stat.milliseconds)} · "
                    f"{stat.encounters} encounter{'s' if stat.encounters != 1 else ''}",
                    self,
                )
                return
        QToolTip.hideText()
