"""设置界面 — 用户配置的图形界面。"""

from __future__ import annotations

import logging
import threading
import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QCheckBox, QComboBox, QTextEdit, QTabWidget,
    QFormLayout, QGroupBox, QMessageBox, QScrollArea, QMenu, QApplication,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QIcon, QFont, QPainter, QPainterPath, QPen, QColor, QIntValidator, QDoubleValidator

from config import config, _KEY_META
from pet.ui.styles import (
    ICON_PATH, SETTING_ICON_PATH, SHOW_ICON_PATH, HIDE_ICON_PATH, PANEL_QSS, BUTTON_QSS, BUTTON_PRIMARY_QSS,
    INPUT_QSS, INPUT_HIGHLIGHT_QSS, COMBOBOX_QSS, TEXTEDIT_QSS, CHECKBOX_QSS,
    TAB_BAR_QSS,
    _COLOR_BG, _COLOR_BORDER_DARK, _COLOR_TEXT_TITLE,
    _COLOR_TEXT_SEC, _COLOR_TEXT_MUTED, _COLOR_WARNING,
    make_minimize_button, make_close_button, ensure_taskbar_icon,
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


class _ModelsFetchWorker(QObject):
    """子线程获取模型列表。"""
    finished = Signal(bool, list, str)  # success, model_ids, error_msg

    def __init__(self, client):
        super().__init__()
        self._client = client

    def run(self):
        try:
            resp = self._client.models.list()
            model_ids = [m.id for m in resp.data]
            model_ids.sort()
            self.finished.emit(True, model_ids, "")
        except Exception as e:
            self.finished.emit(False, [], str(e))


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
            # 强制顶层窗口，确保在 Windows 任务栏有独立图标
            cls._instance = cls(agent, parent=None)
        cls._instance._load_values()
        cls._instance.show()
        cls._instance.raise_()

    def __init__(self, agent, parent=None):
        super().__init__(parent)
        self.agent = agent
        self._llm_thread = None
        self._llm_worker = None
        self._models_thread = None
        self._models_worker = None
        self._fields = {}
        self._snapshot = {}

        self.setObjectName("settingsWindow")
        self.setWindowTitle("设置")
        self.resize(_W, _H)
        self.setFixedSize(_W, _H)
        self.move(QApplication.primaryScreen().geometry().center() - self.rect().center())
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        try:
            self.setWindowIcon(QIcon(SETTING_ICON_PATH))
        except Exception:
            pass

        self._setup_ui()
        self.setStyleSheet(
            PANEL_QSS + BUTTON_QSS + BUTTON_PRIMARY_QSS +
            INPUT_QSS + COMBOBOX_QSS + TEXTEDIT_QSS + CHECKBOX_QSS +
            TAB_BAR_QSS
        )

    def showEvent(self, event):
        super().showEvent(event)
        ensure_taskbar_icon(self)

    # ── UI 构建 ──

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 2, 6, 4)
        root.setSpacing(0)

        # 标题栏
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
        title.setStyleSheet(f"font-size:13px; color:{_COLOR_TEXT_TITLE}; font-weight:bold; background:transparent;")
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(make_minimize_button(self))
        title_row.addWidget(make_close_button(self))
        root.addWidget(title_bar)

        # 标题栏拖拽
        self._drag_pos = None
        title_bar.mousePressEvent = self._header_press
        title_bar.mouseMoveEvent = self._header_move

        # Tab Widget
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.tabBar().setExpanding(True)
        self._tabs.tabBar().setStyleSheet(f"""
            QTabBar {{ background: {_COLOR_BG}; }}
        """)
        self._tabs.addTab(self._build_connection_tab(), "连接")
        self._tabs.addTab(self._build_behavior_tab(), "行为")
        self._tabs.addTab(self._build_personality_tab(), "提示词")
        self._tabs.addTab(self._build_appearance_tab(), "通用")
        root.addWidget(self._tabs, stretch=1)

        # 底部操作栏
        bottom = QHBoxLayout()
        bottom.setContentsMargins(12, 4, 12, 8)
        bottom.addStretch()
        btn_save = QPushButton("保存")
        btn_save.setStyleSheet(BUTTON_PRIMARY_QSS)
        btn_save.clicked.connect(self._on_save)
        bottom.addWidget(btn_save)
        root.addLayout(bottom)

    # ── 无图标消息框 ──

    def _msg(self, title: str, text: str, *,
             icon: QMessageBox.Icon = QMessageBox.Icon.NoIcon,
             warning: bool = False):
        """显示无图标的消息框。warning=True 时用 Warning 图标，否则 NoIcon。"""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(icon if warning else QMessageBox.Icon.NoIcon)
        box.addButton("确定", QMessageBox.ButtonRole.AcceptRole)
        box.exec()

    # ── 值控件映射 ──
    # _fields: dict[str, QWidget] — key 是 Config 属性名

    def _line(self, key: str, placeholder: str = "",
              validator=None) -> QLineEdit:
        """创建 QLineEdit 并注册到 _fields。"""
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setStyleSheet(INPUT_HIGHLIGHT_QSS)
        if validator:
            edit.setValidator(validator)
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
        form.setSpacing(8)

        self._fields = {}

        # 调用模式
        self._brain_combo = QComboBox()
        self._brain_combo.addItems(["local", "api", "ollama"])
        self._fields["BRAIN"] = self._brain_combo
        form.addRow("调用模式:", self._brain_combo)

        self._url_edit = self._line("LLM_URL", "https://api.example.com/v1")
        form.addRow("API 地址:", self._url_edit)

        self._ollama_url_edit = self._line("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        form.addRow("Ollama 地址:", self._ollama_url_edit)

        # API Key + toggle
        key_row = QHBoxLayout()
        self._llm_key_edit = QLineEdit()
        self._llm_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._llm_key_edit.setPlaceholderText("sk-...")
        self._llm_key_edit.setStyleSheet(INPUT_HIGHLIGHT_QSS)
        self._fields["LLM_KEY"] = self._llm_key_edit
        key_row.addWidget(self._llm_key_edit)
        self._key_toggle = QPushButton()
        self._key_toggle.setIcon(QIcon(SHOW_ICON_PATH))
        self._key_toggle.setFixedWidth(28)
        self._key_toggle.setCheckable(True)
        self._key_toggle.toggled.connect(
            lambda checked: (
                self._key_toggle.setIcon(QIcon(HIDE_ICON_PATH if checked else SHOW_ICON_PATH)),
                self._llm_key_edit.setEchoMode(
                    QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
                )
            )[:0]  # suppress True from lambda
        )
        key_row.addWidget(self._key_toggle)
        form.addRow("API Key:", key_row)

        # 模型名称 + 获取按钮
        model_row = QHBoxLayout()
        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText("gpt-4o")
        self._model_edit.setStyleSheet(INPUT_HIGHLIGHT_QSS)
        self._fields["LLM_MODEL"] = self._model_edit
        model_row.addWidget(self._model_edit)
        self._btn_fetch_models = QPushButton("获取列表")
        self._btn_fetch_models.setStyleSheet(BUTTON_PRIMARY_QSS)
        self._btn_fetch_models.setFixedWidth(72)
        self._btn_fetch_models.clicked.connect(self._fetch_models)
        model_row.addWidget(self._btn_fetch_models)
        form.addRow("模型名称:", model_row)

        self._timeout_edit = self._line("LLM_TIMEOUT", "30", QDoubleValidator(1, 300, 1))
        self._timeout_edit.setMaxLength(5)
        form.addRow("请求超时(秒):", self._timeout_edit)

        self._retries_edit = self._line("LLM_MAX_RETRIES", "3", QIntValidator(0, 10))
        self._retries_edit.setMaxLength(2)
        form.addRow("最大重试次数:", self._retries_edit)
        
        self._retry_delay_edit = self._line("LLM_RETRY_DELAY", "1", QDoubleValidator(0, 60, 1))
        self._retry_delay_edit.setMaxLength(4)
        form.addRow("重试延迟(秒):", self._retry_delay_edit)
        
        self._retry_max_delay_edit = self._line("LLM_RETRY_MAX_DELAY", "8", QDoubleValidator(0, 300, 1))
        self._retry_max_delay_edit.setMaxLength(5)
        form.addRow("最大重试延迟(秒):", self._retry_max_delay_edit)

        self._cache_check = self._check("LLM_CACHE_PROMPT", "Prompt 缓存")
        form.addRow("", self._cache_check)

        layout.addLayout(form)

        # 测试连接按钮
        test_row = QHBoxLayout()
        self._btn_test = QPushButton("测试连接")
        self._btn_test.setStyleSheet(BUTTON_PRIMARY_QSS)
        self._btn_test.clicked.connect(self._test_llm)
        test_row.addWidget(self._btn_test)
        self._label_test = QLabel("就绪")
        self._label_test.setStyleSheet(f"color:{_COLOR_TEXT_SEC}; font-size:11px;")
        test_row.addWidget(self._label_test)
        test_row.addStretch()
        layout.addLayout(test_row)

        self._test_output = QTextEdit()
        self._test_output.setReadOnly(True)
        self._test_output.setMaximumHeight(60)
        self._test_output.setFont(QFont("Consolas", 9))
        self._test_output.setStyleSheet(TEXTEDIT_QSS + f"""
            QTextEdit {{
                background: {_COLOR_BG};
            }}
        """)
        layout.addWidget(self._test_output)

        # 模式切换联动
        self._brain_combo.currentTextChanged.connect(self._on_mode_changed)

        layout.addStretch()
        return w

    def _on_mode_changed(self, mode: str):
        """根据调用模式启用/禁用对应字段。"""
        llm_fields = [self._url_edit, self._llm_key_edit, self._key_toggle,
                      self._cache_check]
        ollama_fields = [self._ollama_url_edit]
        common_fields = [self._model_edit, self._btn_fetch_models,
                         self._timeout_edit, self._retries_edit,
                         self._retry_delay_edit, self._retry_max_delay_edit,
                         self._btn_test, self._label_test, self._test_output]

        if mode == "local":
            for w in llm_fields + ollama_fields + common_fields:
                w.setEnabled(False)
        elif mode == "ollama":
            for w in llm_fields:
                w.setEnabled(False)
            for w in ollama_fields + common_fields:
                w.setEnabled(True)
        else:  # api
            for w in ollama_fields:
                w.setEnabled(False)
            for w in llm_fields + common_fields:
                w.setEnabled(True)
        return w

    # ── Tab 2: 行为 ──

    def _build_behavior_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # 调度器
        sched_group = QGroupBox("调度器")
        sched_form = QFormLayout(sched_group)
        sched_form.setSpacing(8)
        sched_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        sched_form.addRow("自主行动间隔(ms):", self._line("SCHEDULER_MID_MS", "300000", QIntValidator(60000, 3600000)))
        sched_form.addRow("", self._check("SCHEDULER_AUTO_START_MID", "默认开启自动行动"))
        layout.addWidget(sched_group)

        # 视觉
        vision_group = QGroupBox("视觉")
        vision_form = QFormLayout(vision_group)
        vision_form.setSpacing(8)
        vision_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        vision_form.addRow("", self._check("VISION_ENABLED", "开启视觉理解（需要模型支持多模态）"))
        vision_form.addRow("截图缩放比例(0.1~1.0):", self._line("VISION_SCALE", "1", QDoubleValidator(0.1, 1.0, 1)))
        layout.addWidget(vision_group)

        # 理智
        sanity_group = QGroupBox("理智")
        sanity_form = QFormLayout(sanity_group)
        sanity_form.setSpacing(8)
        sanity_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        sanity_form.addRow("理智临界值:", self._line("SANITY_CRITICAL_THRESHOLD", "20", QIntValidator(0, 100)))
        hint = QLabel("低于该值会导致异常行为")
        hint.setStyleSheet(f"color:{_COLOR_TEXT_MUTED}; font-size:11px;")
        sanity_form.addRow("", hint)
        layout.addWidget(sanity_group)

        layout.addStretch()
        return w

    # ── Tab 3: 外观 ──

    def _build_appearance_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        form = QFormLayout()
        form.setSpacing(6)

        # 需重启提示
        restart_label = QLabel("以下设置需要重启后生效：")
        restart_label.setStyleSheet(f"color:{_COLOR_WARNING}; font-size:11px; font-weight:bold;")
        form.addRow(restart_label)

        form.addRow("宠物宽度:", self._line("PET_WIDTH", "125", QIntValidator(50, 500)))
        form.addRow("宠物高度:", self._line("PET_HEIGHT", "125", QIntValidator(50, 500)))
        form.addRow("气泡最大宽度:", self._line("BUBBLE_MAX_WIDTH", "300", QIntValidator(100, 1000)))
        form.addRow("气泡字号:", self._line("BUBBLE_FONT_SIZE", "14", QIntValidator(8, 48)))
        form.addRow("", self._check("SHOW_TRAY", "显示托盘图标"))

        layout.addLayout(form)
        layout.addStretch()
        return w

    # ── Tab 4: 提示词 ──

    def _build_personality_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {_COLOR_BG}; }}")
        scroll.viewport().setStyleSheet(f"background: {_COLOR_BG};")

        content = QWidget()
        content.setStyleSheet(f"background: {_COLOR_BG};")
        form = QVBoxLayout(content)
        form.setContentsMargins(8, 8, 8, 8)

        form.addWidget(QLabel("宠物人格 Prompt"))
        te = self._text_area("PET_PERSONALITY")
        te.setMinimumHeight(240)
        form.addWidget(te)

        sep = QLabel()
        sep.setFixedHeight(8)
        form.addWidget(sep)

        form.addWidget(QLabel("交互 Prompt"))
        for label, key in [("抓取", "INTERACT_GRABBED_PROMPT"),
                           ("放下", "INTERACT_RELEASED_PROMPT"),
                           ("窗口消失", "INTERACT_WINDOW_DISAPPEARED_PROMPT")]:
            form.addWidget(QLabel(label))
            form.addWidget(self._text_area(key))

        scroll.setWidget(content)
        layout.addWidget(scroll)

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
        self._take_snapshot()

    def _take_snapshot(self):
        """记录当前各控件的值，用于后续 dirty 检测。"""
        self._snapshot = {}
        for key, widget in self._fields.items():
            if isinstance(widget, QLineEdit):
                self._snapshot[key] = widget.text()
            elif isinstance(widget, QCheckBox):
                self._snapshot[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                self._snapshot[key] = widget.currentText()
            elif isinstance(widget, QTextEdit):
                self._snapshot[key] = widget.toPlainText()

    def _is_dirty(self) -> bool:
        """比较当前控件值与上次快照，判断是否有未保存的修改。"""
        if not hasattr(self, '_snapshot'):
            return False
        for key, widget in self._fields.items():
            if isinstance(widget, QLineEdit):
                current = widget.text()
            elif isinstance(widget, QCheckBox):
                current = widget.isChecked()
            elif isinstance(widget, QComboBox):
                current = widget.currentText()
            elif isinstance(widget, QTextEdit):
                current = widget.toPlainText()
            else:
                continue
            if self._snapshot.get(key) != current:
                return True
        return False

    def _collect_values(self) -> tuple[dict, list[str]]:
        """从控件收集当前值，返回 (values, invalid_keys)。

        invalid_keys 包含无法转换为目标类型的字段 key，
        调用方应向用户提示这些字段未保存。
        """
        result = {}
        invalid_keys = []
        for key, widget in self._fields.items():
            type_name = _KEY_META[key]["type"]
            if isinstance(widget, QLineEdit):
                raw = widget.text().strip()
                if type_name == "int":
                    try:
                        result[key] = int(raw)
                    except ValueError:
                        invalid_keys.append(key)
                        continue
                elif type_name == "float":
                    try:
                        result[key] = float(raw)
                    except ValueError:
                        invalid_keys.append(key)
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
                result[key] = widget.currentText()
            elif isinstance(widget, QTextEdit):
                result[key] = widget.toPlainText()
        return result, invalid_keys

    def _on_save(self):
        """保存所有修改并即时生效。"""
        values, invalid_keys = self._collect_values()
        if invalid_keys:
            bad_names = ", ".join(invalid_keys)
            self._msg("输入有误",
                      f"以下字段包含无效数值，未能保存：\n{bad_names}\n\n请检查数值型字段.")
            return
        needs_restart_keys = []
        needs_rebuild_client = False
        needs_scheduler_update = False

        for key, value in values.items():
            _, needs_restart = config.save(key, value)
            if needs_restart:
                needs_restart_keys.append(key)

            meta = _KEY_META.get(key)
            if meta is None:
                continue
            if meta["category"] == "connection":
                needs_rebuild_client = True
            if meta["category"] == "behavior" and key in (
                "SCHEDULER_FAST_MS", "SCHEDULER_MID_MS", "SCHEDULER_SLOW_MS",
                "SCHEDULER_IDLE_TIMEOUT_MS", "ACTION_TIMEOUT_MS",
                "SCHEDULER_AUTO_START_FAST", "SCHEDULER_AUTO_START_MID",
                "SCHEDULER_AUTO_START_SLOW",
            ):
                needs_scheduler_update = True

        # 调度器更新（必须在主线程，QTimer 是 Qt 对象）
        if needs_scheduler_update and self.agent and hasattr(self.agent, 'scheduler'):
            try:
                self.agent.scheduler.update_config()
            except Exception as e:
                logger.exception(f"[Settings] scheduler.update_config failed: {e}")

        # LLM 客户端重建（在后台线程执行，避免阻塞 GUI）
        if needs_rebuild_client and self.agent and hasattr(self.agent, 'behavior'):
            def _rebuild():
                try:
                    self.agent.behavior.rebuild_client()
                except Exception as e:
                    logger.exception(f"[Settings] rebuild_client failed: {e}")
                logger.info("[Settings] rebuild_client complete")

            t = threading.Thread(target=_rebuild, daemon=True, name="settings-rebuild")
            t.start()

        if needs_restart_keys:
            self._msg("设置已保存", "已保存。部分设置须重启后生效。")
        else:
            self._msg("设置已保存", "所有设置已即时生效。")
        self._take_snapshot()

    # ── LLM 连通性测试 ──

    def _test_llm(self):
        """在子线程测试 LLM 连通性。"""
        if self._llm_thread is not None and self._llm_thread.isRunning():
            return  # 上一次测试还在运行
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

    # ── 获取模型列表 ──

    def _fetch_models(self):
        if self._models_thread and self._models_thread.isRunning():
            return

        brain = self._brain_combo.currentText()
        if brain == "local":
            self._msg("提示", "当前为 local 模式，无需远程模型。")
            return

        url = self._fields["LLM_URL"].text().strip() if "LLM_URL" in self._fields else ""
        key = self._fields["LLM_KEY"].text().strip() if "LLM_KEY" in self._fields else ""
        ollama_url = self._fields["OLLAMA_BASE_URL"].text().strip() if "OLLAMA_BASE_URL" in self._fields else ""

        if brain == "ollama":
            base_url = ollama_url or config.OLLAMA_BASE_URL
            api_key = "ollama"
        else:
            base_url = url
            api_key = key

        if not base_url or (brain != "ollama" and not api_key):
            self._msg("提示", "请先填写 API 地址和 Key。")
            return

        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=config.LLM_TIMEOUT)

        self._btn_fetch_models.setEnabled(False)
        self._btn_fetch_models.setText("获取中…")

        self._models_thread = QThread()
        self._models_worker = _ModelsFetchWorker(client)
        self._models_worker.moveToThread(self._models_thread)
        self._models_thread.started.connect(self._models_worker.run)
        self._models_worker.finished.connect(self._on_models_fetched)
        self._models_worker.finished.connect(self._models_thread.quit)
        self._models_thread.start()

    def _on_models_fetched(self, success: bool, model_ids: list, error_msg: str):
        self._btn_fetch_models.setEnabled(True)
        self._btn_fetch_models.setText("获取列表")

        if not success:
            self._msg("获取失败", f"无法获取模型列表：\n{error_msg}")
            return

        if not model_ids:
            self._msg("提示", "未找到可用模型。")
            return

        # 弹出菜单供选择
        menu = QMenu(self)
        for mid in model_ids:
            action = menu.addAction(mid)
            action.triggered.connect(lambda checked=False, m=mid: self._model_edit.setText(m))

        # 在按钮下方弹出
        pos = self._btn_fetch_models.mapToGlobal(
            self._btn_fetch_models.rect().bottomLeft()
        )
        menu.exec(pos)

    # ── 窗口事件 ──

    def closeEvent(self, event):
        """关闭前检查未保存修改，清理后台线程。"""
        if self._is_dirty():
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
                # 如果保存失败（无效字段），取消关闭
                if self._is_dirty():
                    event.ignore()
                    return
            elif clicked == btn_cancel:
                event.ignore()
                return

        if self._llm_thread is not None and self._llm_thread.isRunning():
            self._llm_thread.quit()
            self._llm_thread.wait(2000)
        if self._models_thread is not None and self._models_thread.isRunning():
            self._models_thread.quit()
            self._models_thread.wait(2000)
        super().closeEvent(event)

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
        painter.fillPath(path, QColor(_COLOR_BG))
        painter.setPen(QPen(QColor("#000000"), 1))
        painter.drawPath(path)