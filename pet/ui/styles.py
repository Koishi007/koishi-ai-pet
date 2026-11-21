"""界面样式兼容层：转发令牌与公共 QSS，保留旧常量名。"""

import ctypes
import os
import sys

from PySide6.QtCore import QEvent, QSize
from PySide6.QtGui import QFont

from pet import theme
from pet.ui.icons import tinted_icon
from pet.ui.ink_button import InkButton
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
CLOSE_ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "ink", "close.svg")
HIDE_ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "ink", "hide.svg")


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
# 收起态气泡高度 40：按钮 32 加上下边距 8，列底估算取固定值
_BUBBLE_COLLAPSED_H = 40


def bubble_column_y(pet_top: int, row_offset: int, height: int, geo) -> int:
    """按钮列中单个气泡的纵坐标。

    列底按 BUBBLE_ROW_MUSIC + 收起态气泡高度估算，超出可用区时整列共用同一上移量，
    气泡展开与收起不改变纵坐标。本气泡自身超屏时再夹回，只影响展开后的高面板。
    """
    y = pet_top + row_offset
    if geo is None:
        return y
    overflow = pet_top + BUBBLE_ROW_MUSIC + _BUBBLE_COLLAPSED_H - geo.bottom()
    if overflow > 0:
        y -= overflow
    return max(geo.top(), min(y, geo.bottom() - height))


def _title_button_qss(hover_color: str, base_color: str,
                      text_color: str = "#fff", font_size: str = "18px") -> str:
    return f"""
        QPushButton {{ background: {theme.SURFACE}; border: {theme.BORDER}px solid {theme.INK};
                     border-radius: 17px; font-size: {font_size}; color: {base_color}; }}
        QPushButton:hover {{ background: {hover_color}; border-color: {theme.INK};
                     color: {text_color}; }}
    """


class _IconTitleButton(InkButton):
    """标题栏圆形图标按钮：纸面墨框加墨影，hover 换底色并重染图标。"""

    def __init__(self, icon_path: str, hover_color: str, base_color: str,
                 hover_icon_color: str, size: int = 34):
        super().__init__(radius=size // 2, shadow_offset=(1, 2))
        self.setFixedSize(size, size)
        self.setStyleSheet(
            f"QPushButton {{ background: {theme.SURFACE};"
            f" border: {theme.BORDER}px solid {theme.INK};"
            f" border-radius: {size // 2}px; }}"
            f"QPushButton:hover {{ background: {hover_color}; }}")
        self._icon_path = icon_path
        self._base_color = base_color
        self._hover_icon_color = hover_icon_color
        self._apply_icon(base_color)
        self._inner.installEventFilter(self)

    def _apply_icon(self, color: str) -> None:
        self.setIcon(tinted_icon(self._icon_path, color, 16))
        self.setIconSize(QSize(16, 16))

    def eventFilter(self, obj, event):
        if obj is self._inner:
            if event.type() == QEvent.Type.Enter:
                self._apply_icon(self._hover_icon_color)
            elif event.type() == QEvent.Type.Leave:
                self._apply_icon(self._base_color)
        return super().eventFilter(obj, event)

    def showEvent(self, event):
        # 隐藏期间收不到 leave 事件，重开时复位图标染色
        self._apply_icon(self._base_color)
        super().showEvent(event)


def make_title_button(text: str, hover_color: str,
                      base_color: str = _COLOR_TEXT_MUTED,
                      text_color: str = "#fff",
                      font_size: str = "18px") -> InkButton:
    """创建无边框面板窗口标题栏上的圆形按钮（关闭 / 最小化）。"""
    btn = InkButton(text, radius=17, shadow_offset=(1, 2))
    btn.setFixedSize(34, 34)
    btn.setStyleSheet(_title_button_qss(hover_color, base_color, text_color, font_size))
    return btn


_LIGHT_BLUE_HOVER = theme.PRIMARY_SOFT


def make_minimize_button(window) -> InkButton:
    """创建主色 hover 的隐藏按钮，点击后最小化指定窗口。"""
    btn = _IconTitleButton(HIDE_ICON_PATH, _LIGHT_BLUE_HOVER,
                           base_color=theme.INK,
                           hover_icon_color=theme.INK)
    btn.clicked.connect(window.showMinimized)
    return btn


def make_close_button(window, on_close=None, size: int = 34) -> InkButton:
    """创建红色 hover 的关闭按钮，点击后关闭窗口或调用自定义回调。"""
    btn = _IconTitleButton(CLOSE_ICON_PATH, theme.DANGER,
                           base_color=theme.INK,
                           hover_icon_color=theme.SURFACE, size=size)
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
