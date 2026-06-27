"""桌宠粒子特效"""

import math
import random
import logging

from PySide6.QtCore import Qt, QTimer, QObject, Signal, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPolygonF, QPainterPath
from PySide6.QtWidgets import QWidget

from pet.config import config

logger = logging.getLogger(__name__)

# ── 粒子数据 ──

class Particle:
    __slots__ = (
        "x", "y", "vx", "vy", "gravity",
        "lifetime", "age", "size", "color", "shape", "text",
    )

    def __init__(
        self,
        x: float, y: float,
        vx: float = 0, vy: float = 0,
        gravity: float = 0,
        lifetime: int = 800,
        size: float = 4,
        color: QColor = QColor(255, 200, 100),
        shape: str = "circle",
        text: str = "",
    ):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.gravity = gravity
        self.lifetime = lifetime
        self.age = 0
        self.size = size
        self.color = color
        self.shape = shape  # circle / star / text
        self.text = text    # shape=="text" 时使用

    @property
    def alive(self) -> bool:
        return self.age < self.lifetime

    @property
    def alpha(self) -> float:
        """1.0 → 0.0 线性衰减，最后 30% 加速消失。"""
        ratio = self.age / self.lifetime
        if ratio < 0.7:
            return 1.0
        return max(0.0, 1.0 - (ratio - 0.7) / 0.3)

    def tick(self, dt_ms: int):
        self.age += dt_ms
        self.vy += self.gravity * dt_ms / 30  # gravity 单位为 px/tick²
        self.x += self.vx * dt_ms / 30
        self.y += self.vy * dt_ms / 30


# ── 特效预设 ──

def _spawn_dust(cx: float, cy: float) -> list[Particle]:
    """落地灰尘：从脚底向上喷射后受重力下落。"""
    particles = []
    for _ in range(7):
        angle = random.uniform(-math.pi * 0.8, -math.pi * 0.2)  # 向上扩散
        speed = random.uniform(1.5, 3.5)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed
        particles.append(Particle(
            x=cx + random.uniform(-8, 8),
            y=cy + random.uniform(-2, 2),
            vx=vx, vy=vy,
            gravity=0.8,
            lifetime=random.randint(400, 700),
            size=random.uniform(2, 4),
            color=QColor(random.randint(180, 220), random.randint(160, 200), random.randint(120, 160)),
            shape="circle",
        ))
    return particles


def _spawn_stars(cx: float, cy: float) -> list[Particle]:
    """开心星星：从身体中心向外扩散上飘。"""
    particles = []
    for _ in range(6):
        angle = random.uniform(0, math.pi * 2)
        speed = random.uniform(0.8, 2.0)
        vx = math.cos(angle) * speed
        vy = -abs(math.sin(angle)) * speed - 0.5  # 始终有上飘分量
        particles.append(Particle(
            x=cx + random.uniform(-10, 10),
            y=cy + random.uniform(-10, 10),
            vx=vx, vy=vy,
            gravity=-0.02,  # 微弱上飘
            lifetime=random.randint(600, 1000),
            size=random.uniform(4, 7),
            color=QColor(255, random.randint(200, 255), random.randint(50, 100)),
            shape="star",
        ))
    return particles


def _spawn_zzz(cx: float, cy: float) -> list[Particle]:
    """睡觉 Zzz：缓慢右上飘，字号递增。"""
    particles = []
    for i in range(3):
        size = 8 + i * 4
        particles.append(Particle(
            x=cx + i * 12,
            y=cy - i * 8,
            vx=0.6,
            vy=-0.4,
            gravity=-0.01,
            lifetime=random.randint(1500, 2000),
            size=size,
            color=QColor(200, 200, 255, 200),
            shape="text",
            text="Z",
        ))
    return particles


def _spawn_notes(cx: float, cy: float) -> list[Particle]:
    """通话音符：♪♫ 缓慢上飘，暖色调。"""
    particles = []
    for i in range(3):
        note = random.choice(["♪", "♫"])
        particles.append(Particle(
            x=cx + random.uniform(-10, 10),
            y=cy + random.uniform(-5, 5),
            vx=random.uniform(0.3, 0.8),
            vy=-random.uniform(0.5, 1.0),
            gravity=-0.01,
            lifetime=random.randint(1200, 1800),
            size=random.randint(10, 14),
            color=QColor(random.randint(255, 255), random.randint(180, 220), random.randint(100, 160), 220),
            shape="text",
            text=note,
        ))
    return particles


def _spawn_hearts(cx: float, cy: float) -> list[Particle]:
    """爱心粒子：从头顶上飘。"""
    particles = []
    for _ in range(4):
        particles.append(Particle(
            x=cx + random.uniform(-12, 12),
            y=cy + random.uniform(-5, 5),
            vx=random.uniform(-0.3, 0.3),
            vy=-random.uniform(0.8, 1.5),
            gravity=-0.02,
            lifetime=random.randint(800, 1200),
            size=random.uniform(5, 8),
            color=QColor(255, random.randint(80, 120), random.randint(80, 120)),
            shape="heart",
        ))
    return particles


