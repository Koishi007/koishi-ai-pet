"""设置页：单位换算、可见项覆盖与表单控件。

界面按秒输入、配置按毫秒存储；hidden=False 的配置项必须在页面上有入口，
否则用户只能手写 settings.json。页签归属的真相在 TAB_SPECS。
"""

from types import SimpleNamespace

import pytest
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QWidget

from pet.config import PET_SCALE_STEPS, Config, _KEY_META
from pet.ui.settings_actions import SettingsActions, apply_pet_scale
from pet.ui.settings_fields import SettingsForm, SettingsWidgets
from pet.ui.settings_spec import TAB_SPECS, display_value, stored_value
from pet.ui.settings_tabs import SettingsTabs
from pet.ui.settings_workers import ProbeRegistry

TAB_KEYS = {key for _label, keys in TAB_SPECS for key in keys}


def test_mid_interval_displays_seconds():
    assert display_value("SCHEDULER_MID_MS", 600000) == 600


def test_mid_interval_stores_milliseconds():
    assert stored_value("SCHEDULER_MID_MS", 600) == 600000


def test_other_keys_keep_units():
    assert display_value("LLM_TIMEOUT", 20.0) == 20.0
    assert stored_value("LLM_TIMEOUT", 20.0) == 20.0


def test_visible_keys_have_controls():
    """hidden=False 的项都在页签清单里；LLM_ACTIVE_PROFILE 由切换按钮负责。"""
    visible = {k for k, m in _KEY_META.items() if not m["hidden"]}
    assert visible - TAB_KEYS - {"LLM_ACTIVE_PROFILE"} == set()
    assert TAB_KEYS <= set(_KEY_META)


def test_tab_specs_match_registered_fields(qt_app):
    """页签声明的键与实际注册到表单的控件一一对应。"""
    form = SettingsForm()
    SettingsTabs(form).build()
    assert set(form.fields) == TAB_KEYS


class TestPetScale:
    """宠物大小只暴露挡位，宽高由挡位映射推导且恒为正方形。"""

    def test_steps_are_hardcoded(self):
        assert list(PET_SCALE_STEPS) == ["50%", "75%", "100%", "125%", "150%", "200%"]

    def test_combo_collects_step_label(self, qt_app):
        combo = QComboBox()
        combo.addItems(list(PET_SCALE_STEPS))
        combo.setCurrentIndex(3)
        form = SettingsForm()
        form.fields["PET_SCALE"] = combo

        values, invalid = form.collect()

        assert invalid == []
        assert values == {"PET_SCALE": "125%"}

    def test_combo_selects_step(self, qt_app):
        combo = QComboBox()
        combo.addItems(list(PET_SCALE_STEPS))

        SettingsForm.select_combo(combo, "125%")
        assert combo.currentText() == "125%"

    @pytest.fixture
    def fresh_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        return Config()

    def test_default_is_125_square(self, fresh_config):
        assert fresh_config.PET_SCALE == "100%"
        assert (fresh_config.PET_WIDTH, fresh_config.PET_HEIGHT) == (125, 125)

    def test_save_step_derives_square_size(self, fresh_config):
        fresh_config.save("PET_SCALE", "200%")
        assert (fresh_config.PET_WIDTH, fresh_config.PET_HEIGHT) == (250, 250)

    def test_unknown_step_falls_back_to_default(self, fresh_config):
        fresh_config.save("PET_SCALE", "1.6")
        assert fresh_config.PET_SCALE == "100%"
        assert fresh_config.PET_WIDTH == 125

    def test_legacy_size_keys_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        path = tmp_path / "KoishiAI" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"PET_WIDTH": 250, "PET_HEIGHT": 250}', encoding="utf-8")

        cfg = Config()

        assert cfg.PET_SCALE == "100%"
        assert cfg.PET_WIDTH == 125

    def test_save_applies_scale_to_window(self, qt_app):
        calls = []
        agent = SimpleNamespace(pet_window=SimpleNamespace(
            apply_scale=lambda: calls.append(1)))

        apply_pet_scale(agent)

        assert calls == [1]

    def test_reset_restores_default_size(self, fresh_config):
        fresh_config.save("PET_SCALE", "200%")

        fresh_config.reset(["PET_SCALE"])

        assert fresh_config.PET_SCALE == "100%"
        assert (fresh_config.PET_WIDTH, fresh_config.PET_HEIGHT) == (125, 125)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _collect(edit: QLineEdit):
    form = SettingsForm()
    form.fields["SCHEDULER_MID_MS"] = edit
    return form.collect()


def test_out_of_range_input_is_rejected(qt_app):
    """validator 的越界值只是 Intermediate，保存时要拦下来。"""
    edit = QLineEdit()
    edit.setValidator(QIntValidator(60, 3600))
    edit.setText("6")

    values, invalid = _collect(edit)

    assert invalid == ["SCHEDULER_MID_MS"]
    assert values == {}


