"""拖拽惯性钟摆：窗口加速度驱动贴图绕抓取点摆动，松手后自由衰减。"""

import math

from PySide6.QtCore import QObject, QTimer, Signal

from pet.config import config


def step_pendulum(theta: float, omega: float, drive: float, dt: float,
                  k: float, c: float, theta_max: float) -> tuple[float, float]:
    """阻尼单摆积分一步（半隐式欧拉）。角度单位弧度，右偏为正。

    drive 是外部激励角加速度（rad/s²），正值为右偏方向；加速度与速度的换算由调用方完成。
    超过 theta_max 时截断角度并把角速度归零，防止贴上限抖动。
    """
    omega += (-k * math.sin(theta) - c * omega + drive) * dt
    theta += omega * dt
    if abs(theta) > theta_max:
        theta = math.copysign(theta_max, theta)
        omega = 0.0
    return theta, omega


class SwingController(QObject):
    """30ms tick 估计拖拽速度与加速度并积分摆角，输出角度（度）。"""

    swing_changed = Signal(float)  # 摆角，度；右偏为正

    _INTERVAL_MS = 30
    _K = 25.0              # 恢复系数（= g/L），固有周期约 1.26s
    _C_DRAG = 2.0          # 拖拽中阻尼
    _C_FREE = 5.0          # 松手后阻尼
    _AX_GAIN = 1e-3        # 加速度映射（px/s² → rad/s²）：加减速时的惯性甩动
    _VX_GAIN = 5e-3        # 速度映射（px/s → rad/s²）：匀速拖动时身体保持滞后倾角
    _EMA_ALPHA = 0.25      # 加速度低通系数
    _LANDING_DECAY = 0.5   # 落地归正：每 tick 角度乘以该系数
    _REST_THETA = math.radians(0.5)    # 静止角度阈值
    _REST_OMEGA = math.radians(5.0)    # 静止角速度阈值（rad/s）
    _REST_TICKS = 12       # 静止持续 tick 数（约 0.36s）

    def __init__(self, pos_getter, parent=None):
        super().__init__(parent)
        self._pos_getter = pos_getter

        self._theta = 0.0
        self._omega = 0.0
        self._ax = 0.0
        self._vx = 0.0
        self._last_x: float | None = None
        self._last_v: float | None = None
        self._dragging = False
        self._relaxing = False
        self._rest_ticks = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def begin(self):
        """抓取：记录基准位置并启动定时器。"""
        if not config.GRAB_SWING_ENABLED:
            return
        self._theta = 0.0
        self._omega = 0.0
        self._ax = 0.0
        self._vx = 0.0
        self._last_x = float(self._pos_getter())
        self._last_v = None
        self._dragging = True
        self._relaxing = False
        self._rest_ticks = 0
        self._timer.start(self._INTERVAL_MS)

    def release(self):
        """松手：断开激励，保留 θ/ω 继续衰减。"""
        self._dragging = False
        self._ax = 0.0
        self._vx = 0.0
        self._last_x = None
        self._last_v = None

    def land(self):
        """落地：残余角度按指数快速归正，不拖到落地之后再摆完。"""
        if not self._timer.isActive():
            return
        self._dragging = False
        self._relaxing = True
        self._ax = 0.0
        self._vx = 0.0
        self._last_x = None
        self._last_v = None

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def _stop(self):
        """结束摆动：归零角度与角速度、停表，并发一次零角度让贴图回正。"""
        self._theta = 0.0
        self._omega = 0.0
        self._rest_ticks = 0
        self._timer.stop()
        self.swing_changed.emit(0.0)

    def _tick(self):
        if not config.GRAB_SWING_ENABLED:
            # 开关即时生效：拖拽中或衰减中关掉就立刻收起摆动
            self.release()
            self._relaxing = False
            self._stop()
            return
        if self._relaxing:
            self._theta *= self._LANDING_DECAY
            self._omega = 0.0
            self.swing_changed.emit(math.degrees(self._theta))
            if abs(self._theta) < self._REST_THETA:
                self._stop()
            return
        if self._dragging:
            self._estimate_motion()
            drive = -self._ax * self._AX_GAIN - self._vx * self._VX_GAIN
            damping = self._C_DRAG
        else:
            drive = 0.0
            damping = self._C_FREE
        self._theta, self._omega = step_pendulum(
            self._theta, self._omega, drive, self._INTERVAL_MS / 1000.0,
            self._K, damping, math.radians(config.GRAB_SWING_MAX_DEG),
        )
        self.swing_changed.emit(math.degrees(self._theta))

        # 拖拽中不因静止停表：松手前随时可能继续移动，停表会让后续移动不再摆动
        if not self._dragging and self._at_rest():
            self._rest_ticks += 1
            if self._rest_ticks >= self._REST_TICKS:
                self._stop()
        else:
            self._rest_ticks = 0

    def _estimate_motion(self):
        """两级差分：速度直接使用，加速度经 EMA 低通；首 tick 只建基准。"""
        x = float(self._pos_getter())
        dt = self._INTERVAL_MS / 1000.0
        if self._last_x is None:
            self._last_x = x
            return
        v = (x - self._last_x) / dt
        self._last_x = x
        self._vx = v
        if self._last_v is None:
            self._last_v = v
            return
        a = (v - self._last_v) / dt
        self._last_v = v
        self._ax += (a - self._ax) * self._EMA_ALPHA

    def _at_rest(self) -> bool:
        return abs(self._theta) < self._REST_THETA and abs(self._omega) < self._REST_OMEGA
