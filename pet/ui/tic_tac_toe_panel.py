"""井字棋棋盘面板 — 继承 GamePanelBase，负责棋盘渲染与点击落子。"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QPushButton, QLabel

from pet.game.gamebase import GAME
from pet.ui.game_panel import GamePanelBase

logger = logging.getLogger(__name__)

_CELL_QSS = (
    "QPushButton {"
    "  background: #ffffff;"
    "  border: 2px solid #d0d6e0;"
    "  border-radius: 12px;"
    "  font-size: 40px;"
    "  font-weight: bold;"
    "  color: #333;"
    "}"
    "QPushButton:hover {"
    "  background: #f0f6ff;"
    "  border-color: #7aa7e0;"
    "}"
)

# 桌宠回合的空格：视觉禁用（灰底、无 hover），点击由 _waiting 拦截
_CELL_DISABLED_QSS = (
    "QPushButton {"
    "  background: #eceef3;"
    "  border: 2px solid #d0d6e0;"
    "  border-radius: 12px;"
    "  font-size: 40px;"
    "  font-weight: bold;"
    "  color: #999;"
    "}"
)


class TicTacToePanel(GamePanelBase):
    """井字棋棋盘面板：3x3 格子，点击空格提交落子。"""

    TITLE = "井字棋"
    WIDTH = 300
    HEIGHT = 368

    def __init__(self, parent=None):
        self._your_mark: str | None = None  # 用户执子（X/O），首次 render 后确定
        self._first: str | None = None      # "user"=用户先手 / "pet"=桌宠先手
        self._board = [[""] * 3 for _ in range(3)]
        super().__init__(parent)

    def _build_content(self, layout):
        # 状态消息
        self._message = QLabel("")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setStyleSheet("font-size: 13px; color: #555;")
        self._message.setFixedHeight(22)
        layout.addWidget(self._message)

        # 棋盘 3x3
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self._cells: list[list[QPushButton]] = []
        for r in range(3):
            row_btns = []
            for c in range(3):
                btn = QPushButton("")
                btn.setFixedSize(80, 80)
                btn.setStyleSheet(_CELL_QSS)
                btn.clicked.connect(lambda checked=False, rr=r, cc=c: self._on_cell_clicked(rr, cc))
                grid.addWidget(btn, r, c)
                row_btns.append(btn)
            self._cells.append(row_btns)
        layout.addLayout(grid)

        # 底部提示
        hint = QLabel("轮到你时点击空格落子")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("font-size: 12px; color: #999;")
        layout.addWidget(hint)

    # ---- 游戏渲染 ----

    def _render_game(self, payload: dict):
        # 执子与先后手：显示在标题栏，用户一眼看到自己该不该走
        your_mark = payload.get("user_mark")
        if your_mark:
            self._your_mark = your_mark
            self._first = payload.get("first")
            side = "先手" if self._first == "user" else "后手"
            self._title.setText(f"井字棋 · 你执 {your_mark}（{side}）")
        board = payload.get("board")
        if board:
            self._board = [row[:] for row in board]  # 拷贝，避免与脑线程共享可变对象
            self._update_cells()

    def _update_cells(self):
        for r in range(3):
            for c in range(3):
                mark = self._board[r][c]
                btn = self._cells[r][c]
                btn.setText(mark)
                if mark == "X":
                    style = _CELL_QSS.replace("color: #333;", "color: #e67e22;")
                elif mark == "O":
                    style = _CELL_QSS.replace("color: #333;", "color: #2e86c1;")
                elif self._waiting:
                    style = _CELL_QSS  # 用户回合：空格可点
                else:
                    style = _CELL_DISABLED_QSS  # 桌宠回合：空格视觉禁用
                btn.setStyleSheet(style)

    # ---- 用户交互 ----

    def _on_cell_clicked(self, r: int, c: int):
        if not self._waiting or not self._game_name:
            return
        if self._board[r][c]:
            return
        if GAME.submit(self._game_name, {"row": r, "col": c}):
            self._waiting = False
            self._countdown_timer.stop()
            self._idle_timer.stop()
            self._update_countdown_label()
            self._message.setText("已落子，等待桌宠…")
        else:
            self._message.setText("游戏已结束或未在等待，无法落子")