def test_valid_input_is_converted_to_ms(qt_app):
    edit = QLineEdit()
    edit.setValidator(QIntValidator(60, 3600))
    edit.setText("600")

    values, invalid = _collect(edit)

    assert invalid == []
    assert values == {"SCHEDULER_MID_MS": 600000}


def _hint_double(key: str, widget, labels: dict):
    form = SettingsForm()
    form.fields[key] = widget
    form._label_cache = labels
    return form.invalid_hint(key)


def test_invalid_hint_uses_ui_label_and_range(qt_app):
    edit = QLineEdit()
    edit.setValidator(QIntValidator(60, 3600))

    hint = _hint_double("SCHEDULER_MID_MS", edit, {"SCHEDULER_MID_MS": "自主行动间隔(秒)"})

    assert hint.startswith("自主行动间隔(秒)")
    assert "60" in hint and "3600" in hint


def test_invalid_hint_falls_back_to_description(qt_app):
    hint = _hint_double("LLM_MODEL", QLineEdit(), {})

    assert hint.startswith(_KEY_META["LLM_MODEL"]["description"])
    assert hint != _KEY_META["LLM_MODEL"]["description"]


def test_probe_registry_shutdown_covers_every_group():
    """关闭时每一组线程都要 quit/wait，语音组不再被漏掉。"""

    class _Thread:
        def __init__(self):
            self.running = True
            self.waited = []

        def isRunning(self):
            return self.running

        def quit(self):
            self.running = False

        def wait(self, ms):
            self.waited.append(ms)

    registry = ProbeRegistry()
    llm, voice = _Thread(), _Thread()
    registry._entries["llm"] = (llm, object())
    registry._entries["voice"] = (voice, object())

    registry.shutdown()

    assert llm.waited == [2000] and voice.waited == [2000]
    assert registry._entries == {}


def test_probe_registry_keeps_stuck_threads():
    """网络调用没返回时 quit() 不生效，仍在跑的线程不能丢引用。"""

    class _StuckThread:
        def __init__(self):
            self.running = True

        def isRunning(self):
            return self.running

        def quit(self):
            pass

        def wait(self, ms):
            return False

    registry = ProbeRegistry()
    thread, worker = _StuckThread(), object()
    registry._entries["llm"] = (thread, worker)

    registry.shutdown()

    assert registry._entries == {}
    assert registry._lingering["llm"] == (thread, worker)
    assert registry.running("llm") is True

    thread.running = False
    registry._prune()
    assert registry._lingering == {}
    assert registry.running("llm") is False


class _CapturedProbe:
    """记录 probes.start 收到的 worker，不真的起线程。"""

    def __init__(self):
        self.workers = {}

    def running(self, name):
        return False

    def start(self, name, worker, on_finished, on_thread_finished=None):
        self.workers[name] = worker
        return True


def _settings_actions(qt_app, monkeypatch):
    form = SettingsForm()
    tabs = SettingsTabs(form)
    tabs.pages = tabs.build()
    tabs.bind(_NoopActions())
    tabs.on_mode_changed(tabs.widgets.brain_combo.currentText())
    actions = SettingsActions(form, tabs.widgets, QWidget(), None,
                              lambda *args, **kwargs: None, lambda: False, lambda: None)
    captured = _CapturedProbe()
    actions.probes = captured
    return form, tabs, actions, captured


