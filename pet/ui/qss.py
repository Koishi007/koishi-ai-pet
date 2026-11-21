"""由令牌拼装的公共控件样式。"""

import os

from PySide6.QtGui import QColor, QFont

from pet import theme

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
CHECKMARK_SVG = os.path.join(
    _PROJECT_ROOT, "assets", "icon", "checkmark_ink.svg").replace("\\", "/")
CHEVRON_SVG = os.path.join(
    _PROJECT_ROOT, "assets", "icon", "ink", "chevron_down.svg").replace("\\", "/")

_MONO_FONT = theme.MONO_FONT_CSS

WINDOW_QSS = """
QWidget#FlatWindow {
    background: transparent;
}
"""

BASE_QSS = f"""
QWidget {{
    color: {theme.INK};
}}
QLabel {{
    color: {theme.INK};
    background: transparent;
}}
"""

PANEL_QSS = f"""
QGroupBox {{
    font-size: 11px;
    font-weight: bold;
    color: {theme.INK_SOFT};
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_LG}px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 1px 8px;
    background: {theme.PAPER_DEEP};
    border-radius: 8px;
    color: {theme.INK_SOFT};
}}
"""

_BTN_BORDER = f"""
    border: {theme.BORDER}px solid {theme.INK};
"""

BUTTON_QSS = f"""
QPushButton {{
    background: {theme.SURFACE};
{_BTN_BORDER}
    border-radius: {theme.RADIUS_MD}px;
    padding: 5px 12px;
    font-size: 12.5px;
    font-weight: bold;
    color: {theme.INK};
}}
QPushButton:hover {{
    background: {theme.SURFACE_WARM};
    color: {theme.INK};
}}
QPushButton:pressed {{
    background: {theme.PAPER_DEEP};
    color: {theme.INK};
}}
QPushButton:checked {{
    background: {theme.PRIMARY};
    color: {theme.TEXT_ON_PRIMARY};
}}
QPushButton:disabled {{
    background: {theme.PAPER_DEEP};
    color: {theme.INK_MUTED};
    border-color: {theme.LINE};
}}
"""

BUTTON_PRIMARY_QSS = f"""
QPushButton {{
    background: {theme.PRIMARY};
{_BTN_BORDER}
    border-radius: {theme.RADIUS_MD}px;
    padding: 5px 14px;
    font-size: 12.5px;
    font-weight: bold;
    color: {theme.TEXT_ON_PRIMARY};
}}
QPushButton:hover {{
    background: {theme.PRIMARY_HOVER};
    color: {theme.TEXT_ON_PRIMARY};
}}
QPushButton:pressed {{
    background: {theme.PRIMARY_DEEP};
    color: {theme.TEXT_ON_PRIMARY};
}}
QPushButton:disabled {{
    background: {theme.PAPER_DEEP};
    color: {theme.INK_MUTED};
    border-color: {theme.LINE};
}}
"""

BUTTON_DANGER_QSS = f"""
QPushButton {{
    background: {theme.SURFACE};
{_BTN_BORDER}
    border-radius: {theme.RADIUS_MD}px;
    padding: 5px 12px;
    font-size: 12.5px;
    font-weight: bold;
    color: {theme.DANGER};
}}
QPushButton:hover {{
    background: {theme.DANGER};
    color: {theme.TEXT_ON_PRIMARY};
}}
QPushButton:disabled {{
    background: {theme.PAPER_DEEP};
    color: {theme.INK_MUTED};
    border-color: {theme.LINE};
}}
"""

INPUT_QSS = f"""
QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_MD}px;
    padding: 5px 10px;
    min-height: 18px;
    font-size: 12.5px;
    color: {theme.INK};
    selection-background-color: {theme.PRIMARY_SOFT};
    selection-color: {theme.INK};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus {{
    border-color: {theme.PRIMARY};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QDateEdit:disabled {{
    background: {theme.PAPER_DEEP};
    color: {theme.INK_MUTED};
    border-color: {theme.LINE};
}}
"""

INPUT_HIGHLIGHT_QSS = f"""
QLineEdit {{
    background: {theme.SURFACE_WARM};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_MD}px;
    padding: 5px 10px;
    font-size: 12.5px;
    color: {theme.INK};
    selection-background-color: {theme.PRIMARY_SOFT};
    selection-color: {theme.INK};
}}
QLineEdit:focus {{
    border-color: {theme.PRIMARY};
    background: {theme.SURFACE};
}}
QLineEdit:disabled {{
    background: {theme.PAPER_DEEP};
    color: {theme.INK_MUTED};
    border-color: {theme.LINE};
}}
"""

