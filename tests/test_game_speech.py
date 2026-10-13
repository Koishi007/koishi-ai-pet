"""游戏台词与 summary 兜底测试：只锁结构与边界，不锁具体文案。"""

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


class _BoardGame:
    """假游戏：钩子全空，逼出 summary 兜底路径。"""

    def name(self):
        return "fake"

    def win_speech(self):
        return None


@pytest.fixture
def ctx(monkeypatch):
    spy = _CtxSpy()
    monkeypatch.setattr(gamebase, "TOOL_CTX", spy)
    return spy


def test_summary_fallback_keeps_first_line_only(ctx):
    """兜底台词只播首行，棋盘等多行细节不该进气泡。"""
    board = [["O", "", "O"], ["", "X", ""], ["X", "X", ""]]
    lead = "轮到谁了呢"
    summary = f"{lead}\n{_board_text(board)}"

    gamebase.GameBase()._emit_speech(_BoardGame(), {"summary": summary, "ended": False})

    assert len(ctx.spoken) == 1
    spoken = ctx.spoken[0]
    assert spoken == lead  # 期望值取自输入首行，不依赖具体台词文案
    assert "\n" not in spoken


def test_tic_tac_toe_summary_excludes_board(ctx):
    """井字棋 summary 只留自然语言，盘面仍由 board_text 字段交给模型。"""
    game = TicTacToeGame()
    state = game.new_state()
    pet = state["pet_mark"]
    state["turn"] = pet
    state["board"][0][0] = pet
    state["board"][0][1] = pet

    result = game.play(state, ttt_move="A3")

    assert result["ended"] and result["won"] is True
    assert len(result["summary"].splitlines()) == 1
    for line in result["board_text"].splitlines():
        assert line not in result["summary"]


@pytest.mark.parametrize(
    "game",
    [GuessNumberGame(), RockPaperScissorsGame(), TicTacToeGame(), TwentyQuestionsGame()],
    ids=lambda game: game.name(),
)
def test_speech_hooks_return_single_line(game):
    """四类钩子都有词可播且是单行：多行会被兜底逻辑截成首行，这里提前拦住。"""
    for hook in ("win_speech", "lose_speech", "stop_speech", "forfeit_speech"):
        text = getattr(game, hook)()
        assert isinstance(text, str) and text.strip()
        assert "\n" not in text
