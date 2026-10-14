"""通知开关测试：总开关关闭后所有通知都不再发送。"""

from pet.config import config
from pet.ui.system_tray import SystemTrayManager


class _FakeTrayIcon:
    """只记录 showMessage 调用的替身。"""

    def __init__(self):
        self.messages = []

    def showMessage(self, title, message, icon=0, duration=0):
        self.messages.append((title, message, duration))


def _manager(monkeypatch, enabled: bool) -> SystemTrayManager:
    """绕过 __init__ 造一个带假托盘图标的管理器，避免真的建 QSystemTrayIcon。"""
    mgr = SystemTrayManager.__new__(SystemTrayManager)
    mgr.tray_icon = _FakeTrayIcon()
    monkeypatch.setattr(config, "NOTIFY_ENABLED", enabled)
    return mgr


def test_notify_shows_when_enabled(monkeypatch):
    mgr = _manager(monkeypatch, True)

    mgr.notify("标题", "正文", 8000)

    assert mgr.tray_icon.messages == [("标题", "正文", 8000)]


def test_notify_dropped_when_disabled(monkeypatch):
    mgr = _manager(monkeypatch, False)

    mgr.notify("标题", "正文", 8000)

    assert mgr.tray_icon.messages == []


def test_notify_dropped_without_tray_icon(monkeypatch):
    mgr = _manager(monkeypatch, True)
    mgr.tray_icon = None

    mgr.notify("标题", "正文")

    assert mgr.tray_icon is None
