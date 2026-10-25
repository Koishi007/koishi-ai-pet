"""游戏台词与结果字段测试：只锁结构与边界，不锁具体文案。"""

import pytest

from pet.game import gamebase
from pet.game.guess_number import GuessNumberGame
from pet.game.rps import RockPaperScissorsGame
from pet.game.tic_tac_toe import TicTacToeGame, _board_text
from pet.game.twenty_questions import TwentyQuestionsGame


class _CtxSpy:
    """顶替 TOOL_CTX：记录播出内容，不触达真实信号与 UI。"""

    def __init__(self):
        self.spoken = []
        self.notes = []

    def speech(self, text, duration=5000):
        self.spoken.append(text)

    def note_event(self, kind, text=""):
        self.notes.append((kind, text))

    def is_model_aside_pending(self):
        return False

    def game_board(self, game_name, payload):
        pass

    def hide_game_board(self, game_name):
        pass


class _SilentGame:
    """假游戏：钩子全空，用来验证无词可播时保持沉默。"""

    def name(self):
        return "fake"

    def win_speech(self):
        return None


class _WinningGame(_SilentGame):
    def win_speech(self):
        return "fake-win-line"


@pytest.fixture
def ctx(monkeypatch):
    spy = _CtxSpy()
    monkeypatch.setattr(gamebase, "TOOL_CTX", spy)
    return spy


def test_headline_is_never_spoken(ctx):
    """headline 只面向模型：即使内容完整也不播出，没有台词就保持沉默。"""
    board = [["O", "", "O"], ["", "X", ""], ["X", "X", ""]]
    headline = f"轮到谁了呢\n{_board_text(board)}"

    gamebase.GameBase()._emit_speech(_SilentGame(), {"headline": headline, "ended": False})

    assert ctx.spoken == []


def test_end_event_still_speaks(ctx):
    """收尾事件仍由游戏钩子出声（模型沉默时也有人说话）。"""
    gamebase.GameBase()._emit_speech(
        _WinningGame(), {"summary": "一行事实", "ended": True, "won": True})

    assert ctx.spoken == ["fake-win-line"]


def _started_base():
    """起一局并固定成「轮到桌宠」的局面，避免 play 阻塞等用户落子。"""
    base = gamebase.GameBase()
    base.register(TicTacToeGame())
    base.init("tic_tac_toe")
    state = base._sessions["tic_tac_toe"]
    state["turn"] = state["pet_mark"]
    return base, state


def test_running_round_hint(ctx):
    """未结束的回合在 agent_hint 里给参数提示，headline 保持一行事实。"""
    base, _ = _started_base()

    result = base.play("tic_tac_toe")

    assert "ttt_move" in result["agent_hint"]
    assert len(result["headline"].splitlines()) == 1


def test_ended_round_hint(ctx):
    """结束的回合给收尾结算指令（与未结束分支对称覆盖）。"""
    base, state = _started_base()
    pet = state["pet_mark"]
    state["board"][0][0] = pet
    state["board"][0][1] = pet

    result = base.play("tic_tac_toe", ttt_move="A3")

    assert result["ended"] and result["won"] is True
    assert result["agent_hint"] is gamebase._MODEL_HINT_ENDED


def test_tic_tac_toe_headline_excludes_board(ctx):
    """井字棋 headline 只留自然语言，盘面仍由 board_text 字段交给模型。"""
    game = TicTacToeGame()
    state = game.new_state()
    pet = state["pet_mark"]
    state["turn"] = pet
    state["board"][0][0] = pet
    state["board"][0][1] = pet

    result = game.play(state, ttt_move="A3")

    assert result["ended"] and result["won"] is True
    for line in result["board_text"].splitlines():
        assert line not in result["headline"]


def test_invalid_move_puts_instruction_in_hint(ctx):
    """无效落子的纠正指令进 agent_hint，headline 只留事实。"""
    base, _ = _started_base()

    result = base.play("tic_tac_toe", ttt_move="Z9")

    assert "A1~C3" not in result["headline"]
    assert "A1~C3" in result["agent_hint"]


@pytest.mark.parametrize(
    "game",
    [GuessNumberGame(), RockPaperScissorsGame(), TicTacToeGame(), TwentyQuestionsGame()],
    ids=lambda game: game.name(),
)
def test_speech_hooks_return_single_line(game):
    """四类钩子都有词可播且是单行。"""
    for hook in ("win_speech", "lose_speech", "stop_speech", "forfeit_speech"):
        text = getattr(game, hook)()
        assert isinstance(text, str) and text.strip()
        assert "\n" not in text
