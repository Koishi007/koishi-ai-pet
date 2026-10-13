"""更大还是更小游戏测试：牌堆与赔率纯函数、判定计分、等待与提交路径。"""

import random
import threading
import time

import pytest

from pet.game.gamebase import GameBase
from pet.game.higher_lower import HigherLowerGame, judge, odds, side_counts


@pytest.fixture
def base(monkeypatch):
    """独立注册表，用例之间互不干扰。"""
    instance = GameBase()
    monkeypatch.setattr("pet.game.GAME", instance)
    instance.register(HigherLowerGame())
    return instance


@pytest.fixture
def game(base):
    return base._games["higher_lower"]


def _session(base):
    """开一局并取出会话状态。"""
    base.init("higher_lower")
    return base._sessions["higher_lower"]


def _start_play(game, state, **params):
    """在辅助线程起一次 play（会阻塞等用户下注），等到真正进入等待再返回。"""
    box = {}

    def runner():
        box["result"] = game.play(state, **params)

    state["wait_event"].set()  # 哨兵：play 进入等待时先 clear，据此判断已起等
    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    deadline = time.monotonic() + 2
    while state["wait_event"].is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    return thread, box


def _finish(thread, box, timeout=5.0):
    thread.join(timeout=timeout)
    assert not thread.is_alive(), "play 未在预期时间内返回"
    return box["result"]


def _round(game, base, state, pet_choice, user_choice, monkeypatch):
    """跑一轮：固定抽到牌堆第一张，两边各押指定边。"""
    monkeypatch.setattr(random, "randrange", lambda n: 0)
    thread, box = _start_play(game, state, hl_choice=pet_choice)
    assert base.submit("higher_lower", {"choice": user_choice})
    return _finish(thread, box)


class TestOdds:
    def test_both_sides(self):
        assert odds(4, 3) == {"higher": 2, "lower": 3}

    def test_cap(self):
        assert odds(5, 1) == {"higher": 2, "lower": 5}

    def test_empty_side_unavailable(self):
        assert odds(1, 0) == {"higher": 1, "lower": None}


class TestSideCounts:
    def test_counts_strictly_compare_with_base(self):
        assert side_counts([1, 2, 3, 4], 2) == (2, 1)

    def test_empty_deck(self):
        assert side_counts([], 5) == (0, 0)


class TestJudge:
    def test_same_side_hit_scores_both(self):
        result = judge(5, 7, "higher", "higher", 4, 4)
        assert result["outcome"] == "same_hit"
        assert result["pet_delta"] == result["user_delta"] > 0

    def test_same_side_miss_changes_nothing(self):
        result = judge(5, 3, "higher", "higher", 4, 4)
        assert result["outcome"] == "same_miss"
        assert result["pet_delta"] == 0 and result["user_delta"] == 0

    def test_user_hit_costs_pet_one(self):
        result = judge(5, 7, "lower", "higher", 4, 4)
        assert result["outcome"] == "user_hit"
        assert result["user_delta"] > 0 and result["pet_delta"] == -1

    def test_pet_hit_costs_user_one(self):
        result = judge(5, 3, "lower", "higher", 1, 1)
        assert result["outcome"] == "pet_hit"
        assert result["pet_delta"] == 2 and result["user_delta"] == -1

    def test_delta_respects_odds_cap(self):
        assert judge(5, 3, "lower", "lower", 5, 1)["pet_delta"] == 5


def test_new_state_opens_first_card_as_base(game):
    state = game.new_state()
    assert len(state["deck"]) == 9
    assert state["base"] not in state["deck"]
    assert state["history"] == [state["base"]]


def test_invalid_choice_does_not_consume_round(game, base):
    state = _session(base)
    result = game.play(state, hl_choice="middle")

    assert result["ended"] is False
    assert len(state["deck"]) == 9 and state["round"] == 0


def test_missing_choice_does_not_consume_round(game, base):
    state = _session(base)
    result = game.play(state)

    assert result["ended"] is False
    assert len(state["deck"]) == 9 and state["round"] == 0


def test_betting_empty_side_is_rejected(game, base):
    state = _session(base)
    state["base"] = 0  # 没有比 0 更小的牌
    result = game.play(state, hl_choice="lower")

    assert result["ended"] is False
    assert result["odds"]["lower"] is None


def test_same_side_hit_scores_both(game, base, monkeypatch):
    state = _session(base)
    state["base"] = 5
    state["deck"] = [7, 3]

    result = _round(game, base, state, "higher", "higher", monkeypatch)

    assert result["outcome"] == "same_hit"
    assert result["pet_score"] == result["user_score"] > 0
    assert result["ended"] is False


def test_loser_score_floors_at_zero(game, base, monkeypatch):
    state = _session(base)
    state["base"] = 5
    state["deck"] = [7, 3]

    result = _round(game, base, state, "lower", "higher", monkeypatch)

    assert result["outcome"] == "user_hit"
    assert result["pet_score"] == 0  # 0 分再扣仍是 0
    assert result["user_score"] > 0


def test_reaching_target_ends_game(game, base, monkeypatch):
    state = _session(base)
    state["base"] = 5
    state["deck"] = [7]
    state["pet_score"] = 5

    result = _round(game, base, state, "higher", "higher", monkeypatch)

    assert result["ended"] is True
    assert result["won"] is True
    assert result["pet_score"] >= 6


def test_both_reach_target_is_draw(game, base, monkeypatch):
    state = _session(base)
    state["base"] = 5
    state["deck"] = [7]
    state["pet_score"] = 5
    state["user_score"] = 5

    result = _round(game, base, state, "higher", "higher", monkeypatch)

    assert result["ended"] is True
    assert result["won"] is None
    assert result["pet_score"] == result["user_score"] >= 6


def test_empty_deck_settles_by_score(game, base, monkeypatch):
    state = _session(base)
    state["base"] = 5
    state["deck"] = [7]

    result = _round(game, base, state, "higher", "higher", monkeypatch)

    assert result["ended"] is True
    assert result["outcome"] == "exhausted"
    assert result["won"] is None  # 同边同分，平局


def test_timeout_ends_without_result(game, base):
    game.MOVE_TIMEOUT = 0.3
    state = _session(base)
    state["base"] = 5
    state["deck"] = [7, 3]

    thread, box = _start_play(game, state, hl_choice="higher")
    result = _finish(thread, box)

    assert result["ended"] is True
    assert result["timeout"] is True
    assert result["won"] is None


def test_cancel_suppresses_speech(game, base):
    state = _session(base)
    state["base"] = 5
    state["deck"] = [7, 3]

    thread, box = _start_play(game, state, hl_choice="higher")
    state["_cancelled"] = True
    state["wait_event"].set()
    result = _finish(thread, box)

    assert result["ended"] is True
    assert result.get("suppress_speech") is True


def test_forfeit_ends_without_result(game, base):
    state = _session(base)
    state["base"] = 5
    state["deck"] = [7, 3]

    thread, box = _start_play(game, state, hl_choice="higher")
    state["_forfeit"] = True
    state["_cancelled"] = True
    state["wait_event"].set()
    result = _finish(thread, box)

    assert result["ended"] is True
    assert result["forfeit"] is True
    assert result["won"] is None
