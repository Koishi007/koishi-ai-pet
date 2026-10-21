"""屏幕贴边：几何判定、掩膜、镜像、动作路由与甩飞边界。"""

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from pet.action.action import PetActions
from pet.action.gravity import GravitySystem
from pet.action.registry import EDGE_ACTION_NAMES, edge_asset
from pet.brain.context_builder import ContextBuilder
from pet.config import config
from pet.ui.emotion import EmotionBubble
from pet.ui.pet_window import PetWindow, _SpriteLabel
from pet.ui.speech_bubble import SpeechBubble
from pet.ui.edge_dock import EDGE_VISIBLE_RATIO, dock_x, pick_dock_side, edge_mask_rect

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "actions"


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


class TestGeometry:

    def test_pick_left_and_right(self):
        assert pick_dock_side(10, 125, 0, 1920, 24) == "left"
        assert pick_dock_side(1920 - 125 - 10, 125, 0, 1920, 24) == "right"

    def test_pick_none_outside_threshold(self):
        assert pick_dock_side(800, 125, 0, 1920, 24) is None

    def test_pick_threshold_is_inclusive(self):
        assert pick_dock_side(24, 125, 0, 1920, 24) == "left"
        assert pick_dock_side(1920 - 125 - 24, 125, 0, 1920, 24) == "right"

    def test_pick_nearest_when_both(self):
        assert pick_dock_side(2, 90, 0, 100, 24) == "left"
        assert pick_dock_side(8, 90, 0, 100, 24) == "right"

    def test_negative_threshold_disables(self):
        assert pick_dock_side(0, 125, 0, 1920, -1) is None

    def test_window_wider_than_screen(self):
        # 超宽窗口两侧同时命中：按有符号间距排序取最小一侧。行为退化但不崩溃，此处只锁定现状
        assert pick_dock_side(0, 300, 0, 200, 24) == "right"
        assert pick_dock_side(-100, 300, 0, 200, 24) == "left"
        # 与屏幕等宽：两侧间距同为 0，取 left
        assert pick_dock_side(0, 200, 0, 200, 24) == "left"

    def test_dock_x(self):
        assert dock_x("left", 0, 1920, 125) == 0
        assert dock_x("right", 0, 1920, 125) == 1795

    def test_dock_x_rejects_unknown_side(self):
        with pytest.raises(ValueError):
            dock_x("top", 0, 1920, 125)

    def test_mask_rect(self):
        visible = round(125 * EDGE_VISIBLE_RATIO)
        assert edge_mask_rect("left", 125, 125) == (0, 0, visible, 125)
        assert edge_mask_rect("right", 125, 125) == (125 - visible, 0, visible, 125)


class TestAssetSet:

    def test_edge_action_names_match_assets(self):
        dirs = {p.name[:-len("_edge")] for p in ASSETS.glob("*_edge") if p.is_dir()}
        assert dirs - {"idle", "grim"} == set(EDGE_ACTION_NAMES)
        assert {"idle", "grim"} <= dirs

    def test_edge_asset_suffix(self):
        assert edge_asset("sleep") == "sleep_edge"


