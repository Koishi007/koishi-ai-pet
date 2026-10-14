"""猜高低面板 — 继承 GamePanelBase，负责牌堆展示与下注提交。"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QLabel

from pet.game.gamebase import GAME
from pet.game.higher_lower import _CN
from pet.ui.game_panel import GamePanelBase

logger = logging.getLogger(__name__)

_BTN_QSS = (
    "QPushButton {"
    "  background: #ffffff;"
    "  border: 2px solid #d0d6e0;"
    "  border-radius: 14px;"
    "  font-size: 14px;"
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


class HigherLowerPanel(GamePanelBase):
    """猜高低面板：分数、桌宠下注、基准与数字链、剩余牌堆、高/低下注按钮。"""

    TITLE = "猜高低 · 先得 6 分者获胜"
    WIDTH = 360
    HEIGHT = 400

    def __init__(self, parent=None):
        self._score = None
        self._pet_bet = None
        self._base_label = None
        self._history_label = None
        self._deck_label = None
        self._bet_btns: dict[str, QPushButton] = {}
        super().__init__(parent)

    def _build_content(self, layout):
        self._score = QLabel("")
        self._score.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._score.setStyleSheet("font-size: 13px; font-weight: bold; color: #555;")
        layout.addWidget(self._score)

        self._message = QLabel("")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setStyleSheet("font-size: 13px; color: #555;")
        self._message.setFixedHeight(22)
        layout.addWidget(self._message)

        self._pet_bet = QLabel("")
        self._pet_bet.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pet_bet.setStyleSheet("font-size: 15px; color: #e67e22;")
        self._pet_bet.setFixedHeight(24)
        layout.addWidget(self._pet_bet)

        self._base_label = QLabel("")
        self._base_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._base_label.setStyleSheet("font-size: 34px; font-weight: bold; color: #2c3e50;")
        self._base_label.setFixedHeight(48)
        layout.addWidget(self._base_label)

        self._history_label = QLabel("")
        self._history_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._history_label.setStyleSheet("font-size: 12px; color: #888;")
        layout.addWidget(self._history_label)

        self._deck_label = QLabel("")
        self._deck_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._deck_label.setWordWrap(True)
        self._deck_label.setStyleSheet("font-size: 13px; color: #555;")
        self._deck_label.setFixedHeight(40)
        layout.addWidget(self._deck_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._bet_btns = {}
        for side in ("higher", "lower"):
            btn = QPushButton(_CN[side])
            btn.setFixedHeight(52)
            btn.setStyleSheet(_BTN_QSS)
            btn.clicked.connect(lambda checked=False, s=side: self._on_bet_clicked(s))
            btn_row.addWidget(btn)
            self._bet_btns[side] = btn
        layout.addLayout(btn_row)

        hint = QLabel("轮到你时点击下注")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("font-size: 12px; color: #999;")
        layout.addWidget(hint)

    def _render_game(self, payload: dict):
        pet_score = payload.get("pet_score", 0)
        user_score = payload.get("user_score", 0)
        target = payload.get("target", 6)
        self._score.setText(f"桌宠 {pet_score} - {user_score} 你（先得 {target} 分者获胜）")

        # 用户下注前只提示「已下注」，下注后随揭晓公开桌宠押哪边
        pet_choice = payload.get("pet_choice")
        if pet_choice:
            self._pet_bet.setText(f"桌宠押：{_CN.get(pet_choice, '')}")
        else:
            self._pet_bet.setText("桌宠已下注" if payload.get("pet_bet") else "")

        base = payload.get("base")
        self._base_label.setText(f"{base}" if base is not None else "")

        history = payload.get("history") or []
        self._history_label.setText("数字链：" + " → ".join(str(n) for n in history))

        deck = payload.get("deck") or []
        self._deck_label.setText("剩余牌：" + (" ".join(str(n) for n in deck) or "无"))

        odds_map = payload.get("odds") or {}
        counts = {"higher": payload.get("above", 0), "lower": payload.get("below", 0)}
        for side, btn in self._bet_btns.items():
            value = odds_map.get(side)
            if value is None:
                btn.setText(f"{_CN[side]}（无牌）")
                btn.setEnabled(False)
            else:
                btn.setText(f"{_CN[side]}（{counts[side]} 张 +{value}）")
                btn.setEnabled(self._waiting)

    def _on_bet_clicked(self, side: str):
        if not self._waiting or not self._game_name:
            return
        if GAME.submit(self._game_name, {"choice": side}):
            self._waiting = False
            self._countdown_timer.stop()
            self._update_countdown_label()
            for btn in self._bet_btns.values():
                btn.setEnabled(False)
            self._message.setText(f"你押「{_CN[side]}」，等待揭晓…")
        else:
            self._message.setText("游戏已结束或未在等待，无法下注")
