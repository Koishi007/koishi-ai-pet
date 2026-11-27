"""设置页的控件工厂、表单模型与跨模块共享的控件引用"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel,
                               QLineEdit, QSlider, QTextEdit, QVBoxLayout,
                               QWidget)

from pet.config import _KEY_META
from pet import theme
from pet.ui.icons import tinted_icon
from pet.ui.ink_button import InkButton
from pet.ui.qss import HELP_BADGE_QSS, VOLUME_SLIDER_QSS
from pet.ui.settings_spec import display_value, stored_value
from pet.ui.styles import (BUTTON_PRIMARY_QSS, COMBOBOX_QSS, HIDE_ICON_PATH,
                           INPUT_HIGHLIGHT_QSS, SHOW_ICON_PATH, _COLOR_TEXT_SEC)
from pet.ui.widgets import NoWheelComboBox


class MarkdownEdit(QWidget):
    """支持 Markdown 渲染的文本编辑器。点击按钮切换编辑/预览模式。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._raw_text = ""
        self._editing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        self._toggle_btn = InkButton("编辑")
        self._toggle_btn.setFixedWidth(72)
        self._toggle_btn.setStyleSheet(BUTTON_PRIMARY_QSS)
        self._toggle_btn.clicked.connect(self._toggle_mode)
        toolbar.addWidget(self._toggle_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._edit = QTextEdit()
        self._edit.setAcceptRichText(False)
        self._edit.setReadOnly(True)
        self._edit.setPlaceholderText("点击「编辑」按钮输入 Markdown")
        layout.addWidget(self._edit)

    def setPlainText(self, text: str):
        self._raw_text = text or ""
        if self._editing:
            self._edit.setPlainText(self._raw_text)
        else:
            self._edit.document().setMarkdown(self._raw_text)

    def toPlainText(self) -> str:
        if self._editing:
            self._raw_text = self._edit.toPlainText()
        return self._raw_text

    def setMinimumHeight(self, h: int):
        self._edit.setMinimumHeight(h)

    def _toggle_mode(self):
        if self._editing:
            self._raw_text = self._edit.toPlainText()
            self._editing = False
            self._edit.setReadOnly(True)
            self._edit.document().setMarkdown(self._raw_text)
            self._toggle_btn.setText("编辑")
        else:
            self._editing = True
            self._edit.setReadOnly(False)
            self._edit.setPlainText(self._raw_text)
            self._toggle_btn.setText("预览")
            self._edit.setFocus()

    def focusOutEvent(self, event):
        if self._editing:
            self._raw_text = self._edit.toPlainText()
        super().focusOutEvent(event)


@dataclass
class SettingsWidgets:
    """页签构建后仍被联动方法与后台动作引用的控件。"""

    brain_combo: object | None = None
    mode_block: object | None = None
    api_section: object | None = None
    ollama_section: object | None = None
    generate_block: object | None = None
    local_hint: object | None = None
    provider_combo: object | None = None
    provider_alt_combo: object | None = None
    effort_combo: object | None = None
    effort_label: object | None = None
    url_edit: object | None = None
    url_label: object | None = None
    ollama_url_edit: object | None = None
    llm_key_edit: object | None = None
    key_toggle: object | None = None
    model_edit: object | None = None
    ollama_model_edit: object | None = None
    ollama_model_alt_edit: object | None = None
    ollama_effort_combo: object | None = None
    ollama_effort_label: object | None = None
    ollama_effort_alt_combo: object | None = None
    ollama_effort_alt_label: object | None = None
    btn_fetch_models: object | None = None
    temperature_edit: object | None = None
    tool_rounds_edit: object | None = None
    thinking_check: object | None = None
    thinking_hint: object | None = None
    label_active_profile: object | None = None
    btn_switch_profile: object | None = None
    fallback_check: object | None = None
    alt_url_edit: object | None = None
    alt_url_label: object | None = None
    alt_key_edit: object | None = None
    alt_key_toggle: object | None = None
    alt_model_edit: object | None = None
    alt_effort_combo: object | None = None
    alt_effort_label: object | None = None
    btn_fetch_models_alt: object | None = None
    btn_test: object | None = None
    label_test: object | None = None
    test_output: object | None = None
    btn_fetch_mem_models: object | None = None
    btn_merge_now: object | None = None
    label_merge: object | None = None
    btn_merge_test: object | None = None
    label_merge_test: object | None = None
    btn_mem_test: object | None = None
    label_mem_test: object | None = None
    conn_test_output: object | None = None
    hotkey_display: object | None = None
    capture_btn: object | None = None
    voice_btn_test: object | None = None
    voice_label_test: object | None = None
    voice_test_output: object | None = None
    capture_listener: object | None = None


class SettingsForm:
    """配置键 → 控件的注册表，兼做读写、快照与校验。"""

    def __init__(self, container: QWidget | None = None):
        self.fields: dict[str, QWidget] = {}
        self.snapshot: dict = {}
        self._container = container
        self._label_cache: dict[str, str] | None = None

    def line(self, key: str, placeholder: str = "", validator=None) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder or _KEY_META.get(key, {}).get("placeholder", ""))
        edit.setStyleSheet(INPUT_HIGHLIGHT_QSS)
        if validator:
            edit.setValidator(validator)
        self.fields[key] = edit
        return edit

    def secret_row(self, key: str, placeholder: str = "",
                    stylesheet: str = INPUT_HIGHLIGHT_QSS) -> QHBoxLayout:
        row = QHBoxLayout()
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setPlaceholderText(placeholder)
        edit.setStyleSheet(stylesheet)
        self.fields[key] = edit
        row.addWidget(edit)

        toggle = InkButton(radius=9, shadow_offset=(1, 2))
        toggle.setIcon(tinted_icon(SHOW_ICON_PATH, theme.INK, 16))
        toggle.setIconSize(QSize(16, 16))
        toggle.setFixedSize(30, 30)
        toggle.setCheckable(True)
        toggle.setStyleSheet(
            f"QPushButton {{ background:{theme.SURFACE};"
            f" border:{theme.BORDER}px solid {theme.INK}; border-radius:9px; }}"
            f"QPushButton:hover {{ background:{theme.PRIMARY_SOFT}; }}"
        )
        toggle.toggled.connect(
            lambda checked: (
                toggle.setIcon(tinted_icon(
                    HIDE_ICON_PATH if checked else SHOW_ICON_PATH, theme.INK, 16)),
                edit.setEchoMode(
                    QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
                )
            )[:0]
        )
        row.addWidget(toggle)
        return row

    def check(self, key: str, label: str) -> QCheckBox:
        cb = QCheckBox(label)
        self.fields[key] = cb
        return cb

    def combo(self, key: str, items: list[str]) -> QComboBox:
        cb = NoWheelComboBox()
        cb.addItems(items)
        cb.setStyleSheet(COMBOBOX_QSS)
        self.fields[key] = cb
        return cb

    def slider_row(self, key: str, minimum: int, maximum: int) -> QWidget:
        """滑块加右侧数值：滑块的取值直接是配置值。"""
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setStyleSheet(VOLUME_SLIDER_QSS)
        # QSlider 的 sizeHint 固定为 15px，圆形滑块需要额外高度才不被裁
        slider.setMinimumHeight(20)
        value_label = QLabel(str(slider.value()))
        value_label.setFixedWidth(28)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value_label.setStyleSheet(f"color:{_COLOR_TEXT_SEC}; font-size:11px;")
        slider.valueChanged.connect(lambda v: value_label.setText(str(v)))
        row.addWidget(slider, 1)
        row.addWidget(value_label)
        self.fields[key] = slider
        setattr(self, f"{key}_value_label", value_label)
        holder.setMinimumWidth(180)
        return holder

    def combo_with_data(self, key: str, items: list[tuple[str, object]]) -> QComboBox:
        """下拉项为 (显示文案, 存储值)；读取时用 currentData()，不用 currentText()。"""
        cb = NoWheelComboBox()
        for label, value in items:
            cb.addItem(label, value)
        cb.setStyleSheet(COMBOBOX_QSS)
        self.fields[key] = cb
        return cb

    @staticmethod
    def select_combo_data(widget: QComboBox, value) -> None:
        """按存储值选中下拉项，找不到时保持第一项。"""
        idx = widget.findData(value)
        widget.setCurrentIndex(max(idx, 0))

    def help_badge(self, tooltip: str) -> QLabel:
        badge = QLabel("?")
        badge.setFixedSize(16, 16)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setCursor(Qt.CursorShape.WhatsThisCursor)
        badge.setStyleSheet(HELP_BADGE_QSS)
        badge.setToolTip(tooltip)
        return badge

    def label_with_help(self, text: str, tooltip: str) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addStretch()
        row.addWidget(QLabel(text))
        row.addWidget(self.help_badge(tooltip))
        return holder

    @staticmethod
    def select_combo(widget: QComboBox, value) -> None:
        """按值选中下拉项，找不到时保持第一项。"""
        idx = widget.findText(str(value))
        widget.setCurrentIndex(max(idx, 0))

    def text_area(self, key: str) -> MarkdownEdit:
        te = MarkdownEdit()
        self.fields[key] = te
        return te

    def load(self, config) -> None:
        """从 config 读取当前值填充各控件，每次打开窗口时调用。"""
        for key, widget in self.fields.items():
            value = getattr(config, key, None)
            if value is None:
                continue
            value = display_value(key, value)
            if isinstance(widget, QLineEdit):
                widget.setText(",".join(value) if isinstance(value, list) else str(value))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                if widget.findData(value) >= 0:
                    self.select_combo_data(widget, value)
                else:
                    self.select_combo(widget, value)
            elif isinstance(widget, QSlider):
                widget.setValue(int(value))
            elif isinstance(widget, (QTextEdit, MarkdownEdit)):
                widget.setPlainText(str(value))
        self.take_snapshot()

    def take_snapshot(self) -> None:
        self.snapshot = {key: self._current(widget)
                        for key, widget in self.fields.items()
                        if self._current(widget) is not None}

    def is_dirty(self) -> bool:
        return any(self.snapshot.get(key) != self._current(widget)
                   for key, widget in self.fields.items()
                   if self._current(widget) is not None)

    @staticmethod
    def _current(widget):
        if isinstance(widget, QLineEdit):
            return widget.text()
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            data = widget.currentData()
            return data if data is not None else widget.currentText()
        if isinstance(widget, QSlider):
            return widget.value()
        if isinstance(widget, (QTextEdit, MarkdownEdit)):
            return widget.toPlainText()
        return None

    def collect(self) -> tuple[dict, list[str]]:
        """返回 (values, invalid_keys)；invalid_keys 为校验失败或转换失败的键。"""
        result: dict = {}
        invalid: list[str] = []
        for key, widget in self.fields.items():
            type_name = _KEY_META[key]["type"]
            if isinstance(widget, QLineEdit):
                if not widget.hasAcceptableInput():
                    invalid.append(key)
                    continue
                raw = widget.text().strip()
                if type_name == "int":
                    try:
                        result[key] = stored_value(key, int(raw))
                    except ValueError:
                        invalid.append(key)
                        continue
                elif type_name == "float":
                    try:
                        result[key] = float(raw)
                    except ValueError:
                        invalid.append(key)
                        continue
                elif type_name == "str_list":
                    if raw == "*":
                        result[key] = ["*"]
                    elif raw:
                        result[key] = [s.strip() for s in raw.split(",") if s.strip()]
                    else:
                        result[key] = []
                else:
                    result[key] = raw
            elif isinstance(widget, QCheckBox):
                result[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                data = widget.currentData()
                result[key] = data if data is not None else widget.currentText()
            elif isinstance(widget, QSlider):
                result[key] = widget.value()
            elif isinstance(widget, (QTextEdit, MarkdownEdit)):
                result[key] = widget.toPlainText()
        return result, invalid

    def field_label(self, key: str) -> str:
        """字段的界面标签：优先 QFormLayout 的 buddy，回退到配置描述。"""
        if self._label_cache is None:
            self._label_cache = {}
            if self._container is not None:
                for label in self._container.findChildren(QLabel):
                    buddy = label.buddy()
                    if buddy is None:
                        continue
                    for k, w in self.fields.items():
                        if w is buddy:
                            self._label_cache[k] = label.text().rstrip(":：")
                            break
        return self._label_cache.get(key) or _KEY_META.get(key, {}).get("description", key)

    def invalid_hint(self, key: str) -> str:
        label = self.field_label(key)
        widget = self.fields.get(key)
        validator = widget.validator() if isinstance(widget, QLineEdit) else None
        if isinstance(validator, (QIntValidator, QDoubleValidator)):
            return f"{label}（允许 {validator.bottom()}–{validator.top()}）"
        return f"{label}（不能为空）"
