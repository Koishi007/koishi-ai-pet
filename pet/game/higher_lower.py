"""猜高低对赌游戏 — 0-9 十张牌，双方各押「高/低」，按赔率计分，先得 6 分者获胜。

桌宠经 game__play 传 hl_choice 先下注，用户在面板点击下注；抽牌在双方下注完成后发生，
双方都无法预知结果。等待用户下注时阻塞脑线程（event.wait），主线程 UI 不受影响。
"""

import logging
import math
import random
import threading
import time

from pet.game.gamebase import Game
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

CHOICES = ("higher", "lower")
TARGET_SCORE = 6   # 先得 6 分者获胜
ODDS_CAP = 5       # 单次得分封顶
DECK_SIZE = 10     # 0-9 各一张

_CN = {"higher": "更高", "lower": "更低"}


def side_counts(deck, base: int) -> tuple[int, int]:
    """返回 (above, below)：牌堆中大于、小于 base 的张数。"""
    above = sum(1 for card in deck if card > base)
    below = sum(1 for card in deck if card < base)
    return above, below


def odds(above: int, below: int) -> dict:
    """两边的分值：张数为 0 的边不可选（值 None），分值向上取整并封顶 ODDS_CAP。"""
    total = above + below
    result = {}
    for side, count in (("higher", above), ("lower", below)):
        result[side] = None if count == 0 else min(ODDS_CAP, math.ceil(total / count))
    return result


def judge(base: int, revealed: int, pet_choice: str, user_choice: str,
          above: int, below: int) -> dict:
    """判定一轮：返回双方分数变化与结果键"""
    odds_map = odds(above, below)
    pet_hit = revealed > base if pet_choice == "higher" else revealed < base
    user_hit = revealed > base if user_choice == "higher" else revealed < base
    if pet_choice == user_choice:
        if pet_hit:
            return {"pet_delta": odds_map[pet_choice], "user_delta": odds_map[pet_choice],
                    "outcome": "same_hit"}
        return {"pet_delta": 0, "user_delta": 0, "outcome": "same_miss"}
    if user_hit:
        return {"pet_delta": -1, "user_delta": odds_map[user_choice], "outcome": "user_hit"}
    return {"pet_delta": odds_map[pet_choice], "user_delta": -1, "outcome": "pet_hit"}


