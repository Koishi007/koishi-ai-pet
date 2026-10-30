"""通用控件。"""

from PySide6.QtWidgets import QComboBox


class NoWheelComboBox(QComboBox):
    """不响应滚轮的下拉框，滚轮事件交给外层滚动区。"""

    def wheelEvent(self, event):
        event.ignore()
