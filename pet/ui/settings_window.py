"""设置界面 — 用户配置的图形界面"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QTabWidget, QVBoxLayout, QWidget)

from pet.auto_start import set_auto_start
from pet.config import _KEY_META, config
from pet.ui.settings_actions import SettingsActions, apply_pet_scale
from pet.ui.settings_fields import SettingsForm
from pet.ui.settings_hooks import invalidate_action_section
from pet.ui.settings_tabs import SettingsTabs
from pet.ui.styles import (BUTTON_PRIMARY_QSS, BUTTON_QSS, CHECKBOX_QSS,
                           COMBOBOX_QSS, INPUT_QSS, PANEL_QSS,
                           SETTING_ICON_PATH, TAB_BAR_QSS, TEXTEDIT_QSS,
                           _COLOR_BG, _COLOR_TEXT_TITLE, ensure_taskbar_icon,
                           make_close_button, make_minimize_button)

logger = logging.getLogger(__name__)

_W = 520
_H = 720


class SettingsWindow(QWidget):
    _instance: SettingsWindow | None = None

    @classmethod
    def show_instance(cls, agent, parent=None):
        """单例模式 — 获取或创建设置窗口并显示。"""
        if cls._instance is not None:
            try:
                cls._instance.isVisible()
            except RuntimeError:
                cls._instance = None
        if cls._instance is None:
            cls._instance = cls(agent, parent=None)
        cls._instance.load_values()
        cls._instance.show()
        cls._instance.raise_()

    def __init__(self, agent, parent=None):
        super().__init__(parent)
        self.agent = agent
        self._drag_pos = None

        self.setObjectName("settingsWindow")
        self.setWindowTitle("设置")
        avail = QApplication.primaryScreen().availableGeometry()
        w = min(_W, max(360, avail.width() - 40))
        h = min(_H, max(320, avail.height() - 40))
        w, h = min(w, avail.width()), min(h, avail.height())
        self.resize(w, h)
        self.setFixedSize(w, h)
        pos = avail.center() - self.rect().center()
        self.move(max(avail.left(), pos.x()), max(avail.top(), pos.y()))
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        try:
            self.setWindowIcon(QIcon(SETTING_ICON_PATH))
        except Exception:
            pass

        self.form = SettingsForm(self)
        self.tabs = SettingsTabs(self.form)
        self.actions = SettingsActions(
            self.form, self.tabs.widgets, self, agent,
            self._msg, self.isVisible, self.tabs.update_profile_ui)
        self._setup_ui()
        self.tabs.bind(self.actions)
        self.setStyleSheet(
            PANEL_QSS + BUTTON_QSS + BUTTON_PRIMARY_QSS +
            INPUT_QSS + COMBOBOX_QSS + TEXTEDIT_QSS + CHECKBOX_QSS +
            TAB_BAR_QSS
        )

    def showEvent(self, event):
        super().showEvent(event)
        ensure_taskbar_icon(self)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 2, 6, 4)
        root.setSpacing(0)

        title_bar = QWidget()
        title_bar.setFixedHeight(38)
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(12, 0, 6, 0)
        title_row.setSpacing(6)
        try:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(QIcon(SETTING_ICON_PATH).pixmap(18, 18))
            title_row.addWidget(icon_lbl)
        except Exception:
            pass
        title = QLabel("设置")
        title.setStyleSheet(
            f"font-size:13px; color:{_COLOR_TEXT_TITLE}; font-weight:bold; background:transparent;")
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(make_minimize_button(self))
        title_row.addWidget(make_close_button(self))
        root.addWidget(title_bar)

        title_bar.mousePressEvent = self._header_press
        title_bar.mouseMoveEvent = self._header_move

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.tabBar().setExpanding(True)
        self._tabs.tabBar().setStyleSheet(f"QTabBar {{ background: {_COLOR_BG}; }}")
        for label, page in self.tabs.build():
            self._tabs.addTab(page, label)
        root.addWidget(self._tabs, stretch=1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(12, 4, 12, 8)
        bottom.addStretch()
        btn_save = QPushButton("保存")
        btn_save.setStyleSheet(BUTTON_PRIMARY_QSS)
        btn_save.clicked.connect(self._on_save)
        bottom.addWidget(btn_save)
        root.addLayout(bottom)

    def _msg(self, title: str, text: str, *,
             icon: QMessageBox.Icon = QMessageBox.Icon.NoIcon,
             warning: bool = False):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(icon if warning else QMessageBox.Icon.NoIcon)
        box.addButton("确定", QMessageBox.ButtonRole.AcceptRole)
        box.exec()

    def load_values(self):
        """从 config 填充各控件，每次打开窗口时调用。"""
        self.form.load(config)
        # 下拉框值没变时不发 currentTextChanged，模式相关的启用状态要显式同步一次
        self.tabs.on_mode_changed(self.tabs.widgets.brain_combo.currentText())
        self.tabs.update_profile_ui()

    def _on_save(self):
        """保存所有修改并即时生效。"""
        values, invalid_keys = self.form.collect()
        if invalid_keys:
            details = "\n".join(f"· {self.form.invalid_hint(k)}" for k in invalid_keys)
            self._msg("输入有误",
                      f"以下字段的数值无效，未能保存：\n{details}\n\n请按提示范围修改后再保存。")
            return
        needs_rebuild_client = False
        needs_scheduler_update = False
        for key, value in values.items():
            config.save(key, value)
            meta = _KEY_META.get(key)
            if meta is None:
                continue
            if meta["category"] == "connection":
                needs_rebuild_client = True
            if meta["category"] == "behavior":
                needs_scheduler_update = True

        if "AUTO_START_ON_BOOT" in values:
            set_auto_start(values["AUTO_START_ON_BOOT"])

        if needs_scheduler_update and self.agent and hasattr(self.agent, "scheduler"):
            try:
                self.agent.scheduler.update_config()
            except Exception as e:
                logger.exception(f"[Settings] scheduler.update_config failed: {e}")

        if needs_scheduler_update:
            try:
                invalidate_action_section()
            except Exception as e:
                logger.exception(f"[Settings] invalidate_action_section failed: {e}")

        if needs_rebuild_client:
            self.actions.rebuild_client()
        if "PET_SCALE" in values:
            apply_pet_scale(self.agent)
        self.tabs.update_profile_ui()
        self._msg("设置已保存", "已保存。部分设置须重启后生效。")
        self.form.take_snapshot()

    def closeEvent(self, event):
        """关闭前检查未保存修改，清理后台线程。"""
        if self.form.is_dirty():
            box = QMessageBox(self)
            box.setWindowTitle("未保存")
            box.setText("有修改尚未保存，是否保存？")
            box.setIcon(QMessageBox.Icon.NoIcon)
            btn_save = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
            btn_discard = box.addButton("不保存", QMessageBox.ButtonRole.DestructiveRole)
            btn_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(btn_cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked == btn_save:
                self._on_save()
                if self.form.is_dirty():
                    event.ignore()
                    return
            elif clicked == btn_cancel:
                event.ignore()
                return
        self.actions.shutdown()
        super().closeEvent(event)

    def _header_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def _header_move(self, event):
        if self._drag_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        painter.fillPath(path, QColor(_COLOR_BG))
        painter.setPen(QPen(QColor("#000000"), 1))
        painter.drawPath(path)