COMBOBOX_QSS = f"""
QComboBox {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_MD}px;
    padding: 5px 10px;
    min-height: 18px;
    font-size: 12.5px;
    color: {theme.INK};
}}
QComboBox:hover {{
    background: {theme.SURFACE_WARM};
}}
QComboBox:focus {{
    border-color: {theme.PRIMARY};
}}
QComboBox:disabled {{
    background: {theme.PAPER_DEEP};
    color: {theme.INK_MUTED};
    border-color: {theme.LINE};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: right center;
    width: 20px;
    border: none;
    image: url("{CHEVRON_SVG}");
}}
"""

# 弹层是独立顶层容器，窗口级样式传不进去，由 NoWheelComboBox 直接挂到容器上
COMBOBOX_POPUP_QSS = f"""
QFrame {{
    background: transparent;
    border: none;
}}
QListView {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_SM}px;
    padding: 2px;
    selection-background-color: {theme.PRIMARY_SOFT};
    selection-color: {theme.INK};
    color: {theme.INK};
    font-size: 12px;
    outline: none;
}}
"""

TEXTEDIT_QSS = f"""
QTextEdit, QPlainTextEdit {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_LG}px;
    padding: 6px 8px;
    font-family: "{_MONO_FONT}", monospace;
    font-size: 12px;
    color: {theme.INK};
    selection-background-color: {theme.PRIMARY_SOFT};
    selection-color: {theme.INK};
}}
QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {theme.PRIMARY};
}}
QTextEdit:disabled, QPlainTextEdit:disabled {{
    background: {theme.PAPER_DEEP};
    color: {theme.INK_MUTED};
    border-color: {theme.LINE};
}}
"""

SCROLLBAR_QSS = f"""
QScrollBar:vertical {{
    background: {theme.PAPER_DEEP};
    width: 10px;
    border: 1.5px solid {theme.INK};
    border-radius: 5px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {theme.PRIMARY};
    border: 1.5px solid {theme.INK};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {theme.ACCENT_SOFT};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
    border: none;
    background: transparent;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}
QScrollBar:horizontal {{
    background: {theme.PAPER_DEEP};
    height: 10px;
    border: 1.5px solid {theme.INK};
    border-radius: 5px;
    margin: 0px;
}}
QScrollBar::handle:horizontal {{
    background: {theme.PRIMARY};
    border: 1.5px solid {theme.INK};
    border-radius: 5px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {theme.ACCENT_SOFT};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
    border: none;
    background: transparent;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}
"""

LIST_QSS = f"""
QListWidget {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_LG}px;
    padding: 4px;
    font-family: "{_MONO_FONT}", monospace;
    font-size: 11px;
    color: {theme.INK};
    outline: none;
    alternate-background-color: {theme.SURFACE_WARM};
}}
QListWidget::item {{
    padding: 4px 6px;
    border-radius: {theme.RADIUS_SM}px;
}}
QListWidget::item:selected {{
    background: {theme.PRIMARY_SOFT};
    color: {theme.INK};
}}
"""

TABLE_QSS = f"""
QTableWidget {{
    background: {theme.SURFACE};
    alternate-background-color: {theme.SURFACE_WARM};
    border: {theme.BORDER}px solid {theme.INK};
    gridline-color: {theme.LINE};
    color: {theme.INK};
    font-size: 11px;
    selection-background-color: {theme.PRIMARY_SOFT};
    selection-color: {theme.INK};
    outline: none;
}}
QHeaderView::section {{
    background: {theme.PAPER_DEEP};
    color: {theme.INK_SOFT};
    border: none;
    border-right: 1px solid {theme.LINE};
    border-bottom: 1px solid {theme.LINE};
    padding: 5px 6px;
    font-size: 11px;
    font-weight: bold;
}}
"""

