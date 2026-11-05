"""设置页：单位换算、可见项覆盖与表单控件。

界面按秒输入、配置按毫秒存储；hidden=False 的配置项必须在页面上有入口，
否则用户只能手写 settings.json。页签归属的真相在 TAB_SPECS。
"""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QIntValidator, QWheelEvent
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


def test_combo_ignores_wheel(qt_app):
    """滚轮扫过下拉框时不能改选中项，事件留给外层滚动区。"""
    combo = SettingsForm().combo("LLM_TOOL_ROLE_MODE", ["auto", "native"])
    combo.setCurrentIndex(0)
    event = QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )

    combo.wheelEvent(event)

    assert combo.currentIndex() == 0
    assert event.isAccepted() is False


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

    assert hint == "自主行动间隔(秒)（允许 60–3600）"


def test_invalid_hint_falls_back_to_description(qt_app):
    hint = _hint_double("LLM_MODEL", QLineEdit(), {})

    assert hint == f"{_KEY_META['LLM_MODEL']['description']}（不能为空）"


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
