"""游戏注册入口测试：整包注册、重复调用与等待期的姿态切换。"""

import time

import pytest

from pet.game import register_all
from pet.game.gamebase import Game, GameBase

BUILTIN = {"guess_number", "tic_tac_toe", "rps", "twenty_questions", "higher_lower"}


@pytest.fixture
def registry(monkeypatch):
    """用独立注册表顶替共享单例，用例只看 register_all 自己的注册结果。"""
    instance = GameBase()
    monkeypatch.setattr("pet.game.GAME", instance)
    return instance


def test_register_all_registers_builtin_games(registry):
    register_all()
    assert BUILTIN <= set(registry.names())


def test_register_all_is_idempotent(registry):
    register_all()
    before = len(registry.names())
    register_all()
    assert len(registry.names()) == before


class _PacedGame(Game):
    """play 阻塞指定秒数，用来观察等待期间的姿态切换。"""

    def __init__(self, delay: float):
        self._delay = delay

    def name(self) -> str:
        return "paced"

    def play(self, state: dict, **params) -> dict:
        time.sleep(self._delay)
        return {"headline": "ok", "ended": False}


def _playing_frames(monkeypatch, delay: float, wait_s: float) -> list[str]:
    """跑一次 play，收集期间切过的动作帧。"""
    from pet.game import gamebase

    frames: list[str] = []
    monkeypatch.setattr(gamebase.TOOL_CTX, "play_action_frame",
                        lambda name, index=0: frames.append(name))
    monkeypatch.setattr(gamebase, "_WAIT_FRAME_DELAY_S", wait_s)
    instance = GameBase()
    instance.register(_PacedGame(delay))
    instance.init("paced")
    instance.play("paced")
    return frames


def test_play_switches_to_operate(monkeypatch):
    assert _playing_frames(monkeypatch, delay=0.01, wait_s=5.0)[0] == "operate"


def test_long_wait_switches_to_waiting(monkeypatch):
    frames = _playing_frames(monkeypatch, delay=0.2, wait_s=0.05)
    assert frames == ["operate", "waiting"]


def test_wait_timer_stops_after_round(monkeypatch):
    """本轮提前返回后，计时器不能在本轮之外切帧。"""
    frames = _playing_frames(monkeypatch, delay=0.01, wait_s=0.05)
    time.sleep(0.1)
    assert frames == ["operate"]
