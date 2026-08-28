"""game 层 游戏注册"""

from pet.game.gamebase import GAME, Game, GameBase

from pet.game.guess_number import GuessNumberGame
from pet.game.tic_tac_toe import TicTacToeGame
from pet.game.rps import RockPaperScissorsGame
from pet.game.twenty_questions import TwentyQuestionsGame

GAME.register(GuessNumberGame())
GAME.register(TicTacToeGame())
GAME.register(RockPaperScissorsGame())
GAME.register(TwentyQuestionsGame())

__all__ = ["GAME", "Game", "GameBase"]
