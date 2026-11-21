"""井字棋棋盘面板 — 继承 GamePanelBase，负责棋盘渲染与点击落子。"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QPushButton, QLabel

from pet.game.gamebase import GAME
from pet import theme
from pet.ui.game_panel import GamePanelBase
from pet.ui.ink_button import InkButton

logger = logging.getLogger(__name__)

_CELL_QSS = (
    "QPushButton {"
    f"  background: {theme.SURFACE};"
    f"  border: {theme.BORDER}px solid {theme.INK};"
    "  border-radius: 12px;"
    "  font-size: 34px;"
    "  font-weight: 800;"
    f"  color: {theme.INK};"
    "}"
    "QPushButton:hover {"
    f"  background: {theme.PRIMARY_SOFT};"
    "}"
)

# 桌宠回合的空格：视觉禁用（浅底、无 hover），点击由 _waiting 拦截
_CELL_DISABLED_QSS = (
    "QPushButton {"
    f"  background: {theme.PAPER_DEEP};"
    f"  border: {theme.BORDER}px solid {theme.LINE};"
    "  border-radius: 12px;"
    "  font-size: 34px;"
    "  font-weight: 800;"
    f"  color: {theme.INK_MUTED};"
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
        self._message = QLabel("")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setStyleSheet(f"font-size: 12.5px; color: {theme.INK_SOFT};")
        self._message.setFixedHeight(22)
        layout.addWidget(self._message)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        self._cells: list[list[QPushButton]] = []
        for r in range(3):
            row_btns = []
            for c in range(3):
                btn = InkButton("", radius=12, shadow_offset=(1, 2))
                btn.setMinimumWidth(76)
                btn.setFixedHeight(76)
                btn.setStyleSheet(_CELL_QSS)
                btn.clicked.connect(lambda checked=False, rr=r, cc=c: self._on_cell_clicked(rr, cc))
                grid.addWidget(btn, r, c)
                row_btns.append(btn)
            self._cells.append(row_btns)
        layout.addLayout(grid)

        hint = QLabel("轮到你时点击空格落子")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"font-size: 11px; color: {theme.INK_MUTED};")
        layout.addWidget(hint)

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
                    style = _CELL_QSS.replace(
                        f"color: {theme.INK};", f"color: {theme.PRIMARY_DEEP};")
                elif mark == "O":
                    style = _CELL_QSS.replace(
                        f"color: {theme.INK};", f"color: {theme.INFO};")
                elif self._waiting:
                    style = _CELL_QSS  # 用户回合：空格可点
                else:
                    style = _CELL_DISABLED_QSS  # 桌宠回合：空格视觉禁用
                btn.setStyleSheet(style)

    def _on_cell_clicked(self, r: int, c: int):
        if not self._waiting or not self._game_name:
            return
        if self._board[r][c]:
            return
        if GAME.submit(self._game_name, {"row": r, "col": c}):
            self._waiting = False
            self._countdown_timer.stop()
            self._update_countdown_label()
            self._message.setText("已落子，等待桌宠…")
        else:
            self._message.setText("游戏已结束或未在等待，无法落子")
