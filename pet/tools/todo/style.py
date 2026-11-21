"""待办面板样式：选择器留在工具内，取值集中在本模块。"""

from pet import theme

# 34px 关闭钮的胶囊半径，取高度一半
CLOSE_RADIUS = 17

PANEL_QSS = f"""
QWidget#todoBg {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_XL}px;
}}
"""

BASE_QSS = f"""
QWidget {{
    color: {theme.INK};
}}
QLabel {{
    color: {theme.INK};
    background: transparent;
}}
QMessageBox, QDialog {{
    background: {theme.PAPER};
}}
QMessageBox QLabel {{
    color: {theme.INK};
    font-size: 12px;
}}
"""

CLOSE_QSS = f"""
QPushButton {{
    background: transparent;
    border: {theme.BORDER}px solid transparent;
    border-radius: {CLOSE_RADIUS}px;
    font-size: 14px;
    color: {theme.INK_MUTED};
}}
QPushButton:hover {{
    background: {theme.DANGER};
    color: {theme.SURFACE};
    border-color: {theme.INK};
}}
"""

BUTTON_QSS = f"""
QPushButton {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_MD}px;
    padding: 4px 12px;
    font-size: 11.5px;
    font-weight: bold;
    color: {theme.INK};
    min-width: 64px;
}}
QPushButton:hover {{
    background: {theme.SURFACE_WARM};
}}
QPushButton:pressed {{
    background: {theme.PAPER_DEEP};
}}
"""

MENU_QSS = f"""
QMenu {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_LG}px;
    padding: 6px 4px;
}}
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
"""

LIST_QSS = f"""
QListWidget {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_LG}px;
    font-size: 12.5px;
    color: {theme.INK};
    outline: none;
    alternate-background-color: {theme.SURFACE_WARM};
    padding: 4px;
}}
QListWidget::item {{
    padding: 7px 8px;
    border-radius: {theme.RADIUS_SM}px;
}}
QListWidget::item:selected {{
    background: {theme.PRIMARY_SOFT};
    color: {theme.INK};
}}
"""
