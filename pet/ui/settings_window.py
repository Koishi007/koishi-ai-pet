"""设置界面 — 替代 .env 的图形化配置。"""

from __future__ import annotations

import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QCheckBox, QComboBox, QTextEdit, QTabWidget,
    QFormLayout, QGroupBox, QMessageBox,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QIcon, QFont, QPainter, QPainterPath, QPen, QColor

from config import config, _KEY_META
from pet.ui.styles import (
    ICON_PATH, PANEL_QSS, BUTTON_QSS, BUTTON_PRIMARY_QSS,
    INPUT_QSS, COMBOBOX_QSS, TEXTEDIT_QSS, CHECKBOX_QSS,
    LABEL_SECONDARY_QSS, TAB_BAR_QSS,
)

logger = logging.getLogger(__name__)

_W = 520
_H = 600


class _LLMTestWorker(QObject):
    """子线程执行 LLM 连通性测试。"""
    finished = Signal(bool, str, float)  # success, content_or_error, elapsed

    def __init__(self, brain):
        super().__init__()
        self._brain = brain

    def run(self):
        import time
        start = time.time()
        try:
            reply = self._brain._llm_call([
                {"role": "system", "content": "你是调试助手。"},
                {"role": "user", "content": "请回复 'OK' 表示联通正常。"},
            ], max_tokens=50)
            elapsed = time.time() - start
            content = reply.choices[0].message.content or "(空响应)"
            self.finished.emit(True, content, elapsed)
        except Exception as e:
            elapsed = time.time() - start
            self.finished.emit(False, str(e), elapsed)


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
            cls._instance = cls(agent, parent=parent)
        cls._instance._load_values()
        cls._instance.show()
        cls._instance.raise_()

    def __init__(self, agent, parent=None):
        super().__init__(parent)
        self.agent = agent
        self._llm_thread = None
        self._llm_worker = None

        self.setObjectName("settingsWindow")
        self.setWindowTitle("⚙ 设置")
        self.resize(_W, _H)
        self.setFixedSize(_W, _H)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        try:
            self.setWindowIcon(QIcon(ICON_PATH))
        except Exception:
            pass

        self._setup_ui()
        self.setStyleSheet(
            PANEL_QSS + BUTTON_QSS + BUTTON_PRIMARY_QSS +
            INPUT_QSS + COMBOBOX_QSS + TEXTEDIT_QSS + CHECKBOX_QSS +
            TAB_BAR_QSS
        )

    # ── UI 构建 ──

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 背景
        bg = QWidget()
        bg.setObjectName("settingsBg")
        bg.setStyleSheet("""
            QWidget#settingsBg {
                background: #f0f0f0;
                border-radius: 8px;
                font-size: 12px;
            }
        """)
        layout = QVBoxLayout(bg)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        root.addWidget(bg)

        # 标题栏
        title_bar = QWidget()
        title_bar.setFixedHeight(30)
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(10, 0, 4, 0)
        title_row.setSpacing(6)
        title = QLabel("⚙ 设置")
        title.setStyleSheet("font-size:13px; color:#444; font-weight:bold; background:transparent;")
        title_row.addWidget(title)
        title_row.addStretch()
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(28, 28)
        btn_close.setStyleSheet("""
            QPushButton { background: transparent; border: none; border-radius: 14px;
                         font-size: 14px; color: #999; }
            QPushButton:hover { background: #e81123; color: #fff; }
        """)
        btn_close.clicked.connect(self.close)
        title_row.addWidget(btn_close)
        layout.addWidget(title_bar)

        # 标题栏拖拽
        self._drag_pos = None
        title_bar.mousePressEvent = self._header_press
        title_bar.mouseMoveEvent = self._header_move

        # Tab Widget
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_connection_tab(), "连接")
        self._tabs.addTab(self._build_behavior_tab(), "行为")
        self._tabs.addTab(self._build_appearance_tab(), "外观")
        self._tabs.addTab(self._build_personality_tab(), "人格")
        layout.addWidget(self._tabs, stretch=1)

        # 底部操作栏
        bottom = QHBoxLayout()
        bottom.setContentsMargins(12, 4, 12, 8)
        btn_reset = QPushButton("重置为默认")
        btn_reset.setStyleSheet(BUTTON_QSS)
        btn_reset.clicked.connect(self._on_reset)
        bottom.addWidget(btn_reset)
        bottom.addStretch()
        btn_save = QPushButton("保存")
        btn_save.setStyleSheet(BUTTON_PRIMARY_QSS)
        btn_save.clicked.connect(self._on_save)
        bottom.addWidget(btn_save)
        layout.addLayout(bottom)

    # ── 值控件映射 ──
    # _fields: dict[str, QWidget] — key 是 Config 属性名

    def _line(self, key: str, placeholder: str = "") -> QLineEdit:
        """创建 QLineEdit 并注册到 _fields。"""
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        self._fields[key] = edit
        return edit

    def _check(self, key: str, label: str) -> QCheckBox:
        """创建 QCheckBox 并注册到 _fields。"""
        cb = QCheckBox(label)
        self._fields[key] = cb
        return cb

    def _text_area(self, key: str) -> QTextEdit:
        """创建 QTextEdit 并注册到 _fields。"""
        te = QTextEdit()
        te.setAcceptRichText(False)
        self._fields[key] = te
        return te

    # ── Tab 1: 连接 ──

    def _build_connection_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        form = QFormLayout()
        form.setSpacing(6)

        self._fields = {}

        # Brain 模式
        self._brain_combo = QComboBox()
        self._brain_combo.addItems(["local", "llm", "ollama"])
        self._fields["BRAIN"] = self._brain_combo
        form.addRow("Brain 模式:", self._brain_combo)

        form.addRow("API 地址:", self._line("LLM_URL", "https://api.example.com/v1"))
        form.addRow("Ollama 地址:", self._line("OLLAMA_BASE_URL", "http://localhost:11434/v1"))

        # API Key + toggle
        key_row = QHBoxLayout()
        self._llm_key_edit = QLineEdit()
        self._llm_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._llm_key_edit.setPlaceholderText("sk-...")
        self._fields["LLM_KEY"] = self._llm_key_edit
        key_row.addWidget(self._llm_key_edit)
        self._key_toggle = QPushButton("👁")
        self._key_toggle.setFixedWidth(28)
        self._key_toggle.setCheckable(True)
        self._key_toggle.setStyleSheet("""
            QPushButton { background: transparent; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }
            QPushButton:checked { background: #e0e0e0; }
        """)
        self._key_toggle.toggled.connect(
            lambda checked: self._llm_key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        key_row.addWidget(self._key_toggle)
        form.addRow("API Key:", key_row)

        form.addRow("模型名称:", self._line("LLM_MODEL", "gpt-4o"))
        form.addRow("请求超时(秒):", self._line("LLM_TIMEOUT", "30"))
        form.addRow("最大重试次数:", self._line("LLM_MAX_RETRIES", "3"))
        form.addRow("重试延迟(秒):", self._line("LLM_RETRY_DELAY", "1"))
        form.addRow("最大重试延迟(秒):", self._line("LLM_RETRY_MAX_DELAY", "8"))
        form.addRow("", self._check("LLM_CACHE_PROMPT", "Prompt 缓存"))

        layout.addLayout(form)

        # 测试连接按钮
        test_row = QHBoxLayout()
        self._btn_test = QPushButton("测试连接")
        self._btn_test.setStyleSheet(BUTTON_QSS)
        self._btn_test.clicked.connect(self._test_llm)
        test_row.addWidget(self._btn_test)
        self._label_test = QLabel("就绪")
        self._label_test.setStyleSheet("color:#666; font-size:11px;")
        test_row.addWidget(self._label_test)
        test_row.addStretch()
        layout.addLayout(test_row)

        self._test_output = QTextEdit()
        self._test_output.setReadOnly(True)
        self._test_output.setMaximumHeight(60)
        self._test_output.setFont(QFont("Consolas", 9))
        self._test_output.setStyleSheet(TEXTEDIT_QSS)
        layout.addWidget(self._test_output)

        layout.addStretch()
        return w

    # ── Tab 2: 行为 ──

    def _build_behavior_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        # 调度器
        sched_group = QGroupBox("调度器")
        sched_form = QFormLayout(sched_group)
        sched_form.setSpacing(6)
        sched_form.addRow("Fast 间隔(ms):", self._line("SCHEDULER_FAST_MS", "1000"))
        sched_form.addRow("Mid 间隔(ms):", self._line("SCHEDULER_MID_MS", "300000"))
        sched_form.addRow("Slow 间隔(ms):", self._line("SCHEDULER_SLOW_MS", "300000"))
        sched_form.addRow("空闲超时(ms):", self._line("SCHEDULER_IDLE_TIMEOUT_MS", "900000"))
        sched_form.addRow("动作超时(ms):", self._line("ACTION_TIMEOUT_MS", "15000"))
        sched_form.addRow("", self._check("SCHEDULER_AUTO_START_FAST", "自动启动 Fast"))
        sched_form.addRow("", self._check("SCHEDULER_AUTO_START_MID", "自动启动 Mid"))
        sched_form.addRow("", self._check("SCHEDULER_AUTO_START_SLOW", "自动启动 Slow"))
        layout.addWidget(sched_group)

        # 理智
        sanity_row = QFormLayout()
        sanity_row.addRow("理智临界值:", self._line("SANITY_CRITICAL_THRESHOLD", "20"))
        layout.addLayout(sanity_row)

        # 交互 prompt
        prompt_group = QGroupBox("交互 Prompt")
        prompt_layout = QVBoxLayout(prompt_group)
        for label, key in [("抓取", "INTERACT_GRABBED_PROMPT"),
                           ("放下", "INTERACT_RELEASED_PROMPT"),
                           ("窗口消失", "INTERACT_WINDOW_DISAPPEARED_PROMPT")]:
            prompt_layout.addWidget(QLabel(label))
            prompt_layout.addWidget(self._text_area(key))
        layout.addWidget(prompt_group)

        layout.addStretch()
        return w

    # ── Tab 3: 外观 ──

    def _build_appearance_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        form = QFormLayout()
        form.setSpacing(6)

        form.addRow("", self._check("VISION_ENABLED", "Vision（截图理解）"))
        form.addRow("截图缩放比例:", self._line("VISION_SCALE", "1"))
        form.addRow("技能插件(逗号分隔,*=全部):", self._line("SKILLS_ENABLED", "*"))

        # 需重启提示
        restart_label = QLabel("以下设置需要重启后生效：")
        restart_label.setStyleSheet("color:#e67e22; font-size:11px; font-weight:bold;")
        form.addRow(restart_label)

        form.addRow("宠物宽度:", self._line("PET_WIDTH", "125"))
        form.addRow("宠物高度:", self._line("PET_HEIGHT", "125"))
        form.addRow("FPS:", self._line("PET_FPS", "15"))
        form.addRow("气泡最大宽度:", self._line("BUBBLE_MAX_WIDTH", "300"))
        form.addRow("气泡字号:", self._line("BUBBLE_FONT_SIZE", "14"))
        form.addRow("", self._check("HIDE_CONSOLE", "隐藏控制台"))
        form.addRow("", self._check("SHOW_TRAY", "显示托盘图标"))
        form.addRow("日志级别:", self._line("LOG_LEVEL", "INFO"))

        layout.addLayout(form)
        layout.addStretch()
        return w

    # ── Tab 4: 人格 ──

    def _build_personality_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        layout.addWidget(QLabel("宠物人格 Prompt"))
        layout.addWidget(self._text_area("PET_PERSONALITY"), stretch=1)

        return w

    # ── 加载 / 保存 ──

    def _load_values(self):
        """从 config 读取当前值填充各控件。"""
        for key, widget in self._fields.items():
            value = getattr(config, key, None)
            if value is None:
                continue
            if isinstance(widget, QLineEdit):
                if isinstance(value, list):
                    widget.setText(",".join(value))
                else:
                    widget.setText(str(value))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                idx = widget.findText(str(value))
                widget.setCurrentIndex(max(idx, 0))
            elif isinstance(widget, QTextEdit):
                widget.setPlainText(str(value))

    def _collect_values(self) -> dict:
        """从控件收集当前值，返回 {key: value}。"""
        result = {}
        for key, widget in self._fields.items():
            type_name = _KEY_META[key][0]
            if isinstance(widget, QLineEdit):
                raw = widget.text().strip()
                if type_name == "int":
                    try:
                        result[key] = int(raw)
                    except ValueError:
                        continue
                elif type_name == "float":
                    try:
                        result[key] = float(raw)
                    except ValueError:
                        continue
                elif type_name == "str_list":
                    result[key] = [s.strip() for s in raw.split(",") if s.strip()] if raw and raw != "*" else ["*"] if raw == "*" else []
                else:
                    result[key] = raw
            elif isinstance(widget, QCheckBox):
                result[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                result[key] = widget.currentText()
            elif isinstance(widget, QTextEdit):
                result[key] = widget.toPlainText()
        return result

    def _on_save(self):
        """保存所有修改并即时生效。"""
        values = self._collect_values()
        needs_restart_keys = []
        needs_rebuild_client = False
        needs_scheduler_update = False

        connection_keys = {"BRAIN", "LLM_URL", "OLLAMA_BASE_URL", "LLM_KEY",
                          "LLM_MODEL", "LLM_TIMEOUT", "LLM_MAX_RETRIES",
                          "LLM_RETRY_DELAY", "LLM_RETRY_MAX_DELAY", "LLM_CACHE_PROMPT"}
        scheduler_keys = {"SCHEDULER_FAST_MS", "SCHEDULER_MID_MS", "SCHEDULER_SLOW_MS",
                         "SCHEDULER_IDLE_TIMEOUT_MS", "ACTION_TIMEOUT_MS",
                         "SCHEDULER_AUTO_START_FAST", "SCHEDULER_AUTO_START_MID",
                         "SCHEDULER_AUTO_START_SLOW"}

        for key, value in values.items():
            _, needs_restart = config.save(key, value)
            if needs_restart:
                needs_restart_keys.append(key)
            if key in connection_keys:
                needs_rebuild_client = True
            if key in scheduler_keys:
                needs_scheduler_update = True

        # 运行时钩子
        if needs_rebuild_client and self.agent and hasattr(self.agent, 'behavior'):
            try:
                self.agent.behavior.rebuild_client()
            except Exception as e:
                logger.exception(f"[Settings] rebuild_client failed: {e}")

        if needs_scheduler_update and self.agent and hasattr(self.agent, 'scheduler'):
            try:
                self.agent.scheduler.update_config()
            except Exception as e:
                logger.exception(f"[Settings] scheduler.update_config failed: {e}")

        if needs_restart_keys:
            QMessageBox.warning(
                self, "设置已保存",
                f"以下设置将在下次启动后生效：\n{', '.join(needs_restart_keys)}"
            )
        else:
            QMessageBox.information(self, "设置已保存", "所有设置已即时生效。")

    def _on_reset(self):
        """重置当前 tab 的所有字段为 .env 默认值。"""
        current_tab = self._tabs.currentIndex()
        category_map = {0: "connection", 1: "behavior", 2: "appearance", 3: "personality"}
        category = category_map.get(current_tab, "connection")
        keys = [k for k, v in _KEY_META.items() if v[2] == category]
        config.reset(keys)
        self._load_values()
        QMessageBox.information(self, "已重置", f"{category} 设置已重置为默认值。")

    # ── LLM 连通性测试 ──

    def _test_llm(self):
        """在子线程测试 LLM 连通性。"""
        if not self.agent or not hasattr(self.agent, 'behavior'):
            return
        brain_cfg = self._brain_combo.currentText()
        if brain_cfg == "local" or not self.agent.behavior._client:
            self._test_output.clear()
            self._test_output.append("⚠ 当前为 local 模式或未配置客户端")
            self._label_test.setText("未配置")
            return

        self._test_output.clear()
        self._test_output.append("测试中...")
        self._btn_test.setEnabled(False)
        self._label_test.setText("测试中...")

        self._llm_thread = QThread()
        self._llm_worker = _LLMTestWorker(self.agent.behavior)
        self._llm_worker.moveToThread(self._llm_thread)
        self._llm_thread.started.connect(self._llm_worker.run)
        self._llm_worker.finished.connect(self._on_llm_test_result)
        self._llm_worker.finished.connect(self._llm_thread.quit)
        self._llm_thread.start()

    def _on_llm_test_result(self, success: bool, content: str, elapsed: float):
        self._test_output.clear()
        if success:
            self._test_output.append(f"✅ 连接成功 ({elapsed:.1f}s)")
            self._test_output.append(f"响应: {content[:200]}")
            self._label_test.setText("✅ 正常")
        else:
            self._test_output.append(f"❌ 连接失败 ({elapsed:.1f}s)")
            self._test_output.append(f"错误: {content}")
            self._label_test.setText("❌ 失败")
        self._btn_test.setEnabled(True)

    # ── 窗口拖拽 ──

    def _header_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def _header_move(self, event):
        if self._drag_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()

    # ── 圆角背景 ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        painter.fillPath(path, QColor("#f0f0f0"))
        painter.setPen(QPen(QColor("#cccccc"), 1))
        painter.drawPath(path)