"""猜拳游戏 — 石头剪刀布，三局两胜。

桌宠通过 game__play 传 rps_move 先出拳，用户在猜拳面板上点击出拳；
平局重新出拳；超过 MOVE_TIMEOUT 秒未出拳则结束本局，不算胜负。
等待用户出拳时阻塞脑线程（event.wait），主线程 UI 不受影响。
"""

import logging
import random
import threading
import time

from pet.game.gamebase import Game
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

MOVES = {"rock": "石头", "paper": "布", "scissors": "剪刀"}
_BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}  # key 克制 value


class RockPaperScissorsGame(Game):
    """猜拳：桌宠先出、用户后出，三局两胜。"""

    MOVE_TIMEOUT = 15   # 用户出拳超时（秒），超时结束本局，不算胜负
    NEED_WINS = 2       # 三局两胜

    _WIN_SPEECH = ["哈哈，我赢了…", "三局两胜，我拿下…", "猜拳我赢了…"]
    _LOSE_SPEECH = ["你赢了…哼", "被你赢了呢…", "猜拳输了…"]
    _STOP_SPEECH = ["不比了…", "今天不猜拳了…", "下次再战…"]
    _FORFEIT_SPEECH = ["这么久没动静，猜拳先到这吧…", "你可能在忙，下次再比…"]
    _TIMEOUT_SPEECH = ["这么久没动静，猜拳先到这吧…", "你可能在忙，下次再比…"]

    def name(self) -> str:
        return "rps"

    def description(self) -> str:
        return (
            "猜拳：石头剪刀布，桌宠先出拳、你后出，三局两胜；"
            f"轮到你时在猜拳面板上点击出拳，{self.MOVE_TIMEOUT} 秒内未出拳则本局自动结束。"
        )

    def new_state(self) -> dict:
        return {
            "pet_wins": 0,
            "user_wins": 0,
            "round": 0,
            "need_wins": self.NEED_WINS,
            "wait_event": threading.Event(),  # 跨线程出拳信号
            "pending_move": None,             # 用户待提交的出拳 {"move": ...}
            "move_lock": threading.Lock(),
            "_cancelled": False,
        }

    def args_schema(self) -> dict:
        return {
            "rps_move": {
                "type": "str",
                "required": True,
                "description": "桌宠本回合出的拳：rock（石头）/ paper（布）/ scissors（剪刀）；"
                               "你执桌宠，每次调用传自己出的拳，用户通过猜拳面板点击出拳，无需你传。"
                               "猜拳是同时博弈，绝对不要在任何 speech/语句里说出你会出什么（如'我出石头'），否则用户必赢",
                "enum": ["rock", "paper", "scissors"],
            },
        }

    def play(self, state: dict, **params) -> dict:
        pet_move = params.get("rps_move")
        if pet_move not in MOVES:
            return {
                "summary": f"无效出拳 {pet_move!r}，请用 rock/paper/scissors",
                "ended": False,
                "pet_move": pet_move,
            }

        # 阶段：桌宠出拳 → 等待用户出拳
        self._emit_panel(state, waiting=True, pet_move=None,
                         message="我已出拳，轮到你出拳")
        move = self._wait_for_move(state)
        if state.get("_cancelled"):
            if state.get("_forfeit"):
                # 用户主动关闭面板结束游戏：不算胜负
                return {
                    "summary": "用户主动结束了猜拳，不算胜负",
                    "ended": True,
                    "won": None,
                    "forfeit": True,
                }
            # 外部取消（stop/闲置收场）已由调用方播收场词，抑制兜底台词避免重复
            return {"summary": "游戏已结束", "ended": True, "won": None,
                    "suppress_speech": True}
        if move is None:
            # 用户超时未出拳 → 结束本局，不算胜负
            self._emit_panel(state, waiting=False, pet_move=pet_move,
                             message="长时间未操作，本局结束")
            return {
                "summary": f"你在 {self.MOVE_TIMEOUT} 秒内没有出拳，本局结束",
                "speech": random.choice(self._TIMEOUT_SPEECH),
                "ended": True,
                "won": None,
                "timeout": True,
                "pet_move": pet_move,
            }

        user_move = move.get("move")
        if user_move not in MOVES:
            # 防御：面板只提交合法出拳
            return {
                "summary": f"无效出拳 {user_move!r}，请点击石头/剪刀/布按钮",
                "ended": False,
                "pet_move": pet_move,
            }

        pet_name, user_name = MOVES[pet_move], MOVES[user_move]
        winner = self._judge(pet_move, user_move)
        if winner == "draw":
            self._emit_panel(state, waiting=False, pet_move=pet_move,
                             message=f"你出「{user_name}」，平局，重新出拳")
            return {
                "summary": f"我出「{pet_name}」，你出「{user_name}」，平局，重新出拳",
                "ended": False,
                "result": "draw",
                "pet_move": pet_move,
                "user_move": user_move,
            }

        if winner == "pet":
            state["pet_wins"] += 1
            result_text = f"我「{pet_name}」克制你「{user_name}」"
        else:
            state["user_wins"] += 1
            result_text = f"你「{user_name}」克制我「{pet_name}」"
        state["round"] += 1
        score = f"比分：我 {state['pet_wins']} - {state['user_wins']} 你"

        if state["pet_wins"] >= state["need_wins"]:
            self._emit_panel(state, waiting=False, pet_move=pet_move,
                             message=f"{result_text}，我获胜")
            return {
                "summary": f"{result_text}。{score}，三局两胜我获胜",
                "ended": True,
                "won": True,
                "pet_move": pet_move,
                "user_move": user_move,
                "pet_wins": state["pet_wins"],
                "user_wins": state["user_wins"],
            }
        if state["user_wins"] >= state["need_wins"]:
            self._emit_panel(state, waiting=False, pet_move=pet_move,
                             message=f"{result_text}，你获胜")
            return {
                "summary": f"{result_text}。{score}，三局两胜你获胜",
                "ended": True,
                "won": False,
                "pet_move": pet_move,
                "user_move": user_move,
                "pet_wins": state["pet_wins"],
                "user_wins": state["user_wins"],
            }

        self._emit_panel(state, waiting=False, pet_move=pet_move,
                         message=f"{result_text}，轮到我")
        return {
            "summary": f"{result_text}。{score}，继续下一局",
            "ended": False,
            "result": winner,
            "pet_move": pet_move,
            "user_move": user_move,
            "pet_wins": state["pet_wins"],
            "user_wins": state["user_wins"],
        }

    @staticmethod
    def _judge(pet_move: str, user_move: str) -> str:
        """判定：'pet' 桌宠胜 / 'user' 用户胜 / 'draw' 平局。"""
        if pet_move == user_move:
            return "draw"
        return "pet" if _BEATS[pet_move] == user_move else "user"

    def _wait_for_move(self, state) -> dict | None:
        """阻塞等待用户出拳（脑线程），返回 {move}；超时或取消返回 None。"""
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

    def _emit_panel(self, state: dict, waiting: bool, pet_move: str | None = None,
                    message: str = ""):
        """通知 UI 渲染猜拳面板（Qt 队列连接，跨线程安全）。"""
        try:
            TOOL_CTX.game_board(self.name(), {
                "pet_move": pet_move,
                "waiting": waiting,
                "message": message,
                "timeout": self.MOVE_TIMEOUT if waiting else 0,
                "pet_wins": state["pet_wins"],
                "user_wins": state["user_wins"],
                "need_wins": state["need_wins"],
            })
        except Exception as e:
            logger.warning(f"[RPS] emit panel failed: {e}")

    def win_speech(self) -> str | None:
        return random.choice(self._WIN_SPEECH)

    def lose_speech(self) -> str | None:
        return random.choice(self._LOSE_SPEECH)

    def stop_speech(self) -> str | None:
        return random.choice(self._STOP_SPEECH)

    def forfeit_speech(self) -> str | None:
        return random.choice(self._FORFEIT_SPEECH)