_MENU_ITEMS_QSS = f"""
QMenu::item {{
    padding: 6px 30px 6px 12px;
    font-size: 12px;
    color: {theme.INK};
    border-radius: {theme.RADIUS_SM}px;
    background: transparent;
}}
QMenu::item:selected {{
    background: {theme.PRIMARY_SOFT};
}}
QMenu::item:disabled {{
    color: {theme.INK_MUTED};
}}
QMenu::separator {{
    height: 1px;
    background: {theme.LINE};
    margin: 4px 10px;
}}
"""

# Windows 由窗口自绘圆角卡片，样式表只负责菜单项
MENU_QSS = f"""
QMenu {{
    background: transparent;
    border: none;
    border-radius: {theme.RADIUS_LG}px;
    padding: 6px 4px;
}}
""" + _MENU_ITEMS_QSS

# 其余平台走原生窗口，样式表自带底色与描边
MENU_SOLID_QSS = f"""
QMenu {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_LG}px;
    padding: 6px 4px;
}}
""" + _MENU_ITEMS_QSS

CHECKBOX_QSS = f"""
QCheckBox {{
    font-size: 12px;
    color: {theme.INK};
    spacing: 6px;
}}
QCheckBox:disabled {{
    color: {theme.INK_MUTED};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    margin: 2px 0;
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: 4px;
}}
QCheckBox::indicator:disabled {{
    background: {theme.PAPER_DEEP};
    border-color: {theme.LINE};
}}
QCheckBox::indicator:checked {{
    background: {theme.ACCENT};
    image: url("{CHECKMARK_SVG}");
}}
QCheckBox::indicator:hover {{
    background: {theme.ACCENT_SOFT};
}}
QCheckBox::indicator:checked:hover {{
    background: {theme.ACCENT};
}}
"""

LABEL_SECONDARY_QSS = f"""
    font-size: 12px;
    color: {theme.INK_SOFT};
"""

LABEL_MONO_QSS = f"""
    font-family: "{_MONO_FONT}", monospace;
    font-size: 12px;
    color: {theme.INK};
"""

TITLE_LABEL_QSS = (
    f"font-size:14px; color:{theme.INK}; font-weight:800; background:transparent;"
)

HELP_BADGE_QSS = f"""
    color: {theme.INK_SOFT};
    border: 1.5px solid {theme.INK};
    border-radius: 8px;
    font-size: 10px;
    font-weight: bold;
    background: {theme.SURFACE};
"""

SEPARATOR_QSS = f"""
QFrame[frameShape="4"] {{
    color: {theme.LINE};
}}
"""

TAB_BAR_QSS = f"""
QTabWidget {{
    background: transparent;
}}
QTabBar {{
    background: transparent;
}}
QTabWidget::pane {{
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_LG}px;
    background: {theme.PAPER};
    padding: 6px;
}}
QTabBar::tab {{
    background: {theme.SURFACE_WARM};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: 13px;
    padding: 5px 0;
    margin: 4px 3px;
    font-size: 12px;
    color: {theme.INK_SOFT};
}}
QTabBar::tab:selected {{
    background: {theme.PRIMARY};
    color: {theme.TEXT_ON_PRIMARY};
    font-weight: bold;
}}
QTabBar::tab:hover:!selected {{
    background: {theme.YELLOW};
}}
"""

TAB_PILL_QSS = f"""
QPushButton {{
    background: {theme.SURFACE_WARM};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: 13px;
    padding: 5px 0;
    font-size: 12px;
    color: {theme.INK_SOFT};
}}
QPushButton:hover:!checked {{
    background: {theme.YELLOW};
}}
QPushButton:checked {{
    background: {theme.PRIMARY};
    color: {theme.TEXT_ON_PRIMARY};
    font-weight: bold;
}}
QPushButton:disabled {{
    background: {theme.PAPER_DEEP};
    color: {theme.INK_MUTED};
    border-color: {theme.LINE};
}}
"""

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

TOOLTIP_QSS = f"""
QToolTip {{
    background: {theme.INK};
    color: {theme.SURFACE};
    border: 1px solid {theme.INK};
    padding: 5px 8px;
    font-size: 11px;
}}
"""

DIALOG_QSS = f"""
QMessageBox, QDialog {{
    background: {theme.PAPER};
}}
QMessageBox QLabel {{
    color: {theme.INK};
    font-size: 12px;
}}
"""


