"""帧动画节奏测试：tick 分配、呼吸位移、配置修正、动作超时兜底。"""

import pytest

from pet.action.action_queue import ActionQueue
from pet.config import config
from pet.ui.pet_animations import PetAnimator


class TestBuildTickPlan:
    def test_ratio_pair_kept(self):
        plan = PetAnimator._build_tick_plan({"tick_counts": 60, "frame_ratios": [0.9, 0.1]}, 2)
        assert plan == [54, 6]

    def test_indivisible_split_stays_even(self):
        # 8 帧 / 90 tick：逐帧独立取整会得到 11×7 + 13，累计取整只差 1 tick
        plan = PetAnimator._build_tick_plan({"tick_counts": 90, "frame_ratios": [0.125] * 8}, 8)
        assert sum(plan) == 90
        assert max(plan) - min(plan) <= 1

    def test_every_frame_gets_at_least_one_tick(self):
        plan = PetAnimator._build_tick_plan({"tick_counts": 5, "frame_ratios": [0.5, 0.5]}, 2)
        assert all(t >= 1 for t in plan)
        assert sum(plan) == 5

    def test_missing_ratios_split_evenly(self):
        assert PetAnimator._build_tick_plan({"tick_counts": 30}, 3) == [10, 10, 10]

    def test_ratio_length_mismatch_falls_back_to_even(self):
        cfg = {"tick_counts": 30, "frame_ratios": [0.5, 0.5]}
        assert PetAnimator._build_tick_plan(cfg, 3) == [10, 10, 10]


class TestSanitizeConfig:
    def test_ratio_sum_normalized(self):
        cfg = PetAnimator._sanitize_config({"tick_counts": 60, "frame_ratios": [3, 1]}, 2, "demo")
        assert cfg["frame_ratios"] == [0.75, 0.25]

    def test_ratio_length_mismatch_dropped(self):
        cfg = PetAnimator._sanitize_config(
            {"tick_counts": 60, "frame_ratios": [0.5, 0.5]}, 3, "demo")
        assert "frame_ratios" not in cfg

    def test_ratios_not_a_list_dropped(self):
        cfg = PetAnimator._sanitize_config({"tick_counts": 60, "frame_ratios": 1.0}, 2, "demo")
        assert "frame_ratios" not in cfg

    def test_non_positive_ratio_dropped(self):
        cfg = PetAnimator._sanitize_config({"tick_counts": 60, "frame_ratios": [1.0, 0]}, 2, "demo")
        assert "frame_ratios" not in cfg

    def test_tick_counts_below_frame_count_bumped(self):
        cfg = PetAnimator._sanitize_config({"tick_counts": 2}, 8, "demo")
        assert cfg["tick_counts"] == 8

    def test_tick_counts_garbage_falls_back(self):
        assert PetAnimator._sanitize_config({"tick_counts": "abc"}, 2, "demo")["tick_counts"] == 30

    def test_input_config_not_mutated(self):
        original = {"tick_counts": 2}
        PetAnimator._sanitize_config(original, 8, "demo")
        assert original == {"tick_counts": 2}


class TestBreathPose:
    def test_rest_at_cycle_start(self):
        assert PetAnimator._breath_pose(0, 2, 1.01, 0.99, 30) == (0, 1.0, 1.0)

    def test_peak_at_halfway(self):
        dy, sx, sy = PetAnimator._breath_pose(15, 2, 1.01, 0.99, 30)
        assert dy == -2
        assert sx == pytest.approx(1.01)
        assert sy == pytest.approx(0.99)

    def test_returns_to_rest_at_cycle_end(self):
        assert PetAnimator._breath_pose(30, 2, 1.01, 0.99, 30) == (0, 1.0, 1.0)

    def test_offset_never_sinks_below_rest(self):
        # 只向上抬，否则脚底会被窗口裁掉
        offsets = [PetAnimator._breath_pose(t, 2, 1.01, 0.99, 30)[0] for t in range(90)]
        assert all(-2 <= o <= 0 for o in offsets)

    def test_scale_never_shrinks_below_one(self):
        # 缩放在吸气时张开、呼气时回到 1，不会缩到比原图更小
        scales = [PetAnimator._breath_pose(t, 2, 1.01, 0.99, 30)[1] for t in range(90)]
        assert min(scales) == pytest.approx(1.0)
        assert max(scales) == pytest.approx(1.01)

    def test_volume_roughly_kept(self):
        # 1.01 × 0.99 ≈ 1：呼吸的缩放近似保体积
        _, sx, sy = PetAnimator._breath_pose(15, 2, 1.01, 0.99, 30)
        assert sx * sy == pytest.approx(1.0, abs=0.001)

    def test_scale_only_config_keeps_offset_zero(self):
        dy, sx, sy = PetAnimator._breath_pose(15, 0, 1.02, 0.98, 30)
        assert dy == 0
        assert sx == pytest.approx(1.02)
        assert sy == pytest.approx(0.98)

    def test_single_pixel_amplitude_still_moves(self):
        # 浮点连续位移：1px 振幅下仍有 0 与 −1 之间的中间值
        offsets = [PetAnimator._breath_pose(t, 1, 1.0, 1.0, 30)[0] for t in range(30)]
        assert max(offsets) == pytest.approx(0.0, abs=1e-9)
        assert min(offsets) == pytest.approx(-1.0)
        assert any(0 > o > -1 for o in offsets)  # 存在中间值，证明连续而非两级跳变

    def test_disabled_returns_rest(self):
        assert PetAnimator._breath_pose(15, 2, 1.01, 0.99, 0) == (0, 1.0, 1.0)


