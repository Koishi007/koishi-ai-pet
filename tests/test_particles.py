"""粒子与失落动作测试：漩涡形状、漂浮运动、动作注册、粒子映射一致性。"""

from pathlib import Path

import numpy as np
import pytest

from pet.action.registry import REGISTRY
from pet.agent.scheduled_tasks import _ACTION_PARTICLES
from pet.ui.particle import (
    _SPAWNERS, _SPIRAL_COLORS, _SpiralGlyph, _draw_particle, _spawn_spiral,
)

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "actions" / "dejected"


class TestSpiralParticles:
    def test_spawns_glyphs(self):
        particles = _spawn_spiral(100.0, 100.0)
        assert 4 <= len(particles) <= 6
        assert all(isinstance(p, _SpiralGlyph) for p in particles)
        assert all(p.shape == "spiral" for p in particles)

    def test_starts_near_head(self):
        for p in _spawn_spiral(100.0, 100.0):
            assert abs(p.x - 100) <= 16
            assert abs(p.y - 100) <= 8

    def test_floats_up_then_fades(self):
        for p in _spawn_spiral(100.0, 100.0):
            y0 = p.y
            p.tick(p.lifetime // 2)  # 半程
            assert p.y < y0
            assert p.alpha == 1.0    # 前 70% 保持不透明
            p.tick(p.lifetime)       # 超过寿命
            assert p.alpha == 0.0    # 末端淡出到全透明

    def test_expires_after_lifetime(self):
        p = _spawn_spiral(0.0, 0.0)[0]
        p.tick(p.lifetime)
        assert not p.alive

    def test_palette_is_purple_black(self):
        for r, g, b in _SPIRAL_COLORS:
            assert b > g           # 紫色系
            assert max(r, g, b) <= 230
        average = sum(sum(c) for c in _SPIRAL_COLORS) / len(_SPIRAL_COLORS)
        assert average < 400       # 整体偏黑


_APP = None  # 必须持有引用，否则 QApplication 会被回收


@pytest.fixture(scope="module")
def qapp():
    global _APP
    from PySide6.QtWidgets import QApplication
    try:
        _APP = QApplication.instance() or QApplication([])
    except Exception as e:  # 无显示环境
        pytest.skip(f"Qt 不可用: {e}")
    return _APP


class TestSpiralGlyphRender:
    """离屏画一遍，确认笔画绕满四周且半径符合 size。"""

    SIDE = 120

    def _ink(self, size: float, turn: float = 0.0, mirror: float = 1.0) -> np.ndarray:
        from PySide6.QtGui import QColor, QImage, QPainter

        img = QImage(self.SIDE, self.SIDE, QImage.Format.Format_RGBA8888)
        img.fill(QColor(0, 0, 0, 0))
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        glyph = _SpiralGlyph(turn=turn, mirror=mirror,
                             x=self.SIDE / 2, y=self.SIDE / 2, size=size,
                             color=QColor(120, 60, 180), shape="spiral")
        _draw_particle(painter, glyph)
        painter.end()
        arr = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(self.SIDE, self.SIDE, 4)
        return arr[..., 3] > 0

    def test_paints_all_quadrants(self, qapp):
        ink = self._ink(size=34.0)
        assert ink.any()
        half = self.SIDE // 2
        quadrants = (ink[:half, :half], ink[:half, half:], ink[half:, :half], ink[half:, half:])
        for quadrant in quadrants:
            assert quadrant.sum() > 5  # 一条线或一个点做不到四象限都满

    def test_spans_expected_radius(self, qapp):
        size = 34.0
        ink = self._ink(size=size)
        ys, xs = np.nonzero(ink)
        center = self.SIDE / 2
        span = max(center - xs.min(), xs.max() - center, center - ys.min(), ys.max() - center)
        assert span == pytest.approx(size, abs=2.0)

    def test_mirror_flips_direction(self, qapp):
        straight = self._ink(size=30.0, turn=0.0, mirror=1.0)
        flipped = self._ink(size=30.0, turn=0.0, mirror=-1.0)
        assert not np.array_equal(straight, flipped)


class TestDejectedAction:
    def test_registered(self):
        assert "dejected" in REGISTRY
        assert REGISTRY["dejected"].params == []

    def test_assets_present(self):
        # 缺 json 或帧，播放器会静默跳过这个动作
        assert (ASSETS / "dejected.json").is_file()
        assert sorted(p.name for p in ASSETS.glob("*.webp")) == ["1.webp"]


class TestActionParticleMap:
    def test_dejected_uses_spiral(self):
        assert _ACTION_PARTICLES["dejected"] == ("spiral", 2)

    def test_every_mapped_effect_has_spawner(self):
        # 名字写错时 spawn 只会 warning，桌面看不到任何特效
        missing = [effect for effect, _ in _ACTION_PARTICLES.values() if effect not in _SPAWNERS]
        assert missing == []


class TestParticleWidgetCleanup:
    """特效播完隐藏前的清理：先擦净再隐藏，否则下次 show() 会闪出上一帧残影。"""

    @pytest.fixture
    def widget(self, qapp):
        from PySide6.QtWidgets import QWidget
        from pet.ui.particle import ParticleWidget

        pet = QWidget()
        widget = ParticleWidget(pet)
        yield widget
        # 不摘掉事件过滤器：widget 被回收后 Qt 仍会向已死的 Python 对象派发事件
        pet.removeEventFilter(widget)

    def test_tick_erases_before_hiding(self, widget, monkeypatch):
        order = []
        monkeypatch.setattr(widget, "repaint", lambda: order.append("repaint"))
        monkeypatch.setattr(widget, "hide", lambda: order.append("hide"))
        widget._tick()
        assert order == ["repaint", "hide"]
