"""界面样式库：米白扁平 QSS、终端暗色色板、字体回退链与窗口通用按钮。"""

import ctypes
import os
import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QPushButton


_PROJECT_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..",
)
ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "index.ico")
SETTING_ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "setting.png")
SHOW_ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "show.png")
HIDE_ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "hide.png")
CHECKMARK_SVG = os.path.join(_PROJECT_ROOT, "assets", "icon", "checkmark.svg").replace("\\", "/")


_COLOR_BG         = "#f5f2ed"
_COLOR_SURFACE     = "#ffffff"
_COLOR_BORDER      = "#ddd"
_COLOR_BORDER_DARK = "#ccc"
_COLOR_BORDER_FOCUS = "#aaa"
_COLOR_TEXT        = "#333"
_COLOR_TEXT_TITLE  = "#444"
_COLOR_TEXT_SEC    = "#666"
_COLOR_TEXT_MUTED  = "#999"
_COLOR_ACCENT      = "#4a90d9"
_COLOR_DANGER      = "#e81123"
_COLOR_WARNING     = "#e67e22"
_COLOR_HOVER_BG   = "#e0e0e0"


_COLOR_BUBBLE_USER        = "#d4edda"
_COLOR_BUBBLE_USER_BORDER = "#b8d4be"
_COLOR_BUBBLE_PET         = "#d6eaf8"
_COLOR_BUBBLE_PET_BORDER  = "#b8cfe0"


WINDOW_RADIUS = 10


WINDOW_QSS = """
QWidget#FlatWindow {
    background: """ + _COLOR_BG + """;
}
"""


PANEL_QSS = """
QGroupBox {
    font-size: 12px;
    font-weight: bold;
    color: """ + _COLOR_TEXT_SEC + """;
    background: transparent;
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 8px;
    margin-top: 10px;
    padding: 14px 8px 8px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: """ + _COLOR_TEXT_SEC + """;
}
"""


_BTN_BASE = """
    background: """ + _COLOR_SURFACE + """;
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 6px;
    padding: 4px 12px;
    font-size: 12px;
    color: """ + _COLOR_TEXT + """;
"""

BUTTON_QSS = """
QPushButton {
""" + _BTN_BASE + """
}
QPushButton:hover {
    background: #e8e8e8;
    border-color: """ + _COLOR_BORDER_FOCUS + """;
}
QPushButton:pressed {
    background: #d8d8d8;
}
QPushButton:checked {
    background: """ + _COLOR_ACCENT + """;
    color: #fff;
    border-color: """ + _COLOR_ACCENT + """;
}
QPushButton:disabled {
    background: #f0f0f0;
    color: #bbb;
    border-color: #e0e0e0;
}
"""

BUTTON_PRIMARY_QSS = """
QPushButton {
""" + _BTN_BASE + """
    background: """ + _COLOR_ACCENT + """;
    color: #fff;
    border-color: """ + _COLOR_ACCENT + """;
}
QPushButton:hover {
    background: #3a7bc8;
}
QPushButton:pressed {
    background: #2d6ab5;
}
QPushButton:disabled {
    background: #e0e0e0;
    color: #aaa;
    border-color: #d0d0d0;
}
"""


BUTTON_DANGER_QSS = """
QPushButton {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 14px;
    color: #888;
}
QPushButton:hover {
    background: #e81123;
    color: #fff;
}
"""


INPUT_QSS = """
QLineEdit, QSpinBox {
    background: """ + _COLOR_SURFACE + """;
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 6px;
    padding: 3px 8px;
    min-height: 20px;
    font-size: 12px;
    color: """ + _COLOR_TEXT + """;
}
QLineEdit:focus, QSpinBox:focus {
    border-color: """ + _COLOR_BORDER_FOCUS + """;
}
QLineEdit:disabled, QSpinBox:disabled {
    background: #f5f5f5;
    color: #bbb;
    border-color: #e0e0e0;
}
"""


