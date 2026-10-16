"""拖拽惯性钟摆：纯函数积分、控制器时序与绘制方向。"""

import math

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from pet.config import config
from pet.ui.pet_window import _SpriteLabel
from pet.ui.swing import SwingController, step_pendulum

DT = 0.03


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


class _PosSeq:
    """按 tick 推进的位置序列：初速 v0、匀加速 accel（px/s²）。"""

    def __init__(self, accel: float, v0: float = 0.0):
        self._accel = accel
        self._x = 0.0
        self._v = v0

    def __call__(self) -> float:
        x = self._x
        self._v += self._accel * DT
        self._x += self._v * DT
        return x


class TestStepPendulum:

    def test_zero_input_decays_to_rest(self):
        theta, omega = 0.2, 0.0
        for _ in range(400):
            theta, omega = step_pendulum(theta, omega, 0.0, DT, 25.0, 5.0, 0.21)
        assert abs(theta) < math.radians(0.5)
        assert abs(omega) < math.radians(5.0)

    def test_negative_drive_deflects_negative(self):
        theta, omega = 0.0, 0.0
        for _ in range(30):
            theta, omega = step_pendulum(theta, omega, -8.0, DT, 25.0, 2.0, 0.21)
        assert theta < 0

    def test_clamp_caps_angle_and_zeroes_omega(self):
        theta, omega = 0.0, 0.0
        for _ in range(50):
            theta, omega = step_pendulum(theta, omega, -100.0, DT, 25.0, 2.0, 0.21)
        assert theta == pytest.approx(-0.21)
        assert omega == 0.0


class TestSwingController:

    def _angles(self, ctrl: SwingController) -> list:
        got: list[float] = []
        ctrl.swing_changed.connect(got.append)
        return got

    def test_accel_drives_negative_angle(self, qt_app):
        ctrl = SwingController(_PosSeq(20000.0))
        angles = self._angles(ctrl)
        ctrl.begin()
        for _ in range(20):
            ctrl._tick()
        assert angles
        assert min(angles) < 0

    def test_constant_velocity_holds_deflection(self, qt_app):
        # 匀速右移：无加速度，速度激励让身体保持左偏（600px/s 稳态约 -7°）
        ctrl = SwingController(_PosSeq(0.0, v0=600.0))
        angles = self._angles(ctrl)
        ctrl.begin()
        for _ in range(60):
            ctrl._tick()
        assert -12 < angles[-1] < -3

    def test_release_decays_and_stops(self, qt_app):
        ctrl = SwingController(_PosSeq(20000.0))
        angles = self._angles(ctrl)
        ctrl.begin()
        for _ in range(20):
            ctrl._tick()
        ctrl.release()
        for _ in range(2000):
            if not ctrl.active:
                break
            ctrl._tick()
        assert not ctrl.active
        assert angles[-1] == 0.0
        assert abs(angles[-2]) < 1.0  # 停表前已接近静止

    def test_restart_from_rest(self, qt_app):
        ctrl = SwingController(_PosSeq(20000.0))
        angles = self._angles(ctrl)
        ctrl.begin()
        for _ in range(20):
            ctrl._tick()
        ctrl.release()
        for _ in range(2000):
            if not ctrl.active:
                break
            ctrl._tick()
        assert not ctrl.active
        ctrl.begin()
        assert ctrl.active
        assert ctrl._theta == 0.0

    def test_disabled_never_starts(self, qt_app, monkeypatch):
        monkeypatch.setattr(config, "GRAB_SWING_ENABLED", False)
        ctrl = SwingController(_PosSeq(20000.0))
        angles = self._angles(ctrl)
        ctrl.begin()
        assert not ctrl.active
        assert angles == []

    def test_disabling_stops_active_swing(self, qt_app, monkeypatch):
        # 开关即时生效：拖拽中途关掉也要立刻收起摆动
        ctrl = SwingController(_PosSeq(20000.0))
        angles = self._angles(ctrl)
        ctrl.begin()
        for _ in range(20):
            ctrl._tick()
        assert ctrl.active

        monkeypatch.setattr(config, "GRAB_SWING_ENABLED", False)
        ctrl._tick()

        assert not ctrl.active
        assert angles[-1] == 0.0

    def test_timer_survives_idle_during_drag(self, qt_app):
        # 拖拽中途停顿：停表会让后续移动不再摆动，只有松手后才按静止收尾
        ctrl = SwingController(_PosSeq(0.0))
        ctrl.begin()
        for _ in range(60):  # 2 秒不动，远超静止阈值
            ctrl._tick()
        assert ctrl.active

    def test_angle_respects_max_deg(self, qt_app):
        ctrl = SwingController(_PosSeq(1e6))
        angles = self._angles(ctrl)
        ctrl.begin()
        for _ in range(50):
            ctrl._tick()
        assert max(abs(a) for a in angles) <= config.GRAB_SWING_MAX_DEG + 1e-9

    def test_land_relaxes_residual_swing(self, qt_app):
        ctrl = SwingController(_PosSeq(20000.0))
        angles = self._angles(ctrl)
        ctrl.begin()
        for _ in range(20):
            ctrl._tick()
        assert abs(ctrl._theta) > math.radians(1)  # 松手时有残余摆角
        ctrl.release()
        ctrl.land()
        ticks = 0
        for _ in range(100):
            if not ctrl.active:
                break
            ctrl._tick()
            ticks += 1
        assert not ctrl.active
        assert ticks <= 10  # 0.3s 内归正，不拖整段回正
        assert angles[-1] == 0.0

    def test_land_without_swing_is_noop(self, qt_app):
        ctrl = SwingController(_PosSeq(0.0))
        ctrl.land()
        assert not ctrl.active


def _bottom_half_centroid_x(swing: float) -> float:
    """渲染 `swing` 度后取下半部分黑色像素的 x 重心，用于判定身体偏向。

    画布用白底，render 时跳过窗口背景（默认的 DrawWindowBackground 会把
    整个画布涂成不透明，无法分辨内容）。
    """
    label = _SpriteLabel()
    label.resize(125, 125)
    pm = QPixmap(125, 125)
    pm.fill(Qt.GlobalColor.black)
    label.setPixmap(pm)
    label.set_swing(swing)

    canvas = QPixmap(label.size())
    canvas.fill(Qt.GlobalColor.white)
    label.render(canvas, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)

    image = canvas.toImage()
    total, count = 0.0, 0
    for y in range(image.height() // 2, image.height()):
        for x in range(image.width()):
            if image.pixel(x, y) & 0xFFFFFF == 0:
                total += x
                count += 1
    return total / count


class TestSwingRender:
    """θ 为正（右偏）时绘制出的身体必须落在垂线右侧。"""

    def test_positive_swing_leans_right(self, qt_app):
        base = _bottom_half_centroid_x(0.0)
        right = _bottom_half_centroid_x(10.0)
        left = _bottom_half_centroid_x(-10.0)
        assert right > base > left