def _asymmetric_pixmap(side: str, dpr: float = 1.0) -> QPixmap:
    """在 125×125 逻辑尺寸的透明底上画一块贴边侧的不透明色块。"""
    pm = QPixmap(round(125 * dpr), round(125 * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)  # 逻辑坐标：QPainter 已按 DPR 自动缩放
    painter.fillRect(90 if side == "right" else 5, 20, 30, 100, QColor("#000000"))
    painter.end()
    return pm


def _half_ink(img) -> tuple[int, int]:
    """统计左右半区的黑色像素：QLabel 的背景可能不透明，不能按 alpha 判断。"""
    left = right = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.red() < 50 and c.green() < 50 and c.blue() < 50:
                if x < img.width() // 2:
                    left += 1
                else:
                    right += 1
    return left, right


class TestFlipRender:

    def test_mirror_moves_body_to_left(self, qt_app):
        label = _SpriteLabel()
        label.resize(125, 125)
        label.setPixmap(_asymmetric_pixmap("right"))

        base = QPixmap(125, 125)
        base.fill(Qt.GlobalColor.transparent)
        label.render(base)
        base_left, base_right = _half_ink(base.toImage())
        assert base_right > base_left

        label.set_flip(True)
        flipped = QPixmap(125, 125)
        flipped.fill(Qt.GlobalColor.transparent)
        label.render(flipped)
        flip_left, flip_right = _half_ink(flipped.toImage())
        assert flip_left > flip_right

    @staticmethod
    def _luma_centroid(label, dpr: float = 1.0) -> float:
        """按亮度算纵向质心；QLabel 背景不透明，不能用 alpha。"""
        img = QPixmap(round(label.width() * dpr), round(label.height() * dpr))
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.GlobalColor.transparent)
        label.render(img)
        image = img.toImage()
        total = acc = 0.0
        for y in range(image.height()):
            for x in range(image.width()):
                w = (255 - image.pixelColor(x, y).red()) / 255.0
                total += w
                acc += w * y
        return acc / total if total else 0.0

    def test_breath_offset_is_subpixel_on_both_sides(self, qt_app):
        label = _SpriteLabel()
        label.resize(125, 125)
        label.setPixmap(_asymmetric_pixmap("right"))
        offsets = (0.0, -0.25, -0.5, -0.75, -1.0)

        def centroids() -> list[float]:
            out = []
            for dy in offsets:
                label.set_pose(dy, 1.0, 1.0)
                out.append(round(self._luma_centroid(label), 2))
            return out

        normal = centroids()
        label.set_flip(True)
        flipped = centroids()
        assert max(abs(a - b) for a, b in zip(flipped, normal)) < 0.1
        # 位移按小数连续生效，而不是被取整成 0 / -1 两级
        assert len(set(normal)) == len(offsets)

    def test_breath_subpixel_with_fractional_dpr(self, qt_app):
        label = _SpriteLabel()
        label.resize(125, 125)
        label.setPixmap(_asymmetric_pixmap("right", dpr=1.5))
        offsets = (0.0, -0.25, -0.5, -0.75, -1.0)

        def centroids() -> list[float]:
            out = []
            for dy in offsets:
                label.set_pose(dy, 1.0, 1.0)
                out.append(round(self._luma_centroid(label, dpr=1.5), 2))
            return out

        normal = centroids()
        label.set_flip(True)
        flipped = centroids()
        assert max(abs(a - b) for a, b in zip(flipped, normal)) < 0.1
        assert len(set(normal)) == len(offsets)


class _FakeAnimator:

    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def play(self, name, duration=None):
        self.calls.append((name, duration))
        return True


class TestActionRouting:

    def test_docked_actions_use_edge_assets(self, qt_app):
        window = QWidget()
        anim = _FakeAnimator()
        actions = PetActions(window, anim)
        actions.gravity.enable(False)
        try:
            actions.set_dock_side("right")
            actions.sleep(10)
            actions.idle()
            actions.grim()
            actions.shy()
            assert ("sleep_edge", 10) in anim.calls
            assert ("idle_edge", None) in anim.calls
            assert ("grim_edge", None) in anim.calls
            assert ("shy_edge", None) in anim.calls
            assert actions.idle_animation == "idle_edge"
            assert actions.grim_animation == "grim_edge"

            actions.set_dock_side("")
            actions.sleep(10)
            assert ("sleep", 10) in anim.calls
        finally:
            actions.gravity.enable(False)
            window.deleteLater()