INPUT_HIGHLIGHT_QSS = """
QLineEdit {
    background: #f8fafc;
    border: 1.5px solid #d0e3ff;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 12px;
    color: """ + _COLOR_TEXT + """;
}
QLineEdit:focus {
    border-color: """ + _COLOR_ACCENT + """;
    background: """ + _COLOR_SURFACE + """;
}
QLineEdit:disabled {
    background: #f5f5f5;
    color: #bbb;
    border-color: #e0e0e0;
}
"""


COMBOBOX_QSS = """
QComboBox {
    background: #f8fafc;
    border: 1.5px solid #d0e3ff;
    border-radius: 6px;
    padding: 5px 10px;
    min-height: 20px;
    font-size: 12px;
    color: """ + _COLOR_TEXT + """;
    min-width: 64px;
}
QComboBox:hover {
    border-color: """ + _COLOR_BORDER_FOCUS + """;
}
QComboBox:focus {
    border-color: """ + _COLOR_ACCENT + """;
    background: """ + _COLOR_SURFACE + """;
}
QComboBox:disabled {
    background: #f5f5f5;
    color: #bbb;
    border-color: #e0e0e0;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: right center;
    width: 16px;
    border: none;
}
QComboBox QAbstractItemView {
    background: """ + _COLOR_SURFACE + """;
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 4px;
    selection-background-color: #e0e0e0;
    color: """ + _COLOR_TEXT + """;
    font-size: 12px;
    outline: none;
}
"""


TEXTEDIT_QSS = """
QTextEdit, QPlainTextEdit {
    background: """ + _COLOR_SURFACE + """;
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 8px;
    padding: 6px 8px;
    font-family: "Consolas", "Microsoft YaHei", monospace;
    font-size: 12px;
    color: """ + _COLOR_TEXT + """;
    selection-background-color: #b3d9ff;
}
QTextEdit:focus, QPlainTextEdit:focus {
    border-color: """ + _COLOR_BORDER_FOCUS + """;
}
QTextEdit:disabled, QPlainTextEdit:disabled {
    background: #f5f5f5;
    color: #bbb;
    border-color: #e0e0e0;
}
"""


SCROLLBAR_QSS = """
QScrollBar:vertical {
    background: """ + _COLOR_BG + """;
    width: 8px;
    border: none;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: """ + _COLOR_BORDER_DARK + """;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background: """ + _COLOR_BORDER_FOCUS + """;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
    border: none;
    background: """ + _COLOR_BG + """;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}
QScrollBar:horizontal {
    background: """ + _COLOR_BG + """;
    height: 8px;
    border: none;
    margin: 0px;
}
QScrollBar::handle:horizontal {
    background: """ + _COLOR_BORDER_DARK + """;
    border-radius: 4px;
    min-width: 24px;
}
QScrollBar::handle:horizontal:hover {
    background: """ + _COLOR_BORDER_FOCUS + """;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
    border: none;
    background: """ + _COLOR_BG + """;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
}
"""


LIST_QSS = """
QListWidget {
    background: """ + _COLOR_SURFACE + """;
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 6px;
    font-family: "Consolas", "Microsoft YaHei", monospace;
    font-size: 11px;
    color: """ + _COLOR_TEXT + """;
    outline: none;
}
QListWidget::item {
    padding: 2px 4px;
}
QListWidget::item:selected {
    background: #e0e0e0;
    color: """ + _COLOR_TEXT + """;
}
"""


MENU_QSS = """
QMenu {
    background: """ + _COLOR_SURFACE + """;
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 8px;
    padding: 6px 4px;
}
QMenu::item {
    padding: 7px 32px 7px 14px;
    font-size: 13px;
    color: """ + _COLOR_TEXT + """;
    border-radius: 4px;
    background: transparent;
}
QMenu::item:selected {
    background: #e8e8e8;
}
QMenu::item:disabled {
    color: #bbb;
}
QMenu::separator {
    height: 1px;
    background: """ + _COLOR_BORDER + """;
    margin: 4px 10px;
}
"""


