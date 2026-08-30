from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QPushButton,
    QToolTip,
    QWidget,
)

from .formatting import format_duration
from .models import FriendMapData, FriendMapLink, FriendMapNode
from .qt_theme import (
    ACCENT,
    BORDER_STRONG,
    SURFACE,
    SURFACE_RAISED,
    TEXT,
    TEXT_MUTED,
)


ACTIVITY_RANK_COLORS = (
    QColor("#a78bfa"),  # Top 5
    QColor("#55c7d8"),  # 6–10
    QColor("#5b9cf6"),  # 11–20
    QColor("#65b98a"),  # 21+
)


def activity_rank_color(rank: int) -> QColor:
    if rank <= 5:
        return QColor(ACTIVITY_RANK_COLORS[0])
    if rank <= 10:
        return QColor(ACTIVITY_RANK_COLORS[1])
    if rank <= 20:
        return QColor(ACTIVITY_RANK_COLORS[2])
    return QColor(ACTIVITY_RANK_COLORS[3])


def activity_rank_legend_html() -> str:
    entries = (
        (ACTIVITY_RANK_COLORS[0], "Top 5"),
        (ACTIVITY_RANK_COLORS[1], "6–10"),
        (ACTIVITY_RANK_COLORS[2], "11–20"),
        (ACTIVITY_RANK_COLORS[3], "21+"),
    )
    return " &nbsp; ".join(
        f'<span style="color:{color.name()}">●</span> {label}'
        for color, label in entries
    )


class SegmentedControl(QWidget):
    value_changed = Signal(str)

    def __init__(
        self,
        values: tuple[str, ...],
        current: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for index, value in enumerate(values):
            button = QPushButton(value)
            button.setObjectName("MapSegmentButton")
            button.setProperty(
                "segmentPosition",
                "first" if index == 0 else "last" if index == len(values) - 1 else "middle",
            )
            button.setCheckable(True)
            button.setMinimumHeight(30)
            button.clicked.connect(
                lambda checked=False, selected=value: self._select(selected)
            )
            self._group.addButton(button)
            self._buttons[value] = button
            layout.addWidget(button)
        self.set_value(current, emit=False)

    def _select(self, value: str) -> None:
        self.set_value(value, emit=False)
        self.value_changed.emit(value)

    def value(self) -> str:
        checked = self._group.checkedButton()
        return checked.text() if checked is not None else ""

    def set_value(self, value: str, *, emit: bool = True) -> None:
        button = self._buttons.get(value)
        if button is None or button.isChecked():
            return
        button.setChecked(True)
        if emit:
            self.value_changed.emit(value)


class TopFriendsBarChart(QWidget):
    friend_selected = Signal(str)
    friend_activated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._nodes: tuple[FriendMapNode, ...] = tuple()
        self._rows: list[tuple[QRectF, FriendMapNode]] = []
        self._hovered_id: str | None = None
        self._selected_id: str | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(220, 180)

    def set_nodes(
        self,
        nodes: tuple[FriendMapNode, ...],
        limit: int | None = None,
    ) -> None:
        self._nodes = nodes if limit is None else nodes[:limit]
        self.setMinimumHeight(max(180, 8 + len(self._nodes) * 36))
        if self._selected_id not in {node.user_id for node in self._nodes}:
            self._selected_id = None
        self.update()

    def set_selected_friend(self, user_id: str | None) -> None:
        self._selected_id = user_id
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._rows = []
        if not self._nodes:
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "No activity in this range"
            )
            return
        maximum = max(node.milliseconds for node in self._nodes) or 1
        row_height = min(46.0, max(31.0, (self.height() - 8) / len(self._nodes)))
        name_width = min(126.0, max(82.0, self.width() * 0.38))
        value_width = 62.0
        bar_left = name_width + 7
        bar_width = max(42.0, self.width() - bar_left - value_width - 10)
        metrics = QFontMetrics(painter.font())

        for index, node in enumerate(self._nodes):
            y = 4.0 + index * row_height
            row = QRectF(0, y, self.width(), row_height - 2)
            self._rows.append((row, node))
            hovered = node.user_id == self._hovered_id
            selected = node.user_id == self._selected_id
            if hovered or selected:
                background = QColor("#29264d" if selected else SURFACE_RAISED)
                background.setAlpha(190)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(background)
                painter.drawRoundedRect(row, 6, 6)
            painter.setPen(QColor(TEXT_MUTED))
            name = metrics.elidedText(
                node.display_name,
                Qt.TextElideMode.ElideRight,
                round(name_width - 6),
            )
            painter.setPen(QColor(TEXT))
            painter.drawText(
                QRectF(3, y, name_width - 6, row_height - 2),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                name,
            )
            track = QRectF(bar_left, y + row_height / 2 - 5, bar_width, 10)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#202b39"))
            painter.drawRoundedRect(track, 4, 4)
            fill = QRectF(
                track.x(),
                track.y(),
                track.width() * math.sqrt(node.milliseconds / maximum),
                track.height(),
            )
            fill_color = activity_rank_color(index + 1)
            if hovered:
                fill_color = fill_color.lighter(120)
            painter.setBrush(fill_color)
            painter.drawRoundedRect(fill, 4, 4)
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(
                QRectF(bar_left + bar_width + 8, y, value_width, row_height - 2),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                format_duration(node.milliseconds),
            )

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        hovered = next(
            (node for rect, node in self._rows if rect.contains(event.position())),
            None,
        )
        hovered_id = hovered.user_id if hovered else None
        if hovered_id != self._hovered_id:
            self._hovered_id = hovered_id
            self.update()
        if hovered is None:
            QToolTip.hideText()
            return
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"<b>{hovered.display_name}</b><br>"
            f"{format_duration(hovered.milliseconds)} around you · "
            f"{hovered.sessions} sessions<br>Double-click to open Insights",
            self,
        )

    def leaveEvent(self, _event) -> None:  # noqa: N802 - Qt API
        self._hovered_id = None
        QToolTip.hideText()
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.MouseButton.LeftButton:
            return
        for rect, node in self._rows:
            if rect.contains(event.position()):
                self.friend_selected.emit(node.user_id)
                return

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.MouseButton.LeftButton:
            return
        for rect, node in self._rows:
            if rect.contains(event.position()):
                self.friend_activated.emit(node.user_id)
                return


