"""井字棋游戏 — 3x3 棋盘，开局随机决定先后手（X 先手）。

桌宠与用户执子随机：先手方执 X、后手方执 O。
用户在棋盘面板上点击落子；超过 MOVE_TIMEOUT 秒未落子直接判用户输。
等待用户落子时阻塞脑线程（event.wait），主线程 UI 不受影响。
"""

import logging
import random
import threading
import time

from pet.game.gamebase import Game
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

PET_MARK = "X"
USER_MARK = "O"

# 行列标签：A1~C3，A 为第 0 行、1 为第 0 列
_GRID_LABELS = [
    ["A1", "A2", "A3"],
    ["B1", "B2", "B3"],
    ["C1", "C2", "C3"],
]
_POS_MAP = {label: (r, c) for r, row in enumerate(_GRID_LABELS) for c, label in enumerate(row)}


def _check_winner(board) -> str | None:
    """返回获胜方标记（X/O），无则返回 None。"""
    for i in range(3):
        if board[i][0] and board[i][0] == board[i][1] == board[i][2]:
            return board[i][0]
        if board[0][i] and board[0][i] == board[1][i] == board[2][i]:
            return board[0][i]
    if board[0][0] and board[0][0] == board[1][1] == board[2][2]:
        return board[0][0]
    if board[0][2] and board[0][2] == board[1][1] == board[2][0]:
        return board[0][2]
    return None


def _is_full(board) -> bool:
    return all(cell for row in board for cell in row)


def _board_text(board) -> str:
    """棋盘文本视图，供模型读取当前局势（X/O 即盘面标记）。"""
    lines = ["  1 2 3"]
    for i, row in enumerate(board):
        cells = [c or "." for c in row]
        lines.append(f"{'ABC'[i]} {' '.join(cells)}")
    return "\n".join(lines)


