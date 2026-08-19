"""game 层 游戏注册"""

from pet.game.gamebase import GAME, Game, GameBase

from pet.game.guess_number import GuessNumberGame
from pet.game.tic_tac_toe import TicTacToeGame

GAME.register(GuessNumberGame())
GAME.register(TicTacToeGame())

__all__ = ["GAME", "Game", "GameBase"]