class _NoopActions:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class TestConnectionTestTarget:
    """「测试连接」测的是当前启用方案，不是填得最全的那一套。"""

    @staticmethod
    def _fill_alt(tabs):
        w = tabs.widgets
        w.alt_url_edit.setText("https://alt.example.com/v1")
        w.alt_key_edit.setText("sk-alt")
        w.alt_model_edit.setText("alt-model")

    def test_primary_test_ignores_filled_alternative(self, qt_app, monkeypatch):
        from pet.config import config
        monkeypatch.setattr(config, "LLM_ACTIVE_PROFILE", "primary")
        _form, tabs, actions, captured = _settings_actions(qt_app, monkeypatch)
        w = tabs.widgets
        w.brain_combo.setCurrentText("api")
        w.model_edit.setText("primary-model")
        w.url_edit.setText("https://primary.example/v1")
        w.llm_key_edit.setText("sk-primary")
        self._fill_alt(tabs)

        actions.test_llm()

        worker = captured.workers["llm"]
        assert worker._model == "primary-model"
        assert worker._key == "sk-primary"

    def test_alternative_profile_tests_alternative(self, qt_app, monkeypatch):
        from pet.config import config
        monkeypatch.setattr(config, "LLM_ACTIVE_PROFILE", "alternative")
        _form, tabs, actions, captured = _settings_actions(qt_app, monkeypatch)
        w = tabs.widgets
        w.brain_combo.setCurrentText("api")
        w.model_edit.setText("primary-model")
        w.url_edit.setText("https://primary.example/v1")
        w.llm_key_edit.setText("sk-primary")
        self._fill_alt(tabs)

        actions.test_llm()

        worker = captured.workers["llm"]
        assert worker._model == "alt-model"
        assert worker._key == "sk-alt"

    def test_incomplete_connection_reports_and_skips_probe(self, qt_app, monkeypatch):
        """连接未填完整时不建 worker，测试栏给出可读原因。"""
        from pet.config import config
        monkeypatch.setattr(config, "LLM_ACTIVE_PROFILE", "primary")
        _form, tabs, actions, captured = _settings_actions(qt_app, monkeypatch)
        w = tabs.widgets
        w.brain_combo.setCurrentText("api")
        w.provider_combo.setCurrentIndex(w.provider_combo.findData("openai"))
        w.model_edit.setText("primary-model")
        w.url_edit.setText("")

        actions.test_llm()

        assert captured.workers == {}
        assert w.test_output.toPlainText() != ""
        assert w.label_test.text() != ""

    def test_ollama_completeness_follows_address_field(self, qt_app, monkeypatch):
        """Ollama 档的地址以界面输入为准，清空后测试连接停在未配置。"""
        from pet.config import config
        monkeypatch.setattr(config, "LLM_ACTIVE_PROFILE", "primary")
        _form, tabs, actions, captured = _settings_actions(qt_app, monkeypatch)
        w = tabs.widgets
        w.brain_combo.setCurrentText("ollama")
        w.model_edit.setText("qwen2.5")
        form = tabs.form
        form.fields["OLLAMA_BASE_URL"].setText("")

        actions.test_llm()

        assert captured.workers == {}
        assert w.test_output.toPlainText() != ""

    def test_ollama_blank_model_tests_default_model(self, qt_app, monkeypatch):
        """Ollama 档模型名留空时，测试连接发的是运行期的默认模型。"""
        from pet.brain.llm_client import OLLAMA_DEFAULT_MODEL
        from pet.config import config
        monkeypatch.setattr(config, "LLM_ACTIVE_PROFILE", "primary")
        _form, tabs, actions, captured = _settings_actions(qt_app, monkeypatch)
        w = tabs.widgets
        w.brain_combo.setCurrentText("ollama")
        w.model_edit.setText("")
        tabs.form.fields["OLLAMA_BASE_URL"].setText("http://127.0.0.1:11434/v1")

        actions.test_llm()

        worker = captured.workers["llm"]
        assert worker._model == OLLAMA_DEFAULT_MODEL
        assert worker._url == "http://127.0.0.1:11434/v1"

    @pytest.mark.parametrize("profile, model_attr, other_attr", [
        ("primary", "ollama_model_edit", "ollama_model_alt_edit"),
        ("alternative", "ollama_model_alt_edit", "ollama_model_edit"),
    ])
    def test_ollama_test_reads_ollama_model_control(
            self, qt_app, monkeypatch, profile, model_attr, other_attr):
        """Ollama 档测试连接发的是该档模型控件里的值，与 API 档控件无关。"""
        from pet.config import config
        monkeypatch.setattr(config, "LLM_ACTIVE_PROFILE", profile)
        _form, tabs, actions, captured = _settings_actions(qt_app, monkeypatch)
        w = tabs.widgets
        w.brain_combo.setCurrentText("ollama")
        w.model_edit.setText("api-model")
        w.alt_model_edit.setText("api-alt-model")
        getattr(w, model_attr).setText("qwen2.5")
        getattr(w, other_attr).setText("qwen3")
        tabs.form.fields["OLLAMA_BASE_URL"].setText("http://127.0.0.1:11434/v1")

        actions.test_llm()

        worker = captured.workers["llm"]
        assert worker._model == "qwen2.5"
        assert worker._url == "http://127.0.0.1:11434/v1"
        assert worker._provider_name == "openai"

    def test_ollama_test_reads_ollama_effort_control(self, qt_app, monkeypatch):
        """Ollama 档测试连接下发的是专属推理强度下拉框当前选中的档位。"""
        from pet.config import config
        monkeypatch.setattr(config, "LLM_ACTIVE_PROFILE", "primary")
        _form, tabs, actions, captured = _settings_actions(qt_app, monkeypatch)
        w = tabs.widgets
        w.brain_combo.setCurrentText("ollama")
        w.ollama_model_edit.setText("deepseek-chat")
        tabs.form.fields["OLLAMA_BASE_URL"].setText("http://127.0.0.1:11434/v1")
        combo = w.ollama_effort_combo
        combo.setCurrentIndex(combo.findData("high"))

        actions.test_llm()

        assert captured.workers["llm"]._reasoning_effort == "high"

    def test_merge_test_in_ollama_mode_uses_service_address(self, qt_app, monkeypatch):
        """Ollama 档汇总沿用对话连接时，测的是服务地址与专属模型输入框。"""
        from pet.config import config
        monkeypatch.setattr(config, "LLM_ACTIVE_PROFILE", "primary")
        monkeypatch.setattr(config, "MEMORY_MERGE_URL", "")
        monkeypatch.setattr(config, "MEMORY_MERGE_KEY", "")
        monkeypatch.setattr(config, "MEMORY_MERGE_MODEL", "")
        form, tabs, actions, captured = _settings_actions(qt_app, monkeypatch)
        w = tabs.widgets
        w.brain_combo.setCurrentText("ollama")
        w.ollama_model_edit.setText("qwen2.5")
        form.fields["OLLAMA_BASE_URL"].setText("http://127.0.0.1:11434/v1")
        form.fields["MEMORY_MERGE_URL"].setText("")
        form.fields["MEMORY_MERGE_KEY"].setText("")
        form.fields["MEMORY_MERGE_MODEL"].setText("")

        actions.test_merge_connection()

        worker = captured.workers["merge_test"]
        assert worker._url == "http://127.0.0.1:11434/v1"
        assert worker._model == "qwen2.5"
        assert worker._provider_name == "openai"

    @pytest.mark.parametrize("merge_url", ["", "https://merge.example/v1"])
    def test_merge_test_uses_dialog_channel(self, qt_app, monkeypatch, merge_url):
        """汇总走对话通道：测试必须打在同一通道上，独立汇总配置也不例外。"""
        from pet.config import config
        monkeypatch.setattr(config, "LLM_ACTIVE_PROFILE", "primary")
        monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
        monkeypatch.setattr(config, "MEMORY_MERGE_URL", merge_url)
        monkeypatch.setattr(config, "MEMORY_MERGE_KEY", "merge-key" if merge_url else "")
        monkeypatch.setattr(config, "MEMORY_MERGE_MODEL",
                            "merge-model" if merge_url else "")
        form, tabs, actions, captured = _settings_actions(qt_app, monkeypatch)
        w = tabs.widgets
        w.brain_combo.setCurrentText("api")
        w.provider_combo.setCurrentIndex(w.provider_combo.findData("gemini"))
        w.model_edit.setText("gemini-2.5-flash")
        w.llm_key_edit.setText("AIza-test")
        w.url_edit.setText("")
        form.fields["MEMORY_MERGE_URL"].setText(merge_url)
        form.fields["MEMORY_MERGE_KEY"].setText("merge-key" if merge_url else "")
        form.fields["MEMORY_MERGE_MODEL"].setText("merge-model" if merge_url else "")

        actions.test_merge_connection()

        assert captured.workers["merge_test"]._provider_name == "gemini"


