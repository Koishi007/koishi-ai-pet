"""通用控件。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLabel

from pet import theme
from pet.ui.icons import tinted_icon
from pet.ui.qss import COMBOBOX_POPUP_QSS


class NoWheelComboBox(QComboBox):
    """不响应滚轮的下拉框，滚轮事件交给外层滚动区。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 弹层是独立顶层容器，窗口级样式传不进去；视口还按调色板 Base 色填充，
        # 暗色系统下会把弹层涂黑，样式直接挂到容器并关掉视口填充
        self.view().viewport().setAutoFillBackground(False)
        self.view().parentWidget().setStyleSheet(COMBOBOX_POPUP_QSS)

    def wheelEvent(self, event):
        event.ignore()


def make_icon_badge(icon_path: str, background: str = theme.YELLOW,
                    size: int = 24, color: str = theme.INK) -> QLabel:
    """标题栏用的圆形图标徽章：上衣黄底加墨色描边与单色图标。"""
    icon_size = size - 10
    badge = QLabel()
    badge.setFixedSize(size, size)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setStyleSheet(
        f"background:{background}; border:{theme.BORDER}px solid {theme.INK};"
        f" border-radius:{size // 2}px;"
    )
    badge.setPixmap(tinted_icon(icon_path, color, icon_size).pixmap(icon_size, icon_size))
    return badge
