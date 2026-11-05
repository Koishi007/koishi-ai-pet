"""设置页的四个页签：只负责搭建控件并登记到 SettingsForm"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator, QFont, QIntValidator
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QTextEdit,
                               QVBoxLayout, QWidget)

from pet.config import PET_SCALE_STEPS, config
from pet.ui.settings_fields import SettingsForm, SettingsWidgets
from pet.ui.settings_spec import TAB_SPECS
from pet.ui.styles import (BUTTON_PRIMARY_QSS, INPUT_HIGHLIGHT_QSS,
                           SCROLLBAR_QSS, TEXTEDIT_QSS, _COLOR_BG,
                           _COLOR_TEXT_MUTED, _COLOR_TEXT_SEC)
from pet.ui.widgets import NoWheelComboBox


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"color:{_COLOR_TEXT_MUTED}; font-size:11px;")
    return label


def _test_output() -> QTextEdit:
    out = QTextEdit()
    out.setReadOnly(True)
    out.setMaximumHeight(60)
    out.setFont(QFont("Consolas", 9))
    out.setStyleSheet(TEXTEDIT_QSS + f"QTextEdit {{ background: {_COLOR_BG}; }}")
    return out


class SettingsTabs:
    """按 TAB_SPECS 的顺序构建页签，并把共享控件登记到 widgets。"""

    def __init__(self, form: SettingsForm):
        self.form = form
        self.widgets = SettingsWidgets()

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
            f"QScrollArea {{ border: none; background: {_COLOR_BG}; }}" + SCROLLBAR_QSS)
        scroll.viewport().setStyleSheet(f"background: {_COLOR_BG};")
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

    # ------------------------------------------------------------------

    def _connection(self) -> QWidget:
        f, w = self.form, self.widgets
        content, inner = self._content()

        form = QFormLayout()
        form.setSpacing(8)

        w.brain_combo = NoWheelComboBox()
        w.brain_combo.addItems(["local", "api", "ollama"])
        f.fields["BRAIN"] = w.brain_combo
        form.addRow("调用模式:", w.brain_combo)

        w.url_edit = f.line("LLM_URL", "https://api.example.com/v1")
        form.addRow("首选 API 地址:", w.url_edit)

        w.ollama_url_edit = f.line("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        form.addRow("Ollama 地址:", w.ollama_url_edit)

        key_row = f.secret_row("LLM_KEY", "sk-...")
        w.llm_key_edit = f.fields["LLM_KEY"]
        w.key_toggle = key_row.itemAt(1).widget()
        form.addRow("首选 API Key:", key_row)

        model_row = QHBoxLayout()
        w.model_edit = QLineEdit()
        w.model_edit.setStyleSheet(INPUT_HIGHLIGHT_QSS)
        f.fields["LLM_MODEL"] = w.model_edit
        model_row.addWidget(w.model_edit)
        w.btn_fetch_models = QPushButton("获取列表")
        w.btn_fetch_models.setStyleSheet(BUTTON_PRIMARY_QSS)
        w.btn_fetch_models.setFixedWidth(72)
        model_row.addWidget(w.btn_fetch_models)
        form.addRow("首选模型名称:", model_row)

        w.temperature_edit = f.line("LLM_TEMPERATURE", "0.7", QDoubleValidator(0, 2, 2))
        w.temperature_edit.setMaxLength(4)
        form.addRow("采样温度:", w.temperature_edit)

        form.addRow("", f.check("LLM_TOOLS_ENABLED", "启用工具调用"))
        form.addRow("", _hint("模型不支持 tools 参数时请关闭此选项"))

        w.tool_rounds_edit = f.line("LLM_TOOL_MAX_ROUNDS", "30", QIntValidator(10, 99))
        form.addRow("工具调用最大轮次:", w.tool_rounds_edit)

        w.cache_check = f.check("LLM_CACHE_PROMPT", "Prompt 缓存")
        form.addRow("", w.cache_check)

        w.thinking_check = f.check("LLM_THINKING_DISABLED", "关闭思考模式")
        form.addRow("", w.thinking_check)
        w.thinking_hint = _hint("关闭可提升调用速度")
        form.addRow("", w.thinking_hint)

        inner.addLayout(form)

        switch_group = QGroupBox("模型方案")
        switch_layout = QVBoxLayout(switch_group)
        switch_layout.setSpacing(6)

        switch_row = QHBoxLayout()
        w.label_active_profile = QLabel("当前启用：首选模型")
        w.label_active_profile.setStyleSheet(f"color:{_COLOR_TEXT_SEC}; font-size:11px;")
        switch_row.addWidget(w.label_active_profile)
        switch_row.addStretch()
        w.btn_switch_profile = QPushButton("切换到备选模型")
        w.btn_switch_profile.setStyleSheet(BUTTON_PRIMARY_QSS)
        switch_row.addWidget(w.btn_switch_profile)
        switch_layout.addLayout(switch_row)

        w.fallback_check = f.check(
            "LLM_FALLBACK_ENABLED", "调用报错/超时重试时自动切换到备选模型")
        switch_layout.addWidget(w.fallback_check)

        fallback_hint = _hint("备选方案的地址/Key/模型名留空时，自动沿用首选方案对应项")
        fallback_hint.setWordWrap(True)
        switch_layout.addWidget(fallback_hint)

        alt_form = QFormLayout()
        alt_form.setSpacing(8)
        w.alt_url_edit = f.line("LLM_URL_ALT", "https://api.example.com/v1")
        alt_form.addRow("备选 API 地址:", w.alt_url_edit)
        alt_key_row = f.secret_row("LLM_KEY_ALT", "sk-...")
        w.alt_key_edit = f.fields["LLM_KEY_ALT"]
        w.alt_key_toggle = alt_key_row.itemAt(1).widget()
        alt_form.addRow("备选 API Key:", alt_key_row)

        alt_model_row = QHBoxLayout()
        w.alt_model_edit = QLineEdit()
        w.alt_model_edit.setStyleSheet(INPUT_HIGHLIGHT_QSS)
        f.fields["LLM_MODEL_ALT"] = w.alt_model_edit
        alt_model_row.addWidget(w.alt_model_edit)
        w.btn_fetch_models_alt = QPushButton("获取列表")
        w.btn_fetch_models_alt.setStyleSheet(BUTTON_PRIMARY_QSS)
        w.btn_fetch_models_alt.setFixedWidth(72)
        alt_model_row.addWidget(w.btn_fetch_models_alt)
        alt_form.addRow("备选模型名称:", alt_model_row)
        switch_layout.addLayout(alt_form)
        inner.addWidget(switch_group)

        test_row = QHBoxLayout()
        w.btn_test = QPushButton("测试连接")
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

    def on_mode_changed(self, mode: str) -> None:
        """根据调用模式启用/禁用对应字段。"""
        w = self.widgets
        llm_fields = [w.url_edit, w.llm_key_edit, w.key_toggle,
                      w.alt_url_edit, w.alt_key_edit, w.alt_key_toggle,
                      w.cache_check]
        ollama_fields = [w.ollama_url_edit]
        common_fields = [w.model_edit, w.btn_fetch_models,
                         w.alt_model_edit, w.btn_fetch_models_alt,
                         w.btn_switch_profile, w.fallback_check,
                         w.temperature_edit, w.tool_rounds_edit,
                         w.thinking_check, w.thinking_hint,
                         w.btn_test, w.label_test, w.test_output]
        if mode == "local":
            for widget in llm_fields + ollama_fields + common_fields:
                widget.setEnabled(False)
        elif mode == "ollama":
            for widget in llm_fields:
                widget.setEnabled(False)
            for widget in ollama_fields + common_fields:
                widget.setEnabled(True)
        else:
            for widget in ollama_fields:
                widget.setEnabled(False)
            for widget in llm_fields + common_fields:
                widget.setEnabled(True)

    def update_profile_ui(self) -> None:
        """刷新「当前启用方案」的按钮文案与状态标签。"""
        w = self.widgets
        active = getattr(config, "LLM_ACTIVE_PROFILE", "primary") or "primary"
        if active == "alternative":
            w.btn_switch_profile.setText("切换回首选模型")
            model = config.LLM_MODEL_ALT or config.LLM_MODEL or "未配置"
            w.label_active_profile.setText(f"当前启用：备选模型（{model}）")
        else:
            w.btn_switch_profile.setText("切换到备选模型")
            model = config.LLM_MODEL or "未配置"
            w.label_active_profile.setText(f"当前启用：首选模型（{model}）")

    # ------------------------------------------------------------------

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

        memory_form = QFormLayout()
        memory_form.setSpacing(8)
        memory_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        memory_form.addRow("API 地址:",
                           f.line("EMBEDDING_URL", "https://open.bigmodel.cn/api/paas/v4"))
        memory_form.addRow("API Key:", f.secret_row("EMBEDDING_KEY"))
        mem_model_row = QHBoxLayout()
        mem_model_row.addWidget(f.line("EMBEDDING_MODEL", "embedding-3"))
        w.btn_fetch_mem_models = QPushButton("获取列表")
        w.btn_fetch_mem_models.setStyleSheet(BUTTON_PRIMARY_QSS)
        w.btn_fetch_mem_models.setFixedWidth(72)
        mem_model_row.addWidget(w.btn_fetch_mem_models)
        memory_form.addRow("模型名:", mem_model_row)
        memory_form.addRow("向量维度:", f.line("EMBEDDING_DIM", "256", QIntValidator(64, 8192)))
        memory_form.addRow("记忆最大容量:", f.line("MEMORY_MAX_CAPACITY", "200", QIntValidator(50, 1000)))
        memory_form.addRow("记忆召回条数:", f.line("MEMORY_RECALL_COUNT", "15", QIntValidator(3, 30)))
        memory_form.addRow("临时记忆过期(天):", f.line("MEMORY_L3_EXPIRE_DAYS", "3", QIntValidator(1, 30)))
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
        w.btn_merge_now = QPushButton("立即汇总")
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
        w.btn_merge_test = QPushButton("测试汇总模型连接")
        w.btn_merge_test.setStyleSheet(BUTTON_PRIMARY_QSS)
        test_row.addWidget(w.btn_merge_test)
        w.label_merge_test = QLabel("就绪")
        w.label_merge_test.setStyleSheet(f"color:{_COLOR_TEXT_SEC}; font-size:11px;")
        test_row.addWidget(w.label_merge_test)
        w.btn_mem_test = QPushButton("测试向量模型连接")
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

    # ------------------------------------------------------------------

    def _personality(self) -> QWidget:
        f = self.form
        content = QWidget()
        content.setStyleSheet(f"background: {_COLOR_BG};")
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

    # ------------------------------------------------------------------

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
            "工作目录根:",
            "用户与恋恋共用的工作目录根，也是终端打开时的初始目录；留空表示桌面。\n"
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
        w.capture_btn = QPushButton("录制")
        w.capture_btn.setStyleSheet(BUTTON_PRIMARY_QSS)
        hotkey_row.addWidget(w.capture_btn)
        voice_form.addRow("热键:", hotkey_row)
        voice_form.addRow("讯飞 APPID:", f.line("XF_APPID", ""))
        voice_form.addRow("讯飞 API Key:", f.secret_row("XF_API_KEY"))
        voice_form.addRow("讯飞 API Secret:", f.secret_row("XF_API_SECRET"))
        voice_layout.addLayout(voice_form)

        test_row = QHBoxLayout()
        test_row.setSpacing(6)
        w.voice_btn_test = QPushButton("测试连接")
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

        inner.addStretch()
        return self._scroll_page(content)
