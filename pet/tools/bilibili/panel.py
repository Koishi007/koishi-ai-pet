"""B 站账号面板：扫码登录、功能开关、搜索关键词、私信 UID、退出登录。

面板只做主线程 UI，网络请求与轮询在子线程执行。
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from pet.tools.bilibili import duty, login, schedule
from pet.tools.bilibili.core import Credential, credential_path

logger = logging.getLogger(__name__)

_W = 360
_H = 780

_ROOT = Path(__file__).resolve().parents[3]
_CHECKMARK_FILE = str(_ROOT / "assets" / "icon" / "checkmark.svg").replace("\\", "/")

_STATE_TEXT = {
    "pending": "用 B 站 App 扫码",
    "scanning": "已扫码，请在手机上确认",
    "expired": "二维码已失效，点刷新",
}

# 开关键、面板文案与悬停说明，顺序即显示顺序
_SWITCHES = (
    ("allow_browse", "刷视频",
     "允许按关键词搜索并读字幕；关闭时直接返回原因，不发请求"),
    ("allow_like", "点赞", "给挑中的视频点赞，可撤销；同一视频只点一次"),
    ("allow_coin", "投币", "投一枚硬币，不可撤回；受每日上限与硬币余额下限约束"),
    ("allow_comment", "评论", "在挑中的视频下留一条评论；先过长度、链接、隐私与重复检查"),
    ("allow_dm", "私信", "给「私信收件人 UID」发私信；未填 UID 时不发送"),
    ("schedule_enabled", "定时任务", "按频率自动触发；只在允许触发时段内运行，夜间自动顺延"),
    ("mention_enabled", "回应 @", "定时任务触发前检查主人的 @，把对应视频写进提示词"),
)

_PANEL_QSS = """
QWidget#biliBg {
    background: #f0f0f0;
    border-radius: 8px;
    font-size: 12px;
}
"""

_CLOSE_QSS = """
QPushButton {
    background: transparent;
    border: none;
    border-radius: 14px;
    font-size: 14px;
    color: #999;
}
QPushButton:hover {
    background: #e81123;
    color: #fff;
}
"""

_SECTION_QSS = "font-size: 13px; font-weight: bold; color: #333;"

_QR_QSS = """
QLabel {
    background: #ffffff;
    border: 1px solid #ddd;
    border-radius: 6px;
    color: #999;
    font-size: 12px;
}
"""

_INPUT_QSS = """
QLineEdit {
    background: #ffffff;
    border: 1px solid #ddd;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
    color: #333;
}
QLineEdit:focus {
    border-color: #4a90d9;
}
"""

_CHECKBOX_QSS = f"""
QCheckBox {{
    font-size: 12px;
    color: #333;
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid #ccc;
    border-radius: 3px;
    background: #ffffff;
}}
QCheckBox::indicator:hover {{
    border-color: #4a90d9;
}}
QCheckBox::indicator:checked {{
    background: #ffffff;
    image: url("{_CHECKMARK_FILE}");
    border: 2px solid #4a90d9;
}}
"""

_BUTTON_QSS = """
QPushButton {
    background: #ffffff;
    border: 1px solid #ddd;
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 12px;
    color: #333;
}
QPushButton:hover {
    background: #e8e8e8;
}
QPushButton:pressed {
    background: #d8d8d8;
}
QPushButton:disabled {
    background: #f0f0f0;
    color: #bbb;
    border-color: #e0e0e0;
}
"""

_current: "AccountPanel | None" = None


class _QrLabel(QLabel):
    """可点击的二维码区域。"""

    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def _help_label(tip: str) -> QLabel:
    """方框问号，悬停显示说明。"""
    label = QLabel("?")
    label.setFixedSize(14, 14)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setCursor(Qt.CursorShape.WhatsThisCursor)
    label.setStyleSheet(
        "QLabel { color: #999; border: 1px solid #ccc; border-radius: 7px; font-size: 10px; }"
    )
    label.setToolTip(tip)
    return label


def _with_help(box: QCheckBox, tip: str) -> QWidget:
    container = QWidget()
    row = QHBoxLayout(container)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.addWidget(box)
    row.addWidget(_help_label(tip))
    row.addStretch()
    return container


def _qr_png(url: str) -> bytes:
    import qrcode

    image = qrcode.make(url)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


class _LoginWorker(QThread):
    """登录轮询线程：取二维码后在子线程内轮询到成功或失效。"""

    qr_ready = Signal(bytes)
    state_changed = Signal(str)
    logged_in = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            info = login.generate()
        except Exception as exc:
            self.failed.emit(f"取二维码失败：{exc}")
            return
        try:
            self.qr_ready.emit(_qr_png(info["url"]))
        except Exception as exc:
            self.failed.emit(f"生成二维码失败：{exc}")
            return
        self.state_changed.emit("pending")

        while not self._stop:
            try:
                state, credential = login.poll(info["key"])
            except Exception as exc:
                self.failed.emit(f"轮询失败：{exc}")
                return
            if state == "success" and credential is not None:
                try:
                    credential.save(credential_path())
                except OSError as exc:
                    self.failed.emit(f"凭证保存失败：{exc}")
                    return
                self.logged_in.emit(credential)
                return
            self.state_changed.emit(state)
            if state in ("expired", "error"):
                return
            for _ in range(20):
                if self._stop:
                    return
                self.msleep(100)


class AccountPanel(QWidget):
    """账号面板：登录状态、二维码、功能开关与配置项。"""

    def __init__(self):
        super().__init__()
        self._worker: _LoginWorker | None = None
        self._drag_pos = None

        self.setObjectName("biliPanel")
        self.setWindowTitle("B 站设置")
        self.resize(_W, _H)
        self.setFixedWidth(_W)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._build()
        self._refresh_login_state()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bg = QWidget()
        bg.setObjectName("biliBg")
        bg.setStyleSheet(_PANEL_QSS)
        layout = QVBoxLayout(bg)
        layout.setContentsMargins(16, 0, 16, 16)
        layout.setSpacing(8)
        root.addWidget(bg)

        title_bar = QWidget()
        title_bar.setFixedHeight(40)
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(0, 0, 0, 0)

        title = QLabel("B 站设置")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #333;")
        title_row.addWidget(title)
        title_row.addStretch()

        btn_close = QPushButton("×")
        btn_close.setFixedSize(28, 28)
        btn_close.setStyleSheet(_CLOSE_QSS)
        btn_close.clicked.connect(self.close)
        title_row.addWidget(btn_close)
        layout.addWidget(title_bar)

        self._qr = _QrLabel("点这里扫码登录")
        self._qr.setFixedSize(328, 220)
        self._qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr.setStyleSheet(_QR_QSS)
        self._qr.setCursor(Qt.CursorShape.PointingHandCursor)
        self._qr.clicked.connect(self._on_qr_clicked)
        layout.addWidget(self._qr)

        self._state = QLabel("")
        self._state.setWordWrap(True)
        self._state.setStyleSheet("font-size: 12px; color: #333;")
        layout.addWidget(self._state)

        switches = QLabel("功能开关")
        switches.setStyleSheet(_SECTION_QSS)
        layout.addWidget(switches)

        cfg = duty.load_config()
        self._checks: dict[str, QCheckBox] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for index, (key, text, tip) in enumerate(_SWITCHES):
            box = QCheckBox(text)
            box.setStyleSheet(_CHECKBOX_QSS)
            box.setChecked(bool(cfg.get(key)))
            box.toggled.connect(lambda checked, k=key: self._on_toggle(k, checked))
            grid.addWidget(_with_help(box, tip), index // 2, index % 2)
            self._checks[key] = box
        layout.addLayout(grid)

        hide_hint = QLabel("评论与回应 @ 有概率被 B 站隐藏（仅自己可见、对方收不到通知），建议不启用")
        hide_hint.setWordWrap(True)
        hide_hint.setStyleSheet("font-size: 11px; color: #999;")
        layout.addWidget(hide_hint)

        window_hint = QLabel(f"定时任务在夜间休息段（{cfg['schedule_active_hours']} 之外）自动停止")
        window_hint.setStyleSheet("font-size: 11px; color: #999;")
        layout.addWidget(window_hint)

        interval_row = QHBoxLayout()
        interval_row.setSpacing(4)
        interval_title = QLabel("定时任务间隔（分钟）")
        interval_title.setStyleSheet(_SECTION_QSS)
        interval_row.addWidget(interval_title)
        interval_row.addWidget(_help_label("两次定时任务的最小间隔，10-1440，留空恢复 180；桌宠在忙时按退避顺延"))
        interval_row.addStretch()
        layout.addLayout(interval_row)

        self._interval = QLineEdit()
        self._interval.setStyleSheet(_INPUT_QSS)
        self._interval.setPlaceholderText("10-1440，默认 180")
        self._interval.setText(str(cfg["schedule_interval_minutes"]))
        self._interval.editingFinished.connect(self._save_interval)
        layout.addWidget(self._interval)

        pause_row = QHBoxLayout()
        pause_row.setSpacing(4)
        pause_title = QLabel("定时任务禁用时段")
        pause_title.setStyleSheet(_SECTION_QSS)
        pause_row.addWidget(pause_title)
        pause_row.addWidget(_help_label("填写的区间内不触发；多个区间用逗号分隔，留空表示不暂停"))
        pause_row.addStretch()
        layout.addLayout(pause_row)

        self._pause_hours = QLineEdit()
        self._pause_hours.setStyleSheet(_INPUT_QSS)
        self._pause_hours.setPlaceholderText("如 09:00-12:00,14:00-18:00")
        self._pause_hours.setText(cfg["schedule_pause_hours"])
        self._pause_hours.editingFinished.connect(self._save_pause_hours)
        layout.addWidget(self._pause_hours)

        trigger_row = QHBoxLayout()
        trigger_row.setSpacing(4)
        self._btn_trigger = QPushButton("触发定时任务")
        self._btn_trigger.setStyleSheet(_BUTTON_QSS)
        self._btn_trigger.clicked.connect(self._trigger_now)
        trigger_row.addWidget(self._btn_trigger)
        trigger_row.addWidget(_help_label("不检查开关与时段，直接发起一次定时任务；不改变下一次定时"))
        trigger_row.addStretch()
        layout.addLayout(trigger_row)

        kw_title = QLabel("搜索关键词")
        kw_title.setStyleSheet(_SECTION_QSS)
        layout.addWidget(kw_title)

        self._keywords = QLineEdit()
        self._keywords.setStyleSheet(_INPUT_QSS)
        self._keywords.setPlaceholderText("用逗号分隔，留空用默认")
        self._keywords.setText("，".join(cfg["keywords"]))
        self._keywords.editingFinished.connect(self._save_keywords)
        layout.addWidget(self._keywords)

        uid_title = QLabel("私信收件人 UID")
        uid_title.setStyleSheet(_SECTION_QSS)
        layout.addWidget(uid_title)

        self._dm_uid = QLineEdit()
        self._dm_uid.setStyleSheet(_INPUT_QSS)
        self._dm_uid.setPlaceholderText("不填则不发私信")
        self._dm_uid.setText(str(cfg["dm_uid"]) if cfg["dm_uid"] else "")
        self._dm_uid.editingFinished.connect(self._save_dm_uid)
        layout.addWidget(self._dm_uid)

        self._hint = QLabel("")
        self._hint.setStyleSheet("font-size: 11px; color: #666;")
        layout.addWidget(self._hint)

        self._btn_logout = QPushButton("退出登录")
        self._btn_logout.setStyleSheet(_BUTTON_QSS)
        self._btn_logout.clicked.connect(self._logout)
        layout.addWidget(self._btn_logout)

    def _refresh_login_state(self):
        credential = Credential.from_file(credential_path())
        if credential is None:
            self._state.setText("未登录")
            self._qr.setText("点这里扫码登录")
            self._btn_logout.setEnabled(False)
            return
        self._state.setText(f"已登录，UID {credential.dedeuserid}")
        self._qr.setText("已登录")
        self._btn_logout.setEnabled(True)

    def _on_qr_clicked(self):
        if Credential.from_file(credential_path()) is not None:
            self._hint.setText("已登录，换号请先退出登录")
            QTimer.singleShot(1500, self._hint.clear)
            return
        self._start_login()

    def _start_login(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self._qr.setText("正在取二维码…")
        self._state.setText("")
        self._worker = _LoginWorker(self)
        self._worker.qr_ready.connect(self._on_qr)
        self._worker.state_changed.connect(self._on_state)
        self._worker.logged_in.connect(self._on_logged_in)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_login_finished)
        self._worker.start()

    def _on_login_finished(self):
        """轮询线程结束后释放引用，重新扫码可新建线程。"""
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()

    def _on_qr(self, data: bytes):
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self._qr.setPixmap(pixmap.scaled(
                self._qr.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def _on_state(self, state: str):
        self._state.setText(_STATE_TEXT.get(state, state))

    def _on_logged_in(self, credential):
        self._state.setText(f"登录成功，UID {credential.dedeuserid}")
        self._qr.setText("已登录")
        self._btn_logout.setEnabled(True)

    def _on_failed(self, message: str):
        self._state.setText(message)

    def _save_interval(self):
        try:
            cfg = duty.set_interval_minutes(self._interval.text())
        except ValueError as exc:
            self._hint.setText(f"间隔无效：{exc}")
            return
        except OSError as exc:
            logger.warning(f"[bili] 保存间隔失败：{exc}")
            self._hint.setText(f"保存失败：{exc}")
            return
        self._interval.setText(str(cfg["schedule_interval_minutes"]))
        if cfg["schedule_enabled"]:
            schedule.stop()
            schedule.start(cfg)
        self._hint.setText("已保存")
        QTimer.singleShot(1500, self._hint.clear)

    def _save_pause_hours(self):
        text = self._pause_hours.text().strip()
        bad = schedule.invalid_pause_parts(text)
        if bad:
            self._hint.setText(f"时段格式无效：{'、'.join(bad)}")
            return
        try:
            cfg = duty.update_config({"schedule_pause_hours": text})
        except OSError as exc:
            logger.warning(f"[bili] 保存禁用时段失败：{exc}")
            self._hint.setText(f"保存失败：{exc}")
            return
        self._pause_hours.setText(cfg["schedule_pause_hours"])
        if cfg["schedule_enabled"]:
            schedule.stop()
            schedule.start(cfg)
        self._hint.setText("已保存")
        QTimer.singleShot(1500, self._hint.clear)

    def _trigger_now(self):
        """立即发起一次定时任务，不看开关与时段。"""
        schedule.trigger_now()
        self._hint.setText("已触发一次")
        QTimer.singleShot(1500, self._hint.clear)

    def _save_keywords(self):
        try:
            cfg = duty.set_keywords(self._keywords.text())
        except OSError as exc:
            logger.warning(f"[bili] 保存关键词失败：{exc}")
            self._hint.setText(f"保存失败：{exc}")
            return
        self._keywords.setText("，".join(cfg["keywords"]))
        self._hint.setText("已保存")
        QTimer.singleShot(1500, self._hint.clear)

    def _save_dm_uid(self):
        try:
            cfg = duty.set_dm_uid(self._dm_uid.text())
        except ValueError as exc:
            self._hint.setText(f"UID 无效：{exc}")
            return
        except OSError as exc:
            logger.warning(f"[bili] 保存 UID 失败：{exc}")
            self._hint.setText(f"保存失败：{exc}")
            return
        self._dm_uid.setText(str(cfg["dm_uid"]) if cfg["dm_uid"] else "")
        self._hint.setText("已保存")
        QTimer.singleShot(1500, self._hint.clear)

    def _on_toggle(self, key: str, checked: bool):
        try:
            cfg = duty.update_config({key: bool(checked)})
        except OSError as exc:
            logger.warning(f"[bili] 保存开关失败：{exc}")
            self._hint.setText(f"保存失败：{exc}")
            return
        if key == "schedule_enabled":
            schedule.stop()
            if cfg["schedule_enabled"]:
                schedule.start(cfg)
        self._hint.setText("已保存")
        QTimer.singleShot(1500, self._hint.clear)

    def _logout(self):
        try:
            credential_path().unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"[bili] 删除凭证失败：{exc}")
        self._qr.clear()
        self._qr.setText("已退出")
        self._refresh_login_state()

    def closeEvent(self, event):
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(1500)
        super().closeEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)


def show_panel() -> None:
    """打开账号面板：已销毁的重建，存活的置顶。"""
    global _current
    if _current is not None:
        try:
            _current.show()
            _current.raise_()
            _current.activateWindow()
            return
        except RuntimeError:
            _current = None
    _current = AccountPanel()
    _current.show()
