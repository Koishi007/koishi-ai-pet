"""game 层 游戏注册"""

from pet.game.gamebase import GAME, Game, GameBase

from pet.game.guess_number import GuessNumberGame

GAME.register(GuessNumberGame())

__all__ = ["GAME", "Game", "GameBase"]