def _spawn_dark_hearts(cx: float, cy: float) -> list[Particle]:
    """黑色心型粒子：随机漂浮上升，理智低落时散发。"""
    particles = []
    for _ in range(random.randint(3, 5)):
        particles.append(Particle(
            x=cx + random.uniform(-14, 14),
            y=cy + random.uniform(-8, 8),
            vx=random.uniform(-0.5, 0.5),
            vy=-random.uniform(0.4, 1.0),
            gravity=-0.01,
            lifetime=random.randint(1000, 1800),
            size=random.uniform(6, 10),
            color=QColor(random.randint(30, 60), random.randint(30, 60), random.randint(30, 60)),
            shape="heart",
        ))
    return particles

# ── 粒子绘制 ──

def _draw_star(painter: QPainter, x: float, y: float, size: float, color: QColor, alpha: float):
    """绘制五角星。"""
    c = QColor(color)
    c.setAlphaF(alpha)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(c)
    pts = []
    for i in range(5):
        angle = math.pi / 2 + i * 2 * math.pi / 5
        pts.append((x + math.cos(angle) * size, y - math.sin(angle) * size))
        angle += math.pi / 5
        pts.append((x + math.cos(angle) * size * 0.4, y - math.sin(angle) * size * 0.4))
    polygon = QPolygonF([QPointF(px, py) for px, py in pts])
    painter.drawPolygon(polygon)


def _draw_heart(painter: QPainter, x: float, y: float, size: float, color: QColor, alpha: float):
    """绘制心形。"""
    c = QColor(color)
    c.setAlphaF(alpha)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(c)
    s = size
    path = QPainterPath()
    path.moveTo(QPointF(x, y + s * 0.3))
    path.cubicTo(QPointF(x, y - s * 0.3), QPointF(x - s, y - s * 0.3), QPointF(x - s, y + s * 0.1))
    path.cubicTo(QPointF(x - s, y + s * 0.6), QPointF(x, y + s), QPointF(x, y + s * 0.8))
    path.cubicTo(QPointF(x, y + s), QPointF(x + s, y + s * 0.6), QPointF(x + s, y + s * 0.1))
    path.cubicTo(QPointF(x + s, y - s * 0.3), QPointF(x, y - s * 0.3), QPointF(x, y + s * 0.3))
    painter.drawPath(path)




def _draw_particle(painter: QPainter, p: Particle):
    """根据粒子类型绘制。"""
    alpha = p.alpha
    if alpha <= 0:
        return

    c = QColor(p.color)
    c.setAlphaF(alpha)

    if p.shape == "circle":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c)
        painter.drawEllipse(int(p.x - p.size), int(p.y - p.size), int(p.size * 2), int(p.size * 2))
    elif p.shape == "star":
        _draw_star(painter, p.x, p.y, p.size, p.color, alpha)
    elif p.shape == "heart":
        _draw_heart(painter, p.x, p.y, p.size, p.color, alpha)
    elif p.shape == "text":
        font = QFont("Microsoft YaHei", int(p.size))
        painter.setFont(font)
        painter.setPen(QPen(c))
        painter.drawText(int(p.x), int(p.y), p.text)


# ── ParticleWidget ──

# 粒子窗口比宠物大一圈，留出特效扩散空间
_MARGIN = 100