class FriendMapWidget(QWidget):
    friend_selected = Signal(str)
    friend_activated = Signal(str)
    selection_cleared = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = FriendMapData(tuple(), tuple())
        self._nodes: tuple[FriendMapNode, ...] = tuple()
        self._links: tuple[FriendMapLink, ...] = tuple()
        self._positions: dict[str, QPointF] = {}
        self._node_rects: dict[str, QRectF] = {}
        self._edge_segments: list[tuple[QPointF, QPointF, FriendMapLink]] = []
        self._hovered_id: str | None = None
        self._hovered_link: FriendMapLink | None = None
        self._selected_id: str | None = None
        self._connection_metric = "Time overlap"
        self._zoom = 1.0
        self._pan = QPointF()
        self._drag_origin: QPointF | None = None
        self._dragged = False
        self.setMouseTracking(True)
        self.setMinimumHeight(470)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_data(
        self,
        data: FriendMapData,
        node_limit: int = 20,
        connection_detail: str = "Focused",
        connection_metric: str = "Time overlap",
    ) -> None:
        self._data = data
        self._connection_metric = connection_metric
        visible_ids = {node.user_id for node in data.nodes[:node_limit]}
        self._nodes = tuple(node for node in data.nodes if node.user_id in visible_ids)
        candidates = sorted(
            (
            link
            for link in data.links
            if link.source_user_id in visible_ids
            and link.target_user_id in visible_ids
            ),
            key=lambda link: -self._link_value(link),
        )
        if connection_detail == "Focused" and candidates:
            strongest_value = self._link_value(candidates[0])
            candidates = [
                link
                for link in candidates
                if self._link_value(link) >= strongest_value * 0.08
            ]
        if connection_detail in ("All", "All connections"):
            self._links = tuple(candidates)
        else:
            multiplier = 1 if connection_detail == "Focused" else 2
            budget = max(10, len(self._nodes) * multiplier)
            chosen: list[FriendMapLink] = []
            chosen_pairs: set[tuple[str, str]] = set()
            covered: set[str] = set()
            for link in candidates:
                if (
                    link.source_user_id in covered
                    and link.target_user_id in covered
                ):
                    continue
                pair = (link.source_user_id, link.target_user_id)
                chosen.append(link)
                chosen_pairs.add(pair)
                covered.update(pair)
            for link in candidates:
                if len(chosen) >= budget:
                    break
                pair = (link.source_user_id, link.target_user_id)
                if pair not in chosen_pairs:
                    chosen.append(link)
                    chosen_pairs.add(pair)
            self._links = tuple(chosen)
        if self._selected_id not in visible_ids:
            self._selected_id = None
        self._calculate_layout()
        self.update()

    def selected_node(self) -> FriendMapNode | None:
        return next(
            (node for node in self._nodes if node.user_id == self._selected_id),
            None,
        )

    def set_selected_friend(self, user_id: str | None) -> None:
        visible_ids = {node.user_id for node in self._nodes}
        self._selected_id = user_id if user_id in visible_ids else None
        self.update()

    def visible_connection_count(self, user_id: str) -> int:
        return sum(
            1
            for link in self._links
            if user_id in (link.source_user_id, link.target_user_id)
        )

    def visible_counts(self) -> tuple[int, int]:
        return len(self._nodes), len(self._links)

    def visible_nodes(self) -> tuple[FriendMapNode, ...]:
        return self._nodes

    def measured_connection_count(self) -> int:
        visible_ids = {node.user_id for node in self._nodes}
        return sum(
            1
            for link in self._data.links
            if link.source_user_id in visible_ids
            and link.target_user_id in visible_ids
        )

    def measured_relationship_count(self, user_id: str) -> int:
        visible_ids = {node.user_id for node in self._nodes}
        return sum(
            1
            for link in self._data.links
            if user_id in (link.source_user_id, link.target_user_id)
            and link.source_user_id in visible_ids
            and link.target_user_id in visible_ids
        )

    def rank_for(self, user_id: str) -> int | None:
        for index, node in enumerate(self._nodes, start=1):
            if node.user_id == user_id:
                return index
        return None

    def strongest_connection(
        self, user_id: str
    ) -> tuple[str, FriendMapLink] | None:
        candidates = [
            link
            for link in self._links
            if user_id in (link.source_user_id, link.target_user_id)
        ]
        if not candidates:
            return None
        link = max(candidates, key=self._link_value)
        other_id = (
            link.target_user_id
            if link.source_user_id == user_id
            else link.source_user_id
        )
        return other_id, link

    def _link_value(self, link: FriendMapLink) -> float:
        return (
            link.likelihood
            if self._connection_metric == "Co-appearance likelihood"
            else float(link.milliseconds)
        )

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._pan = QPointF()
        self.update()

    def _calculate_layout(self) -> None:
        self._positions = {}
        count = len(self._nodes)
        if not count:
            return
        golden_angle = math.pi * (3 - math.sqrt(5))
        anchors: dict[str, QPointF] = {}
        for index, node in enumerate(self._nodes):
            radius = 0.50 + ((index * 7) % 5) * 0.07
            angle = index * golden_angle - math.pi / 2
            anchor = QPointF(
                math.cos(angle) * radius,
                math.sin(angle) * radius,
            )
            anchors[node.user_id] = anchor
            self._positions[node.user_id] = QPointF(anchor)

        link_lookup = {
            tuple(sorted((link.source_user_id, link.target_user_id))): link
            for link in self._links
        }
        maximum_link = max((self._link_value(link) for link in self._links), default=1)
        for _iteration in range(110):
            forces = {node.user_id: QPointF() for node in self._nodes}
            for first_index, first in enumerate(self._nodes):
                first_position = self._positions[first.user_id]
                for second in self._nodes[first_index + 1 :]:
                    second_position = self._positions[second.user_id]
                    delta = first_position - second_position
                    distance_squared = max(0.015, delta.x() ** 2 + delta.y() ** 2)
                    distance = math.sqrt(distance_squared)
                    repulsion = 0.0048 / distance_squared
                    direction = delta / distance
                    forces[first.user_id] += direction * repulsion
                    forces[second.user_id] -= direction * repulsion

                    link = link_lookup.get(tuple(sorted((first.user_id, second.user_id))))
                    if link is not None:
                        strength = math.sqrt(self._link_value(link) / maximum_link)
                        target = 0.34 + (1.0 - strength) * 0.18
                        attraction = (distance - target) * (0.007 + strength * 0.011)
                        forces[first.user_id] -= direction * attraction
                        forces[second.user_id] += direction * attraction

            for node in self._nodes:
                position = self._positions[node.user_id]
                forces[node.user_id] += (
                    anchors[node.user_id] - position
                ) * 0.030

            for node in self._nodes:
                position = self._positions[node.user_id] + forces[node.user_id]
                distance = math.hypot(position.x(), position.y())
                if distance < 0.38:
                    position *= 0.38 / max(distance, 0.001)
                elif distance > 0.88:
                    position *= 0.88 / distance
                self._positions[node.user_id] = position

    def _screen_point(self, position: QPointF) -> QPointF:
        center = QPointF(self.width() / 2, (self.height() - 40) / 2) + self._pan
        horizontal, vertical = self._screen_scales()
        return center + QPointF(
            position.x() * horizontal,
            position.y() * vertical,
        )

    def _screen_scales(self) -> tuple[float, float]:
        return (
            max(90.0, (self.width() - 120) * 0.50) * self._zoom,
            max(90.0, (self.height() - 92) * 0.50) * self._zoom,
        )

    def _node_radius(self, node: FriendMapNode) -> float:
        maximum = max((item.milliseconds for item in self._nodes), default=1)
        if len(self._nodes) > 30:
            minimum, maximum_radius = 8.0, 18.0
        elif len(self._nodes) > 20:
            minimum, maximum_radius = 9.0, 21.0
        else:
            minimum, maximum_radius = 10.0, 24.0
        return minimum + (maximum_radius - minimum) * math.sqrt(
            node.milliseconds / maximum
        )

    def _link_visual_ratio(self, link: FriendMapLink, maximum: float) -> float:
        normalized = min(1.0, self._link_value(link) / max(maximum, 1e-9))
        if self._connection_metric == "Co-appearance likelihood":
            return normalized
        return math.sqrt(normalized)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(SURFACE))
        self._node_rects = {}
        self._edge_segments = []
        if not self._nodes:
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "No friend activity is available for this range",
            )
            return

        center = self._screen_point(QPointF())
        maximum_link = max((self._link_value(link) for link in self._links), default=1)
        points = {
            node.user_id: self._screen_point(self._positions[node.user_id])
            for node in self._nodes
        }
        radii = {node.user_id: self._node_radius(node) for node in self._nodes}
        self._node_rects = {
            node.user_id: QRectF(
                points[node.user_id].x() - radii[node.user_id],
                points[node.user_id].y() - radii[node.user_id],
                radii[node.user_id] * 2,
                radii[node.user_id] * 2,
            )
            for node in self._nodes
        }
        focused_id = self._selected_id or self._hovered_id
        focused_ids: set[str] = set()
        if focused_id:
            focused_ids.add(focused_id)
            for link in self._links:
                if link.source_user_id == focused_id:
                    focused_ids.add(link.target_user_id)
                elif link.target_user_id == focused_id:
                    focused_ids.add(link.source_user_id)
        elif self._hovered_link is not None:
            focused_ids.update(
                (
                    self._hovered_link.source_user_id,
                    self._hovered_link.target_user_id,
                )
            )
        strongest_link = None
        if focused_id:
            strongest = self.strongest_connection(focused_id)
            strongest_link = strongest[1] if strongest else None

        for link in sorted(self._links, key=self._link_value):
            first = points[link.source_user_id]
            second = points[link.target_user_id]
            self._edge_segments.append((first, second, link))
            ratio = self._link_visual_ratio(link, maximum_link)
            color = QColor(
                "#cf8cff"
                if self._connection_metric == "Co-appearance likelihood"
                else "#62c9d7"
            )
            connected = (
                focused_id in (link.source_user_id, link.target_user_id)
                if focused_id
                else self._hovered_link == link
            )
            if focused_ids and not connected:
                color.setAlpha(7 if self._selected_id else 16)
                width = 0.55
            else:
                color.setAlpha(52 + round(ratio * (180 if connected else 120)))
                width = 0.8 + ratio * (4.6 if connected else 3.0)
            if link == strongest_link or link == self._hovered_link:
                color = QColor("#e6b85c")
                color.setAlpha(235)
                width += 1.5
            painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine))
            painter.drawLine(first, second)

        center_glow = QColor(ACCENT)
        center_glow.setAlpha(38)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(center_glow)
        painter.drawEllipse(center, 40, 40)
        painter.setPen(QPen(QColor("#958aff"), 2.8))
        painter.setBrush(QColor("#29264d"))
        painter.drawEllipse(center, 31, 31)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#cf8cff"), 1.3))
        painter.drawEllipse(center, 25, 25)
        center_font = QFont(painter.font())
        center_font.setPointSize(8)
        center_font.setBold(True)
        painter.setFont(center_font)
        painter.setPen(QColor(TEXT))
        painter.drawText(
            QRectF(center.x() - 27, center.y() - 14, 54, 28),
            Qt.AlignmentFlag.AlignCenter,
            "YOU",
        )

        label_font = QFont(painter.font())
        label_font.setPointSize(8)
        label_metrics = QFontMetrics(label_font)
        for index, node in enumerate(self._nodes):
            point = points[node.user_id]
            selected = node.user_id == self._selected_id
            hovered = node.user_id == self._hovered_id
            connected = not focused_ids or node.user_id in focused_ids
            radius = radii[node.user_id] * (1.10 if hovered else 1.0)
            node_rect = self._node_rects[node.user_id]
            color = activity_rank_color(index + 1)
            if hovered:
                color = color.lighter(120)
            if not connected:
                color.setAlpha(48 if self._selected_id else 85)
            halo = QColor(color)
            halo.setAlpha(70 if selected else 32 if connected else 8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(point, radius + 5, radius + 5)
            if selected:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#cf8cff"), 3.0))
                painter.drawEllipse(point, radius + 7, radius + 7)
            painter.setPen(
                QPen(
                    QColor("#ffffff" if selected or hovered else BORDER_STRONG),
                    2.2 if selected or hovered else 1.0,
                )
            )
            painter.setBrush(color)
            painter.drawEllipse(point, radius, radius)

            rank_font = QFont(center_font)
            rank_font.setPointSize(6 if index >= 9 or radius < 13 else 7)
            painter.setFont(rank_font)
            badge_text_color = QColor("#ffffff")
            if not connected:
                badge_text_color.setAlpha(85)
            painter.setPen(badge_text_color)
            painter.drawText(
                QRectF(
                    point.x() - radius,
                    point.y() - radius,
                    radius * 2,
                    radius * 2,
                ),
                Qt.AlignmentFlag.AlignCenter,
                str(index + 1),
            )

        occupied = [rect.adjusted(-4, -4, 4, 4) for rect in self._node_rects.values()]
        canvas = QRectF(8, 8, self.width() - 16, self.height() - 16)
        for index, node in enumerate(self._nodes):
            selected = node.user_id == self._selected_id
            hovered = node.user_id == self._hovered_id
            connected = not focused_ids or node.user_id in focused_ids
            if index >= 12 and not selected and not hovered:
                continue
            point = points[node.user_id]
            radius = radii[node.user_id]
            painter.setFont(label_font)
            label = label_metrics.elidedText(
                node.display_name, Qt.TextElideMode.ElideRight, 132
            )
            width = label_metrics.horizontalAdvance(label) + 14
            candidates = (
                QRectF(point.x() - width / 2, point.y() + radius + 7, width, 22),
                QRectF(point.x() - width / 2, point.y() - radius - 29, width, 22),
                QRectF(point.x() + radius + 7, point.y() - 11, width, 22),
                QRectF(point.x() - radius - width - 7, point.y() - 11, width, 22),
            )
            label_rect = next(
                (
                    candidate
                    for candidate in candidates
                    if canvas.contains(candidate)
                    and not any(candidate.intersects(rect) for rect in occupied)
                ),
                None,
            )
            if label_rect is None:
                continue
            occupied.append(label_rect.adjusted(-3, -3, 3, 3))
            painter.setPen(QPen(QColor(BORDER_STRONG), 1))
            painter.setBrush(QColor(SURFACE_RAISED))
            painter.drawRoundedRect(label_rect, 6, 6)
            label_color = QColor(TEXT)
            if not connected:
                label_color.setAlpha(58 if self._selected_id else 100)
            painter.setPen(label_color)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)

    def _node_at(self, position: QPointF) -> str | None:
        for user_id, rect in reversed(tuple(self._node_rects.items())):
            if rect.contains(position):
                return user_id
        return None

    def _link_at(self, position: QPointF) -> FriendMapLink | None:
        closest: tuple[float, FriendMapLink] | None = None
        for start, end, link in self._edge_segments:
            delta = end - start
            length_squared = delta.x() ** 2 + delta.y() ** 2
            if length_squared <= 0:
                continue
            offset = position - start
            factor = max(
                0.0,
                min(1.0, (offset.x() * delta.x() + offset.y() * delta.y()) / length_squared),
            )
            projection = start + delta * factor
            distance = math.hypot(
                position.x() - projection.x(), position.y() - projection.y()
            )
            if distance <= 7.0 and (closest is None or distance < closest[0]):
                closest = (distance, link)
        return closest[1] if closest else None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drag_origin is not None:
            delta = event.position() - self._drag_origin
            if abs(delta.x()) + abs(delta.y()) > 2:
                self._dragged = True
            self._pan += delta
            self._drag_origin = event.position()
            self.update()
            return
        hovered = self._node_at(event.position())
        hovered_link = None if hovered else self._link_at(event.position())
        if hovered != self._hovered_id or hovered_link != self._hovered_link:
            self._hovered_id = hovered
            self._hovered_link = hovered_link
            self.update()
        if hovered is None and hovered_link is None:
            QToolTip.hideText()
            return
        if hovered_link is not None:
            nodes = {node.user_id: node for node in self._nodes}
            first = nodes[hovered_link.source_user_id]
            second = nodes[hovered_link.target_user_id]
            if self._connection_metric == "Co-appearance likelihood":
                metric_name = "Co-appearance likelihood"
                value = f"{hovered_link.likelihood:.0%}"
            else:
                metric_name = "Time overlap"
                value = format_duration(hovered_link.milliseconds)
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"<b>{first.display_name} ↔ {second.display_name}</b><br><br>"
                f"{metric_name}<br><b>{value}</b>",
                self,
            )
            return
        node = next(item for item in self._nodes if item.user_id == hovered)
        relationships = self.measured_relationship_count(hovered)
        rank = self.rank_for(hovered)
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"<b>{node.display_name}</b><br>"
            f"{format_duration(node.milliseconds)} recorded around you<br>"
            f"Rank #{rank} · {relationships} measured relationship"
            f"{'s' if relationships != 1 else ''}<br>"
            f"{node.sessions} shared session{'s' if node.sessions != 1 else ''}",
            self,
        )

    def leaveEvent(self, _event) -> None:  # noqa: N802 - Qt API
        self._hovered_id = None
        self._hovered_link = None
        QToolTip.hideText()
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.position()
            self._dragged = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_origin = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if self._dragged:
            return
        selected = self._node_at(event.position())
        self._selected_id = selected
        if selected is not None:
            self.friend_selected.emit(selected)
        else:
            self.selection_cleared.emit()
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        selected = self._node_at(event.position())
        if selected is not None:
            self._selected_id = selected
            self.friend_activated.emit(selected)
            self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        factor = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
        self._zoom = min(2.7, max(0.65, self._zoom * factor))
        self.update()
        event.accept()