class TestFlickEdge:

    def _system(self, qt_app):
        window = QWidget()
        system = GravitySystem(window, _FakeAnimator(), [])
        system.enable(False)
        return window, system

    def test_edge_hit_when_enabled(self, qt_app, monkeypatch):
        monkeypatch.setattr(config, "EDGE_DOCK_ENABLED", True)
        monkeypatch.setattr("pet.action.gravity.get_visible_windows", lambda: [])
        window, system = self._system(qt_app)
        try:
            geo = (window.screen() or QApplication.primaryScreen()).availableGeometry()
            window.move(geo.left() + 2, 100)
            hits: list[str] = []
            system.edge_hit.connect(hits.append)
            system._in_flick = True
            system._vx = -10.0
            system._tick_flick()
            assert hits == ["left"]
            assert not system._in_flick
            assert window.x() == geo.left()
        finally:
            system.enable(False)
            window.deleteLater()

    def test_edge_hit_clamps_y(self, qt_app, monkeypatch):
        monkeypatch.setattr(config, "EDGE_DOCK_ENABLED", True)
        monkeypatch.setattr("pet.action.gravity.get_visible_windows", lambda: [])
        window, system = self._system(qt_app)
        try:
            geo = (window.screen() or QApplication.primaryScreen()).availableGeometry()
            bottom_lim = geo.bottom() - window.height()

            # 向上甩飞撞左边：贴边位置不超过屏幕上界
            window.move(geo.left() + 2, geo.top() + 5)
            system._in_flick = True
            system._vx = -10.0
            system._vy = -25.0
            system._tick_flick()
            assert window.y() == geo.top()

            # 向下甩飞撞左边：贴边位置不超过屏幕下界
            window.move(geo.left() + 2, bottom_lim - 5)
            system._in_flick = True
            system._vx = -10.0
            system._vy = 25.0
            system._tick_flick()
            assert window.y() == bottom_lim
        finally:
            system.enable(False)
            window.deleteLater()

    def test_bounce_when_disabled(self, qt_app, monkeypatch):
        monkeypatch.setattr(config, "EDGE_DOCK_ENABLED", False)
        monkeypatch.setattr("pet.action.gravity.get_visible_windows", lambda: [])
        window, system = self._system(qt_app)
        try:
            geo = (window.screen() or QApplication.primaryScreen()).availableGeometry()
            window.move(geo.left() + 2, 100)
            hits: list[str] = []
            system.edge_hit.connect(hits.append)
            system._in_flick = True
            system._vx = -10.0
            system._tick_flick()
            assert hits == []
            assert system._vx > 0
        finally:
            system.enable(False)
            window.deleteLater()


