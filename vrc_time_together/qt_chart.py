from __future__ import annotations

from datetime import date

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .formatting import ENGLISH_MONTHS, ENGLISH_WEEKDAYS, format_duration
from .qt_theme import ACCENT, BORDER, GRID, SURFACE, TEXT_MUTED


class PeriodAxis(pg.AxisItem):
    def __init__(self) -> None:
        super().__init__(orientation="bottom")
        self.days: list[date] = []
        self.granularity = "Daily"

    def set_periods(self, days: list[date], granularity: str) -> None:
        self.days = days
        self.granularity = granularity
        self.picture = None
        self.update()

    def tickStrings(self, values, scale, spacing):  # noqa: N802 - Qt API
        labels = []
        for value in values:
            index = round(value)
            if not 0 <= index < len(self.days):
                labels.append("")
                continue
            day = self.days[index]
            if self.granularity == "Monthly":
                labels.append(f"{ENGLISH_MONTHS[day.month][:3]} {day.year}")
            elif len(self.days) > 120:
                labels.append(f"{ENGLISH_MONTHS[day.month][:3]} {day.year % 100:02d}")
            else:
                labels.append(f"{day.day:02d} {ENGLISH_MONTHS[day.month][:3]}")
        return labels


class TimeSeriesChart(QWidget):
    """Native interactive multi-series chart backed by PyQtGraph."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._series: list[tuple[str, list[tuple[date, int]], str]] = []
        self._granularity = "Daily"
        self._metric_label = "Time with friends"
        self._axis = PeriodAxis()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        self.legend = QLabel()
        self.legend.setObjectName("ChartLegend")
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        self.legend.setWordWrap(True)
        self.legend.hide()
        layout.addWidget(self.legend)
        self.tooltip = QLabel("Hover the chart for exact values")
        self.tooltip.setObjectName("Muted")
        self.tooltip.setTextFormat(Qt.TextFormat.RichText)
        self.tooltip.setMinimumHeight(24)
        self.tooltip.setWordWrap(True)
        layout.addWidget(self.tooltip)

        self.plot = pg.PlotWidget(axisItems={"bottom": self._axis})
        self.plot.setBackground(SURFACE)
        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=True, y=True, alpha=0.18)
        self.plot.getPlotItem().hideButtons()
        self.plot.getPlotItem().setContentsMargins(0, 0, 0, 0)
        self.plot.getAxis("left").setTextPen(pg.mkPen(TEXT_MUTED))
        self.plot.getAxis("bottom").setTextPen(pg.mkPen(TEXT_MUTED))
        self.plot.getAxis("left").setPen(pg.mkPen(BORDER))
        self.plot.getAxis("bottom").setPen(pg.mkPen(BORDER))
        self.plot.getAxis("left").setGrid(90)
        self.plot.getAxis("left").setLabel("hours", color=TEXT_MUTED)
        self.plot.setMinimumHeight(250)
        layout.addWidget(self.plot, 1)

        self.crosshair = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen(ACCENT, width=1, style=Qt.PenStyle.DashLine)
        )
        self.crosshair.hide()
        self.plot.addItem(self.crosshair, ignoreBounds=True)
        self.plot.scene().sigMouseMoved.connect(self._mouse_moved)

    def set_series(
        self,
        series: list[tuple[str, list[tuple[date, int]], str]],
        granularity: str,
        metric_label: str,
    ) -> None:
        self._series = series
        self._granularity = granularity
        self._metric_label = metric_label
        self.plot.clear()
        self.plot.addItem(self.crosshair, ignoreBounds=True)
        self.crosshair.hide()

        if not series or not series[0][1]:
            self._axis.set_periods([], granularity)
            self.legend.hide()
            self.tooltip.setText("No activity in this date range")
            return

        days = [day for day, _value in series[0][1]]
        self._axis.set_periods(days, granularity)
        if len(series) > 1:
            self.legend.setText(
                "&nbsp;&nbsp;&nbsp;".join(
                    f"<span style='color:{color}'>●</span>&nbsp;{name}"
                    for name, _values, color in series
                )
            )
            self.legend.show()
        else:
            self.legend.hide()
        x_values = list(range(len(days)))
        for index, (name, values, color) in enumerate(series):
            hours = [milliseconds / 3_600_000 for _day, milliseconds in values]
            kwargs = {
                "x": x_values,
                "y": hours,
                "name": name,
                "pen": pg.mkPen(color, width=2.2),
                "symbol": "o" if len(values) <= 31 else None,
                "symbolSize": 5,
                "symbolPen": pg.mkPen(color),
                "symbolBrush": pg.mkBrush(color),
                "antialias": True,
                "connect": "finite",
            }
            if len(series) == 1:
                kwargs["fillLevel"] = 0
                fill = QColor(color)
                fill.setAlpha(35)
                kwargs["brush"] = pg.mkBrush(fill)
            curve = self.plot.plot(**kwargs)
            curve.setClipToView(True)
            curve.setDownsampling(auto=True, method="peak")
        self.plot.setXRange(0, max(1, len(days) - 1), padding=0.015)
        self.plot.enableAutoRange(axis="y", enable=True)
        self.plot.setLimits(xMin=-0.5, xMax=max(0.5, len(days) - 0.5))
        self.tooltip.setText(
            f"<span style='color:{TEXT_MUTED}'>{metric_label} · {granularity.lower()} · "
            "scroll to zoom, drag to pan</span>"
        )

    def reset_view(self) -> None:
        if not self._series or not self._series[0][1]:
            return
        count = len(self._series[0][1])
        self.plot.setXRange(0, max(1, count - 1), padding=0.015)
        self.plot.enableAutoRange(axis="y", enable=True)

    def _mouse_moved(self, scene_position) -> None:
        if not self._series or not self.plot.sceneBoundingRect().contains(scene_position):
            self.crosshair.hide()
            return
        mouse = self.plot.getPlotItem().vb.mapSceneToView(scene_position)
        index = max(0, min(len(self._series[0][1]) - 1, round(mouse.x())))
        self.crosshair.setPos(index)
        self.crosshair.show()
        day = self._series[0][1][index][0]
        if self._granularity == "Weekly":
            period = f"Week of {day.day:02d} {ENGLISH_MONTHS[day.month]} {day.year}"
        elif self._granularity == "Monthly":
            period = f"{ENGLISH_MONTHS[day.month]} {day.year}"
        else:
            period = (
                f"{ENGLISH_WEEKDAYS[day.weekday()]}, {day.day:02d} "
                f"{ENGLISH_MONTHS[day.month]} {day.year}"
            )
        rows = []
        for name, values, color in self._series:
            value = values[index][1]
            rows.append(
                f"<span style='color:{color}'>●</span> "
                f"<b>{name}</b>&nbsp;&nbsp;{format_duration(value)}"
            )
        self.tooltip.setText(
            f"<b>{period}</b>&nbsp;&nbsp;<span style='color:{TEXT_MUTED}'>·</span>&nbsp;&nbsp;"
            + "&nbsp;&nbsp;&nbsp;".join(rows)
        )
