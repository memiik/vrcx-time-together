from __future__ import annotations

import hashlib
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from .formatting import format_duration
from .models import FriendMapData, FriendMapLink, FriendMapNode
from .qt_theme import (
    ACCENT,
    BORDER_STRONG,
    SERIES_COLORS,
    SURFACE,
    SURFACE_RAISED,
    TEXT,
    TEXT_MUTED,
)


class FriendMapWidget(QWidget):
    friend_selected = Signal(str)
    friend_activated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = FriendMapData(tuple(), tuple())
        self._nodes: tuple[FriendMapNode, ...] = tuple()
        self._links: tuple[FriendMapLink, ...] = tuple()
        self._positions: dict[str, QPointF] = {}
        self._node_rects: dict[str, QRectF] = {}
        self._hovered_id: str | None = None
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
        if connection_detail == "All connections":
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

    def visible_connection_count(self, user_id: str) -> int:
        return sum(
            1
            for link in self._links
            if user_id in (link.source_user_id, link.target_user_id)
        )

    def visible_counts(self) -> tuple[int, int]:
        return len(self._nodes), len(self._links)

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
        maximum = max(node.milliseconds for node in self._nodes) or 1
        golden_angle = math.pi * (3 - math.sqrt(5))
        anchors: dict[str, QPointF] = {}
        for index, node in enumerate(self._nodes):
            activity = math.sqrt(node.milliseconds / maximum)
            radius = 0.43 + (1.0 - activity) * 0.37
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
        return 10.0 + 16.0 * math.sqrt(node.milliseconds / maximum)

    @staticmethod
    def _node_color(user_id: str) -> QColor:
        digest = hashlib.blake2b(user_id.encode("utf-8"), digest_size=2).digest()
        return QColor(SERIES_COLORS[int.from_bytes(digest, "big") % len(SERIES_COLORS)])

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(SURFACE))
        self._node_rects = {}
        if not self._nodes:
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "No friend activity is available for this range",
            )
            return

        center = self._screen_point(QPointF())
        maximum_time = max(node.milliseconds for node in self._nodes) or 1
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

        orbit_color = QColor(BORDER_STRONG)
        orbit_color.setAlpha(42)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(orbit_color, 1, Qt.PenStyle.DotLine))
        horizontal_scale, vertical_scale = self._screen_scales()
        for orbit in (0.44, 0.64, 0.84):
            painter.drawEllipse(
                center,
                horizontal_scale * orbit,
                vertical_scale * orbit,
            )

        for node in self._nodes:
            point = points[node.user_id]
            ratio = math.sqrt(node.milliseconds / maximum_time)
            color = QColor(ACCENT)
            color.setAlpha(20 + round(ratio * 28))
            painter.setPen(QPen(color, 0.7 + ratio))
            painter.drawLine(center, point)

        focused_id = self._selected_id or self._hovered_id
        for link in sorted(self._links, key=self._link_value):
            first = points[link.source_user_id]
            second = points[link.target_user_id]
            ratio = math.sqrt(self._link_value(link) / maximum_link)
            color = QColor(
                "#cf8cff"
                if self._connection_metric == "Co-appearance likelihood"
                else "#62c9d7"
            )
            connected = focused_id in (link.source_user_id, link.target_user_id)
            if focused_id and not connected:
                color.setAlpha(22)
                width = 0.8
            else:
                color.setAlpha(48 + round(ratio * (175 if connected else 115)))
                width = 0.9 + ratio * (4.8 if connected else 3.1)
            painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine))
            painter.drawLine(first, second)

        center_glow = QColor(ACCENT)
        center_glow.setAlpha(36)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(center_glow)
        painter.drawEllipse(center, 39, 39)
        painter.setPen(QPen(QColor(ACCENT), 2.4))
        painter.setBrush(QColor("#29264d"))
        painter.drawEllipse(center, 29, 29)
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
            radius = radii[node.user_id]
            node_rect = self._node_rects[node.user_id]
            color = self._node_color(node.user_id)
            halo = QColor(color)
            halo.setAlpha(42)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(point, radius + 5, radius + 5)
            selected = node.user_id == self._selected_id
            hovered = node.user_id == self._hovered_id
            painter.setPen(
                QPen(QColor("#ffffff" if selected or hovered else BORDER_STRONG), 2.4 if selected else 1.2)
            )
            painter.setBrush(color)
            painter.drawEllipse(node_rect)

            rank = str(index + 1)
            painter.setFont(center_font)
            painter.setPen(QColor("#071019"))
            painter.drawText(node_rect, Qt.AlignmentFlag.AlignCenter, rank)

        occupied = [rect.adjusted(-4, -4, 4, 4) for rect in self._node_rects.values()]
        canvas = QRectF(8, 8, self.width() - 16, self.height() - 48)
        for index, node in enumerate(self._nodes):
            selected = node.user_id == self._selected_id
            hovered = node.user_id == self._hovered_id
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
            painter.setPen(QColor(TEXT))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)

        painter.setFont(label_font)
        painter.setPen(QColor(TEXT_MUTED))
        painter.drawText(
            QRectF(14, self.height() - 30, self.width() - 28, 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            (
                "Node size = time around you  ·  line weight = co-appearance likelihood"
                if self._connection_metric == "Co-appearance likelihood"
                else "Node size = time around you  ·  line weight = same-instance overlap"
            )
            + "  ·  drag to pan  ·  wheel to zoom",
        )

    def _node_at(self, position: QPointF) -> str | None:
        for user_id, rect in reversed(tuple(self._node_rects.items())):
            if rect.contains(position):
                return user_id
        return None

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
        if hovered != self._hovered_id:
            self._hovered_id = hovered
            self.update()
        if hovered is None:
            QToolTip.hideText()
            return
        node = next(item for item in self._nodes if item.user_id == hovered)
        connections = self.visible_connection_count(hovered)
        strongest = self.strongest_connection(hovered)
        strongest_text = ""
        if strongest is not None:
            other_id, link = strongest
            other = next(item for item in self._nodes if item.user_id == other_id)
            metric = (
                f"{link.likelihood:.0%} co-appearance likelihood"
                if self._connection_metric == "Co-appearance likelihood"
                else f"{format_duration(link.milliseconds)} overlap"
            )
            strongest_text = f"<br>Strongest with {other.display_name} · {metric}"
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"<b>{node.display_name}</b><br>"
            f"{format_duration(node.milliseconds)} around you · {node.sessions} sessions<br>"
            f"{connections} visible same-instance connection{'s' if connections != 1 else ''}"
            f"{strongest_text}",
            self,
        )

    def leaveEvent(self, _event) -> None:  # noqa: N802 - Qt API
        self._hovered_id = None
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
