"""单色图标：把灰阶 PNG 染成墨色并缓存。"""

from functools import lru_cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from pet import theme


@lru_cache(maxsize=64)
def tinted_icon(path: str, color: str = theme.INK, size: int = 28) -> QIcon:
    """加载图标、平滑缩放并整体染色，找不到文件时返回空图标。"""
    source = QPixmap(path)
    if source.isNull():
        return QIcon()
    source = source.scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    canvas = QPixmap(source.size())
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap(0, 0, source)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(canvas.rect(), QColor(color))
    painter.end()
    return QIcon(canvas)
