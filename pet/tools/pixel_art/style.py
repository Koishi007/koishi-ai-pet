"""像素画面板样式：选择器留在工具内，取值集中在本模块。"""

from pet import theme

# 34px 关闭钮与 26px 导出钮的专用圆角，不套 RADIUS_* 令牌
CLOSE_RADIUS = 17
BUTTON_RADIUS = 9

# 棋盘底保持中性灰，白色像素与透明像素可区分；不取手绘色板
CHECKER_LIGHT = "#E6E6E6"
CHECKER_DARK = "#C9C9C9"

PANEL_QSS = f"""
QWidget#pixelArtBg {{
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
    border-radius: {BUTTON_RADIUS}px;
    padding: 0 10px;
    font-size: 11px;
    color: {theme.INK};
}}
QPushButton:hover {{
    background: {theme.PRIMARY_SOFT};
}}
QPushButton:disabled {{
    background: {theme.PAPER_DEEP};
    color: {theme.INK_MUTED};
    border-color: {theme.LINE};
}}
"""
