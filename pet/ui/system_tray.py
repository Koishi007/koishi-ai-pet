import ctypes
import logging
import os
import sys

import psutil

from PySide6.QtWidgets import QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QAction, QCursor
from PySide6.QtCore import QObject, QTimer

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
        """定时更新托盘 tooltip，显示当前进程内存和 CPU 占用。"""
        try:
            mem_info = _PROCESS.memory_info()
            cpu_pct = _PROCESS.cpu_percent(interval=0)
            mem_str = _format_bytes(mem_info.rss)
            self.tray_icon.setToolTip(f"DeskPet | 内存: {mem_str} | CPU: {cpu_pct:.1f}%")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self.tray_icon.setToolTip("DeskPet")

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