class HigherLowerGame(Game):
    """猜高低：桌宠先押、用户后押，抽牌后按赔率计分，先得 6 分者获胜。"""

    MOVE_TIMEOUT = 15  # 用户下注超时（秒），超时结束本局，不算胜负

    _WIN_SPEECH = ["压中了…嘿嘿ー", "恋恋押对了…", "分数够啦…诶～"]
    _LOSE_SPEECH = ["唔…差一点", "被你押中了呀…", "分数不够呢…"]
    _STOP_SPEECH = ["不猜了…牌收起来", "嗯～先到这ー", "牌堆藏起来了…"]
    _FORFEIT_SPEECH = ["人不见了…牌也收起来", "唔…那就到这吧"]
    _TIMEOUT_SPEECH = ["等不到你…牌先收起来", "唔…数字凉了呀"]

    def name(self) -> str:
        return "higher_lower"

    def description(self) -> str:
        return (
            "猜高低：0-9 十张牌，双方各自押下一张牌比基准高还是低，抽牌在双方下注后发生；"
            f"押中按赔率得 1-{ODDS_CAP} 分，两边押不同时押错方扣 1 分，先得 {TARGET_SCORE} 分者获胜；"
            f"轮到你时在面板点击下注，{self.MOVE_TIMEOUT} 秒内未下注则本局自动结束。"
        )

    def new_state(self) -> dict:
        deck = list(range(DECK_SIZE))
        random.shuffle(deck)
        base = deck.pop(0)  # 首张作基准，移出牌堆
        return {
            "deck": deck,
            "base": base,
            "history": [base],
            "user_score": 0,
            "pet_score": 0,
            "round": 0,
            "target": TARGET_SCORE,
            "wait_event": threading.Event(),  # 跨线程下注信号
            "pending_move": None,             # 用户待提交的下注 {"choice": ...}
            "move_lock": threading.Lock(),
            "_cancelled": False,
        }

    def args_schema(self) -> dict:
        return {
            "hl_choice": {
                "type": "str",
                "required": True,
                "description": "桌宠本轮押注：higher（下一张比基准高）/ lower（下一张比基准低）；"
                               "每次调用传自己押的边，用户通过面板点击下注，无需你传；"
                               "抽牌发生在用户点击之后，不要在台词里暗示已知道结果",
                "enum": list(CHOICES),
            },
        }

    def play(self, state: dict, **params) -> dict:
        deck = state["deck"]
        base = state["base"]
        above, below = side_counts(deck, base)
        odds_map = odds(above, below)

        choice = params.get("hl_choice")
        if choice not in CHOICES:
            return {
                "headline": f"无效押注 {choice!r}，请用 higher/lower",
                "ended": False,
                "hl_choice": choice,
                "base": base,
                "above": above,
                "below": below,
                "odds": odds_map,
            }
        if odds_map[choice] is None:
            other = "lower" if choice == "higher" else "higher"
            return {
                "headline": f"基准 {base} 没有比它{_CN[choice]}的牌了，请改押{_CN[other]}",
                "ended": False,
                "hl_choice": choice,
                "base": base,
                "above": above,
                "below": below,
                "odds": odds_map,
            }

        self._emit_panel(state, waiting=True, pet_bet=True, message="轮到你下注")
        move = self._wait_for_move(state)
        if state.get("_cancelled"):
            if state.get("_forfeit"):
                return {
                    "headline": "用户主动结束了猜高低，不算胜负",
                    "ended": True,
                    "won": None,
                    "forfeit": True,
                }
            return {"headline": "游戏已结束", "ended": True, "won": None,
                    "suppress_speech": True}
        if move is None:
            self._emit_panel(state, waiting=False, pet_bet=True,
                             message="长时间未操作，本局结束")
            return {
                "headline": f"你在 {self.MOVE_TIMEOUT} 秒内没有下注，本局结束",
                "speech": random.choice(self._TIMEOUT_SPEECH),
                "ended": True,
                "won": None,
                "timeout": True,
            }

        user_choice = move.get("choice")
        if user_choice not in CHOICES or odds_map[user_choice] is None:
            # 防御：面板只提交可选边，异常值不消耗回合
            return {
                "headline": f"无效押注 {user_choice!r}，请点击面板上的下注按钮",
                "ended": False,
                "hl_choice": choice,
                "base": base,
                "above": above,
                "below": below,
                "odds": odds_map,
            }

        revealed = deck.pop(random.randrange(len(deck)))
        judged = judge(base, revealed, choice, user_choice, above, below)
        state["user_score"] = max(0, state["user_score"] + judged["user_delta"])
        state["pet_score"] = max(0, state["pet_score"] + judged["pet_delta"])
        state["round"] += 1
        state["base"] = revealed
        state["history"].append(revealed)

        pet_score, user_score = state["pet_score"], state["user_score"]
        score_text = f"比分：我 {pet_score} - {user_score} 你"
        detail = self._detail(judged, choice)
        outcome = judged["outcome"]
        ended, won = False, None
        if pet_score >= TARGET_SCORE and user_score >= TARGET_SCORE:
            ended, won = True, None  # 同次揭晓双方同到目标分：平局
        elif pet_score >= TARGET_SCORE:
            ended, won = True, True
        elif user_score >= TARGET_SCORE:
            ended, won = True, False
        elif not deck:
            ended, outcome = True, "exhausted"
            won = True if pet_score > user_score else (False if user_score > pet_score else None)
            detail = f"{detail}，牌堆已空"

        self._emit_panel(state, waiting=False, pet_bet=True, pet_choice=choice,
                         revealed=revealed, outcome=outcome,
                         message=f"揭晓 {revealed}，{score_text}")
        return {
            "headline": (f"基准 {base}，我押{_CN[choice]}，你押{_CN[user_choice]}，"
                        f"揭晓 {revealed}；{detail}。{score_text}"),
            "ended": ended,
            "won": won,
            "outcome": outcome,
            "revealed": revealed,
            "pet_choice": choice,
            "user_choice": user_choice,
            "pet_delta": judged["pet_delta"],
            "user_delta": judged["user_delta"],
            "pet_score": pet_score,
            "user_score": user_score,
            "target": TARGET_SCORE,
            "base": state["base"],
            "history": list(state["history"]),
            "deck": list(deck),
            "above": above,
            "below": below,
            "odds": odds_map,
        }

    @staticmethod
    def _detail(judged: dict, choice: str) -> str:
        """把判定结果写成一句自然语言（供 summary 与日志阅读）。"""
        outcome = judged["outcome"]
        if outcome == "same_hit":
            return f"两边都押{_CN[choice]}，命中 +{judged['pet_delta']}"
        if outcome == "same_miss":
            return f"两边都押{_CN[choice]}，没中"
        if outcome == "user_hit":
            return f"你押中 +{judged['user_delta']}，我 -1"
        return f"我押中 +{judged['pet_delta']}，你 -1"

    def _wait_for_move(self, state) -> dict | None:
        """阻塞等待用户下注（脑线程），返回 {choice}；超时或取消返回 None。"""
        ev = state["wait_event"]
        with state["move_lock"]:
            state["pending_move"] = None
        ev.clear()
        deadline = time.monotonic() + self.MOVE_TIMEOUT
        while True:
            if state.get("_cancelled"):
                return None
            ev.wait(timeout=0.2)
            with state["move_lock"]:
                mv = state["pending_move"]
            if mv is not None:
                return mv
            if time.monotonic() >= deadline:
                return None

    def _emit_panel(self, state: dict, waiting: bool, pet_bet: bool = False,
                    pet_choice: str | None = None,
                    revealed: int | None = None, outcome: str | None = None,
                    message: str = ""):
        """通知 UI 渲染猜高低面板"""
        above, below = side_counts(state["deck"], state["base"])
        try:
            TOOL_CTX.game_board(self.name(), {
                "base": state["base"],
                "history": list(state["history"]),
                "deck": list(state["deck"]),
                "above": above,
                "below": below,
                "odds": odds(above, below),
                "pet_bet": pet_bet,
                "pet_choice": pet_choice,
                "user_score": state["user_score"],
                "pet_score": state["pet_score"],
                "target": TARGET_SCORE,
                "revealed": revealed,
                "outcome": outcome,
                "waiting": waiting,
                "message": message,
                "timeout": self.MOVE_TIMEOUT if waiting else 0,
            })
        except Exception as e:
            logger.warning(f"[HigherLower] emit panel failed: {e}")

    def win_speech(self) -> str | None:
        return random.choice(self._WIN_SPEECH)

    def lose_speech(self) -> str | None:
        return random.choice(self._LOSE_SPEECH)

    def stop_speech(self) -> str | None:
        return random.choice(self._STOP_SPEECH)

    def forfeit_speech(self) -> str | None:
        return random.choice(self._FORFEIT_SPEECH)
