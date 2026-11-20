"""界面样式兼容层：转发令牌与公共 QSS，保留旧常量名。"""

import ctypes
import os
import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QPushButton

from pet import theme
from pet.ui.qss import (BUTTON_DANGER_QSS, BUTTON_PRIMARY_QSS, BUTTON_QSS,
                        CHECKBOX_QSS, CHECKMARK_SVG, COMBOBOX_QSS,
                        HELP_BADGE_QSS, INPUT_HIGHLIGHT_QSS, INPUT_QSS,
                        LABEL_MONO_QSS, LABEL_SECONDARY_QSS, LIST_QSS,
                        MENU_QSS, MENU_SOLID_QSS, PANEL_QSS,
                        SCROLL_AREA_TRANSPARENT_QSS,
                        SCROLLBAR_QSS, SEPARATOR_QSS, TABLE_QSS, TAB_BAR_QSS,
                        TEXTEDIT_QSS, TITLE_LABEL_QSS, TOOLTIP_QSS,
                        WINDOW_QSS)


_PROJECT_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..",
)
ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "index.ico")
SETTING_ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "setting.png")
SHOW_ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "show.png")
HIDE_ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "hide.png")
LOG_ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "ink", "log.svg")
MEMORY_ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "ink", "memory.svg")
DEBUG_ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "ink", "debug.svg")
HISTORY_ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "ink", "history.svg")


_COLOR_BG = theme.PAPER
_COLOR_SURFACE = theme.SURFACE
_COLOR_BORDER = theme.INK
_COLOR_BORDER_DARK = theme.INK
_COLOR_BORDER_FOCUS = theme.PRIMARY
_COLOR_TEXT = theme.INK
_COLOR_TEXT_TITLE = theme.INK
_COLOR_TEXT_SEC = theme.INK_SOFT
_COLOR_TEXT_MUTED = theme.INK_MUTED
_COLOR_ACCENT = theme.PRIMARY
_COLOR_DANGER = theme.DANGER
_COLOR_WARNING = theme.ACCENT_DEEP
_COLOR_HOVER_BG = theme.PRIMARY_SOFT

_COLOR_BUBBLE_USER = theme.ACCENT_SOFT
_COLOR_BUBBLE_USER_BORDER = theme.INK
_COLOR_BUBBLE_PET = theme.SURFACE_WARM
_COLOR_BUBBLE_PET_BORDER = theme.INK


WINDOW_RADIUS = theme.RADIUS_XL


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
    """创建无边框面板窗口标题栏上的圆形按钮（关闭 / 最小化）。"""
    btn = QPushButton(text)
    btn.setFixedSize(34, 34)
    btn.setStyleSheet(f"""
        QPushButton {{ background: transparent; border: {theme.BORDER}px solid transparent;
                     border-radius: 17px; font-size: {font_size}; color: {base_color}; }}
        QPushButton:hover {{ background: {hover_color}; border-color: {theme.INK};
                     color: {text_color}; }}
    """)
    return btn


_LIGHT_BLUE_HOVER = theme.PRIMARY_SOFT


def make_minimize_button(window) -> QPushButton:
    """创建主色 hover 的最小化按钮，点击后最小化指定窗口。"""
    btn = make_title_button("−", _LIGHT_BLUE_HOVER,
                            base_color=theme.INK_SOFT, text_color=theme.INK,
                            font_size="16px")
    btn.clicked.connect(window.showMinimized)
    return btn


def make_close_button(window, on_close=None) -> QPushButton:
    """创建红色 hover 的关闭按钮，点击后关闭窗口或调用自定义回调。"""
    btn = make_title_button("✕", theme.DANGER, font_size="14px")
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


# 终端暗色色板：值来自 theme，保持与手绘皮肤分离


TERMINAL_BG = theme.TERMINAL_BG
TERMINAL_PANEL = theme.TERMINAL_PANEL
TERMINAL_CODE_BG = theme.TERMINAL_CODE_BG
TERMINAL_BORDER = theme.TERMINAL_BORDER
TERMINAL_HOVER = theme.TERMINAL_HOVER

TERMINAL_TEXT = theme.TERMINAL_TEXT
TERMINAL_STDOUT = theme.TERMINAL_STDOUT
TERMINAL_STDERR = theme.TERMINAL_STDERR
TERMINAL_MUTED = theme.TERMINAL_MUTED
TERMINAL_MUTED_LIGHT = theme.TERMINAL_MUTED_LIGHT

TERMINAL_ACCENT = theme.TERMINAL_ACCENT
TERMINAL_ACCENT_DIM = theme.TERMINAL_ACCENT_DIM
TERMINAL_ACCENT_TEXT = theme.TERMINAL_ACCENT_TEXT
TERMINAL_USER = theme.TERMINAL_USER
TERMINAL_DANGER = theme.TERMINAL_DANGER
TERMINAL_IDLE = theme.TERMINAL_IDLE

TERMINAL_MONO_FONTS = ("JetBrains Mono", "Cascadia Mono", "Consolas", "Menlo",
                       "DejaVu Sans Mono", "Noto Sans Mono")
TERMINAL_UI_FONTS = theme.UI_FONTS

TERMINAL_SOURCE_LABEL_USER = "[用户] $"
TERMINAL_SOURCE_LABEL_MODEL = "[恋恋] $"

TERMINAL_WINDOW_RADIUS = theme.TERMINAL_WINDOW_RADIUS
TERMINAL_CONTROL_RADIUS = theme.TERMINAL_CONTROL_RADIUS
TERMINAL_CARD_RADIUS = theme.TERMINAL_CARD_RADIUS
TERMINAL_BLOCK_GAP = theme.TERMINAL_BLOCK_GAP


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
