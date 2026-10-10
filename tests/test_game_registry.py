"""游戏注册入口测试：整包注册与重复调用。"""

from pet.game import register_all
from pet.game.gamebase import GAME

BUILTIN = {"guess_number", "tic_tac_toe", "rps", "twenty_questions"}


def test_register_all_registers_builtin_games():
    register_all()
    assert BUILTIN <= set(GAME.names())


def test_register_all_is_idempotent():
    register_all()
    before = len(GAME.names())
    register_all()
    assert len(GAME.names()) == before
