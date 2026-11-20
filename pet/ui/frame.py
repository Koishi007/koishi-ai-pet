"""窗口级圆角墨框与硬投影。"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

from pet import theme


def paint_ink_frame(widget, *, fill: str = theme.PAPER,
                    radius: int = theme.RADIUS_XL,
                    border: int = theme.BORDER,
                    shadow: int = theme.SHADOW_WINDOW) -> None:
    """在窗口矩形内绘制偏移投影、圆角卡片与墨色描边。

    卡片右、下各留出 shadow 像素给投影，窗口外框尺寸不变。
    """
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    inset = border / 2
    card = QRectF(inset, inset,
                  widget.width() - shadow - border,
                  widget.height() - shadow - border)
    if shadow > 0:
        shadow_path = QPainterPath()
        shadow_path.addRoundedRect(card.translated(shadow, shadow), radius, radius)
        painter.fillPath(shadow_path, QColor(theme.INK))
    path = QPainterPath()
    path.addRoundedRect(card, radius, radius)
    painter.fillPath(path, QColor(fill))
    painter.setPen(QPen(QColor(theme.INK), border))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.end()
