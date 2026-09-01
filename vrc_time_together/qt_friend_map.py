from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QPushButton,
    QToolTip,
    QWidget,
)

from .friend_groups import detect_friend_groups, same_instance_strength
from .formatting import format_duration, format_local_datetime
from .models import FriendIntroduction, FriendMapData, FriendMapLink, FriendMapNode
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
FRIEND_GROUP_COLORS = (
    QColor("#a78bfa"),
    QColor("#55c7d8"),
    QColor("#f0b35a"),
    QColor("#65b98a"),
    QColor("#e879b7"),
    QColor("#5b9cf6"),
    QColor("#e98263"),
    QColor("#9ac65d"),
)
UNGROUPED_COLOR = QColor("#778394")


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


def friend_group_color(group_id: int) -> QColor:
    if group_id <= 0:
        return QColor(UNGROUPED_COLOR)
    return QColor(FRIEND_GROUP_COLORS[(group_id - 1) % len(FRIEND_GROUP_COLORS)])


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
    friend_hovered = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._nodes: tuple[FriendMapNode, ...] = tuple()
        self._node_colors: dict[str, QColor] = {}
        self._rows: list[tuple[QRectF, FriendMapNode]] = []
        self._hovered_id: str | None = None
        self._selected_id: str | None = None
        self.setMouseTracking(True)
        self.setAccessibleName("Friend activity ranking")
        self.setMinimumSize(0, 180)

    def set_nodes(
        self,
        nodes: tuple[FriendMapNode, ...],
        limit: int | None = None,
        colors: dict[str, QColor] | None = None,
    ) -> None:
        self._nodes = nodes if limit is None else nodes[:limit]
        self._node_colors = {
            user_id: QColor(color) for user_id, color in (colors or {}).items()
        }
        self.setMinimumHeight(max(180, 8 + len(self._nodes) * 52))
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
        row_height = min(58.0, max(50.0, (self.height() - 8) / len(self._nodes)))
        rank_width = 28.0
        content_left = rank_width + 7.0
        content_right = max(content_left + 42.0, self.width() - 7.0)
        value_width = 68.0
        name_right = max(content_left + 30.0, content_right - value_width - 8.0)
        for index, node in enumerate(self._nodes):
            y = 4.0 + index * row_height
            row = QRectF(0, y, self.width(), row_height - 3)
            self._rows.append((row, node))
            hovered = node.user_id == self._hovered_id
            selected = node.user_id == self._selected_id
            if hovered or selected:
                background = QColor("#29264d" if selected else SURFACE_RAISED)
                background.setAlpha(210 if selected else 175)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(background)
                painter.drawRoundedRect(row, 6, 6)
            fill_color = QColor(
                self._node_colors.get(node.user_id, activity_rank_color(index + 1))
            )
            if hovered:
                fill_color = fill_color.lighter(118)
            if selected:
                painter.setBrush(fill_color)
                painter.drawRoundedRect(QRectF(0, y + 7, 3, row_height - 17), 1.5, 1.5)

            rank_color = QColor(fill_color)
            rank_color.setAlpha(215 if hovered or selected else 150)
            painter.setPen(rank_color)
            rank_font = QFont(painter.font())
            rank_font.setPointSizeF(max(7.5, rank_font.pointSizeF() - 1.0))
            painter.setFont(rank_font)
            painter.drawText(
                QRectF(5, y + 4, rank_width - 6, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{index + 1:02d}",
            )

            name_font = QFont(painter.font())
            name_font.setPointSizeF(rank_font.pointSizeF() + 1.0)
            name_font.setBold(selected)
            painter.setFont(name_font)
            name_metrics = QFontMetrics(name_font)
            name = name_metrics.elidedText(
                node.display_name,
                Qt.TextElideMode.ElideRight,
                max(20, round(name_right - content_left)),
            )
            painter.setPen(QColor(TEXT))
            painter.drawText(
                QRectF(content_left, y + 3, name_right - content_left, 22),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                name,
            )
            painter.setFont(name_font)
            painter.setPen(QColor(TEXT if selected else TEXT_MUTED))
            painter.drawText(
                QRectF(content_right - value_width, y + 3, value_width, 22),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                format_duration(node.milliseconds),
            )

            track = QRectF(
                content_left,
                y + row_height - 17,
                max(42.0, content_right - content_left),
                7,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            track_color = QColor(BORDER_STRONG)
            track_color.setAlpha(145)
            painter.setBrush(track_color)
            painter.drawRoundedRect(track, 3.5, 3.5)
            fill = QRectF(
                track.x(),
                track.y(),
                track.width() * (node.milliseconds / maximum),
                track.height(),
            )
            painter.setBrush(fill_color)
            painter.drawRoundedRect(fill, 3.5, 3.5)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        hovered = next(
            (node for rect, node in self._rows if rect.contains(event.position())),
            None,
        )
        hovered_id = hovered.user_id if hovered else None
        if hovered_id != self._hovered_id:
            self._hovered_id = hovered_id
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if hovered is not None
                else Qt.CursorShape.ArrowCursor
            )
            self.friend_hovered.emit(
                (
                    f"{format_duration(hovered.milliseconds)} with "
                    f"{hovered.display_name} · "
                    f"{hovered.sessions} session"
                    f"{'s' if hovered.sessions != 1 else ''}"
                )
                if hovered is not None
                else ""
            )
            self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802 - Qt API
        self._hovered_id = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.friend_hovered.emit("")
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
    group_selected = Signal(int)
    selection_cleared = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = FriendMapData(tuple(), tuple())
        self._nodes: tuple[FriendMapNode, ...] = tuple()
        self._links: tuple[FriendMapLink, ...] = tuple()
        self._measured_links: tuple[FriendMapLink, ...] = tuple()
        self._groups: dict[str, int] = {}
        self._introductions: dict[str, FriendIntroduction] = {}
        self._root_children: set[str] = set()
        self._root_position = QPointF()
        self._positions: dict[str, QPointF] = {}
        self._node_rects: dict[str, QRectF] = {}
        self._label_rects: dict[str, QRectF] = {}
        self._group_rects: dict[int, QRectF] = {}
        self._edge_segments: list[tuple[QPointF, QPointF, FriendMapLink]] = []
        self._hovered_id: str | None = None
        self._hovered_link: FriendMapLink | None = None
        self._hovered_group = 0
        self._selected_id: str | None = None
        self._selected_group = 0
        self._connection_metric = "Time overlap"
        self._color_mode = "Activity"
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
        node_limit: int | None = 20,
        connection_detail: str = "Focused",
        connection_metric: str = "Time overlap",
        color_mode: str = "Activity",
    ) -> None:
        self._data = data
        self._connection_metric = connection_metric
        self._color_mode = color_mode
        visible_nodes = data.nodes if node_limit is None else data.nodes[:node_limit]
        visible_ids = {node.user_id for node in visible_nodes}
        self._nodes = tuple(node for node in data.nodes if node.user_id in visible_ids)
        self._measured_links = tuple(
            link
            for link in data.links
            if link.source_user_id in visible_ids
            and link.target_user_id in visible_ids
        )
        self._introductions = {
            item.child_user_id: item
            for item in data.introductions
            if item.child_user_id in visible_ids
        }
        self._groups = (
            detect_friend_groups(self._nodes, self._measured_links)
            if color_mode == "Friend groups"
            else {node.user_id: 0 for node in self._nodes}
        )
        if (
            color_mode != "Friend groups"
            or self._selected_group not in self._groups.values()
        ):
            self._selected_group = 0
        if color_mode == "Origins":
            self._connection_metric = "Introduction evidence"
            self._root_children = set()
            tree_links: list[FriendMapLink] = []
            minimum_evidence = (
                0.75
                if connection_detail == "Focused"
                else 0.55
                if connection_detail == "Balanced"
                else 0.0
            )
            for node in self._nodes:
                introduction = self._introductions.get(node.user_id)
                parent_id = introduction.parent_user_id if introduction else None
                if (
                    parent_id not in visible_ids
                    or introduction.evidence_score < minimum_evidence
                ):
                    self._root_children.add(node.user_id)
                    continue
                tree_links.append(
                    FriendMapLink(
                        source_user_id=parent_id,
                        target_user_id=node.user_id,
                        milliseconds=round(introduction.evidence_score * 1000),
                        encounters=1,
                        likelihood=introduction.evidence_score,
                    )
                )
            candidates = sorted(tree_links, key=lambda link: -link.likelihood)
        else:
            self._root_children = set()
            candidates = sorted(
                self._measured_links,
                key=lambda link: -self._link_value(link),
            )
        if color_mode != "Origins" and connection_detail == "Focused" and candidates:
            strongest_value = self._link_value(candidates[0])
            candidates = [
                link
                for link in candidates
                if self._link_value(link) >= strongest_value * 0.08
            ]
        if color_mode == "Origins" or connection_detail in ("All", "All connections"):
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

    def introduction_for(self, user_id: str) -> FriendIntroduction | None:
        return self._introductions.get(user_id)

    def introduction_path_visible(self, user_id: str) -> bool:
        return any(link.target_user_id == user_id for link in self._links)

    def visible_counts(self) -> tuple[int, int]:
        return len(self._nodes), len(self._links)

    def visible_nodes(self) -> tuple[FriendMapNode, ...]:
        return self._nodes

    def group_count(self) -> int:
        return len({group_id for group_id in self._groups.values() if group_id > 0})

    def group_for(self, user_id: str) -> int:
        return self._groups.get(user_id, 0)

    def group_members(self, group_id: int) -> tuple[FriendMapNode, ...]:
        if group_id <= 0:
            return self._nodes
        return tuple(
            node
            for node in self._nodes
            if self._groups.get(node.user_id, 0) == group_id
        )

    def set_selected_group(self, group_id: int) -> None:
        available = {value for value in self._groups.values() if value > 0}
        selected = group_id if group_id in available else 0
        if selected == self._selected_group:
            return
        self._selected_group = selected
        if selected and self._selected_id not in {
            node.user_id for node in self.group_members(selected)
        }:
            self._selected_id = None
        self.update()

    def selected_group(self) -> int:
        return self._selected_group

    def node_colors(self) -> dict[str, QColor]:
        return {
            node.user_id: self._node_color(index, node)
            for index, node in enumerate(self._nodes)
        }

    def group_legend_html(self) -> str:
        entries = [
            f'<span style="color:{friend_group_color(group_id).name()}">●</span> '
            f"Group {group_id}"
            for group_id in range(1, self.group_count() + 1)
        ]
        if any(group_id == 0 for group_id in self._groups.values()):
            entries.append(
                f'<span style="color:{UNGROUPED_COLOR.name()}">●</span> Unclustered'
            )
        return " &nbsp; ".join(entries)

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
            if self._connection_metric in (
                "Co-appearance likelihood",
                "Introduction evidence",
            )
            else float(link.milliseconds)
        )

    def _layout_link_value(self, link: FriendMapLink) -> float:
        if self._color_mode == "Friend groups":
            return same_instance_strength(link)
        return self._link_value(link)

    def _node_color(self, index: int, node: FriendMapNode) -> QColor:
        if self._color_mode == "Friend groups":
            return friend_group_color(self._groups.get(node.user_id, 0))
        if self._color_mode == "Origins":
            introduction = self._introductions.get(node.user_id)
            if introduction is None or introduction.parent_user_id is None:
                return QColor("#778394")
            if introduction.evidence_score >= 0.75:
                return QColor("#65b98a")
            if introduction.evidence_score >= 0.55:
                return QColor("#55c7d8")
            return QColor("#f0b35a")
        return activity_rank_color(index + 1)

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._pan = QPointF()
        self.update()

    def zoom_in(self) -> None:
        self._zoom = min(3.2, self._zoom * 1.22)
        self.update()

    def zoom_out(self) -> None:
        self._zoom = max(0.55, self._zoom / 1.22)
        self.update()

    def focus_on_friend(self, user_id: str) -> None:
        if self._color_mode != "Origins" or user_id not in self._positions:
            return
        focused_ids = self._origin_focus_ids(user_id)
        positions = [self._positions[item] for item in focused_ids]
        positions.append(self._root_position)
        minimum_x = min(point.x() for point in positions)
        maximum_x = max(point.x() for point in positions)
        minimum_y = min(point.y() for point in positions)
        maximum_y = max(point.y() for point in positions)
        span_x = max(0.32, maximum_x - minimum_x)
        span_y = max(0.32, maximum_y - minimum_y)
        base_horizontal = max(90.0, (self.width() - 120) * 0.50)
        base_vertical = max(90.0, (self.height() - 92) * 0.50)
        self._zoom = min(
            2.5,
            max(
                0.8,
                min(
                    (self.width() - 190) / (span_x * base_horizontal),
                    (self.height() - 150) / (span_y * base_vertical),
                ),
            ),
        )
        midpoint = QPointF(
            (minimum_x + maximum_x) / 2,
            (minimum_y + maximum_y) / 2,
        )
        self._pan = QPointF(
            -midpoint.x() * base_horizontal * self._zoom,
            -midpoint.y() * base_vertical * self._zoom,
        )
        self.update()

    def _calculate_layout(self) -> None:
        self._positions = {}
        self._root_position = QPointF()
        count = len(self._nodes)
        if not count:
            return
        if self._color_mode == "Origins":
            self._calculate_origin_layout()
            return
        golden_angle = math.pi * (3 - math.sqrt(5))
        anchors: dict[str, QPointF] = {}
        ungrouped_ids: set[str] = set()
        if self._color_mode == "Friend groups":
            grouped_nodes: dict[int, list[FriendMapNode]] = {}
            for node in self._nodes:
                grouped_nodes.setdefault(
                    self._groups.get(node.user_id, 0), []
                ).append(node)
            group_ids = sorted(group_id for group_id in grouped_nodes if group_id > 0)
            group_centers = {
                group_id: QPointF(
                    math.cos(index * 2 * math.pi / len(group_ids) - math.pi / 2)
                    * 0.58,
                    math.sin(index * 2 * math.pi / len(group_ids) - math.pi / 2)
                    * 0.58,
                )
                for index, group_id in enumerate(group_ids)
            }
            for group_id in group_ids:
                members = grouped_nodes[group_id]
                center = group_centers[group_id]
                for member_index, node in enumerate(members):
                    local_radius = min(0.34, 0.045 * math.sqrt(member_index + 1))
                    local_angle = member_index * golden_angle
                    anchor = center + QPointF(
                        math.cos(local_angle) * local_radius,
                        math.sin(local_angle) * local_radius,
                    )
                    anchors[node.user_id] = anchor
                    self._positions[node.user_id] = QPointF(anchor)
            ungrouped = grouped_nodes.get(0, [])
            ungrouped_ids = {node.user_id for node in ungrouped}
            maximum_per_orbit = 28
            orbit_count = max(1, math.ceil(len(ungrouped) / maximum_per_orbit))
            orbit_sizes = [
                len(ungrouped) // orbit_count
                + (1 if orbit < len(ungrouped) % orbit_count else 0)
                for orbit in range(orbit_count)
            ]
            node_index = 0
            for orbit, orbit_size in enumerate(orbit_sizes):
                radius = max(0.68, 0.88 - orbit * 0.10)
                phase = -math.pi / 2 + orbit * (math.pi / max(orbit_size, 1))
                for slot in range(orbit_size):
                    node = ungrouped[node_index]
                    node_index += 1
                    angle = phase + slot * 2 * math.pi / orbit_size
                    anchor = QPointF(
                        math.cos(angle) * radius,
                        math.sin(angle) * radius,
                    )
                    anchors[node.user_id] = anchor
                    self._positions[node.user_id] = QPointF(anchor)
        else:
            for index, node in enumerate(self._nodes):
                radius = 0.34 + 0.48 * math.sqrt((index + 0.5) / count)
                angle = index * golden_angle - math.pi / 2
                anchor = QPointF(
                    math.cos(angle) * radius,
                    math.sin(angle) * radius,
                )
                anchors[node.user_id] = anchor
                self._positions[node.user_id] = QPointF(anchor)

        layout_links = (
            self._measured_links
            if self._color_mode == "Friend groups"
            else self._links
        )
        maximum_link = max(
            (self._layout_link_value(link) for link in layout_links), default=1
        )
        iterations = 110 if count <= 60 else 44
        exact_repulsion = count <= 72
        repulsion_scale = min(1.0, 34 / count)
        if self._color_mode == "Friend groups":
            repulsion_scale *= 0.38
        for _iteration in range(iterations):
            forces = {node.user_id: QPointF() for node in self._nodes}
            if exact_repulsion:
                for first_index, first in enumerate(self._nodes):
                    first_position = self._positions[first.user_id]
                    for second in self._nodes[first_index + 1 :]:
                        second_position = self._positions[second.user_id]
                        delta = first_position - second_position
                        distance_squared = max(0.015, delta.x() ** 2 + delta.y() ** 2)
                        distance = math.sqrt(distance_squared)
                        repulsion = 0.0048 * repulsion_scale / distance_squared
                        direction = delta / distance
                        forces[first.user_id] += direction * repulsion
                        forces[second.user_id] -= direction * repulsion

            for link in layout_links:
                first_position = self._positions[link.source_user_id]
                second_position = self._positions[link.target_user_id]
                delta = first_position - second_position
                distance = max(0.001, math.hypot(delta.x(), delta.y()))
                direction = delta / distance
                strength = math.sqrt(self._layout_link_value(link) / maximum_link)
                same_group = (
                    self._color_mode == "Friend groups"
                    and self._groups.get(link.source_user_id, 0) > 0
                    and self._groups.get(link.source_user_id)
                    == self._groups.get(link.target_user_id)
                )
                involves_ungrouped = (
                    self._color_mode == "Friend groups"
                    and (
                        link.source_user_id in ungrouped_ids
                        or link.target_user_id in ungrouped_ids
                    )
                )
                target = (
                    0.22 + (1.0 - strength) * 0.12
                    if same_group
                    else 0.42 + (1.0 - strength) * 0.16
                )
                attraction = (distance - target) * (
                    0.010 + strength * 0.016
                    if same_group
                    else 0.005 + strength * 0.009
                )
                if involves_ungrouped:
                    attraction *= 0.16
                forces[link.source_user_id] -= direction * attraction
                forces[link.target_user_id] += direction * attraction

            for node in self._nodes:
                position = self._positions[node.user_id]
                forces[node.user_id] += (
                    anchors[node.user_id] - position
                ) * (
                    0.22
                    if node.user_id in ungrouped_ids
                    else 0.075
                    if self._color_mode == "Friend groups"
                    else 0.030
                )

            for node in self._nodes:
                position = self._positions[node.user_id] + forces[node.user_id]
                distance = math.hypot(position.x(), position.y())
                if distance < 0.38:
                    position *= 0.38 / max(distance, 0.001)
                elif distance > 0.88:
                    position *= 0.88 / distance
                self._positions[node.user_id] = position

        for user_id in ungrouped_ids:
            self._positions[user_id] = (
                self._positions[user_id] * 0.12 + anchors[user_id] * 0.88
            )

    def _calculate_origin_layout(self) -> None:
        """Lay out the inferred single-parent graph as a left-to-right tree."""

        visible_ids = {node.user_id for node in self._nodes}
        active_parent = {
            link.target_user_id: link.source_user_id for link in self._links
        }
        children: dict[str | None, list[str]] = {None: []}
        for node in self._nodes:
            parent_id = active_parent.get(node.user_id)
            if parent_id not in visible_ids:
                parent_id = None
            children.setdefault(parent_id, []).append(node.user_id)
        rank = {node.user_id: index for index, node in enumerate(self._nodes)}
        for members in children.values():
            members.sort(key=lambda user_id: rank[user_id])

        depths: dict[str, int] = {}
        leaf_y: dict[str, float] = {}
        leaf_index = 0

        def visit(user_id: str, depth: int) -> float:
            nonlocal leaf_index
            depths[user_id] = depth
            descendants = children.get(user_id, [])
            if not descendants:
                value = float(leaf_index)
                leaf_index += 1
                leaf_y[user_id] = value
                return value
            child_values = [visit(child_id, depth + 1) for child_id in descendants]
            value = sum(child_values) / len(child_values)
            leaf_y[user_id] = value
            return value

        for root_child in children[None]:
            visit(root_child, 1)
        maximum_depth = max(depths.values(), default=1)
        self._root_position = QPointF(-0.82, 0.0)
        by_depth: dict[int, list[str]] = {}
        for user_id in visible_ids:
            by_depth.setdefault(depths.get(user_id, 1), []).append(user_id)
        layer_step = 1.42 / max(1, maximum_depth - 1)
        max_rows = 28 if len(self._nodes) > 60 else 34
        for depth, members in by_depth.items():
            members.sort(key=lambda user_id: leaf_y.get(user_id, 0.0))
            column_count = max(1, math.ceil(len(members) / max_rows))
            column_sizes = [
                len(members) // column_count
                + (1 if column < len(members) % column_count else 0)
                for column in range(column_count)
            ]
            layer_center = -0.62 + (depth - 1) * layer_step
            spread = min(0.22, layer_step * 0.58)
            member_index = 0
            for column, column_size in enumerate(column_sizes):
                column_offset = (
                    0.0
                    if column_count == 1
                    else -spread / 2 + column * spread / (column_count - 1)
                )
                for row in range(column_size):
                    user_id = members[member_index]
                    member_index += 1
                    y = (
                        0.0
                        if column_size == 1
                        else -0.82 + row * (1.64 / (column_size - 1))
                    )
                    self._positions[user_id] = QPointF(
                        layer_center + column_offset, y
                    )

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
        if self._color_mode == "Origins" and len(self._nodes) > 100:
            minimum, maximum_radius = 7.0, 10.5
        elif self._color_mode == "Origins" and len(self._nodes) > 60:
            minimum, maximum_radius = 7.5, 11.0
        elif self._color_mode == "Origins" and len(self._nodes) > 30:
            minimum, maximum_radius = 8.0, 13.0
        elif self._color_mode == "Origins":
            minimum, maximum_radius = 9.0, 17.0
        elif len(self._nodes) > 150:
            minimum, maximum_radius = 3.5, 9.0
        elif len(self._nodes) > 80:
            minimum, maximum_radius = 4.5, 11.0
        elif len(self._nodes) > 30:
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
        self._label_rects = {}
        self._group_rects = {}
        self._edge_segments = []
        if not self._nodes:
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "No friend activity is available for this range",
            )
            return

        focused_id = self._selected_id or self._hovered_id
        focused_ids = self._focused_ids(focused_id)
        if not focused_ids and self._hovered_link is not None:
            focused_ids.update(
                (
                    self._hovered_link.source_user_id,
                    self._hovered_link.target_user_id,
                )
            )
        center = self._screen_point(self._root_position)
        maximum_link = max((self._link_value(link) for link in self._links), default=1)
        points = {
            node.user_id: self._screen_point(self._positions[node.user_id])
            for node in self._nodes
        }
        radii = {
            node.user_id: max(
                self._node_radius(node),
                13.0
                if self._color_mode == "Origins" and node.user_id in focused_ids
                else 0.0,
            )
            for node in self._nodes
        }
        self._node_rects = {
            node.user_id: QRectF(
                points[node.user_id].x() - radii[node.user_id],
                points[node.user_id].y() - radii[node.user_id],
                radii[node.user_id] * 2,
                radii[node.user_id] * 2,
            )
            for node in self._nodes
        }
        if self._color_mode == "Friend groups":
            group_font = QFont(painter.font())
            group_font.setPointSize(7)
            group_font.setBold(True)
            painter.setFont(group_font)
            for group_id in range(1, self.group_count() + 1):
                member_rects = [
                    self._node_rects[node.user_id]
                    for node in self._nodes
                    if self._groups.get(node.user_id) == group_id
                ]
                if not member_rects:
                    continue
                bounds = QRectF(member_rects[0])
                for rect in member_rects[1:]:
                    bounds = bounds.united(rect)
                bounds = bounds.adjusted(-22, -28, 22, 22)
                self._group_rects[group_id] = bounds
                group_color = friend_group_color(group_id)
                selected_group = group_id == self._selected_group
                emphasized_group = selected_group or group_id == self._hovered_group
                subdued_group = bool(self._selected_group and not selected_group)
                fill = QColor(group_color)
                fill.setAlpha(5 if subdued_group else 28 if emphasized_group else 13)
                border = QColor(group_color)
                border.setAlpha(
                    24 if subdued_group else 190 if emphasized_group else 58
                )
                painter.setPen(QPen(border, 2.0 if emphasized_group else 1.0))
                painter.setBrush(fill)
                painter.drawRoundedRect(bounds, 22, 22)
                label_color = QColor(group_color)
                if subdued_group:
                    label_color.setAlpha(46)
                painter.setPen(label_color)
                painter.drawText(
                    bounds.adjusted(10, 4, -10, -4),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    f"GROUP {group_id}"
                    + (" · FOCUSED" if selected_group else " · CLICK TO EXPLORE"),
                )
        active_group_ids = {
            node.user_id for node in self.group_members(self._selected_group)
        } if self._selected_group else set()
        strongest_link = None
        if focused_id:
            strongest = self.strongest_connection(focused_id)
            strongest_link = strongest[1] if strongest else None

        if self._color_mode == "Origins":
            for child_id in self._root_children:
                second = points.get(child_id)
                if second is None:
                    continue
                introduction = self._introductions.get(child_id)
                known_date = introduction is not None and introduction.befriended_at is not None
                root_color = QColor("#778394")
                on_path = not focused_ids or child_id in focused_ids
                root_color.setAlpha(
                    190
                    if on_path and focused_ids
                    else 38
                    if known_date and on_path
                    else 22
                    if on_path
                    else 8
                )
                style = Qt.PenStyle.DashLine if known_date else Qt.PenStyle.DotLine
                painter.setPen(
                    QPen(
                        root_color,
                        2.2 if focused_ids and on_path else 0.9,
                        style,
                    )
                )
                path = QPainterPath(center)
                midpoint = (center.x() + second.x()) / 2
                path.cubicTo(
                    midpoint,
                    center.y(),
                    midpoint,
                    second.y(),
                    second.x(),
                    second.y(),
                )
                painter.drawPath(path)

        for link in sorted(self._links, key=self._link_value):
            first = points[link.source_user_id]
            second = points[link.target_user_id]
            ratio = self._link_visual_ratio(link, maximum_link)
            color = QColor(
                "#65b98a"
                if self._connection_metric == "Introduction evidence"
                and link.likelihood >= 0.75
                else "#f0b35a"
                if self._connection_metric == "Introduction evidence"
                and link.likelihood < 0.55
                else "#cf8cff"
                if self._connection_metric == "Co-appearance likelihood"
                else "#62c9d7"
            )
            connected = (
                link.source_user_id in focused_ids
                and link.target_user_id in focused_ids
                if self._color_mode == "Origins" and focused_ids
                else focused_id in (link.source_user_id, link.target_user_id)
                if focused_id
                else self._hovered_link == link
            )
            inside_active_group = not active_group_ids or (
                link.source_user_id in active_group_ids
                and link.target_user_id in active_group_ids
            )
            if not inside_active_group:
                color.setAlpha(4)
                width = 0.45
            elif focused_ids and not connected:
                color.setAlpha(7 if self._selected_id else 16)
                width = 0.55
            else:
                color.setAlpha(52 + round(ratio * (180 if connected else 120)))
                width = 0.8 + ratio * (4.6 if connected else 3.0)
            if (
                self._color_mode != "Origins" and link == strongest_link
            ) or link == self._hovered_link:
                color = QColor("#e6b85c")
                color.setAlpha(235)
                width += 1.5
            style = (
                Qt.PenStyle.DashLine
                if self._color_mode == "Origins" and link.likelihood < 0.75
                else Qt.PenStyle.SolidLine
            )
            painter.setPen(QPen(color, width, style))
            if self._color_mode == "Origins":
                path = QPainterPath(first)
                midpoint = (first.x() + second.x()) / 2
                path.cubicTo(
                    midpoint,
                    first.y(),
                    midpoint,
                    second.y(),
                    second.x(),
                    second.y(),
                )
                painter.drawPath(path)
                previous = path.pointAtPercent(0.0)
                for step in range(1, 13):
                    current = path.pointAtPercent(step / 12)
                    self._edge_segments.append((previous, current, link))
                    previous = current
            else:
                painter.drawLine(first, second)
                self._edge_segments.append((first, second, link))

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
            inside_active_group = (
                not active_group_ids or node.user_id in active_group_ids
            )
            radius = radii[node.user_id] * (1.10 if hovered else 1.0)
            node_rect = self._node_rects[node.user_id]
            color = self._node_color(index, node)
            if hovered:
                color = color.lighter(120)
            if not inside_active_group:
                color.setAlpha(25)
            elif not connected:
                color.setAlpha(48 if self._selected_id else 85)
            path_node = self._color_mode == "Origins" and bool(focused_ids) and connected
            halo = QColor(color)
            halo.setAlpha(92 if selected else 66 if path_node else 32 if connected else 8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(point, radius + 5, radius + 5)
            if selected:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#cf8cff"), 3.0))
                painter.drawEllipse(point, radius + 7, radius + 7)
            node_border = QColor(
                "#ffffff"
                if selected or hovered
                else "#b9f4ff"
                if path_node
                else BORDER_STRONG
            )
            painter.setPen(QPen(node_border, 2.2 if selected or hovered or path_node else 1.0))
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
        indexed_nodes = list(enumerate(self._nodes))
        indexed_nodes.sort(
            key=lambda item: (
                0
                if item[1].user_id in (self._selected_id, self._hovered_id)
                else 1
                if item[1].user_id in focused_ids
                else 2,
                item[0],
            )
        )
        for index, node in indexed_nodes:
            selected = node.user_id == self._selected_id
            hovered = node.user_id == self._hovered_id
            connected = not focused_ids or node.user_id in focused_ids
            inside_active_group = (
                not active_group_ids or node.user_id in active_group_ids
            )
            group_rank = (
                sum(
                    1
                    for previous in self._nodes[:index]
                    if previous.user_id in active_group_ids
                )
                if active_group_ids
                else index
            )
            label_limit = 24 if active_group_ids else 12
            path_node = (
                self._color_mode == "Origins"
                and bool(focused_ids)
                and node.user_id in focused_ids
            )
            if (
                (not inside_active_group or group_rank >= label_limit)
                and not selected
                and not hovered
                and not path_node
            ):
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
            if label_rect is None and path_node:
                label_rect = next(
                    (candidate for candidate in candidates if canvas.contains(candidate)),
                    None,
                )
            if label_rect is None:
                continue
            self._label_rects[node.user_id] = label_rect
            occupied.append(label_rect.adjusted(-3, -3, 3, 3))
            label_border = (
                self._node_color(index, node).lighter(125)
                if path_node
                else QColor(BORDER_STRONG)
            )
            painter.setPen(QPen(label_border, 1.6 if path_node else 1.0))
            painter.setBrush(QColor(SURFACE_RAISED))
            painter.drawRoundedRect(label_rect, 6, 6)
            label_color = QColor(TEXT)
            if not connected:
                label_color.setAlpha(58 if self._selected_id else 100)
            elif path_node:
                label_color = QColor("#ffffff")
            painter.setPen(label_color)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)

    def _node_at(self, position: QPointF) -> str | None:
        for user_id, rect in reversed(tuple(self._label_rects.items())):
            if rect.contains(position):
                return user_id
        for user_id, rect in reversed(tuple(self._node_rects.items())):
            if rect.contains(position):
                return user_id
        return None

    def _origin_path_ids(self, user_id: str) -> set[str]:
        active_parent = {
            link.target_user_id: link.source_user_id for link in self._links
        }
        path: set[str] = set()
        current_id: str | None = user_id
        while current_id is not None and current_id not in path:
            path.add(current_id)
            current_id = active_parent.get(current_id)
        return path

    def _origin_focus_ids(self, user_id: str) -> set[str]:
        focused = self._origin_path_ids(user_id)
        focused.update(
            link.target_user_id
            for link in self._links
            if link.source_user_id == user_id
        )
        return focused

    def _focused_ids(self, focused_id: str | None) -> set[str]:
        if focused_id is None:
            return set()
        if self._color_mode == "Origins":
            return self._origin_focus_ids(focused_id)
        focused = {focused_id}
        for link in self._links:
            if link.source_user_id == focused_id:
                focused.add(link.target_user_id)
            elif link.target_user_id == focused_id:
                focused.add(link.source_user_id)
        return focused

    def _group_at(self, position: QPointF) -> int:
        matches = [
            (rect.width() * rect.height(), group_id)
            for group_id, rect in self._group_rects.items()
            if rect.contains(position)
        ]
        return min(matches, default=(0.0, 0))[1]

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
        hovered_group = (
            self._group_at(event.position())
            if hovered is None and hovered_link is None
            else 0
        )
        if (
            hovered != self._hovered_id
            or hovered_link != self._hovered_link
            or hovered_group != self._hovered_group
        ):
            self._hovered_id = hovered
            self._hovered_link = hovered_link
            self._hovered_group = hovered_group
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if hovered or hovered_link or hovered_group
                else Qt.CursorShape.OpenHandCursor
            )
            self.update()
        if hovered_group:
            members = self.group_members(hovered_group)
            total_time = sum(node.milliseconds for node in members)
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"<b>Group {hovered_group}</b><br>"
                f"{len(members)} people · {format_duration(total_time)} combined time"
                "<br>Click to explore this group",
                self,
            )
            return
        if hovered is None and hovered_link is None:
            QToolTip.hideText()
            return
        if hovered_link is not None:
            nodes = {node.user_id: node for node in self._nodes}
            first = nodes[hovered_link.source_user_id]
            second = nodes[hovered_link.target_user_id]
            if self._connection_metric == "Introduction evidence":
                metric_name = "Introduction evidence score"
                value = f"{hovered_link.likelihood * 100:.0f} / 100"
            elif self._connection_metric == "Co-appearance likelihood":
                metric_name = "Co-appearance likelihood"
                value = f"{hovered_link.likelihood:.0%}"
            else:
                metric_name = "Time overlap"
                value = format_duration(hovered_link.milliseconds)
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"<b>{first.display_name} ↔ {second.display_name}</b><br><br>"
                f"{metric_name}<br><b>{value}</b>"
                + (
                    f"<br><br>{hovered_link.encounters} measured same-instance "
                    f"encounter{'s' if hovered_link.encounters != 1 else ''} contribute "
                    "to group detection"
                    if self._color_mode == "Friend groups"
                    else ""
                ),
                self,
            )
            return
        node = next(item for item in self._nodes if item.user_id == hovered)
        relationships = self.measured_relationship_count(hovered)
        rank = self.rank_for(hovered)
        group_id = self.group_for(hovered)
        group_line = (
            f"<br>Friend group {group_id} · inferred from repeated same-instance overlap"
            if self._color_mode == "Friend groups" and group_id > 0
            else "<br>Unclustered · not enough same-instance evidence"
            if self._color_mode == "Friend groups"
            else ""
        )
        if self._color_mode == "Origins":
            introduction = self._introductions.get(hovered)
            names = {item.user_id: item.display_name for item in self._nodes}
            parent_name = (
                names.get(introduction.parent_user_id, "an earlier friend")
                if introduction and introduction.parent_user_id
                else "YOU / no credible introducer observed"
            )
            evidence_score = (
                f"{introduction.evidence_score * 100:.0f} / 100"
                if introduction and introduction.parent_user_id
                else "unresolved"
            )
            evidence = introduction.evidence if introduction else "No history evidence available."
            if (
                introduction
                and introduction.parent_user_id
                and not self.introduction_path_visible(hovered)
            ):
                evidence += " Choose Balanced or All connections to display this path."
            friend_since = (
                format_local_datetime(introduction.befriended_at)
                if introduction and introduction.befriended_at
                else "Date unavailable"
            )
            QToolTip.showText(
                event.globalPosition().toPoint() + QPoint(18, 20),
                f"<div style='min-width:260px'><b>{node.display_name}</b><br>"
                f"<span style='color:#9fb1c8'>Possible path via</span> {parent_name}<br>"
                f"<span style='color:#9fb1c8'>Evidence score</span> {evidence_score}<br>"
                f"<span style='color:#9fb1c8'>Friendship recorded</span> {friend_since}"
                f"<br><br>{evidence}<br><br>"
                "<span style='color:#9fb1c8'>The highlighted route shows the possible path "
                "back to YOU and directly connected branches.</span></div>",
                self,
            )
            return
        QToolTip.showText(
            event.globalPosition().toPoint() + QPoint(18, 20),
            f"<b>{node.display_name}</b><br>"
            f"{format_duration(node.milliseconds)} recorded around you<br>"
            f"Rank #{rank} · {relationships} measured relationship"
            f"{'s' if relationships != 1 else ''}{group_line}<br>"
            f"{node.sessions} shared session{'s' if node.sessions != 1 else ''}",
            self,
        )

    def leaveEvent(self, _event) -> None:  # noqa: N802 - Qt API
        self._hovered_id = None
        self._hovered_link = None
        self._hovered_group = 0
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
            group_id = self._group_at(event.position())
            self._selected_group = group_id
            self.group_selected.emit(group_id)
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
