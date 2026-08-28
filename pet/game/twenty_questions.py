"""二十问游戏 — 用户心想一个东西，桌宠通过最多 20 个是/否问题猜出它。

桌宠每次 game__play 传 tq_question（一个能用是/否回答的问题）提问，
用户在面板上点击"是/否/不确定"作答；也可以随时传 tq_guess 给出最终猜测，
用户点击"猜对了/猜错了"确认。问题用完仍未猜中算桌宠输。
等待用户作答时阻塞脑线程（event.wait），主线程 UI 不受影响。
"""

import logging
import random
import threading
import time

from pet.game.gamebase import Game
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

ANSWER_TEXT = {"yes": "是", "no": "否", "unknown": "不确定"}


class TwentyQuestionsGame(Game):
    """二十问：桌宠提问猜用户心想之物，最多 20 个是/否问题。"""

    MAX_QUESTIONS = 20   # 最多可问的是/否问题数
    ANSWER_TIMEOUT = 20  # 用户作答/确认超时（秒），超时判用户输

    _WIN_SPEECH = ["我猜中了…嘿嘿", "二十问拿下…", "猜中了，我果然很聪明…"]
    _LOSE_SPEECH = ["没猜中呢…", "猜错了…被你难住了", "再让我想一会就好了…"]
    _STOP_SPEECH = ["不猜了…", "今天先到这吧…", "想不出来，不玩了…"]
    _FORFEIT_SPEECH = ["你认输了吧，算我赢…", "不让我猜了，我赢了…", "你放弃了，算我赢…"]
    _TIMEOUT_SPEECH = ["你太慢了，超时判负…", "等你太久，这局算我赢…"]

    def name(self) -> str:
        return "twenty_questions"

    def description(self) -> str:
        return (
            f"二十问：你心里想一个具体的东西（动物/植物/食物/物品/人物/地点等），"
            f"我来问最多 {self.MAX_QUESTIONS} 个只能用'是/否'回答的问题猜出它；"
            f"你也可以在面板上直接作答，任何时候我都能给出最终猜测，猜中算我赢，"
            f"问题用完还没猜中算我输。"
        )

    def new_state(self) -> dict:
        return {
            "round": 0,
            "max": self.MAX_QUESTIONS,
            "log": [],                      # 问题记录：[{"question", "answer"}]
            "wait_event": threading.Event(),  # 跨线程作答信号
            "pending_move": None,           # 用户提交的 {"answer": ...} 或 {"correct": ...}
            "move_lock": threading.Lock(),
            "_cancelled": False,
        }

    def args_schema(self) -> dict:
        return {
            "tq_question": {
                "type": "str",
                "required": False,
                "description": (
                    "你本轮要问的'是/否'问题：只能问一个，且必须能用'是/否'回答，"
                    "如'它是动物吗'。用户通过面板点击'是/否/不确定'作答，"
                    "你不需要也无法传答案。问题用完前请持续提问来缩小范围。"
                ),
            },
            "tq_guess": {
                "type": "str",
                "required": False,
                "description": (
                    "你的最终猜测：一个具体的东西名（如'苹果'、'猫'、'电影院'）。"
                    "传了 tq_guess 就不再提问，本回合直接由用户确认猜测是否正确；"
                    "问题将尽（tq_question 返回的 questions_left 很小时）务必给出猜测，"
                    "否则问题用完还没猜中算你输。"
                ),
            },
        }

    def play(self, state: dict, **params) -> dict:
        question = (params.get("tq_question") or "").strip()
        guess = (params.get("tq_guess") or "").strip()

        if guess and question:
            return {
                "summary": "tq_question 和 tq_guess 不要同时传：要么问问题，要么给出最终猜测。"
                           "请重新调用 game__play 只传其中一个参数继续本局，不要输出最终答复。",
                "ended": False,
            }
        if guess:
            return self._handle_guess(state, guess)
        if not question:
            return {
                "summary": "请传 tq_question（一个'是/否'问题）继续提问，或传 tq_guess 给出最终猜测；"
                           "请继续调用 game__play 推进本局，不要输出最终答复。",
                "ended": False,
            }
        if state["round"] >= state["max"]:
            return {
                "summary": f"问题已用完（{state['max']} 个），本局尚未结束，"
                           f"请继续调用 game__play 并传 tq_guess 给出最终猜测。",
                "ended": False,
            }

        # 提问：面板展示问题，等待用户是/否/不确定
        state["round"] += 1
        round_no = state["round"]
        self._emit_panel(state, mode="question",
                         text=f"第 {round_no}/{state['max']} 问：{question}",
                         round_text=f"第 {round_no}/{state['max']} 问",
                         waiting=True, message="请作答（是/否/不确定）")
        mv = self._wait_for_move(state)
        if state.get("_cancelled"):
            if state.get("_forfeit"):
                return {
                    "summary": "用户主动结束了二十问，判用户输",
                    "ended": True,
                    "won": False,
                    "forfeit": True,
                }
            return {"summary": "游戏已结束", "ended": True, "won": None,
                    "suppress_speech": True}
        if mv is None:
            self._emit_panel(state, mode="question", text="",
                             round_text="", waiting=False,
                             message="你超时了，桌宠获胜")
            return {
                "summary": f"你在 {self.ANSWER_TIMEOUT} 秒内没有作答，判你输，桌宠获胜",
                "speech": random.choice(self._TIMEOUT_SPEECH),
                "ended": True,
                "won": True,
                "timeout": True,
                "round": round_no,
            }
        answer = mv.get("answer")
        if answer not in ANSWER_TEXT:
            # 防御：面板只提交合法作答，忽略继续等待
            state["round"] -= 1
            return {
                "summary": "作答无效，请重新作答（是/否/不确定）",
                "ended": False,
                "round": state["round"],
            }
        state["log"].append({"question": question, "answer": answer})
        questions_left = state["max"] - round_no
        self._emit_panel(state, mode="question", text="",
                         round_text="", waiting=False,
                         message=f"你的回答：{ANSWER_TEXT[answer]}")
        return {
            "summary": f"第 {round_no} 问：{question} → 你的回答是「{ANSWER_TEXT[answer]}」。"
                       f"（已问 {round_no}/{state['max']}，还可问 {questions_left} 个）"
                       f"本局尚未结束，请继续调用 game__play 提出下一个问题，"
                       f"或把握较大时传 tq_guess 给出最终猜测。",
            "ended": False,
            "answer": answer,
            "round": round_no,
            "questions_left": questions_left,
            "log": list(state["log"]),
        }

    def _handle_guess(self, state: dict, guess: str) -> dict:
        """最终猜测：面板展示猜测，等待用户确认猜对/猜错。"""
        state["round"] += 1
        round_no = state["round"]
        self._emit_panel(state, mode="guess", text=f"我猜是：{guess}",
                         round_text=f"第 {round_no}/{state['max']} 轮",
                         waiting=True, message="我猜对了吗？")
        mv = self._wait_for_move(state)
        if state.get("_cancelled"):
            if state.get("_forfeit"):
                return {
                    "summary": "用户主动结束了二十问，判用户输",
                    "ended": True,
                    "won": False,
                    "forfeit": True,
                }
            return {"summary": "游戏已结束", "ended": True, "won": None,
                    "suppress_speech": True}
        if mv is None:
            self._emit_panel(state, mode="guess", text="",
                             round_text="", waiting=False,
                             message="你超时了，桌宠获胜")
            return {
                "summary": f"你在 {self.ANSWER_TIMEOUT} 秒内没有确认，判你输，桌宠获胜",
                "speech": random.choice(self._TIMEOUT_SPEECH),
                "ended": True,
                "won": True,
                "timeout": True,
                "guess": guess,
                "round": round_no,
            }

        correct = mv.get("correct")
        if correct is None:
            # 防御：面板只提交合法判定，忽略继续等待
            state["round"] -= 1
            return {
                "summary": "判定无效，请确认'猜对了/猜错了'",
                "ended": False,
                "round": state["round"],
            }

        if correct:
            self._emit_panel(state, mode="guess", text="",
                             round_text="", waiting=False,
                             message="猜对了！")
            return {
                "summary": f"我猜中了！答案是「{guess}」，用了 {round_no} 轮",
                "ended": True,
                "won": True,
                "target": guess,
                "round": round_no,
            }

        if round_no >= state["max"]:
            self._emit_panel(state, mode="guess", text="",
                             round_text="", waiting=False,
                             message="没猜中，问题也问完了")
            return {
                "summary": f"我猜的「{guess}」不对，且 {state['max']} 轮已用完，算我输。"
                           f"正确答案就由你悄悄留着吧…",
                "ended": True,
                "won": False,
                "guess": guess,
                "round": round_no,
            }

        questions_left = state["max"] - round_no
        self._emit_panel(state, mode="guess", text="",
                         round_text="", waiting=False,
                         message="没猜中，继续猜")
        return {
            "summary": f"我猜的「{guess}」不对。还剩 {questions_left} 个问题可用。"
                       f"本局尚未结束，请继续调用 game__play 提问或再次给出猜测（tq_guess）。",
            "ended": False,
            "correct": False,
            "guess": guess,
            "round": round_no,
            "questions_left": questions_left,
        }

    def _wait_for_move(self, state) -> dict | None:
        """阻塞等待用户作答/确认（脑线程），返回提交内容；超时或取消返回 None。"""
        ev = state["wait_event"]
        with state["move_lock"]:
            state["pending_move"] = None
        ev.clear()
        deadline = time.monotonic() + self.ANSWER_TIMEOUT
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

    def _emit_panel(self, state: dict, mode: str, text: str, round_text: str,
                    waiting: bool, message: str = ""):
        """通知 UI 渲染二十问面板（Qt 队列连接，跨线程安全）。"""
        try:
            TOOL_CTX.game_board(self.name(), {
                "mode": mode,
                "text": text,
                "round_text": round_text,
                "waiting": waiting,
                "message": message,
                "timeout": self.ANSWER_TIMEOUT if waiting else 0,
            })
        except Exception as e:
            logger.warning(f"[TwentyQuestions] emit panel failed: {e}")

    def win_speech(self) -> str | None:
        return random.choice(self._WIN_SPEECH)

    def lose_speech(self) -> str | None:
        return random.choice(self._LOSE_SPEECH)

    def stop_speech(self) -> str | None:
        return random.choice(self._STOP_SPEECH)

    def forfeit_speech(self) -> str | None:
        return random.choice(self._FORFEIT_SPEECH)