class TestMenuDock:

    @staticmethod
    def _dock_via_menu(window) -> tuple[list[str], object | None]:
        """走完菜单链路：淡出 → 回调里贴边 → 淡入，返回调用顺序与移动动画终点。"""
        calls: list[str] = []
        captured: dict = {}

        def _fade_out(duration=300, callback=None):
            calls.append("out")
            captured["cb"] = callback

        def _fade_in(duration=300):
            calls.append("in")

        window.pet_actions.fade_out = _fade_out
        window.pet_actions.fade_in = _fade_in
        window._dock_nearest()
        captured["cb"]()                          # 淡出结束
        anim = window._dock_anim
        end = anim.endValue() if anim is not None else None
        if anim is not None:
            anim.finished.emit()                  # 移动动画结束
        return calls, end

    def test_menu_dock_uses_screen_center(self, qt_app):
        window = PetWindow()
        try:
            geo = (window.screen() or QApplication.primaryScreen()).availableGeometry()
            # 窗口中心正好在屏幕中线：贴右
            window.move(geo.center().x() - window.width() // 2, 100)
            self._dock_via_menu(window)
            assert window.dock_side == "right"
            window.undock()

            # 偏左：贴左
            window.move(geo.center().x() - 200, 100)
            self._dock_via_menu(window)
            assert window.dock_side == "left"
        finally:
            window.undock()
            window.shutdown()
            window.deleteLater()

    def test_menu_dock_fades_out_then_in(self, qt_app):
        window = PetWindow()
        try:
            geo = (window.screen() or QApplication.primaryScreen()).availableGeometry()
            window.move(geo.left() + 2, 100)
            calls, _ = self._dock_via_menu(window)
            assert calls == ["out", "in"]
            assert window.is_docked
        finally:
            window.undock()
            window.shutdown()
            window.deleteLater()

    def test_fade_in_waits_for_move(self, qt_app):
        window = PetWindow()
        try:
            geo = (window.screen() or QApplication.primaryScreen()).availableGeometry()
            window.move(geo.left() + 2, 100)
            calls: list[str] = []
            captured: dict = {}

            def _fade_out(duration=300, callback=None):
                captured["cb"] = callback

            window.pet_actions.fade_out = _fade_out
            window.pet_actions.fade_in = lambda duration=300: calls.append("in")

            window._dock_nearest()
            captured["cb"]()                    # 淡出结束，开始移动
            assert calls == []                  # 移动动画还没结束，不该淡入
            window._dock_anim.finished.emit()   # 移动动画结束
            assert calls == ["in"]
        finally:
            window.undock()
            window.shutdown()
            window.deleteLater()

    def test_menu_dock_skipped_when_grabbed(self, qt_app):
        window = PetWindow()
        try:
            geo = (window.screen() or QApplication.primaryScreen()).availableGeometry()
            window.move(geo.left() + 2, 100)
            calls: list[str] = []
            captured: dict = {}

            def _fade_out(duration=300, callback=None):
                captured["cb"] = callback

            window.pet_actions.fade_out = _fade_out
            window.pet_actions.fade_in = lambda duration=300: calls.append("in")

            window._dock_nearest()
            window._grab_local = QPoint(10, 10)     # 淡出期间开始拖拽
            captured["cb"]()
            assert calls == ["in"]
            assert not window.is_docked
            assert window._dock_anim is None
        finally:
            window._grab_local = None
            window.undock()
            window.shutdown()
            window.deleteLater()

    def test_menu_dock_moves_to_vertical_center(self, qt_app):
        window = PetWindow()
        try:
            geo = (window.screen() or QApplication.primaryScreen()).availableGeometry()
            expected_y = max(geo.top(),
                             min(geo.center().y() - window.height() // 2,
                                 geo.bottom() - window.height()))

            # 菜单路径：y 移到可用区垂直中点
            window.move(geo.left() + 2, geo.top() + 5)
            _, end = self._dock_via_menu(window)
            assert end.y() == expected_y
            window.undock()

            # 拖拽松手 / 甩飞撞边：y 保持当前高度
            window.move(geo.left() + 2, geo.top() + 5)
            window.dock_to("left")
            assert window._dock_anim.endValue().y() == geo.top() + 5
        finally:
            window.undock()
            window.shutdown()
            window.deleteLater()


class TestDockEvent:

    def test_labels_live_in_event_table(self):
        assert ContextBuilder._EVENT_LABELS.get("dock")
        assert ContextBuilder._EVENT_LABELS.get("undock")

    def test_agent_notes_event_on_change(self):
        from pet.agent.pet_agent import PetAgent

        agent = PetAgent.__new__(PetAgent)
        agent._dock_side = ""
        agent._food_tool_was_enabled = False
        agent._recent_events = []
        agent._sync_food_tool = lambda: None
        agent.can_interrupt = lambda: False

        agent._on_dock_changed("left")
        assert agent._recent_events[-1][0] == "dock"
        assert agent._recent_events[-1][2] == ""

        agent._on_dock_changed(None)
        assert agent._recent_events[-1][0] == "undock"
        assert agent._recent_events[-1][2] == ""

    def test_both_directions_render(self):
        now = time.time()
        events = [("dock", now - 5, ""), ("undock", now, "")]
        note = ContextBuilder(recent_events_fn=lambda: events)._recent_events_note()
        assert ContextBuilder._EVENT_LABELS["dock"] in note
        assert ContextBuilder._EVENT_LABELS["undock"] in note


class TestDockInterrupt:

    @staticmethod
    def _agent(inflight: bool):
        from pet.agent.pet_agent import PetAgent

        agent = PetAgent.__new__(PetAgent)
        agent._dock_side = ""
        agent._food_tool_was_enabled = False
        agent._recent_events = []
        agent._sync_food_tool = lambda: None
        agent.calls: list[str] = []
        agent.can_interrupt = lambda: inflight
        agent.interrupt_current_call = lambda reason="user": agent.calls.append(reason) or True
        return agent

    def test_dock_interrupts_inflight_call(self):
        agent = self._agent(True)
        agent._on_dock_changed("left")
        assert agent._dock_side == "left"
        assert agent.calls == ["dock state change"]

    def test_undock_interrupts_inflight_call(self):
        agent = self._agent(True)
        agent._dock_side = "left"
        agent._on_dock_changed(None)
        assert agent._dock_side == ""
        assert agent.calls == ["dock state change"]

    def test_idle_call_kept(self):
        agent = self._agent(False)
        agent._on_dock_changed("right")
        assert agent.calls == []


class TestFoodToolSync:

    def test_dock_disables_and_undock_restores(self):
        from pet.agent.pet_agent import PetAgent
        from pet.tools.registry import TOOL_REGISTRY

        if TOOL_REGISTRY.get_tool("food") is None:
            pytest.skip("food 工具未注册")
        TOOL_REGISTRY.set_enabled("food", True)
        agent = PetAgent.__new__(PetAgent)
        agent._dock_side = "right"
        agent._food_tool_was_enabled = False
        try:
            agent._sync_food_tool()
            assert not TOOL_REGISTRY.is_enabled("food")
            agent._dock_side = ""
            agent._sync_food_tool()
            assert TOOL_REGISTRY.is_enabled("food")
        finally:
            TOOL_REGISTRY.set_enabled("food", True)


class TestPetWindowDock:

    def test_dock_mask_and_action_filter(self, qt_app):
        window = PetWindow()
        try:
            screen = window.screen() or QApplication.primaryScreen()
            geo = screen.availableGeometry()
            window.move(dock_x("right", geo.left(), geo.right(), window.width()), 100)
            window.dock_to("right")
            assert window.is_docked
            assert window.dock_side == "right"
            mask = window.mask().boundingRect()
            assert mask.width() == round(window.width() * EDGE_VISIBLE_RATIO)
            assert mask.right() == window.width() - 1
            assert window.pet_actions.idle_animation == "idle_edge"

            for name, kwargs in (("walk", {"direction": "right", "distance": 200}),
                                 ("sit", {"duration": 5}),
                                 ("fade_out", {})):
                window.queue_enqueue_action(name, (), kwargs)
            assert window.action_queue.describe() == []
            window.queue_enqueue_action("sleep", (), {"duration": 5})
            assert window.action_queue.describe()
        finally:
            window.undock()
            window.shutdown()
            window.deleteLater()

    def test_emotion_anchors_to_visible_body(self, qt_app):
        window = PetWindow()
        bubble = EmotionBubble(window)
        try:
            geo = (window.screen() or QApplication.primaryScreen()).availableGeometry()

            # 未贴边：窗口右上角
            window.move(geo.left() + 400, 100)
            assert bubble._final_position(None)[0] == window.x() + config.PET_WIDTH - 16

            # 贴右：移到可见主体靠内的一侧，不再顶到窗口右上角
            window.move(dock_x("right", geo.left(), geo.right(), window.width()), 100)
            window.dock_to("right")
            right_x = bubble._final_position(None)[0]
            assert right_x < window.x() + config.PET_WIDTH - 16
            assert right_x >= geo.left() and right_x + bubble.width() <= geo.right()

            window.undock()
            # 贴左：可见主体在窗口左侧，情绪跟到主体那一侧
            window.move(dock_x("left", geo.left(), geo.right(), window.width()), 100)
            window.dock_to("left")
            left_x = bubble._final_position(None)[0]
            assert left_x < window.x() + config.PET_WIDTH - 16
            assert left_x >= geo.left() and left_x + bubble.width() <= geo.right()
        finally:
            window.undock()
            window.shutdown()
            bubble.deleteLater()
            window.deleteLater()

    def test_speech_tail_follows_dock_side(self, qt_app):
        window = PetWindow()
        bubble = SpeechBubble(window)
        try:
            geo = (window.screen() or QApplication.primaryScreen()).availableGeometry()
            bubble.setText("这是一句足够长的台词，用来把气泡撑到比较宽")
            bubble._resize_to_text()
            bubble.show()

            # 未贴边：尾巴在气泡中央
            window.move(geo.left() + 400, 300)
            bubble._reposition()
            free_tail = bubble._tail_x()
            assert free_tail == bubble.width() // 2

            # 贴右：尾巴偏向贴边那一侧
            window.move(dock_x("right", geo.left(), geo.right(), window.width()), 300)
            window.dock_to("right")
            bubble._reposition()
            assert bubble._tail_x() > free_tail

            # 贴左：尾巴同样偏向贴边那一侧
            window.undock()
            window.move(dock_x("left", geo.left(), geo.right(), window.width()), 300)
            window.dock_to("left")
            bubble._reposition()
            assert bubble._tail_x() < free_tail
        finally:
            bubble.hide()
            window.undock()
            window.shutdown()
            bubble.deleteLater()
            window.deleteLater()

    def test_dock_and_undock_clear_queue(self, qt_app):
        window = PetWindow()
        try:
            geo = (window.screen() or QApplication.primaryScreen()).availableGeometry()
            window.move(dock_x("right", geo.left(), geo.right(), window.width()), 100)

            window.queue_enqueue_action("sleep", (), {"duration": 5})
            assert window.action_queue.describe()
            window.dock_to("right")
            assert window.action_queue.describe() == []

            window.queue_enqueue_action("sleep", (), {"duration": 5})
            assert window.action_queue.describe()
            window.undock()
            assert window.action_queue.describe() == []
        finally:
            window.undock()
            window.shutdown()
            window.deleteLater()