class TicTacToeGame(Game):
    """井字棋：开局随机先后手，先手方执 X。"""

    MOVE_TIMEOUT = 15  # 用户落子超时（秒），超时直接判用户输

    _WIN_SPEECH = ["嘿嘿，三连了，我赢了…", "我赢了…这局拿下…", "三连，赢了…"]
    _LOSE_SPEECH = ["被你赢了呢…", "呜，你赢了…", "这局是你赢了…"]
    _STOP_SPEECH = ["不下棋了…", "棋盘收起来了…", "下次再战…"]
    _FORFEIT_SPEECH = ["你自己认输了吧，算我赢…", "不下了认输，我赢了…", "你弃权了，这局归我…"]
    _TIMEOUT_SPEECH = ["你太慢了，超时判负…", "等你太久，这局算我赢…", "落子超时，我赢了…"]

    def name(self) -> str:
        return "tic_tac_toe"

    def description(self) -> str:
        return (
            "井字棋：3x3 棋盘，你和桌宠轮流落子，开局随机决定先后手（X 先手），"
            f"先连成三子获胜；轮到你时在棋盘面板上点击落子，{self.MOVE_TIMEOUT} 秒内未落子算你输。"
        )

    def new_state(self) -> dict:
        """随机先后手：先手方执 X，后手方执 O。"""
        pet_first = random.choice([True, False])
        return {
            "board": [[""] * 3 for _ in range(3)],
            "pet_mark": PET_MARK if pet_first else USER_MARK,
            "user_mark": USER_MARK if pet_first else PET_MARK,
            "turn": PET_MARK,  # X 恒先手：桌宠先手则桌宠执 X，用户先手则用户执 X
            "first": "pet" if pet_first else "user",
            "wait_event": threading.Event(),  # 跨线程落子信号
            "pending_move": None,             # 用户待落子的坐标 {row, col}
            "move_lock": threading.Lock(),
            "_cancelled": False,
        }

    def args_schema(self) -> dict:
        return {
            "ttt_move": {
                "type": "str",
                "required": False,
                "description": (
                    "本回合桌宠落子的位置，用 A1~C3 格式（A 为行、1 为列），如 A1、B2、C3；"
                    "开局先后手随机（X 先手），你执 X 还是 O、是否先手，以首次 game__play 返回的 "
                    "pet_mark/user_mark/first 字段为准；仅当轮到桌宠落子时才传 ttt_move，"
                    "用户落子通过棋盘面板点击完成，无需你传。"
                ),
            },
        }

    def play(self, state: dict, **params) -> dict:
        board = state["board"]
        pet_mark = state["pet_mark"]
        user_mark = state["user_mark"]

        # 首次调用：在 summary 里告知双方执子与先后手，供模型 speech 转述给用户
        first_notice = ""
        if not state.get("_notified"):
            state["_notified"] = True
            if state["first"] == "user":
                first_notice = f"你执 {pet_mark}，用户执 {user_mark}，用户先手。"
            else:
                first_notice = f"你执 {pet_mark}，用户执 {user_mark}，你（桌宠）先手。"

        def _s(text: str) -> str:
            return f"{first_notice}{text}"

        # 阶段 1：轮到桌宠时处理模型落子
        if state["turn"] == pet_mark:
            if not params.get("ttt_move"):
                return {
                    "summary": _s("轮到桌宠（你）落子，请传 ttt_move 参数（A1~C3）。"),
                    "ended": False,
                    "pet_mark": pet_mark,
                    "user_mark": user_mark,
                    "first": state["first"],
                    "board_text": _board_text(board),
                }
            pos = _POS_MAP.get(str(params["ttt_move"]).strip().upper())
            if pos is None:
                return {
                    "summary": _s(f"无效位置 {params['ttt_move']}，请用 A1~C3 格式"),
                    "ended": False,
                    "pet_mark": pet_mark,
                    "user_mark": user_mark,
                    "first": state["first"],
                    "board_text": _board_text(board),
                }
            r, c = pos
            if board[r][c]:
                return {
                    "summary": _s(f"位置 {params['ttt_move']} 已经有子了，请换个位置"),
                    "ended": False,
                    "pet_mark": pet_mark,
                    "user_mark": user_mark,
                    "first": state["first"],
                    "board_text": _board_text(board),
                }
            board[r][c] = pet_mark
            state["turn"] = user_mark
            winner = _check_winner(board)
            if winner == pet_mark:
                self._finish(state, "桌宠获胜")
                return {
                    "summary": _s(f"桌宠落子 {params['ttt_move']} 连成三子，桌宠获胜。\n{_board_text(board)}"),
                    "ended": True,
                    "won": True,
                    "pet_mark": pet_mark,
                    "user_mark": user_mark,
                    "first": state["first"],
                    "board": board,
                    "board_text": _board_text(board),
                }
            if _is_full(board):
                self._finish(state, "平局")
                return {
                    "summary": _s(f"桌宠落子 {params['ttt_move']} 后棋盘已满，平局。\n{_board_text(board)}"),
                    "ended": True,
                    "won": None,
                    "pet_mark": pet_mark,
                    "user_mark": user_mark,
                    "first": state["first"],
                    "board": board,
                    "board_text": _board_text(board),
                }

        # 阶段 2：等待用户落子（阻塞脑线程，UI 不受影响）
        self._emit_board(state, waiting=True, message="轮到你落子")
        move = self._wait_for_move(state)
        if state.get("_cancelled"):
            if state.get("_forfeit"):
                # 用户主动关闭棋盘结束游戏：判用户输，结果以工具形式返回给模型
                return {
                    "summary": _s(f"用户主动结束了游戏，判用户输。\n{_board_text(board)}"),
                    "ended": True,
                    "won": False,
                    "forfeit": True,
                    "pet_mark": pet_mark,
                    "user_mark": user_mark,
                    "first": state["first"],
                    "board": board,
                    "board_text": _board_text(board),
                }
            self._emit_board(state, waiting=False, message="游戏已结束")
            # 外部取消（如 game__stop/闲置收场）已由调用方播收场词，这里抑制兜底台词避免重复
            return {"summary": _s("游戏已结束"), "ended": True, "won": None,
                    "suppress_speech": True, "board": board}
        if move is None:
            # 用户超时未落子 → 直接判用户输（独立超时台词，不走 win_speech）
            self._emit_board(state, waiting=False, message="你超时了，桌宠获胜")
            return {
                "summary": _s(f"你在 {self.MOVE_TIMEOUT} 秒内没有落子，判你输，桌宠获胜。\n{_board_text(board)}"),
                "speech": random.choice(self._TIMEOUT_SPEECH),
                "ended": True,
                "won": True,
                "timeout": True,
                "pet_mark": pet_mark,
                "user_mark": user_mark,
                "first": state["first"],
                "board": board,
                "board_text": _board_text(board),
            }

        r, c = move["row"], move["col"]
        board[r][c] = user_mark
        state["turn"] = pet_mark
        winner = _check_winner(board)
        if winner == user_mark:
            self._emit_board(state, waiting=False, message="你获胜")
            return {
                "summary": _s(f"用户落子 {_GRID_LABELS[r][c]} 连成三子，用户获胜。\n{_board_text(board)}"),
                "ended": True,
                "won": False,
                "pet_mark": pet_mark,
                "user_mark": user_mark,
                "first": state["first"],
                "board": board,
                "board_text": _board_text(board),
            }
        if _is_full(board):
            self._emit_board(state, waiting=False, message="平局")
            return {
                "summary": _s(f"用户落子 {_GRID_LABELS[r][c]} 后棋盘已满，平局。\n{_board_text(board)}"),
                "ended": True,
                "won": None,
                "pet_mark": pet_mark,
                "user_mark": user_mark,
                "first": state["first"],
                "board": board,
                "board_text": _board_text(board),
            }

        self._emit_board(state, waiting=False, message="轮到桌宠")
        return {
            "summary": _s(f"用户落子 {_GRID_LABELS[r][c]}，轮到桌宠。\n{_board_text(board)}"),
            "ended": False,
            "pet_mark": pet_mark,
            "user_mark": user_mark,
            "first": state["first"],
            "board": board,
            "board_text": _board_text(board),
        }

    def _wait_for_move(self, state) -> dict | None:
        """阻塞等待用户落子（脑线程），返回 {row, col}；超时或取消返回 None。

        进入等待前先 clear event、清空 pending_move，避免上一轮残留；
        分段 wait 以便循环中响应 _cancelled。
        """
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
                r, c = mv.get("row", -1), mv.get("col", -1)
                if 0 <= r < 3 and 0 <= c < 3 and not state["board"][r][c]:
                    return mv
                # 非法落子（占位/越界）：忽略继续等
                with state["move_lock"]:
                    state["pending_move"] = None
                continue
            if time.monotonic() >= deadline:
                return None

    def _finish(self, state: dict, message: str):
        """游戏结束：停止等待并刷新面板结果。"""
        self._emit_board(state, waiting=False, message=message)

    def _emit_board(self, state: dict, waiting: bool, message: str = ""):
        """通知 UI 渲染棋盘（Qt 队列连接，跨线程安全）。"""
        try:
            TOOL_CTX.game_board(self.name(), {
                "board": state["board"],
                "turn": state.get("turn"),
                "waiting": waiting,
                "message": message,
                "timeout": self.MOVE_TIMEOUT if waiting else 0,
                "user_mark": state["user_mark"],  # 用户执子（面板视角）
                "first": state.get("first"),
            })
        except Exception as e:
            logger.warning(f"[TicTacToe] emit board failed: {e}")

    def win_speech(self) -> str | None:
        return random.choice(self._WIN_SPEECH)

    def lose_speech(self) -> str | None:
        return random.choice(self._LOSE_SPEECH)

    def stop_speech(self) -> str | None:
        return random.choice(self._STOP_SPEECH)

    def forfeit_speech(self) -> str | None:
        return random.choice(self._FORFEIT_SPEECH)
