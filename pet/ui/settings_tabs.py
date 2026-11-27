"""设置页的四个页签：只负责搭建控件并登记到 SettingsForm"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator, QFont, QIntValidator
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QScrollArea, QTextEdit,
                               QVBoxLayout, QWidget)

from pet.brain.llm_client import resolve_llm_profile
from pet.brain.providers.registry import (channel_choices, config_keys,
                                           config_keys_for, normalize_provider,
                                           reasoning_efforts, url_placeholder)
from pet.brain.reasoning import effective_efforts
from pet.config import PET_SCALE_STEPS, config
from pet import theme
from pet.ui.ink_button import InkButton
from pet.ui.settings_fields import SettingsForm, SettingsWidgets
from pet.ui.settings_spec import (EFFORT_DEFAULT_LABEL, MODE_ORDER, TAB_SPECS,
                                   VOICE_PRESET_CHOICES, VOICE_TRIGGER_CHOICES,
                                   effort_items)
from pet.ui.styles import (BUTTON_PRIMARY_QSS, COMBOBOX_QSS, INPUT_HIGHLIGHT_QSS,
                           SCROLLBAR_QSS, TEXTEDIT_QSS,
                           _COLOR_TEXT_MUTED, _COLOR_TEXT_SEC)
from pet.ui.widgets import NoWheelComboBox


_EFFORT_UNSUPPORTED = "当前模型不支持推理强度设置"
_EFFORT_THINKING_OFF = "关闭思考时不下发推理强度"


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"color:{_COLOR_TEXT_MUTED}; font-size:11px;")
    return label


def _test_output() -> QTextEdit:
    out = QTextEdit()
    out.setReadOnly(True)
    out.setMaximumHeight(60)
    out.setFont(QFont("Consolas", 9))
    out.setStyleSheet(
        TEXTEDIT_QSS + f"QTextEdit {{ background: {theme.SURFACE_WARM}; }}")
    return out


class SettingsTabs:
    """按 TAB_SPECS 的顺序构建页签，并把共享控件登记到 widgets。"""

    def __init__(self, form: SettingsForm):
        self.form = form
        self.widgets = SettingsWidgets()
        # 各档位下拉上次写入的选项与状态，输入过程中不重复重建
        self._effort_cache: dict[int, tuple] = {}

        # 每行不可用的原因：空串可用，unsupported 端点无档位
        self._effort_reason: dict[int, str] = {}

    def build(self) -> tuple[tuple[str, QWidget], ...]:
        builders = {
            "连接": self._connection,
            "行为": self._behavior,
            "提示词": self._personality,
            "通用": self._appearance,
        }
        return tuple((label, builders[label]()) for label, _keys in TAB_SPECS)

    def bind(self, actions) -> None:
        w = self.widgets
        w.brain_combo.currentTextChanged.connect(self.on_mode_changed)
        w.provider_combo.currentTextChanged.connect(self._apply_provider_visibility)
        w.provider_alt_combo.currentIndexChanged.connect(self._apply_provider_visibility)
        w.thinking_check.toggled.connect(self._sync_effort_enabled)
        for edit in (w.model_edit, w.alt_model_edit, w.alt_url_edit, w.alt_key_edit,
                     w.ollama_model_edit, w.ollama_model_alt_edit):
            edit.textChanged.connect(lambda _text: self._refresh_effort_options())
        w.btn_switch_profile.clicked.connect(actions.switch_profile)
        w.btn_fetch_models.clicked.connect(lambda: actions.fetch_models("primary"))
        w.btn_fetch_models_alt.clicked.connect(
            lambda: actions.fetch_models("alternative"))
        w.btn_test.clicked.connect(actions.test_llm)
        w.btn_fetch_mem_models.clicked.connect(actions.fetch_embedding_models)
        w.btn_merge_now.clicked.connect(actions.manual_merge)
        w.btn_merge_test.clicked.connect(actions.test_merge_connection)
        w.btn_mem_test.clicked.connect(actions.test_embedding)
        w.capture_btn.clicked.connect(actions.capture_hotkey)
        w.voice_btn_test.clicked.connect(actions.test_voice_connection)

    @staticmethod
    def _scroll_page(content: QWidget) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }" + SCROLLBAR_QSS)
        scroll.viewport().setStyleSheet("background: transparent;")
        scroll.setWidget(content)
        layout.addWidget(scroll)
        return page

    @staticmethod
    def _content(spacing: int | None = 10) -> tuple[QWidget, QVBoxLayout]:
        content = QWidget()
        inner = QVBoxLayout(content)
        inner.setContentsMargins(8, 8, 8, 8)
        if spacing is not None:
            inner.setSpacing(spacing)
        return content, inner


    def _connection(self) -> QWidget:
        f, w = self.form, self.widgets
        content, inner = self._content()

        mode_group = QGroupBox("调用模式")
        mode_form = QFormLayout(mode_group)
        mode_form.setSpacing(8)
        mode_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        w.brain_combo = NoWheelComboBox()
        w.brain_combo.addItems(MODE_ORDER)
        f.fields["BRAIN"] = w.brain_combo
        mode_form.addRow("调用模式:", w.brain_combo)
        inner.addWidget(mode_group)

        w.mode_block = self._mode_block()
        inner.addWidget(w.mode_block)
        w.local_hint = QLabel("离线模式不调用模型，下面的连接与生成设置都不生效。")
        w.local_hint.setWordWrap(True)
        w.local_hint.setStyleSheet(f"color:{_COLOR_TEXT_MUTED}; font-size:11px;")
        inner.addWidget(w.local_hint)

        w.generate_block = self._generate_block()
        inner.addWidget(w.generate_block)

        profile_group = QGroupBox("模型方案")
        switch_layout = QVBoxLayout(profile_group)
        switch_layout.setSpacing(6)
        switch_row = QHBoxLayout()
        w.label_active_profile = QLabel("当前启用：首选模型")
        w.label_active_profile.setStyleSheet(f"color:{_COLOR_TEXT_SEC}; font-size:11px;")
        switch_row.addWidget(w.label_active_profile)
        switch_row.addStretch()
        w.btn_switch_profile = InkButton("切换到备选模型")
        w.btn_switch_profile.setStyleSheet(BUTTON_PRIMARY_QSS)
        switch_row.addWidget(w.btn_switch_profile)
        switch_layout.addLayout(switch_row)
        w.fallback_check = f.check(
            "LLM_FALLBACK_ENABLED", "调用报错/超时重试时自动切换到备选模型")
        switch_layout.addWidget(w.fallback_check)
        inner.addWidget(profile_group)

        test_row = QHBoxLayout()
        w.btn_test = InkButton("测试连接")
        w.btn_test.setStyleSheet(BUTTON_PRIMARY_QSS)
        test_row.addWidget(w.btn_test)
        w.label_test = QLabel("就绪")
        w.label_test.setStyleSheet(f"color:{_COLOR_TEXT_SEC}; font-size:11px;")
        test_row.addWidget(w.label_test)
        test_row.addStretch()
        inner.addLayout(test_row)

        w.test_output = _test_output()
        inner.addWidget(w.test_output)
        inner.addStretch()
        return self._scroll_page(content)

    def _mode_block(self) -> QGroupBox:
        """按调用模式切换内容的固定区块。

        两个模式各自渲染一套连接与模型控件，键互不重叠，按模式显隐。
        """
        f, w = self.form, self.widgets
        group = QGroupBox("模型连接")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        w.api_section = self._api_section()
        layout.addWidget(w.api_section)
        w.ollama_section = self._ollama_section()
        layout.addWidget(w.ollama_section)
        return group

    def _api_row(self, form, prefix: str, profile_label: str) -> None:
        """一套方案的连接与模型字段。"""
        f, w = self.form, self.widgets
        alt = prefix == "ALT"

        provider_key = "LLM_PROVIDER_ALT" if alt else "LLM_PROVIDER"
        combo = f.combo_with_data(provider_key, list(channel_choices()))
        form.addRow(f"{profile_label}通道:", combo)

        url_edit = f.line("LLM_URL_ALT" if alt else "LLM_URL",
                          "https://api.example.com/v1")
        form.addRow(f"{profile_label} API 地址:", url_edit)
        url_label = form.labelForField(url_edit)

        key_row = f.secret_row("LLM_KEY_ALT" if alt else "LLM_KEY", "sk-...")
        key_edit = f.fields["LLM_KEY_ALT" if alt else "LLM_KEY"]
        form.addRow(f"{profile_label} API Key:", key_row)

        model_edit = QLineEdit()
        model_edit.setStyleSheet(INPUT_HIGHLIGHT_QSS)
        f.fields["LLM_MODEL_ALT" if alt else "LLM_MODEL"] = model_edit
        fetch_btn = InkButton("获取列表")
        fetch_btn.setStyleSheet(BUTTON_PRIMARY_QSS)
        fetch_btn.setFixedWidth(84)
        model_row = QHBoxLayout()
        model_row.addWidget(model_edit)
        model_row.addWidget(fetch_btn)
        form.addRow(f"{profile_label}模型名称:", model_row)

        effort_combo = NoWheelComboBox()
        effort_combo.setStyleSheet(COMBOBOX_QSS)
        f.fields["LLM_REASONING_EFFORT_ALT" if alt else "LLM_REASONING_EFFORT"] = effort_combo
        form.addRow(f"{profile_label}推理强度:", effort_combo)
        effort_label = form.labelForField(effort_combo)

        if alt:
            w.provider_alt_combo = combo
            w.alt_url_edit, w.alt_url_label = url_edit, url_label
            w.alt_key_edit, w.alt_key_toggle = key_edit, key_row.itemAt(1).widget()
            w.alt_model_edit, w.btn_fetch_models_alt = model_edit, fetch_btn
            w.alt_effort_combo, w.alt_effort_label = effort_combo, effort_label
        else:
            w.provider_combo = combo
            w.url_edit, w.url_label = url_edit, url_label
            w.llm_key_edit, w.key_toggle = key_edit, key_row.itemAt(1).widget()
            w.model_edit, w.btn_fetch_models = model_edit, fetch_btn
            w.effort_combo, w.effort_label = effort_combo, effort_label

    def _api_section(self) -> QWidget:
        """API 档：首选一套字段，备选一套字段。"""
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        primary_form = QFormLayout()
        primary_form.setSpacing(8)
        primary_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._api_row(primary_form, "", "首选")
        layout.addLayout(primary_form)

        alt_form = QFormLayout()
        alt_form.setSpacing(8)
        alt_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._api_row(alt_form, "ALT", "备选")
        layout.addLayout(alt_form)
        return section

    def _ollama_section(self) -> QWidget:
        f, w = self.form, self.widgets
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        url_form = QFormLayout()
        url_form.setSpacing(8)
        url_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        w.ollama_url_edit = f.line("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        url_form.addRow("服务地址:", w.ollama_url_edit)
        layout.addLayout(url_form)

        primary_form = QFormLayout()
        primary_form.setSpacing(8)
        primary_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._ollama_row(primary_form, "首选", alt=False)
        layout.addLayout(primary_form)

        alt_form = QFormLayout()
        alt_form.setSpacing(8)
        alt_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._ollama_row(alt_form, "备选", alt=True)
        layout.addLayout(alt_form)
        return section

    def _ollama_row(self, form, profile_label: str, *, alt: bool) -> None:
        f, w = self.form, self.widgets
        suffix = "_ALT" if alt else ""
        model_edit = QLineEdit()
        model_edit.setStyleSheet(INPUT_HIGHLIGHT_QSS)
        f.fields[f"OLLAMA_LLM_MODEL{suffix}"] = model_edit
        form.addRow(f"{profile_label}模型名称:", model_edit)

        effort_combo = NoWheelComboBox()
        effort_combo.setStyleSheet(COMBOBOX_QSS)
        f.fields[f"OLLAMA_LLM_REASONING_EFFORT{suffix}"] = effort_combo
        form.addRow(f"{profile_label}推理强度:", effort_combo)
        effort_label = form.labelForField(effort_combo)

        if alt:
            w.ollama_model_alt_edit = model_edit
            w.ollama_effort_alt_combo = effort_combo
            w.ollama_effort_alt_label = effort_label
        else:
            w.ollama_model_edit = model_edit
            w.ollama_effort_combo = effort_combo
            w.ollama_effort_label = effort_label

    def _generate_block(self) -> QGroupBox:
        f, w = self.form, self.widgets
        group = QGroupBox("生成与工具")
        form = QFormLayout(group)
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        w.temperature_edit = f.line("LLM_TEMPERATURE", "0.7", QDoubleValidator(0, 2, 2))
        w.temperature_edit.setMaxLength(4)
        form.addRow("采样温度:", w.temperature_edit)

        w.thinking_check = f.check("LLM_THINKING_DISABLED", "关闭思考模式")
        form.addRow("", w.thinking_check)
        w.thinking_hint = _hint("关闭可提升调用速度")
        form.addRow("", w.thinking_hint)

        form.addRow("", f.check("LLM_TOOLS_ENABLED", "启用工具调用"))
        form.addRow("", _hint("模型不支持 tools 参数时请关闭此选项"))

        w.tool_rounds_edit = f.line("LLM_TOOL_MAX_ROUNDS", "99", QIntValidator(10, 99))
        form.addRow("工具调用最大轮次:", w.tool_rounds_edit)
        return group

    def on_mode_changed(self, mode: str) -> None:
        """按调用模式切换区块内容与可用状态。

        两套模式各有自己的控件与配置键，这里只切显隐。
        """
        w = self.widgets

        is_local = mode == "local"
        is_api = mode == "api"
        is_ollama = mode == "ollama"

        w.mode_block.setVisible(not is_local)
        w.local_hint.setVisible(is_local)
        w.api_section.setVisible(is_api)
        w.ollama_section.setVisible(is_ollama)

        # Ollama 固定走 OpenAI 兼容接口，通道不可选
        w.provider_combo.setEnabled(is_api)
        w.provider_alt_combo.setEnabled(is_api)

        # 全程可编辑但 local 档下没有意义的字段
        for widget in (w.btn_switch_profile, w.fallback_check, w.btn_test,
                       w.label_test, w.test_output, w.temperature_edit,
                       w.tool_rounds_edit, w.thinking_check, w.thinking_hint):
            widget.setEnabled(not is_local)

        self._apply_provider_visibility()
        self._sync_effort_enabled()

    def _fill_effort_combo(self, combo, efforts: tuple, empty_label: str) -> None:
        """按端点可用档位重建下拉项；选项未变时保留当前选中。"""
        if self._effort_cache.get(id(combo)) == (efforts, empty_label):
            return
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for label, value in effort_items(efforts, empty_label):
            combo.addItem(label, value)
        combo.setCurrentIndex(max(combo.findData(current), 0))
        combo.blockSignals(False)
        self._effort_cache[id(combo)] = (efforts, empty_label)

    def _alt_channel(self) -> str:
        return normalize_provider(self.widgets.provider_alt_combo.currentData())

    def _effort_rows(self) -> tuple:
        """两档的档位行：下拉、标签、模型输入框、通道、清单键、取值键。"""
        w = self.widgets
        api_channel = normalize_provider(w.provider_combo.currentData())
        alt_channel = self._alt_channel()
        return (
            (w.effort_combo, w.effort_label, w.model_edit, api_channel,
             "LLM_REASONING_EFFORTS", "LLM_REASONING_EFFORT"),
            (w.alt_effort_combo, w.alt_effort_label, w.alt_model_edit, alt_channel,
             "LLM_REASONING_EFFORTS_ALT", "LLM_REASONING_EFFORT_ALT"),
            (w.ollama_effort_combo, w.ollama_effort_label, w.ollama_model_edit,
             "openai", "OLLAMA_LLM_REASONING_EFFORTS", "OLLAMA_LLM_REASONING_EFFORT"),
            (w.ollama_effort_alt_combo, w.ollama_effort_alt_label,
             w.ollama_model_alt_edit, "openai", "OLLAMA_LLM_REASONING_EFFORTS_ALT",
             "OLLAMA_LLM_REASONING_EFFORT_ALT"),
        )

    def _refresh_effort_options(self) -> None:
        """按通道声明、端点配置与模型名重建各档位下拉；不可用时灰置并提示。"""
        rows = self._effort_rows()
        for combo, combo_label, model_edit, channel, efforts_key, value_key in rows:
            declared = reasoning_efforts(channel)
            override = getattr(config, efforts_key, "")
            efforts = effective_efforts(declared, override, model_edit.text())
            self._fill_effort_combo(combo, efforts, EFFORT_DEFAULT_LABEL)
            self._restore_effort_value(combo, getattr(config, value_key, "") or "")
            self._effort_reason[id(combo)] = "" if efforts else "unsupported"
            combo.setVisible(bool(declared))
            combo_label.setVisible(bool(declared))
        self._sync_effort_enabled()

    def _restore_effort_value(self, combo, value: str) -> None:
        """档位下拉按存储值选中；值不在当前选项内时回到首项。"""
        combo.blockSignals(True)
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _sync_effort_enabled(self) -> None:
        """关闭思考或端点没有档位时禁用对应下拉，并说明原因。"""
        w = self.widgets
        ready = w.brain_combo.currentText() != "local"
        thinking_off = w.thinking_check.isChecked()
        for combo, label, _model, _channel, _efforts_key, _value_key in self._effort_rows():
            reason = self._effort_reason.get(id(combo), "")
            combo.setEnabled(ready and not thinking_off and not reason)
            if reason == "unsupported":
                text = _EFFORT_UNSUPPORTED
            elif thinking_off:
                text = _EFFORT_THINKING_OFF
            else:
                text = ""
            combo.setToolTip(text)
            label.setToolTip(text)

    def _apply_provider_visibility(self) -> None:
        """按登记表的 config_keys 显隐通道专属键；标签随控件一起显隐。"""
        w = self.widgets
        primary_name = w.provider_combo.currentData()
        alt_name = w.provider_alt_combo.currentData()
        shown = set(config_keys(primary_name))
        alt_shown = set(config_keys_for(alt_name, "alternative"))
        w.url_edit.setPlaceholderText(url_placeholder(primary_name))
        w.alt_url_edit.setPlaceholderText(url_placeholder(alt_name))
        # 每行一项：控件、随行的标签、对应的配置键、该键按哪套方案的通道决定显隐
        for widget, label, key, scope in (
            (w.url_edit, w.url_label, "LLM_URL", "primary"),
            (w.alt_url_edit, w.alt_url_label, "LLM_URL_ALT", "alternative"),
        ):
            pool = {"primary": shown, "alternative": alt_shown}[scope]
            visible = key in pool
            widget.setVisible(visible)
            if label is not None:
                label.setVisible(visible)
        self._refresh_effort_options()

    def update_profile_ui(self) -> None:
        """刷新「当前启用方案」的按钮文案与状态标签。"""
        w = self.widgets
        active = getattr(config, "LLM_ACTIVE_PROFILE", "primary") or "primary"
        model = resolve_llm_profile(active)[2] or "未配置"
        if active == "alternative":
            w.btn_switch_profile.setText("切换回首选模型")
            w.label_active_profile.setText(f"当前启用：备选模型（{model}）")
        else:
            w.btn_switch_profile.setText("切换到备选模型")
            w.label_active_profile.setText(f"当前启用：首选模型（{model}）")


    def _behavior(self) -> QWidget:
        f, w = self.form, self.widgets
        content, inner = self._content()

        sched_group = QGroupBox("调度器")
        sched_form = QFormLayout(sched_group)
        sched_form.setSpacing(8)
        sched_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        sched_form.addRow("自主行动间隔(秒):",
                         f.line("SCHEDULER_MID_MS", "300", QIntValidator(60, 3600)))
        sched_form.addRow("", f.check("SCHEDULER_AUTO_START_MID", "默认开启自动行动"))
        sched_form.addRow("每轮注入上下文数量上限:",
                         f.line("CONTEXT_HISTORY_ENTRIES", "15", QIntValidator(1, 50)))
        inner.addWidget(sched_group)

        vision_group = QGroupBox("视觉")
        vision_form = QFormLayout(vision_group)
        vision_form.setSpacing(8)
        vision_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        vision_form.addRow("", f.check("VISION_ENABLED", "开启视觉理解（需要模型支持多模态）"))
        vision_form.addRow("截图缩放比例(0.1~1.0):",
                           f.line("VISION_SCALE", "0.7", QDoubleValidator(0.1, 1.0, 1)))
        vision_form.addRow("截图编码格式:", f.combo("SCREENSHOT_FORMAT", ["jpeg", "png"]))
        vision_form.addRow("", _hint("JPEG 体积更小，PNG 无损"))
        inner.addWidget(vision_group)

        sanity_group = QGroupBox("理智")
        sanity_form = QFormLayout(sanity_group)
        sanity_form.setSpacing(8)
        sanity_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        sanity_form.addRow("理智临界值:",
                           f.line("SANITY_CRITICAL_THRESHOLD", "20", QIntValidator(0, 100)))
        sanity_form.addRow("", _hint("低于该值会导致异常行为"))
        inner.addWidget(sanity_group)

        memory_group = QGroupBox("记忆")
        memory_layout = QVBoxLayout(memory_group)
        memory_layout.setSpacing(8)
        memory_layout.addWidget(f.check("EMBEDDING_ENABLED", "启用向量记忆"))
        vector_hint = _hint("仅支持 OpenAI 兼容的 Embedding 接口")
        vector_hint.setWordWrap(True)
        memory_layout.addWidget(vector_hint)

        memory_form = QFormLayout()
        memory_form.setSpacing(8)
        memory_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        memory_form.addRow("API 地址:",
                           f.line("EMBEDDING_URL", "https://open.bigmodel.cn/api/paas/v4"))
        memory_form.addRow("API Key:", f.secret_row("EMBEDDING_KEY"))
        mem_model_row = QHBoxLayout()
        mem_model_row.addWidget(f.line("EMBEDDING_MODEL", "embedding-3"))
        w.btn_fetch_mem_models = InkButton("获取列表")
        w.btn_fetch_mem_models.setStyleSheet(BUTTON_PRIMARY_QSS)
        w.btn_fetch_mem_models.setFixedWidth(84)
        mem_model_row.addWidget(w.btn_fetch_mem_models)
        memory_form.addRow("模型名:", mem_model_row)
        memory_form.addRow("向量维度:", f.line("EMBEDDING_DIM", "256", QIntValidator(64, 8192)))
        memory_form.addRow("记忆最大容量:", f.line("MEMORY_MAX_CAPACITY", "200", QIntValidator(50, 1000)))
        memory_form.addRow("记忆召回条数:", f.line("MEMORY_RECALL_COUNT", "15", QIntValidator(3, 30)))
        memory_form.addRow("旧事注入条数:", f.line("MEMORY_EVENT_RECALL_COUNT", "1", QIntValidator(0, 5)))
        memory_layout.addLayout(memory_form)

        merge_head = QHBoxLayout()
        merge_head.setSpacing(6)
        merge_head.addWidget(f.check("MEMORY_EVENT_MERGE_ENABLED", "整点汇总事件记忆"))
        merge_head.addWidget(f.help_badge(
            "整点（或点下方「立即汇总」）把同主题的 event 与 conversation 记忆交给模型，合并成一条 event 汇总：\n"
            "将散落的同一主题事件合并成一条，记忆召回内容更加完整。\n\n"
            "风险：\n"
            "· 被合并的原始记忆会被删除，只留汇总，不可撤销\n"
            "· 汇总由模型生成，可能丢细节或概括偏差\n"
            "· 每次调用都消耗模型 token；未单独填汇总模型时用当前对话模型"
        ))
        merge_head.addStretch()
        memory_layout.addLayout(merge_head)

        merge_form = QFormLayout()
        merge_form.setSpacing(8)
        merge_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        merge_form.addRow("汇总模型 API 地址:", f.line("MEMORY_MERGE_URL"))
        merge_form.addRow("汇总模型 API Key:", f.secret_row("MEMORY_MERGE_KEY"))
        merge_form.addRow("汇总模型名:", f.line("MEMORY_MERGE_MODEL"))
        memory_layout.addLayout(merge_form)

        merge_row = QHBoxLayout()
        merge_row.setSpacing(6)
        w.btn_merge_now = InkButton("立即汇总")
        w.btn_merge_now.setStyleSheet(BUTTON_PRIMARY_QSS)
        w.btn_merge_now.setFixedWidth(88)
        merge_row.addWidget(w.btn_merge_now)
        w.label_merge = QLabel("就绪")
        w.label_merge.setStyleSheet(f"color:{_COLOR_TEXT_SEC}; font-size:11px;")
        merge_row.addWidget(w.label_merge)
        merge_row.addStretch()
        memory_layout.addLayout(merge_row)

        test_row = QHBoxLayout()
        test_row.setSpacing(6)
        w.btn_merge_test = InkButton("测试汇总模型连接")
        w.btn_merge_test.setStyleSheet(BUTTON_PRIMARY_QSS)
        test_row.addWidget(w.btn_merge_test)
        w.label_merge_test = QLabel("就绪")
        w.label_merge_test.setStyleSheet(f"color:{_COLOR_TEXT_SEC}; font-size:11px;")
        test_row.addWidget(w.label_merge_test)
        w.btn_mem_test = InkButton("测试向量模型连接")
        w.btn_mem_test.setStyleSheet(BUTTON_PRIMARY_QSS)
        test_row.addWidget(w.btn_mem_test)
        w.label_mem_test = QLabel("就绪")
        w.label_mem_test.setStyleSheet(f"color:{_COLOR_TEXT_SEC}; font-size:11px;")
        test_row.addWidget(w.label_mem_test)
        test_row.addStretch()
        memory_layout.addLayout(test_row)

        w.conn_test_output = _test_output()
        memory_layout.addWidget(w.conn_test_output)

        memory_hint = _hint("修改向量记忆相关设置需重启后生效。")
        memory_hint.setWordWrap(True)
        memory_layout.addWidget(memory_hint)
        inner.addWidget(memory_group)

        inner.addStretch()
        return self._scroll_page(content)


    def _personality(self) -> QWidget:
        f = self.form
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)

        layout.addWidget(QLabel("宠物人格提示词"))
        te = f.text_area("PET_PERSONALITY")
        te.setMinimumHeight(240)
        layout.addWidget(te)

        layout.addWidget(QLabel("人格台词范例（每行一句，用于校准说话语感）"))
        ex = f.text_area("PET_PERSONALITY_EXAMPLES")
        ex.setMinimumHeight(120)
        layout.addWidget(ex)

        sep = QLabel()
        sep.setFixedHeight(8)
        layout.addWidget(sep)

        layout.addWidget(QLabel("交互提示词"))
        for label, key in [("抓取", "INTERACT_GRABBED_PROMPT"),
                           ("放下", "INTERACT_RELEASED_PROMPT"),
                           ("窗口消失", "INTERACT_WINDOW_DISAPPEARED_PROMPT")]:
            layout.addWidget(QLabel(label))
            ta = f.text_area(key)
            ta.setMinimumHeight(240)
            layout.addWidget(ta)

        return self._scroll_page(content)


    def _appearance(self) -> QWidget:
        f, w = self.form, self.widgets
        content, inner = self._content(None)

        form = QFormLayout()
        form.setSpacing(6)
        form.addRow("宠物大小:", f.combo("PET_SCALE", list(PET_SCALE_STEPS)))
        form.addRow("", _hint("100%: 125px×125px"))
        form.addRow("气泡最大宽度:", f.line("BUBBLE_MAX_WIDTH", "300", QIntValidator(100, 1000)))
        form.addRow("气泡字号（需重启）:", f.line("BUBBLE_FONT_SIZE", "14", QIntValidator(8, 48)))
        tools_edit = f.line("TOOLS_ENABLED", "*")
        tools_edit.setPlaceholderText("* 表示全部，多个用逗号分隔")
        form.addRow("启用工具（需重启）:", tools_edit)
        form.addRow("", f.check("SHOW_TRAY", "显示托盘图标（需重启）"))
        form.addRow("", f.check("NOTIFY_ENABLED", "允许发送通知"))
        form.addRow("", f.check("AUTO_START_ON_BOOT", "开机自动启动"))
        form.addRow("", f.check("GRAB_SWING_ENABLED", "拖拽时身体惯性摆动"))
        form.addRow("", f.check("EDGE_DOCK_ENABLED", "开启贴边吸附（拖到屏幕边缘松手贴住）"))
        inner.addLayout(form)

        terminal_group = QGroupBox("终端")
        terminal_form = QFormLayout(terminal_group)
        terminal_form.setSpacing(8)
        terminal_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        enable_row = QHBoxLayout()
        enable_row.setSpacing(6)
        enable_row.addWidget(f.check("TERMINAL_ENABLED", "允许恋恋执行命令（需重启）"))
        enable_row.addWidget(f.help_badge(
            "开启后恋恋获得终端工具，可执行系统命令；每条命令按「命令放行策略」判定，"
            "且只能在工作目录根及其子目录内执行。\n"
            "python、node、cmd、powershell 等解释器始终弹卡片询问。\n\n"
            "风险：\n"
            "· 命令由恋恋发起，提示注入可能让它执行你没预期的操作\n"
            "· 允许写操作类程序会改动磁盘上的文件"
        ))
        enable_row.addStretch()
        terminal_form.addRow("", enable_row)

        terminal_mode = f.combo("TERMINAL_MODE", ["deny", "ask", "auto"])
        terminal_form.addRow(f.label_with_help(
            "命令放行策略:",
            "控制恋恋发起的命令如何放行；终端里你自己输入的命令不受影响。\n"
            "· deny：不允许执行任何命令\n"
            "· ask：每条命令都弹确认卡片\n"
            "· auto：清单里的命令直接执行，其余询问\n"
            "解释器在 auto 档也询问；命中危险规则时同样转询问。\n\n"
            "风险：\n"
            "· auto 档不弹卡片就直接执行，免确认清单写得过宽会放大影响面"
        ), terminal_mode)

        terminal_allow = f.line("TERMINAL_ALLOW", "逗号分隔，如 git status, git log, dir *")
        terminal_form.addRow(f.label_with_help(
            "auto 档免确认命令:",
            "auto 档里免确认的命令，默认已填常用只读命令，留空表示全部询问；ask 与 deny 档不读取。\n"
            "格式为「程序名 + 参数前缀」，逗号分隔：git status 放行该子命令及其后续参数，"
            "git * 放行任意子命令。\n"
            "解释器写了也不生效：python、node、npm、powershell、cmd、bash、sh、wsl 等程序"
            "不论怎么写进清单，auto 档仍弹卡片。\n\n"
            "风险：\n"
            "· 写操作命令会免确认直接改动文件\n"
            "· 条目写得过宽（如 git *）会把 push、reset 这类写操作一并放开"
        ), terminal_allow)

        terminal_root = f.line("TERMINAL_SANDBOX_ROOT", "留空为桌面")
        terminal_form.addRow(f.label_with_help(
            "默认工作目录根:",
            "默认工作目录根，也是终端打开时的初始目录；留空表示桌面。\n"
            "终端里 cd 切换后，恋恋的默认工作目录跟着变，命令只能在该目录及其子目录里运行；"
            "恋恋自己不能切换目录。\n\n"
            "风险：\n"
            "· 目录切到盘符根后，恋恋可读写范围随之扩大到该盘"
        ), terminal_root)
        inner.addWidget(terminal_group)

        voice_group = QGroupBox("语音输入")
        voice_layout = QVBoxLayout(voice_group)
        voice_layout.setSpacing(8)
        voice_layout.addWidget(f.check("VOICE_INPUT_ENABLED", "启用语音输入"))

        voice_form = QFormLayout()
        voice_form.setSpacing(8)
        voice_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        hotkey_row = QHBoxLayout()
        hotkey_row.setSpacing(6)
        w.hotkey_display = QLineEdit()
        w.hotkey_display.setReadOnly(True)
        w.hotkey_display.setPlaceholderText("点击右侧按钮设置")
        w.hotkey_display.setStyleSheet(INPUT_HIGHLIGHT_QSS)
        f.fields["VOICE_HOTKEY"] = w.hotkey_display
        hotkey_row.addWidget(w.hotkey_display)
        w.capture_btn = InkButton("录制")
        w.capture_btn.setStyleSheet(BUTTON_PRIMARY_QSS)
        hotkey_row.addWidget(w.capture_btn)
        voice_form.addRow("热键:", hotkey_row)
        w.voice_trigger_combo = f.combo_with_data(
            "VOICE_TRIGGER_MODE", [(label, value) for label, value in VOICE_TRIGGER_CHOICES],
        )
        voice_form.addRow(f.label_with_help(
            "触发方式:",
            "两种方式都是按住热键说话、松开结束识别。\n"
            "· 单次长按：按下热键即开始录音\n"
            "· 双击后长按：先短按一下，再按住才开始录音；短按后约 1.2 秒内按下才生效\n\n"
            "双击模式用于避免误触：单独按住不会录音，按得更久也不算解除防备。"
        ), w.voice_trigger_combo)
        voice_form.addRow("讯飞 APPID:", f.line("XF_APPID", ""))
        voice_form.addRow("讯飞 API Key:", f.secret_row("XF_API_KEY"))
        voice_form.addRow("讯飞 API Secret:", f.secret_row("XF_API_SECRET"))
        voice_layout.addLayout(voice_form)

        test_row = QHBoxLayout()
        test_row.setSpacing(6)
        w.voice_btn_test = InkButton("测试连接")
        w.voice_btn_test.setStyleSheet(BUTTON_PRIMARY_QSS)
        test_row.addWidget(w.voice_btn_test)
        w.voice_label_test = QLabel("就绪")
        w.voice_label_test.setStyleSheet(f"color:{_COLOR_TEXT_SEC}; font-size:11px;")
        test_row.addWidget(w.voice_label_test)
        test_row.addStretch()
        voice_layout.addLayout(test_row)

        w.voice_test_output = _test_output()
        voice_layout.addWidget(w.voice_test_output)
        inner.addWidget(voice_group)

        output_group = QGroupBox("语音输出")
        output_layout = QVBoxLayout(output_group)
        output_layout.setSpacing(8)
        w.voice_output_enabled = f.check("VOICE_OUTPUT_ENABLED", "允许发出声音")
        output_layout.addWidget(w.voice_output_enabled)

        output_form = QFormLayout()
        output_form.setSpacing(8)
        output_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        w.voice_preset_combo = f.combo_with_data(
            "VOICE_OUTPUT_PRESET",
            [(label, value) for label, value in VOICE_PRESET_CHOICES],
        )
        output_form.addRow("音高:", w.voice_preset_combo)
        output_form.addRow("音量:", f.slider_row("VOICE_OUTPUT_VOLUME", 0, 100))
        output_layout.addLayout(output_form)

        w.voice_output_hint = QLabel("首次发声需要初始化音频设备，第一句之前约有 1 秒静默")
        w.voice_output_hint.setStyleSheet(f"color:{_COLOR_TEXT_SEC}; font-size:11px;")
        w.voice_output_hint.setWordWrap(True)
        output_layout.addWidget(w.voice_output_hint)
        inner.addWidget(output_group)

        inner.addStretch()
        return self._scroll_page(content)
