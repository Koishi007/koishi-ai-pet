"""游戏层 — 四个回合制小游戏的导出与整包注册。"""

from pet.game.gamebase import GAME, Game, GameBase

from pet.game.guess_number import GuessNumberGame
from pet.game.rps import RockPaperScissorsGame
from pet.game.tic_tac_toe import TicTacToeGame
from pet.game.twenty_questions import TwentyQuestionsGame

__all__ = ["GAME", "Game", "GameBase", "GuessNumberGame", "RockPaperScissorsGame",
           "TicTacToeGame", "TwentyQuestionsGame", "register_all"]


def register_all() -> None:
    """把四个内置游戏登记进 GAME，重复调用无副作用。"""
    for game in (GuessNumberGame(), TicTacToeGame(), RockPaperScissorsGame(), TwentyQuestionsGame()):
        GAME.register(game)