class TestParseBreath:
    REST = {"amplitude": 0, "scale_x": 1.0, "scale_y": 1.0, "period_ticks": 0}

    def test_missing_breath_is_off(self):
        assert PetAnimator._parse_breath({}, "demo") == self.REST

    def test_values_clamped(self):
        cfg = {"breath": {"amplitude": 9, "scale_x": 3.0, "scale_y": 0.1, "period_ticks": 5000}}
        assert PetAnimator._parse_breath(cfg, "demo") == {
            "amplitude": 4, "scale_x": 2.0, "scale_y": 0.5, "period_ticks": 900}

    def test_garbage_breath_ignored(self):
        assert PetAnimator._parse_breath({"breath": {"amplitude": "big"}}, "demo") == self.REST

    def test_scale_defaults_to_one(self):
        parsed = PetAnimator._parse_breath({"breath": {"amplitude": 2, "period_ticks": 30}}, "demo")
        assert (parsed["scale_x"], parsed["scale_y"]) == (1.0, 1.0)


class TestAnimTimeout:
    def test_base_timeout_without_duration(self):
        assert ActionQueue._anim_timeout_ms({}) == max(1000, config.ACTION_TIMEOUT_MS)

    def test_extended_beyond_action_duration(self):
        # sleep 默认时长 108s，超过 90s 兜底超时
        assert ActionQueue._anim_timeout_ms({"duration": 108}) >= 108_000

    def test_garbage_duration_falls_back_to_base(self):
        assert ActionQueue._anim_timeout_ms({"duration": "abc"}) == max(1000, config.ACTION_TIMEOUT_MS)


_APP = None  # 必须持有引用，否则 QApplication 会被回收


@pytest.fixture(scope="module")
def animator():
    global _APP
    from PySide6.QtWidgets import QApplication
    try:
        _APP = QApplication.instance() or QApplication([])
    except Exception as e:  # 无显示环境
        pytest.skip(f"Qt 不可用: {e}")
    anim = PetAnimator()
    yield anim
    anim.stop()


class TestBreathWiring:
    """走真实素材：确认 JSON 配置能传到动画器并按时发姿态。"""

    def test_idle_declares_bob_only(self, animator):
        breath = animator._load_action("idle")["breath"]
        assert breath["amplitude"] > 0
        assert (breath["scale_x"], breath["scale_y"]) == (1.0, 1.0)
        assert breath["period_ticks"] > 0

    def test_sleep_declares_stretch_only(self, animator):
        breath = animator._load_action("sleep")["breath"]
        assert breath["amplitude"] == 0
        assert breath["scale_x"] > 1.0 > breath["scale_y"]
        # 躺姿素材满宽（511/512），X 张太多会横向切边
        assert breath["scale_x"] <= 1.02

    def test_pose_emitted_at_peak_and_reset(self, animator):
        breath = animator._load_action("idle")["breath"]
        period = breath["period_ticks"]
        seen: list[tuple] = []
        animator.pose_changed.connect(lambda dy, sx, sy: seen.append((dy, sx, sy)))

        assert animator.play("idle", duration=1)
        for _ in range(period // 2):  # 半周期处吸到最深处
            animator._next_frame()
        dy, sx, sy = seen[-1]
        assert dy == -breath["amplitude"]
        assert (sx, sy) == (1.0, 1.0)  # 待机只做位移

        animator.stop()
        assert seen[-1] == (0, 1.0, 1.0)


class TestUnconsciousness:
    """无意识化素材：6 帧 one-shot，播完停在最后一帧。"""

    def test_loads_as_six_frame_one_shot(self, animator):
        data = animator._load_action("unconsciousness")
        assert data is not None
        assert data["loop"] is False
        assert len(data["frames"]) == 6
        assert len(data["tick_plan"]) == 6

    def test_holds_last_frame_after_finish(self, animator):
        shown = []
        animator.frame_changed.connect(shown.append)
        assert animator.play("unconsciousness")
        for _ in range(sum(animator._tick_plan)):
            animator._next_frame()
        assert animator.is_playing is False
        # 信号传值会重建 python 包装，用 cacheKey 判定是同一份像素
        assert shown[-1].cacheKey() == animator._frames[-1].cacheKey()
        animator.stop()
