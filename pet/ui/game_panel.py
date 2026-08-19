"""游戏面板基类 — 通用无边框窗口框架 + 统一渲染入口 + 收场机制。

子类继承 GamePanelBase，只需：
  - 覆写 TITLE / WIDTH / HEIGHT
  - 实现 _build_content(layout)：构建游戏特定 UI（须创建 self._message 供 render 更新）
  - 实现 _render_game(payload)：渲染游戏特定内容（waiting 已解析到 self._waiting）
  - 实现用户交互（点击时调用 GAME.submit(game_name, payload)）

render 是唯一入口，只做通用骨架（close/计时/闲置/定位），不包含任何具体游戏逻辑。
"""

import logging

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
)

from pet.game.gamebase import GAME

logger = logging.getLogger(__name__)

_BG_QSS = (
    "QWidget#gamePanelBg {"
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


class GamePanelBase(QWidget):
    """游戏面板基类：通用窗口框架 + 渲染入口 + 收场机制。"""

    TITLE = "游戏"
    WIDTH = 300
    HEIGHT = 300
    IDLE_TIMEOUT_MS = 30_000  # 面板 30s 无更新（模型未继续/游戏已结束）自动收场

    def __init__(self, parent=None):
        super().__init__(parent)
        self._game_name: str | None = None
        self._waiting = False
        self._placed = False          # 是否已定位/被用户拖动过
        self._drag_pos: QPoint | None = None
        self._llm_loading = False     # 脑线程运行中：暂停闲置收场
        self._countdown = 0

        self.setObjectName("gamePanel")
        self.setWindowTitle(self.TITLE)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._tick_countdown)
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
        bg.setObjectName("gamePanelBg")
        bg.setStyleSheet(_BG_QSS)
        self._content_layout = QVBoxLayout(bg)
        self._content_layout.setContentsMargins(14, 4, 14, 12)
        self._content_layout.setSpacing(8)
        root.addWidget(bg)

        # 标题栏（子类可经 _title 覆盖标题文本）
        title_bar = QWidget()
        title_bar.setFixedHeight(32)
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel(self.TITLE)
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
        self._content_layout.addWidget(title_bar)

        self._build_content(self._content_layout)

    # ---- 子类钩子 ----

    def _build_content(self, layout: QVBoxLayout):
        """子类构建游戏特定 UI（须创建 self._message 供 render 更新）。"""
        raise NotImplementedError

    def _render_game(self, payload: dict):
        """子类渲染游戏特定内容（payload 的 waiting 已解析到 self._waiting）。"""
        raise NotImplementedError

    # ---- 渲染入口（唯一入口，通用骨架） ----

    def render(self, game_name: str, payload: dict):
        if payload.get("action") == "close":
            self._close_panel()
            return
        self._game_name = game_name
        self._title.setText(self.TITLE)
        waiting = bool(payload.get("waiting"))
        self._waiting = waiting
        self._render_game(payload)  # 子类渲染游戏内容（可覆盖标题）
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
            if not self._llm_loading:
                self._idle_timer.start(self.IDLE_TIMEOUT_MS)  # 等待桌宠/结束：无更新则自动收场
        self._update_countdown_label()
        if not self.isVisible():
            self._place_near_pet()
            self.show()
            self.raise_()

    def set_llm_loading(self, loading: bool):
        """脑线程运行状态。

        运行中（loading=True）：暂停闲置收场定时器，避免模型慢响应/重试被误收场；
        结束（loading=False）：若面板仍显示且不在等待用户（游戏结束/模型提前退出），
        重启闲置计时——模型继续下棋会由新一轮 render 重置，否则超时后自动收场。
        """
        self._llm_loading = loading
        if loading:
            if self._idle_timer.isActive():
                self._idle_timer.stop()
        else:
            if not self._waiting and self._game_name and self.isVisible():
                self._idle_timer.start(self.IDLE_TIMEOUT_MS)

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

    # ---- 收场机制 ----

    def _on_close_clicked(self):
        """关闭/结束面板：用户主动结束游戏，判用户输并提示。"""
        if self._game_name:
            try:
                GAME.forfeit(self._game_name)
            except Exception:
                pass
        self._close_panel()

    def _on_idle_timeout(self):
        """面板长时间无更新（模型未继续/游戏已结束）自动收场。"""
        if self._game_name:
            try:
                GAME.stop(self._game_name)
            except Exception:
                pass
        self._close_panel()

    def _close_panel(self):
        self._countdown_timer.stop()
        self._idle_timer.stop()
        self._game_name = None
        self._waiting = False
        self._title.setText(self.TITLE)
        self.hide()

    # ---- 窗口定位与拖动 ----

    def set_pet_window(self, window):
        self._pet_window = window

    def _place_near_pet(self):
        """定位到桌宠右侧（屏幕放不下时放左侧）。"""
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
