"""猜数字游戏 — 1-100 随机目标，7 次内猜中算赢。"""

import random

from pet.game.gamebase import Game


class GuessNumberGame(Game):
    """每次传 number 猜一个数，反馈大了/小了，7 次内猜中算赢。"""

    RANGE_MIN, RANGE_MAX = 1, 100
    MAX_GUESSES = 7

    def name(self) -> str:
        return "guess_number"

    def description(self) -> str:
        return (
            f"猜数字：我心里想了一个 {self.RANGE_MIN}-{self.RANGE_MAX} 的数字，"
            f"每次传 number 猜一个数，我会告诉你大了还是小了，"
            f"{self.MAX_GUESSES} 次内猜中算赢。"
        )

    def new_state(self) -> dict:
        return {
            "target": random.randint(self.RANGE_MIN, self.RANGE_MAX),
            "guesses": 0,
            "max": self.MAX_GUESSES,
        }

    def play(self, state: dict, **params) -> dict:
        number = params.get("number")
        if not isinstance(number, int) or not (self.RANGE_MIN <= number <= self.RANGE_MAX):
            return {
                "summary": f"请传一个 {self.RANGE_MIN}-{self.RANGE_MAX} 之间的整数 number",
                "ended": False,
            }
        state["guesses"] += 1
        left = state["max"] - state["guesses"]
        if number == state["target"]:
            return {
                "summary": f"猜中了！答案是 {state['target']}，用了 {state['guesses']} 次",
                "ended": True,
                "won": True,
                "target": state["target"],
                "guesses_used": state["guesses"],
            }
        if left <= 0:
            return {
                "summary": f"机会用完了，答案是 {state['target']}。下次加油",
                "ended": True,
                "won": False,
                "target": state["target"],
                "guesses_used": state["guesses"],
            }
        hint = "大了" if number > state["target"] else "小了"
        return {
            "summary": f"猜 {number}：{hint}，还有 {left} 次机会",
            "ended": False,
            "result": hint,
            "guesses_left": left,
            "speech": f"我猜 {number}！{hint}，还有 {left} 次机会",
        }
