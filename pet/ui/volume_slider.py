"""音量滑块：45 度黄绿斜纹填充。"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider

from pet import theme
from pet.ui.qss import SLIDER_METRICS_QSS

# 斜带宽度，对应渲染稿 .progress 的 7px 色带
_STRIPE_W = 7
_BORDER = theme.BORDER


class VolumeSlider(QSlider):
    """墨色凹槽加斜纹填充；几何取自 QSS，paintEvent 自绘。"""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setStyleSheet(SLIDER_METRICS_QSS)
        # QSlider 的 sizeHint 是 16px，圆形滑块需要额外高度
        self.setMinimumHeight(20)

    def _sub_rects(self) -> tuple[QRectF, QRectF]:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        groove = style.subControlRect(
            QStyle.ComplexControl.CC_Slider, option,
            QStyle.SubControl.SC_SliderGroove, self)
        handle = style.subControlRect(
            QStyle.ComplexControl.CC_Slider, option,
            QStyle.SubControl.SC_SliderHandle, self)
        # 轨道铺满整个控件宽度，两端与滑块行程对齐
        groove.setLeft(0)
        groove.setRight(self.width() - 1)
        return QRectF(groove), QRectF(handle)

    def paintEvent(self, event):
        track, handle = self._sub_rects()
        # track 是描边外沿；描边压在 track 内侧，填充区再让开一个描边宽度
        half = _BORDER / 2
        frame = track.adjusted(half, half, -half, -half)
        inner = track.adjusted(_BORDER, _BORDER, -_BORDER, -_BORDER)
        radius = frame.height() / 2
        inner_radius = inner.height() / 2

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        slot = QPainterPath()
        slot.addRoundedRect(inner, inner_radius, inner_radius)
        painter.fillPath(slot, QColor(theme.PAPER_DEEP))

        filled = self._filled_width(inner)
        if filled > 0:
            painter.save()
            painter.setClipPath(slot)
            painter.fillRect(
                QRectF(inner.left(), inner.top(), filled, inner.height()),
                QColor(theme.ACCENT))
            self._paint_stripes(painter, inner, filled)
            painter.restore()

        painter.setPen(QPen(QColor(theme.INK), _BORDER))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(frame, radius, radius)

        self._paint_handle(painter, handle)
        painter.end()

    def _filled_width(self, inner: QRectF) -> float:
        """填充宽度按取值比例算，不按滑块中心。"""
        span = self.maximum() - self.minimum()
        ratio = 0.0 if span <= 0 else (self.value() - self.minimum()) / span
        return inner.width() * ratio

    @staticmethod
    def _paint_stripes(painter: QPainter, inner: QRectF, filled: float) -> None:
        """45 度平行色带，每条是一个平行四边形，向右下倾斜。

        斜带宽度按渲染稿的 7px 取法线方向，落到水平方向乘 √2。
        """
        height = inner.height()
        run = _STRIPE_W * 2 ** 0.5
        left, top, bottom = inner.left(), inner.top(), inner.bottom()
        x = left - run
        while x < left + filled:
            band = QPainterPath()
            band.moveTo(QPointF(x, top))
            band.lineTo(QPointF(x + run, top))
            band.lineTo(QPointF(x + run + height, bottom))
            band.lineTo(QPointF(x + height, bottom))
            band.closeSubpath()
            painter.fillPath(band, QColor(theme.ACCENT_HOVER))
            x += run * 2

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def _paint_handle(self, painter: QPainter, handle: QRectF) -> None:
        if not self.isEnabled():
            fill = theme.PAPER_DEEP
        elif self.underMouse():
            fill = theme.ACCENT_SOFT
        else:
            fill = theme.SURFACE_WARM
        painter.setPen(QPen(QColor(theme.INK), _BORDER))
        painter.setBrush(QColor(fill))
        painter.drawEllipse(handle)
