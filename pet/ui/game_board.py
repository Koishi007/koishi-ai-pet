"""井字棋棋盘面板 — 无边框置顶窗口，显示棋盘并接收用户点击落子。

渲染由 agent.game_board_requested 信号（跨线程队列连接）驱动；
点击格子直接调用 GAME.submit_move 提交落子并唤醒脑线程。
"""

import logging

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel,
)

from pet.game.gamebase import GAME

logger = logging.getLogger(__name__)

_W = 300
_H = 368

# 棋盘 30s 无更新（模型未继续下棋/游戏已结束）自动收场
_IDLE_TIMEOUT_MS = 30_000

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

_BG_QSS = (
    "QWidget#boardBg {"
    "  background: #f5f6fa;"
    "  border-radius: 12px;"
    "  font-size: 13px;"
    "}"
)

_BTN_CLOSE_QSS = (
    "QPushButton {"
    "  background: transparent;"
    "  border: none;"
    "  border-radius: 13px;"
    "  font-size: 15px;"
    "  color: #999;"
    "}"
    "QPushButton:hover {"
    "  background: #e81123;"
    "  color: #fff;"
    "}"
)


class GameBoardPanel(QWidget):
    """井字棋棋盘窗口，可拖动，点击格子提交落子。"""

    def __init__(self):
        super().__init__()
        self._game_name: str | None = None
        self._waiting = False
        self._your_mark: str | None = None  # 用户执子（X/O），首次 render 后确定
        self._first: str | None = None      # "user"=用户先手 / "pet"=桌宠先手
        self._placed = False          # 是否已定位/被用户拖动过
        self._llm_loading = False     # 脑线程运行中：暂停闲置收场，避免模型慢响应被误收场
        self._idle_paused_by_loading = False
        self._drag_pos: QPoint | None = None
        self._board = [[""] * 3 for _ in range(3)]
        self._countdown = 0

        self.setObjectName("gameBoardPanel")
        self.setWindowTitle("井字棋")
        self.setFixedSize(_W, _H)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._tick_countdown)
        # 等待桌宠（waiting=False）且长时间无新 render → 模型已提前退出，自动收场
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._on_idle_timeout)

        self._setup_ui()
        self.hide()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bg = QWidget()
        bg.setObjectName("boardBg")
        bg.setStyleSheet(_BG_QSS)
        layout = QVBoxLayout(bg)
        layout.setContentsMargins(14, 4, 14, 12)
        layout.setSpacing(6)
        root.addWidget(bg)

        # 标题栏
        title_bar = QWidget()
        title_bar.setFixedHeight(32)
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel("井字棋")
        self._title.setStyleSheet("font-size: 15px; font-weight: bold; color: #333;")
        title_row.addWidget(self._title)
        title_row.addStretch()
        self._countdown_label = QLabel("")
        self._countdown_label.setStyleSheet("font-size: 12px; font-weight: bold; color: #e74c3c;")
        title_row.addWidget(self._countdown_label)
        btn_close = QPushButton("×")
        btn_close.setFixedSize(26, 26)
        btn_close.setStyleSheet(_BTN_CLOSE_QSS)
        btn_close.setToolTip("结束游戏")
        btn_close.clicked.connect(self._on_close_clicked)
        title_row.addWidget(btn_close)
        layout.addWidget(title_bar)

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
        self._hint = QLabel("轮到你时点击空格落子")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setStyleSheet("font-size: 12px; color: #999;")
        layout.addWidget(self._hint)

    # ---- 渲染（主线程，由信号调用） ----

    def render(self, game_name: str, payload: dict):
        if payload.get("action") == "close":
            self._close_board()
            return
        self._game_name = game_name
        # 首次确定执子与先后手：显示在标题栏，用户一眼看到自己该不该走
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
        waiting = bool(payload.get("waiting"))
        self._waiting = waiting
        msg = payload.get("message")
        if msg:
            self._message.setText(msg)
        if waiting:
            self._countdown = int(payload.get("timeout") or 0)
            self._countdown_timer.start(1000)
            self._idle_timer.stop()  # 等待用户期间由倒计时驱动，不设闲置
        else:
            self._countdown = 0
            self._countdown_timer.stop()
            self._idle_paused_by_loading = False
            if not self._llm_loading:
                self._idle_timer.start(_IDLE_TIMEOUT_MS)  # 等待桌宠/游戏结束：无更新则自动收场
        self._update_countdown_label()
        if not self.isVisible():
            self._place_near_pet()
            self.show()
            self.raise_()

    def set_llm_loading(self, loading: bool):
        """脑线程运行状态：运行中暂停闲置收场定时器。

        模型推理/重试可能超过 30s，若在"轮到桌宠"时误收场会拆掉会话并重开一局；
        本轮交互结束后（loading=False）再恢复闲置计时。
        """
        self._llm_loading = loading
        if loading:
            if self._idle_timer.isActive():
                self._idle_timer.stop()
                self._idle_paused_by_loading = True
        else:
            if self._idle_paused_by_loading:
                self._idle_paused_by_loading = False
                if not self._waiting and self._game_name and self.isVisible():
                    self._idle_timer.start(_IDLE_TIMEOUT_MS)

    def _update_cells(self):
        for r in range(3):
            for c in range(3):
                mark = self._board[r][c]
                btn = self._cells[r][c]
                btn.setText(mark)
                style = _CELL_QSS
                if mark == "X":
                    style = style.replace("color: #333;", "color: #e67e22;")
                elif mark == "O":
                    style = style.replace("color: #333;", "color: #2e86c1;")
                btn.setStyleSheet(style)

    def _update_countdown_label(self):
        if self._waiting and self._countdown > 0:
            self._countdown_label.setText(f"⏳ {self._countdown}s")
        else:
            self._countdown_label.setText("")

    def _tick_countdown(self):
        self._countdown -= 1
        self._update_countdown_label()
        if self._countdown <= 0:
            self._countdown_timer.stop()

    # ---- 用户交互 ----

    def _on_cell_clicked(self, r: int, c: int):
        if not self._waiting or not self._game_name:
            return
        if self._board[r][c]:
            return
        if GAME.submit_move(self._game_name, r, c):
            self._waiting = False
            self._countdown_timer.stop()
            self._idle_timer.stop()
            self._update_countdown_label()
            self._message.setText("已落子，等待桌宠…")
        else:
            self._message.setText("游戏已结束或未在等待，无法落子")

    def _on_close_clicked(self):
        """关闭/结束面板：用户主动结束游戏，判用户输并提示。"""
        if self._game_name:
            try:
                GAME.forfeit(self._game_name)
            except Exception:
                pass
        self._close_board()

    def _on_idle_timeout(self):
        """棋盘长时间无更新（模型未继续下棋/游戏已结束）自动收场。"""
        if self._game_name:
            try:
                GAME.stop(self._game_name)
            except Exception:
                pass
        self._close_board()

    def _close_board(self):
        self._countdown_timer.stop()
        self._idle_timer.stop()
        self._game_name = None
        self._waiting = False
        self._your_mark = None
        self._first = None
        self._title.setText("井字棋")
        self.hide()

    # ---- 窗口定位与拖动 ----

    def set_pet_window(self, window):
        self._pet_window = window

    def _place_near_pet(self, force: bool = True):
        """定位到桌宠右侧（屏幕放不下时放左侧）。"""
        if self._placed and not force:
            return
        pet = getattr(self, "_pet_window", None)
        if pet is not None and pet.isVisible():
            geo = pet.geometry()
            x = geo.right() + 10
            y = geo.top()
            screen = pet.screen()
            if screen:
                avail = screen.availableGeometry()
                if x + self.width() > avail.right():
                    x = max(avail.left(), geo.left() - self.width() - 10)
                y = min(max(avail.top(), y), avail.bottom() - self.height())
            self.move(x, y)
            self._placed = True
        else:
            # 无桌宠可用时放屏幕中央
            from PySide6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                self.move(geo.center() - self.rect().center())
            self._placed = True

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()
            self._placed = True
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()
