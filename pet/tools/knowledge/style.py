"""知识库面板样式：选择器留在工具内，取值集中在本模块。"""

from pet import theme

# 34px 关闭钮的胶囊半径，取高度一半
CLOSE_RADIUS = 17

PANEL_QSS = f"""
QWidget#kbBg {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_LG}px;
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
QDialog QLabel {{
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

INPUT_QSS = f"""
QLineEdit {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_MD}px;
    padding: 5px 10px;
    font-size: 12px;
    color: {theme.INK};
}}
QLineEdit:focus {{
    border-color: {theme.PRIMARY};
}}
"""

DIALOG_QSS = BASE_QSS + INPUT_QSS + f"""
QTextEdit {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_MD}px;
    padding: 5px 10px;
    font-size: 12px;
    color: {theme.INK};
}}
QTextEdit:focus {{
    border-color: {theme.PRIMARY};
}}
"""

BUTTON_QSS = f"""
QPushButton {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_MD}px;
    padding: 4px 12px;
    font-size: 12px;
    color: {theme.INK};
    min-width: 64px;
}}
QPushButton:hover {{
    background: {theme.SURFACE_WARM};
}}
QPushButton:pressed {{
    background: {theme.PAPER_DEEP};
}}
QPushButton:disabled {{
    color: {theme.INK_MUTED};
    background: {theme.PAPER_DEEP};
    border-color: {theme.LINE};
}}
"""

PRIMARY_BUTTON_QSS = BUTTON_QSS + f"""
QPushButton {{
    background: {theme.PRIMARY};
    color: {theme.TEXT_ON_PRIMARY};
    font-weight: bold;
}}
QPushButton:hover {{
    background: {theme.PRIMARY_HOVER};
}}
"""

LIST_QSS = f"""
QListWidget {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_LG}px;
    font-size: 12px;
    color: {theme.INK};
    outline: none;
    padding: 4px;
}}
QListWidget::item {{
    padding: 8px;
    border-bottom: 1px solid {theme.LINE};
    border-radius: {theme.RADIUS_SM}px;
}}
QListWidget::item:selected {{
    background: {theme.PRIMARY_SOFT};
    color: {theme.INK};
}}
"""

TITLE_QSS = f"font-size: 14px; font-weight: 800; color: {theme.INK};"
HINT_QSS = f"font-size: 11px; color: {theme.INK_MUTED};"