def bubble_button_qss(background: str, hover: str | None = None,
                      radius: int = 16) -> str:
    """圆形气泡按钮：2px 墨框，radius 为圆角半径，32px 按钮取 16。

    hover 缺省时按底色明度推导：浅底压深、深底提亮，避免统一变白。
    """
    if hover is None:
        color = QColor(background)
        hover = (color.darker(110) if color.lightness() > 160
                 else color.lighter(110)).name()
    return f"""
QPushButton {{
    background: {background};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {radius}px;
}}
QPushButton:hover {{
    background: {hover};
}}
"""


def chip_qss(background: str = theme.SURFACE) -> str:
    """墨框胶囊徽章；配合 setObjectName("chip") 按名字匹配。

    圆角不超过半高：超大半径会被 Qt 裁成方角。
    """
    return f"""
QLabel#chip {{
    background: {background};
    border: 1.5px solid {theme.INK};
    border-radius: 9px;
    padding: 1px 8px;
    font-size: 11px;
    font-weight: bold;
    color: {theme.INK};
}}
"""


BUBBLE_INPUT_QSS = f"""
QLineEdit {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: 14px;
    padding: 2px 10px;
    font-size: 13px;
    color: {theme.INK};
}}
QLineEdit:focus {{
    border-color: {theme.PRIMARY};
}}
"""

BUBBLE_INPUT_BUSY_QSS = f"""
QLineEdit {{
    background: {theme.PAPER_DEEP};
    border: {theme.BORDER}px solid {theme.LINE};
    border-radius: 14px;
    padding: 2px 10px;
    font-size: 13px;
    color: {theme.INK_MUTED};
}}
"""

MUSIC_PANEL_QSS = f"""
QWidget#musicPanel {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: 16px;
}}
"""

MUSIC_ICON_BUTTON_QSS = f"""
QPushButton {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_SM}px;
}}
QPushButton:hover {{
    background: {theme.PRIMARY_SOFT};
}}
"""

# 斜纹用双色斜向渐变近似，QSS 没有重复渐变
MUSIC_VOLUME_BAR_QSS = f"""
QProgressBar {{
    background: {theme.PAPER_DEEP};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: 7px;
}}
QProgressBar::chunk {{
    border-radius: 5px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {theme.ACCENT}, stop:0.1 {theme.ACCENT},
        stop:0.1 {theme.ACCENT_HOVER}, stop:0.2 {theme.ACCENT_HOVER},
        stop:0.2 {theme.ACCENT}, stop:0.3 {theme.ACCENT},
        stop:0.3 {theme.ACCENT_HOVER}, stop:0.4 {theme.ACCENT_HOVER},
        stop:0.4 {theme.ACCENT}, stop:0.5 {theme.ACCENT},
        stop:0.5 {theme.ACCENT_HOVER}, stop:0.6 {theme.ACCENT_HOVER},
        stop:0.6 {theme.ACCENT}, stop:0.7 {theme.ACCENT},
        stop:0.7 {theme.ACCENT_HOVER}, stop:0.8 {theme.ACCENT_HOVER},
        stop:0.8 {theme.ACCENT}, stop:0.9 {theme.ACCENT},
        stop:0.9 {theme.ACCENT_HOVER}, stop:1 {theme.ACCENT_HOVER});
}}
"""


def ui_font(point_size: int = 9) -> QFont:
    """应用级界面字体：QSS 的 font-family 不保证按列表回退，统一在这里设置。"""
    font = QFont()
    font.setFamilies(list(theme.UI_FONTS))
    font.setPointSize(point_size)
    return font


def app_stylesheet() -> str:
    """合并公共样式，供解析检查与整窗套用。

    同选择器的规则后者覆盖前者：通用按钮/输入样式放同族最后，
    窗口级只兜底未单独设置样式的控件，单独设置过的以自身为准。
    """
    return "".join((
        BASE_QSS, WINDOW_QSS, PANEL_QSS, BUTTON_PRIMARY_QSS,
        BUTTON_DANGER_QSS, BUTTON_QSS, INPUT_HIGHLIGHT_QSS, INPUT_QSS,
        COMBOBOX_QSS, TEXTEDIT_QSS, SCROLLBAR_QSS, LIST_QSS, TABLE_QSS,
        MENU_SOLID_QSS, CHECKBOX_QSS, SEPARATOR_QSS, TAB_BAR_QSS,
        TOOLTIP_QSS, DIALOG_QSS, chip_qss(),
    ))
