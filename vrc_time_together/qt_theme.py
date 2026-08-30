from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

BG = "#090d12"
SIDEBAR = "#0d1219"
SURFACE = "#121923"
SURFACE_RAISED = "#17212d"
SURFACE_HOVER = "#1d2938"
BORDER = "#263242"
BORDER_STRONG = "#34445a"
TEXT = "#edf2f7"
TEXT_MUTED = "#8d9aab"
TEXT_SUBTLE = "#657286"
ACCENT = "#8174ff"
ACCENT_HOVER = "#958aff"
ACCENT_SOFT = "#29264d"
SUCCESS = "#56c596"
WARNING = "#e6b85c"
DANGER = "#e57878"
GRID = "#202b39"

SERIES_COLORS = (
    "#958aff",
    "#5ba7ff",
    "#56c596",
    "#e6b85c",
    "#ef7d76",
    "#cf8cff",
    "#62c9d7",
    "#9acb65",
    "#f59c5c",
    "#e887b7",
)


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE_RAISED))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(SURFACE_RAISED))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)


STYLESHEET = f"""
* {{
    outline: none;
}}
QMainWindow, QDialog, QWidget#Root {{
    background: {BG};
    color: {TEXT};
}}
QWidget#Sidebar {{
    background: {SIDEBAR};
    border-right: 1px solid {BORDER};
}}
QWidget#TopBar {{
    background: {BG};
    border-bottom: 1px solid {BORDER};
}}
QFrame#Card, QFrame#Panel {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#DetailStrip {{
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#InlineDatePanel {{
    background: {SIDEBAR};
    border-bottom: 1px solid {BORDER_STRONG};
}}
QFrame#TopFriendRow {{
    background: {SURFACE_RAISED};
    border: 1px solid transparent;
    border-radius: 7px;
}}
QFrame#TopFriendRow:hover {{
    background: {SURFACE_HOVER};
    border-color: {BORDER};
}}
QLabel {{
    color: {TEXT};
    background: transparent;
}}
QLabel#Brand {{
    font-size: 17px;
    font-weight: 700;
}}
QLabel#PageTitle {{
    font-size: 24px;
    font-weight: 700;
}}
QLabel#SectionTitle {{
    font-size: 13px;
    font-weight: 650;
}}
QLabel#Muted {{
    color: {TEXT_MUTED};
}}
QLabel#Subtle {{
    color: {TEXT_SUBTLE};
    font-size: 9px;
}}
QLabel#MetricLabel {{
    color: {TEXT_MUTED};
    font-size: 9px;
    font-weight: 650;
}}
QLabel#MetricValue {{
    font-size: 23px;
    font-weight: 700;
}}
QLabel#MetricDetail {{
    color: {TEXT_MUTED};
    font-size: 9px;
}}
QLabel#MapInspectorValue {{
    color: {TEXT};
    font-size: 14px;
    font-weight: 700;
}}
QLabel#MapInspectorLabel {{
    color: {TEXT_MUTED};
    font-size: 9px;
}}
QLabel#KpiInfo {{
    color: {TEXT_MUTED};
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    font-size: 10px;
    font-weight: 700;
}}
QLabel#KpiInfo:hover {{
    color: white;
    background: {ACCENT_SOFT};
    border-color: {ACCENT};
}}
QLabel#ChartLegend {{
    color: {TEXT_MUTED};
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 7px 10px;
}}
QLabel#RankBadge {{
    color: {ACCENT_HOVER};
    background: {ACCENT_SOFT};
    border: 1px solid #3a356b;
    border-radius: 12px;
    font-size: 9px;
    font-weight: 700;
}}
QLabel#TopFriendName {{
    font-weight: 650;
}}
QLabel#TopFriendDuration {{
    color: {TEXT};
    font-size: 12px;
    font-weight: 700;
}}
QLabel#PrivacyChip {{
    color: {SUCCESS};
    background: #14271f;
    border: 1px solid #244638;
    border-radius: 9px;
    padding: 3px 8px;
    font-size: 9px;
    font-weight: 600;
}}
QPushButton {{
    min-height: 32px;
    padding: 0 13px;
    color: {TEXT};
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER};
    border-radius: 7px;
    font-weight: 550;
}}
QPushButton:hover {{
    background: {SURFACE_HOVER};
    border-color: {BORDER_STRONG};
}}
QPushButton:pressed {{
    background: {ACCENT_SOFT};
}}
QPushButton:disabled {{
    color: {TEXT_SUBTLE};
    background: {SURFACE};
}}
QPushButton#PrimaryButton {{
    color: white;
    background: {ACCENT};
    border-color: {ACCENT};
    font-weight: 650;
}}
QPushButton#PrimaryButton:hover {{
    background: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton#RangeButton {{
    min-width: 285px;
    min-height: 36px;
    padding: 0 14px;
    text-align: left;
    color: {TEXT};
    background: {SURFACE};
    border-color: {BORDER_STRONG};
}}
QPushButton#RangeButton:hover {{
    background: {SURFACE_RAISED};
    border-color: {ACCENT};
}}
QPushButton#RangeButton:checked {{
    color: white;
    background: {ACCENT_SOFT};
    border-color: {ACCENT};
}}
QPushButton#QuietButton {{
    min-height: 28px;
    color: {TEXT_MUTED};
    background: transparent;
    border-color: transparent;
}}
QPushButton#QuietButton:hover {{
    color: {TEXT};
    background: {SURFACE_RAISED};
}}
QPushButton#MapSegmentButton {{
    min-height: 28px;
    padding: 0 10px;
    color: {TEXT_MUTED};
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-right: 0;
    border-radius: 0;
    font-size: 9px;
    font-weight: 650;
}}
QPushButton#MapSegmentButton[segmentPosition="first"] {{
    border-top-left-radius: 7px;
    border-bottom-left-radius: 7px;
}}
QPushButton#MapSegmentButton[segmentPosition="last"] {{
    border-right: 1px solid {BORDER};
    border-top-right-radius: 7px;
    border-bottom-right-radius: 7px;
}}
QPushButton#MapSegmentButton:hover {{
    color: {TEXT};
    background: {SURFACE_HOVER};
}}
QPushButton#MapSegmentButton:checked {{
    color: white;
    background: {ACCENT_SOFT};
    border-color: {ACCENT};
    border-right: 1px solid {ACCENT};
}}
QPushButton#PresetButton {{
    min-width: 94px;
    min-height: 34px;
    padding: 0 8px;
}}
QPushButton#PresetButton:hover {{
    color: white;
    background: {ACCENT_SOFT};
    border-color: {ACCENT};
}}
QPushButton#NavButton {{
    min-height: 38px;
    padding: 0 14px;
    text-align: left;
    color: {TEXT_MUTED};
    background: transparent;
    border: 0;
    border-radius: 7px;
    font-weight: 600;
}}
QPushButton#NavButton:hover {{
    color: {TEXT};
    background: {SURFACE_RAISED};
}}
QPushButton#NavButton:checked {{
    color: white;
    background: {ACCENT_SOFT};
    border-left: 3px solid {ACCENT};
    padding-left: 11px;
}}
QLineEdit, QComboBox, QDateEdit {{
    min-height: 32px;
    padding: 0 10px;
    color: {TEXT};
    selection-background-color: {ACCENT};
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER};
    border-radius: 7px;
}}
QLineEdit:hover, QComboBox:hover, QDateEdit:hover {{
    border-color: {BORDER_STRONG};
}}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down, QDateEdit::drop-down {{
    width: 26px;
    border: 0;
}}
QComboBox QAbstractItemView {{
    color: {TEXT};
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER_STRONG};
    selection-background-color: {ACCENT_SOFT};
    padding: 4px;
}}
QCalendarWidget QWidget {{
    alternate-background-color: {SURFACE_RAISED};
}}
QCalendarWidget QAbstractItemView:enabled {{
    color: {TEXT};
    background: {SURFACE};
    selection-background-color: {ACCENT};
    selection-color: white;
}}
QTableView {{
    color: {TEXT};
    background: {SURFACE};
    alternate-background-color: #151e29;
    border: 0;
    gridline-color: {BORDER};
    selection-background-color: {ACCENT_SOFT};
    selection-color: white;
}}
QTableView::item {{
    padding: 7px 8px;
    border-bottom: 1px solid #1f2a37;
}}
QTableView::item:hover {{
    background: {SURFACE_HOVER};
}}
QHeaderView::section {{
    min-height: 34px;
    padding: 0 8px;
    color: {TEXT_MUTED};
    background: {SURFACE_RAISED};
    border: 0;
    border-bottom: 1px solid {BORDER};
    font-size: 9px;
    font-weight: 650;
}}
QListWidget {{
    color: {TEXT};
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 5px;
}}
QListWidget::item {{
    min-height: 34px;
    padding: 3px 7px;
    border-radius: 6px;
}}
QListWidget::item:hover {{
    background: {SURFACE_HOVER};
}}
QListWidget::item:selected {{
    background: {ACCENT_SOFT};
}}
QCheckBox {{
    color: {TEXT};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 4px;
    background: {SURFACE_RAISED};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QScrollBar:vertical {{
    width: 10px;
    margin: 2px;
    background: transparent;
}}
QScrollBar::handle:vertical {{
    min-height: 28px;
    background: {BORDER_STRONG};
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{
    background: {TEXT_SUBTLE};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    height: 0;
    background: transparent;
}}
QScrollBar:horizontal {{
    height: 10px;
    background: transparent;
}}
QScrollBar::handle:horizontal {{
    min-width: 28px;
    background: {BORDER_STRONG};
    border-radius: 5px;
}}
QProgressBar {{
    min-height: 3px;
    max-height: 3px;
    background: {SURFACE_RAISED};
    border: 0;
}}
QProgressBar::chunk {{
    background: {ACCENT};
}}
QToolTip {{
    color: {TEXT};
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER_STRONG};
    padding: 6px;
}}
QStatusBar {{
    color: {TEXT_MUTED};
    background: {SIDEBAR};
    border-top: 1px solid {BORDER};
}}
"""
