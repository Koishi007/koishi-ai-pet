"""二十问面板 — 桌宠提问猜东西，用户在面板点击"是/否/不确定"作答，
桌宠给出猜测时点击"猜对了/猜错了"确认。"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

from pet.game.gamebase import GAME
from pet.game.twenty_questions import ANSWER_TEXT
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


class TwentyQuestionsPanel(GamePanelBase):
    """二十问面板：展示问题/猜测 + 是/否/不确定 或 猜对/猜错 按钮。"""

    TITLE = "二十问"
    WIDTH = 360
    HEIGHT = 320

    def __init__(self, parent=None):
        self._round_label: QLabel | None = None
        self._question_label: QLabel | None = None
        self._answer_btns: dict[str, QPushButton] = {}
        self._correct_btns: dict[bool, QPushButton] = {}
        super().__init__(parent)

    def _build_content(self, layout):
        self._round_label = QLabel("")
        self._round_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._round_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #555;")
        layout.addWidget(self._round_label)

        self._question_label = QLabel("")
        self._question_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._question_label.setWordWrap(True)
        self._question_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #2c3e50;")
        self._question_label.setMinimumHeight(64)
        layout.addWidget(self._question_label)

        self._message = QLabel("")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setStyleSheet("font-size: 13px; color: #555;")
        self._message.setFixedHeight(22)
        layout.addWidget(self._message)

        # 作答按钮行：是 / 否 / 不确定
        ans_row = QHBoxLayout()
        ans_row.setSpacing(8)
        for key, text in (("yes", "是"), ("no", "否"), ("unknown", "不确定")):
            btn = QPushButton(text)
            btn.setFixedHeight(42)
            btn.setStyleSheet(_BTN_QSS)
            btn.clicked.connect(lambda checked=False, k=key: self._on_answer_clicked(k))
            ans_row.addWidget(btn)
            self._answer_btns[key] = btn
        layout.addLayout(ans_row)

        # 判定按钮行：猜对了 / 猜错了
        ok_row = QHBoxLayout()
        ok_row.setSpacing(8)
        for correct, text in ((True, "猜对了"), (False, "猜错了")):
            btn = QPushButton(text)
            btn.setFixedHeight(42)
            btn.setStyleSheet(_BTN_QSS)
            btn.clicked.connect(lambda checked=False, c=correct: self._on_correct_clicked(c))
            ok_row.addWidget(btn)
            self._correct_btns[correct] = btn
        layout.addLayout(ok_row)

        hint = QLabel("心里想一个东西（动物/食物/物品等），让桌宠来猜")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("font-size: 12px; color: #999;")
        layout.addWidget(hint)

    def _render_game(self, payload: dict):
        self._round_label.setText(payload.get("round_text") or "")
        self._question_label.setText(payload.get("text") or "")
        mode = payload.get("mode")
        if mode == "question":
            self._set_answer_enabled(self._waiting)
            self._set_correct_enabled(False)
        elif mode == "guess":
            self._set_answer_enabled(False)
            self._set_correct_enabled(self._waiting)
        else:
            self._set_answer_enabled(False)
            self._set_correct_enabled(False)

    def _set_answer_enabled(self, enabled: bool):
        for btn in self._answer_btns.values():
            btn.setEnabled(enabled)

    def _set_correct_enabled(self, enabled: bool):
        for btn in self._correct_btns.values():
            btn.setEnabled(enabled)

    def _on_answer_clicked(self, answer: str):
        if not self._waiting or not self._game_name:
            return
        if GAME.submit(self._game_name, {"answer": answer}):
            self._waiting = False
            self._countdown_timer.stop()
            self._update_countdown_label()
            self._set_answer_enabled(False)
            self._message.setText(f"你回答：{ANSWER_TEXT[answer]}，等待下一问…")
        else:
            self._message.setText("游戏已结束或未在等待，无法作答")

    def _on_correct_clicked(self, correct: bool):
        if not self._waiting or not self._game_name:
            return
        if GAME.submit(self._game_name, {"correct": correct}):
            self._waiting = False
            self._countdown_timer.stop()
            self._update_countdown_label()
            self._set_correct_enabled(False)
            self._message.setText("猜对了！" if correct else "没猜中，桌宠继续…")
        else:
            self._message.setText("游戏已结束或未在等待")
