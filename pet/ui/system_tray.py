import ctypes
import logging
import os
import sys

import psutil

from PySide6.QtWidgets import QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QAction, QCursor, QPainter, QPainterPath, QColor, QPen
from PySide6.QtCore import QObject, QTimer, Qt
from pet.ui.styles import MENU_QSS

from config import config

logger = logging.getLogger(__name__)

_ICON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "icon", "sys_tray.png",
)

_PROCESS = psutil.Process(os.getpid())


def _format_bytes(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(b) < 1024:
            return f"{b:.1f}{unit}"
        b /= 1024
    return f"{b:.1f}TB"


_MENU_R = 8  # 菜单圆角半径


def _wrap_menu_paint(menu: QMenu):
    """让 QMenu 自绘圆角背景"""
    _orig = menu.paintEvent

    def _rounded_paint(event):
        p = QPainter(menu)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(menu.rect().adjusted(0, 0, -1, -1), _MENU_R, _MENU_R)
        p.fillPath(path, QColor("#ffffff"))
        p.setPen(QPen(QColor("#dddddd"), 1))
        p.drawPath(path)
        p.setClipPath(path)
        _orig(event)
        p.end()

    menu.paintEvent = _rounded_paint


class SystemTrayManager(QObject):

    def __init__(self, app, pet_window):
        super().__init__()
        self.app = app
        self.pet = pet_window
        self.tray_icon = None

        if not config.SHOW_TRAY:
            return

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(_ICON_PATH))
        self._update_tooltip()

        self.tray_icon.activated.connect(self._on_activated)
        self.tray_icon.show()

        # 定时更新资源信息 tooltip
        self._tooltip_timer = QTimer(self)
        self._tooltip_timer.timeout.connect(self._update_tooltip)
        self._tooltip_timer.start(3000)

    def _update_tooltip(self):
        """定时更新托盘 tooltip，显示 pulse 参数 + 进程资源。"""
        lines = ["DeskPet"]
        # pulse 参数
        agent = self.pet._agent if self.pet else None
        if agent:
            v = agent.vitals
            m = agent.mood
            if v:
                ns = v.numeric_summary()
                lines.append(f"饱食度 {ns['satiety']:.0f} | 精力 {ns['energy']:.0f}")
            if m:
                ms = m.numeric_summary()
                lines.append(f"好感 {ms['affection']:.0f} | 愉悦 {ms['joy']:.0f} | 理智 {ms['sanity']:.0f}")
        # 资源占用
        try:
            mem_info = _PROCESS.memory_info()
            cpu_pct = _PROCESS.cpu_percent(interval=0)
            mem_str = _format_bytes(mem_info.rss)
            lines.append(f"内存: {mem_str} | CPU: {cpu_pct:.1f}%")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        self.tray_icon.setToolTip("\n".join(lines))

    def _show_menu(self):
        self.pet.raise_()
        try:
            if sys.platform == "darwin":
                from AppKit import NSApp
                NSApp.activateIgnoringOtherApps_(True)
            else:
                ctypes.windll.user32.SetForegroundWindow(int(self.pet.winId()))
        except Exception:
            pass

        menu = QMenu(self.pet)
        menu.setStyleSheet(MENU_QSS)
        # Windows 原生菜单不认 QSS border-radius，需 frameless + 自绘
        if sys.platform == "win32":
            menu.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Popup | Qt.WindowType.NoDropShadowWindowHint)
            menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            _wrap_menu_paint(menu)

        if self.pet.isVisible():
            hide_action = QAction("隐藏", menu)
            hide_action.triggered.connect(self.pet.hide)
            menu.addAction(hide_action)
        else:
            show_action = QAction("显示", menu)
            show_action.triggered.connect(self.pet.show)
            menu.addAction(show_action)

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.app.quit)
        menu.addAction(quit_action)

        menu.exec(QCursor.pos())

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Context:
            self._show_menu()

    def hide(self):
        if self.tray_icon:
            self.tray_icon.hide()
