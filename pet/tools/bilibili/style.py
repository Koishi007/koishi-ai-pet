"""B 站面板样式：只引用令牌，选择器留在工具内。"""

from pathlib import Path

from pet import theme

_ROOT = Path(__file__).resolve().parents[3]
CHECKMARK_SVG = str(_ROOT / "assets" / "icon" / "checkmark_ink.svg").replace("\\", "/")

# 34px 关闭钮的胶囊半径取高度一半；其余为按控件定形的专用圆角
CLOSE_RADIUS = 17
CHECK_RADIUS = 4
HELP_RADIUS = 7

PANEL_QSS = f"""
QWidget#biliBg {{
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

SECTION_QSS = f"font-size: 12px; font-weight: 800; color: {theme.INK_SOFT};"

QR_QSS = f"""
QLabel {{
    background: {theme.SURFACE_WARM};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_LG}px;
    color: {theme.INK_MUTED};
    font-size: 12px;
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

CHECKBOX_QSS = f"""
QCheckBox {{
    font-size: 12px;
    color: {theme.INK};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {CHECK_RADIUS}px;
    background: {theme.SURFACE};
}}
QCheckBox::indicator:hover {{
    background: {theme.ACCENT_SOFT};
}}
QCheckBox::indicator:checked {{
    background: {theme.ACCENT};
    image: url("{CHECKMARK_SVG}");
}}
"""

BUTTON_QSS = f"""
QPushButton {{
    background: {theme.SURFACE};
    border: {theme.BORDER}px solid {theme.INK};
    border-radius: {theme.RADIUS_MD}px;
    padding: 5px 12px;
    font-size: 12px;
    color: {theme.INK};
}}
QPushButton:hover {{
    background: {theme.SURFACE_WARM};
}}
QPushButton:pressed {{
    background: {theme.PAPER_DEEP};
}}
QPushButton:disabled {{
    background: {theme.PAPER_DEEP};
    color: {theme.INK_MUTED};
    border-color: {theme.LINE};
}}
"""

HELP_QSS = f"""
QLabel {{
    color: {theme.INK_SOFT};
    border: 1.5px solid {theme.INK};
    border-radius: {HELP_RADIUS}px;
    font-size: 10px;
    background: {theme.SURFACE};
}}
"""

HINT_QSS = f"font-size: 11px; color: {theme.INK_MUTED};"
STATE_QSS = f"font-size: 12px; color: {theme.INK};"
