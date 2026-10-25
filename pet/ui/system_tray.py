import ctypes
import logging
import os
import sys

import psutil

from PySide6.QtWidgets import QMessageBox, QSystemTrayIcon, QMenu
from PySide6.QtGui import (QAction, QColor, QCursor, QGuiApplication, QIcon,
                           QPainter, QPainterPath, QPen)
from PySide6.QtCore import QObject, QTimer, Qt
from pet.ui.styles import ICON_PATH, MENU_QSS
from pet.ui.settings_window import SettingsWindow

from pet.config import config
from pet.version_check import get_local_version

logger = logging.getLogger(__name__)

_PROCESS = None


def _get_process() -> psutil.Process:
    """进程句柄首次读取时创建。"""
    global _PROCESS
    if _PROCESS is None:  # 无锁竞态最坏重复创建一次，无副作用
        _PROCESS = psutil.Process(os.getpid())
    return _PROCESS


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
        self._agent = None

        if not config.SHOW_TRAY:
            return

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(ICON_PATH))
        self._update_tooltip()

        self.tray_icon.activated.connect(self._on_activated)
        self.tray_icon.show()

        # 定时更新资源信息 tooltip
        self._tooltip_timer = QTimer(self)
        self._tooltip_timer.timeout.connect(self._update_tooltip)
        self._tooltip_timer.start(3000)

    def set_agent(self, agent):
        self._agent = agent

    def notify(self, title: str, message: str, duration: int = 5000):
        """发送一条通知；总开关关闭或无托盘图标时静默丢弃。"""
        if self.tray_icon is None or not config.NOTIFY_ENABLED:
            return
        self.tray_icon.showMessage(title, message,
                                   QSystemTrayIcon.MessageIcon.Information, duration)

    def _update_tooltip(self):
        """定时更新托盘 tooltip，显示 pulse 参数 + 进程资源。"""
        local_ver = get_local_version() or "?"
        lines = [f"Koishi v{local_ver}"]
        # pulse 参数
        agent = self.pet.agent if self.pet else None
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
            process = _get_process()
            mem_info = process.memory_info()
            cpu_pct = process.cpu_percent(interval=0)
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

        # 鼠标穿透开关
        pen = self.pet.mouse_penetration
        pen_action = QAction("关闭鼠标穿透" if pen else "开启鼠标穿透", menu)
        pen_action.triggered.connect(
            lambda: self.pet.set_mouse_penetration(not self.pet.mouse_penetration)
        )
        menu.addAction(pen_action)
        menu.addSeparator()

        new_session_action = QAction("切到新会话", menu)
        new_session_action.setEnabled(self._agent is not None)
        new_session_action.triggered.connect(self._confirm_new_session)
        menu.addAction(new_session_action)
        menu.addSeparator()

        # 设置入口
        settings_action = QAction("设置", menu)
        settings_action.triggered.connect(lambda: SettingsWindow.show_instance(self._agent, self.pet))
        menu.addAction(settings_action)
        menu.addSeparator()

        quit_action = QAction("退出", menu)
        if hasattr(self, "quit_fn"):
            quit_action.triggered.connect(self.quit_fn)
        else:
            quit_action.triggered.connect(self.app.quit)
        menu.addAction(quit_action)

        menu.exec(QCursor.pos())

    def _confirm_new_session(self):
        """确认后切换到新会话，结束当前会话并回收旧事件。"""
        if self._agent is None:
            return
        if self._ask_new_session() != QMessageBox.StandardButton.Yes:
            return
        try:
            self._agent.behavior.clear_context()
        except Exception as e:
            logger.exception(f"[Tray] 切换会话失败: {e}")
            parent = self.pet if self.pet.isVisible() else None
            QMessageBox.warning(parent, "切换失败", str(e))
            return
        logger.info("[Tray] 已切换到新会话")

    def _ask_new_session(self) -> QMessageBox.StandardButton:
        """屏幕居中的确认框；返回用户选择。"""
        parent = self.pet if self.pet.isVisible() else None
        box = QMessageBox(parent)
        box.setWindowTitle("切到新会话")
        box.setText(
            "切到一个新会话，当前会话的上下文会被丢弃，不可恢复。\n\n"
            "不影响长期记忆\n\n"
            "确定切到新会话吗？"
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            box.adjustSize()
            box.move(screen.availableGeometry().center() - box.rect().center())
        return box.exec()

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Context:
            self._show_menu()

    def hide(self):
        if self.tray_icon:
            self.tray_icon.hide()
