"""猜拳面板 — 继承 GamePanelBase，负责出拳展示与点击提交。"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QLabel

from pet.game.gamebase import GAME
from pet.game.rps import MOVES
from pet.ui.game_panel import GamePanelBase

logger = logging.getLogger(__name__)

_BTN_QSS = (
    "QPushButton {"
    "  background: #ffffff;"
    "  border: 2px solid #d0d6e0;"
    "  border-radius: 14px;"
    "  font-size: 15px;"
    "  font-weight: bold;"
    "  color: #333;"
    "}"
    "QPushButton:hover {"
    "  background: #f0f6ff;"
    "  border-color: #7aa7e0;"
    "}"
    "QPushButton:disabled {"
    "  background: #eceef3;"
    "  color: #999;"
    "}"
)

_MOVE_ICONS = {
    "rock": "✊ 石头",
    "scissors": "✌️ 剪刀",
    "paper": "✋ 布",
}


class RpsPanel(GamePanelBase):
    """猜拳面板：桌宠出拳展示 + 石头/剪刀/布按钮。"""

    TITLE = "猜拳 · 三局两胜"
    WIDTH = 300
    HEIGHT = 300

    def __init__(self, parent=None):
        self._score = None
        self._pet_move_label = None
        self._move_btns: dict[str, QPushButton] = {}
        super().__init__(parent)

    def _build_content(self, layout):
        # 比分
        self._score = QLabel("")
        self._score.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._score.setStyleSheet("font-size: 13px; font-weight: bold; color: #555;")
        layout.addWidget(self._score)

        # 消息
        self._message = QLabel("")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setStyleSheet("font-size: 13px; color: #555;")
        self._message.setFixedHeight(22)
        layout.addWidget(self._message)

        # 桌宠出拳展示
        self._pet_move_label = QLabel("")
        self._pet_move_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pet_move_label.setStyleSheet("font-size: 15px; color: #e67e22;")
        self._pet_move_label.setFixedHeight(24)
        layout.addWidget(self._pet_move_label)

        # 出拳按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._move_btns = {}
        for move in ("rock", "scissors", "paper"):
            btn = QPushButton(_MOVE_ICONS[move])
            btn.setFixedSize(80, 80)
            btn.setStyleSheet(_BTN_QSS)
            btn.clicked.connect(lambda checked=False, m=move: self._on_move_clicked(m))
            btn_row.addWidget(btn)
            self._move_btns[move] = btn
        layout.addLayout(btn_row)

        # 底部提示
        hint = QLabel("轮到你时点击出拳")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("font-size: 12px; color: #999;")
        layout.addWidget(hint)

    # ---- 游戏渲染 ----

    def _render_game(self, payload: dict):
        pet_move = payload.get("pet_move")
        if pet_move:
            self._pet_move_label.setText(f"桌宠出：{MOVES.get(pet_move, pet_move)}")
        else:
            self._pet_move_label.setText("")
        pet_wins = payload.get("pet_wins", 0)
        user_wins = payload.get("user_wins", 0)
        need_wins = payload.get("need_wins", 2)
        self._score.setText(f"桌宠 {pet_wins} - {user_wins} 你（先赢 {need_wins} 局）")
        self._set_buttons_enabled(self._waiting)

    def _set_buttons_enabled(self, enabled: bool):
        for btn in self._move_btns.values():
            btn.setEnabled(enabled)

    # ---- 用户交互 ----

    def _on_move_clicked(self, move: str):
        if not self._waiting or not self._game_name:
            return
        if GAME.submit(self._game_name, {"move": move}):
            self._waiting = False
            self._countdown_timer.stop()
            self._idle_timer.stop()
            self._update_countdown_label()
            self._set_buttons_enabled(False)
            self._message.setText(f"你出「{MOVES[move]}」，等待判定…")
        else:
            self._message.setText("游戏已结束或未在等待，无法出拳")
