"""带墨色硬投影与按压反馈的按钮：外层画影，内层按钮沿用 QSS 呈现。"""

from PySide6.QtCore import (QEasingCurve, QRectF, QSize, QVariantAnimation,
                            Qt)
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QPushButton, QWidget

from pet import theme


def paint_ink_shadow(painter, rect: QRectF, radius: int,
                     offset: tuple = (2, 3)) -> None:
    """在 rect 的 offset 处画零模糊墨影，面由子控件绘制盖在上面。"""
    shadow = QPainterPath()
    shadow.addRoundedRect(rect.translated(offset[0], offset[1]), radius, radius)
    painter.fillPath(shadow, QColor(theme.INK))


class InkButton(QWidget):
    """包住 QPushButton：外层画墨色硬投影，按下时内层下沉、松开回弹"""

    def __init__(self, text: str = "", parent=None, *,
                 radius: int = theme.RADIUS_MD,
                 shadow_offset: tuple = (2, 3),
                 pressed_when_checked: bool = False):
        super().__init__(parent)
        self._radius = radius
        self._dx, self._dy = shadow_offset
        self._press = 0.0
        self._pressed_when_checked = pressed_when_checked
        self._inner = QPushButton(text, self)
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_anim)
        self._inner.pressed.connect(lambda: self._animate(1.0, 70))
        self._inner.released.connect(self._on_released)
        if pressed_when_checked:
            self._inner.toggled.connect(self._on_toggled)
        self._inner.show()

    def __getattr__(self, name):
        # 转发到内层按钮：setText / setStyleSheet / clicked 等调用方式不变
        inner = self.__dict__.get("_inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)

    def sizeHint(self) -> QSize:
        return self._inner.sizeHint() + QSize(self._dx, self._dy)

    def minimumSizeHint(self) -> QSize:
        return self._inner.minimumSizeHint() + QSize(self._dx, self._dy)

    def setFixedSize(self, w, h=None):
        if h is None:
            size = QSize(w) if not isinstance(w, QSize) else w
            self._inner.setFixedSize(size)
            super().setFixedSize(size + QSize(self._dx, self._dy))
            return
        self._inner.setFixedSize(w, h)
        super().setFixedSize(w + self._dx, h + self._dy)

    def setFixedWidth(self, w: int) -> None:
        self._inner.setFixedWidth(w)
        super().setFixedWidth(w + self._dx)

    def setFixedHeight(self, h: int) -> None:
        self._inner.setFixedHeight(h)
        super().setFixedHeight(h + self._dy)

    def setEnabled(self, enabled: bool) -> None:
        # 外层不是按钮，禁用态要落到内层才生效
        super().setEnabled(enabled)
        self._inner.setEnabled(enabled)

    def setFocusPolicy(self, policy) -> None:
        # 焦点策略属于按钮本体，测试与回车限制都读内层
        self._inner.setFocusPolicy(policy)

    def focusPolicy(self):
        return self._inner.focusPolicy()

    def setFocus(self, *args) -> None:
        # 焦点落点属于按钮本体
        self._inner.setFocus(*args)

    def resizeEvent(self, event):
        self._inner.setGeometry(
            0, 0, max(1, self.width() - self._dx), max(1, self.height() - self._dy))
        super().resizeEvent(event)

    def paintEvent(self, event):
        # 禁用态不投影，对齐渲染稿 .btn[disabled]
        if not self._inner.isEnabled():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_ink_shadow(
            painter, QRectF(self._inner.geometry()), self._radius,
            (self._dx * (1.0 - self._press), self._dy * (1.0 - self._press)))
        painter.end()

    def _on_anim(self, value) -> None:
        self._press = max(0.0, min(1.0, float(value)))
        self._inner.move(round(self._dx * self._press), round(self._dy * self._press))
        self.update()

    def _on_released(self) -> None:
        # 选中即按下的按钮松开后保持沉下
        keep = self._pressed_when_checked and self._inner.isChecked()
        self._animate(1.0 if keep else 0.0, 110)

    def _on_toggled(self, checked: bool) -> None:
        self._animate(1.0 if checked else 0.0, 70 if checked else 110)

    def _animate(self, target: float, duration: int) -> None:
        self._anim.stop()
        self._anim.setDuration(duration)
        self._anim.setStartValue(self._press)
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()
