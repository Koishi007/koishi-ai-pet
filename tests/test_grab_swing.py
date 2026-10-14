"""拖拽惯性钟摆：纯函数积分与控制器时序。"""

import math

import pytest
from PySide6.QtCore import QCoreApplication

from pet.config import config
from pet.ui.swing import SwingController, step_pendulum

DT = 0.03


@pytest.fixture(scope="module")
def qcore_app():
    return QCoreApplication.instance() or QCoreApplication([])


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

    def test_accel_drives_negative_angle(self, qcore_app):
        ctrl = SwingController(_PosSeq(20000.0))
        angles = self._angles(ctrl)
        ctrl.begin()
        for _ in range(20):
            ctrl._tick()
        assert angles
        assert min(angles) < 0

    def test_constant_velocity_holds_deflection(self, qcore_app):
        # 匀速右移：无加速度，速度激励让身体保持左偏（600px/s 稳态约 -7°）
        ctrl = SwingController(_PosSeq(0.0, v0=600.0))
        angles = self._angles(ctrl)
        ctrl.begin()
        for _ in range(60):
            ctrl._tick()
        assert -12 < angles[-1] < -3

    def test_release_decays_and_stops(self, qcore_app):
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

    def test_restart_from_rest(self, qcore_app):
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

    def test_disabled_never_starts(self, qcore_app, monkeypatch):
        monkeypatch.setattr(config, "GRAB_SWING_ENABLED", False)
        ctrl = SwingController(_PosSeq(20000.0))
        angles = self._angles(ctrl)
        ctrl.begin()
        assert not ctrl.active
        assert angles == []

    def test_angle_respects_max_deg(self, qcore_app):
        ctrl = SwingController(_PosSeq(1e6))
        angles = self._angles(ctrl)
        ctrl.begin()
        for _ in range(50):
            ctrl._tick()
        assert max(abs(a) for a in angles) <= config.GRAB_SWING_MAX_DEG + 1e-9