def test_actions_shutdown_stops_hotkey_listener(qt_app):
    class _Listener:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    widgets = SettingsWidgets()
    actions = SettingsActions(
        SettingsForm(), widgets, QWidget(), None,
        lambda *args, **kwargs: None, lambda: False, lambda: None)
    listener = _Listener()
    widgets.capture_listener = listener

    actions.shutdown()

    assert listener.stopped
    assert widgets.capture_listener is None


def test_hooks_dispatch_to_injected_implementations(monkeypatch):
    from pet.ui import settings_hooks

    calls = []
    monkeypatch.setattr(settings_hooks, "_embedding_factory",
                        lambda *args: calls.append(("embed", args)) or object())
    monkeypatch.setattr(settings_hooks, "_invalidate_action_section",
                        lambda: calls.append(("invalidate",)))

    settings_hooks.make_embedding_client("u", "k", "m", 256)
    settings_hooks.invalidate_action_section()

    assert calls == [("embed", ("u", "k", "m", 256)), ("invalidate",)]


def test_embedding_worker_uses_injected_client(qt_app, monkeypatch):
    from pet.ui import settings_hooks
    from pet.ui.settings_workers import EmbeddingTestWorker

    class _Client:
        def embed(self, text):
            return [[0.1, 0.2]]

    created = []

    def factory(url, key, model, dim):
        created.append((url, key, model, dim))
        return _Client()

    monkeypatch.setattr(settings_hooks, "_embedding_factory", factory)
    worker = EmbeddingTestWorker("u", "k", "m", 256)
    results = []
    worker.finished.connect(lambda ok, content, elapsed: results.append((ok, content)))

    worker.run()

    assert created == [("u", "k", "m", 256)]
    assert len(results) == 1 and results[0][0] is True
    assert "向量维度: 2" in results[0][1]