class ParticleWidget(QWidget):
    """粒子特效浮窗 —— 在宠物周围绘制粒子动画。"""

    effect_triggered = Signal(str)  # 调试用：特效名

    def __init__(self, pet_window: QWidget, parent=None):
        super().__init__(parent)
        self._pet = pet_window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._particles: list[Particle] = []
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(30)
        self._tick_timer.timeout.connect(self._tick)
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(50)
        self._follow_timer.timeout.connect(self._reposition)

        self._loading_active = False
        self._loading_phase = 0.0  # 动画相位

        # 宠物移动时重定位
        self._pet.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is self._pet and event.type() in (
            QEvent.Type.Move,
            QEvent.Type.Resize,
        ):
            self._reposition()
        return super().eventFilter(obj, event)

    # ── 默认播放位置（相对于宠物区域的偏移像素，0=宠物顶部） ──

    _DEFAULT_Y = {
        "dust":   -1,         # 特殊: 脚底
        "stars":  1 / 4,      # 头部附近
        "notes":       1 / 4,  # 头部附近
        "hearts":      1 / 4,  # 头部附近
        "dark_hearts": 1 / 4,  # 头部附近
        "zzz":         1 / 2,  # 窗口中部
    }

    def spawn(self, effect: str, cx: float | None = None, cy: float | None = None):
        """触发粒子特效"""
        # ── effect 校验 ──
        spawner = {
            "dust": _spawn_dust,
            "stars": _spawn_stars,
            "zzz": _spawn_zzz,
            "notes": _spawn_notes,
            "hearts": _spawn_hearts,
            "dark_hearts": _spawn_dark_hearts,
        }.get(effect)
        if spawner is None:
            logger.warning(f"Unknown particle effect: {effect!r}, expected one of dust/stars/zzz/notes/hearts")
            return

        # ── cx/cy 校验 ──
        if cx is not None:
            if not isinstance(cx, (int, float)):
                logger.warning(f"spawn({effect!r}): cx must be number, got {type(cx).__name__}")
                return
            if not (0 <= cx <= 10000):
                logger.warning(f"spawn({effect!r}): cx={cx} out of range [0, 10000]")
                return

        if cy is not None:
            if not isinstance(cy, (int, float)):
                logger.warning(f"spawn({effect!r}): cy must be number, got {type(cy).__name__}")
                return
            if not (0 <= cy <= 10000):
                logger.warning(f"spawn({effect!r}): cy={cy} out of range [0, 10000]")
                return

        if (cx is not None and math.isnan(cx)) or (cy is not None and math.isnan(cy)):
            logger.warning(f"spawn({effect!r}): cx/cy is NaN")
            return

        # ── 位置默认值 ──
        if cx is None:
            cx = self.width() // 2

        if cy is None:
            fraction = self._DEFAULT_Y.get(effect, 1 / 3)
            if effect == "dust":
                cy = self.height() - _MARGIN - 5  # 脚底
            else:
                cy = _MARGIN + int(self._pet.height() * fraction)

        new = spawner(cx, cy)
        self._particles.extend(new)
        if not self._tick_timer.isActive():
            self._tick_timer.start()
        self._ensure_visible()
        self.effect_triggered.emit(effect)
        logger.debug(f"particle: {effect} ({len(new)} particles)")

    # ── 加载中粒子（LLM 等待） ──

    _LOADING_OFFSET_Y = 15  # 头顶上方 15px
    _LOADING_DOT_COUNT = 4
    _LOADING_DOT_SPACING = 8  # 圆点水平间距
    _LOADING_DOT_RADIUS = 2.5
    _LOADING_AMPLITUDE = 4  # 上下波动幅度
    _LOADING_COLOR = QColor(100, 180, 255)

    def start_loading(self):
        """开始播放加载中粒子效果。"""
        if self._loading_active:
            return
        self._loading_active = True
        self._reposition()
        if not self.isVisible():
            self.show()
        self.raise_()
        if not self._follow_timer.isActive():
            self._follow_timer.start()
        if not self._tick_timer.isActive():
            self._tick_timer.start()
        logger.debug("particle: loading started")

    def stop_loading(self):
        """停止加载粒子。"""
        self._loading_active = False
        logger.debug("particle: loading stopped")

    def _draw_loading_dots(self, painter: QPainter):
        """绘制 4 个此起彼伏的圆点，位于宠物头顶上方。"""
        cx = self.width() / 2
        # 圆点在 margin 区域内、宠物窗口顶部上方
        cy = _MARGIN - self._LOADING_OFFSET_Y
        total_width = (self._LOADING_DOT_COUNT - 1) * self._LOADING_DOT_SPACING
        start_x = cx - total_width / 2

        for i in range(self._LOADING_DOT_COUNT):
            # 每个圆点相位错开 π/2，形成波浪效果
            phase = self._loading_phase + i * (math.pi / 2)
            offset_y = math.sin(phase) * self._LOADING_AMPLITUDE
            x = start_x + i * self._LOADING_DOT_SPACING
            y = cy + offset_y

            # 透明度随高度变化：在波谷时更亮
            alpha = 0.4 + 0.6 * (math.sin(phase) + 1) / 2

            c = QColor(self._LOADING_COLOR)
            c.setAlphaF(alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(c)
            painter.drawEllipse(
                QPointF(x, y),
                self._LOADING_DOT_RADIUS,
                self._LOADING_DOT_RADIUS,
            )

    # ── 内部 ──

    def _ensure_visible(self):
        if not self.isVisible():
            self._reposition()
            self.show()
            self._follow_timer.start()
        self.raise_()

    def _reposition(self):
        """让粒子窗口始终覆盖宠物及其周围区域。"""
        px = self._pet.x()
        py = self._pet.y()
        pw = self._pet.width()
        ph = self._pet.height()
        self.setGeometry(
            px - _MARGIN,
            py - _MARGIN,
            pw + 2 * _MARGIN,
            ph + 2 * _MARGIN,
        )

    def _tick(self):
        dt = self._tick_timer.interval()
        for p in self._particles:
            p.tick(dt)
        self._particles = [p for p in self._particles if p.alive]
        # 加载动画相位推进（~1 个完整周期/秒）
        if self._loading_active:
            self._loading_phase += dt * 0.006  # 30ms * 0.006 ≈ 0.18 rad/tick
        self.update()  # 触发 paintEvent
        if not self._particles and not self._loading_active:
            self._tick_timer.stop()
            self._follow_timer.stop()
            self.hide()

    def paintEvent(self, event):
        if not self._particles and not self._loading_active:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for p in self._particles:
            _draw_particle(painter, p)
        if self._loading_active:
            self._draw_loading_dots(painter)
        painter.end()