CHECKBOX_QSS = """
QCheckBox {
    font-size: 12px;
    color: """ + _COLOR_TEXT + """;
    spacing: 6px;
}
QCheckBox:disabled {
    color: #bbb;
}
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    margin: 3px 0;
    background: """ + _COLOR_SURFACE + """;
    border: 1px solid """ + _COLOR_BORDER_FOCUS + """;
    border-radius: 3px;
}
QCheckBox::indicator:disabled {
    background: #f5f5f5;
    border-color: #e0e0e0;
}
QCheckBox::indicator:checked {
    background: """ + _COLOR_SURFACE + """;
    image: url(""" + CHECKMARK_SVG + """);
    border-color: """ + _COLOR_ACCENT + """;
    border-width: 2px;
}
QCheckBox::indicator:hover {
    border-color: """ + _COLOR_ACCENT + """;
}
"""


LABEL_SECONDARY_QSS = """
    font-size: 12px;
    color: """ + _COLOR_TEXT_SEC + """;
"""

LABEL_MONO_QSS = """
    font-family: "Consolas", "Microsoft YaHei", monospace;
    font-size: 12px;
    color: """ + _COLOR_TEXT + """;
"""


TITLE_LABEL_QSS = f"font-size:13px; color:{_COLOR_TEXT_TITLE}; font-weight:bold; background:transparent;"


SEPARATOR_QSS = """
QFrame[frameShape="4"] {
    color: """ + _COLOR_BORDER + """;
}
"""

# 标签栏

TAB_BAR_QSS = """
QTabWidget::pane {
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 6px;
    background: """ + _COLOR_SURFACE + """;
    padding: 4px;
    top: -1px;
}
QTabBar::tab {
    background: """ + _COLOR_BG + """;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 0;
    font-size: 12px;
    color: """ + _COLOR_TEXT_SEC + """;
}
QTabBar::tab:selected {
    background: """ + _COLOR_BG + """;
    color: """ + _COLOR_TEXT + """;
    font-weight: bold;
    border-bottom: 2px solid """ + _COLOR_ACCENT + """;
}
QTabBar::tab:hover:!selected {
    background: #e8e8e8;
}
"""

# 滚动容器透明：窗口自绘圆角背景
SCROLL_AREA_TRANSPARENT_QSS = """
QScrollArea {
    background: transparent;
    border: none;
}
QScrollArea > QWidget {
    background: transparent;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
"""


# 宠物右侧按钮列的纵向偏移（相对宠物窗口顶部）
BUBBLE_ROW_CHAT = 5
BUBBLE_ROW_FEED = 45
BUBBLE_ROW_MUSIC = 85


def bubble_column_y(pet_top: int, row_offset: int, height: int, geo) -> int:
    """按钮列中单个气泡的纵坐标。

    列底按 BUBBLE_ROW_MUSIC + 本气泡高度估算：超出可用区时整列上移，
    各自夹取会让三个气泡落到同一位置互相遮挡。
    """
    y = pet_top + row_offset
    if geo is None:
        return y
    overflow = pet_top + BUBBLE_ROW_MUSIC + height - geo.bottom()
    if overflow > 0:
        y -= overflow
    return max(geo.top(), y)


def make_title_button(text: str, hover_color: str,
                      base_color: str = _COLOR_TEXT_MUTED,
                      text_color: str = "#fff",
                      font_size: str = "18px") -> QPushButton:
    """创建无边框面板窗口标题栏上的标准按钮（关闭 / 最小化）。"""
    btn = QPushButton(text)
    btn.setFixedSize(36, 36)
    btn.setStyleSheet(f"""
        QPushButton {{ background: transparent; border: none; border-radius: 18px;
                     font-size: {font_size}; color: {base_color}; }}
        QPushButton:hover {{ background: {hover_color}; color: {text_color}; }}
    """)
    return btn


