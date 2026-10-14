"""游戏注册入口测试：整包注册与重复调用。"""

import pytest

from pet.game import register_all
from pet.game.gamebase import GameBase

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