_LIGHT_BLUE_HOVER = "#87CEFA"


def make_minimize_button(window) -> QPushButton:
    """创建淡蓝色 hover 的最小化按钮，点击后最小化指定窗口。"""
    btn = make_title_button("−", _LIGHT_BLUE_HOVER, font_size="16px")
    btn.clicked.connect(window.showMinimized)
    return btn


def make_close_button(window, on_close=None) -> QPushButton:
    """创建红色 hover 的关闭按钮，点击后关闭窗口或调用自定义回调。"""
    btn = make_title_button("✕", _COLOR_DANGER)
    if on_close is None:
        btn.clicked.connect(window.close)
    else:
        btn.clicked.connect(on_close)
    return btn


def ensure_taskbar_icon(window):
    """强制无边框顶层窗口在 Windows 任务栏显示图标。

    需要在窗口 show() 之后调用（通常在 showEvent 中）。
    """
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
    except Exception:
        return
    if not hwnd:
        return
    GWL_EXSTYLE = -20
    WS_EX_APPWINDOW = 0x00040000
    try:
        exstyle = ctypes.windll.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, exstyle | WS_EX_APPWINDOW)
    except Exception:
        pass


# 终端与确认卡片暗色色板，取值与上方米白配色互不共用

TERMINAL_BG = "#17191a"
TERMINAL_PANEL = "#1f2224"
TERMINAL_CODE_BG = "#101213"
TERMINAL_BORDER = "#2f3437"
TERMINAL_HOVER = "#262a2c"

TERMINAL_TEXT = "#e8e5de"
TERMINAL_STDOUT = "#c9c6bf"
TERMINAL_STDERR = "#e08b86"
TERMINAL_MUTED = "#8a9195"
TERMINAL_MUTED_LIGHT = "#a8adaf"

TERMINAL_ACCENT = "#5fbfa8"
TERMINAL_ACCENT_DIM = "#3f7f72"
TERMINAL_ACCENT_TEXT = "#0f1413"
TERMINAL_USER = "#d9a441"
TERMINAL_DANGER = "#d9534f"
TERMINAL_IDLE = "#4a4f52"

TERMINAL_MONO_FONTS = ("JetBrains Mono", "Cascadia Mono", "Consolas", "Menlo",
                    "DejaVu Sans Mono", "Noto Sans Mono")
TERMINAL_UI_FONTS = ("Segoe UI", "PingFang SC", "Microsoft YaHei UI", "Noto Sans CJK SC")

TERMINAL_SOURCE_LABEL_USER = "[用户] $"
TERMINAL_SOURCE_LABEL_MODEL = "[恋恋] $"

TERMINAL_WINDOW_RADIUS = 8
TERMINAL_CONTROL_RADIUS = 6
TERMINAL_CARD_RADIUS = 10
TERMINAL_BLOCK_GAP = 14


def terminal_source_color(source: str) -> str:
    """来源色：用户琥珀，恋恋青绿。"""
    return TERMINAL_USER if source == "user" else TERMINAL_ACCENT


def terminal_source_label(source: str) -> str:
    """来源标记文本。"""
    return TERMINAL_SOURCE_LABEL_USER if source == "user" else TERMINAL_SOURCE_LABEL_MODEL


def terminal_mono_font(size: int = 12, bold: bool = False) -> QFont:
    """等宽字体，按回退链取第一个可用项。"""
    font = QFont()
    font.setFamilies(list(TERMINAL_MONO_FONTS))
    font.setPointSize(size)
    font.setBold(bold)
    return font


def terminal_ui_font(size: int = 12, bold: bool = False) -> QFont:
    """界面字体，按回退链取第一个可用项。"""
    font = QFont()
    font.setFamilies(list(TERMINAL_UI_FONTS))
    font.setPointSize(size)
    font.setBold(bold)
    return font
