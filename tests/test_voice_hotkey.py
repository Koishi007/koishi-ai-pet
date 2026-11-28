"""语音热键：两种触发模式的状态机。

用假按键对象驱动 HotkeyManager 的回调，不起真实监听线程，也不依赖键盘。
"""

import pytest
from PySide6.QtCore import QObject

from pet.voice import hotkey_manager
from pet.voice.hotkey_manager import HotkeyManager


class _Key:
    """最小按键替身：只提供 char / name 两种取法。"""

    def __init__(self, name: str = "", char: str | None = None):
        self.name = name
        self.char = char


class _Clock:
    """可推进的单调时钟，替掉 hotkey_manager.time.monotonic。"""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(hotkey_manager.time, "monotonic", fake)
    return fake


def _make(mode: str, hotkey: str = "alt_l") -> tuple[HotkeyManager, list[str]]:
    manager = HotkeyManager.__new__(HotkeyManager)
    QObject.__init__(manager)
    manager._listener = None
    manager._hotkey = hotkey
    manager._mode = mode
    manager._key_down = False
    manager._recording = False
    manager._press_at = 0.0
    manager._armed_at = 0.0
    manager._intercept_enter = False
    manager._enter_handled = False
    events: list[str] = []
    manager.voice_start.connect(lambda: events.append("start"))
    manager.voice_stop.connect(lambda: events.append("stop"))
    return manager, events


KEY = _Key(name="alt_l")


class TestSingleMode:
    def test_press_starts_and_release_stops(self, clock):
        manager, events = _make("single")
        manager._on_press(KEY)
        assert events == ["start"]
        clock.advance(0.5)
        manager._on_release(KEY)
        assert events == ["start", "stop"]

    def test_auto_repeat_does_not_restart(self, clock):
        manager, events = _make("single")
        manager._on_press(KEY)
        manager._on_press(KEY)
        manager._on_press(KEY)
        assert events == ["start"]

    def test_other_key_ignored(self, clock):
        manager, events = _make("single")
        manager._on_press(_Key(name="f8"))
        assert events == []

    def test_release_without_press_ignored(self, clock):
        manager, events = _make("single")
        manager._on_release(KEY)
        assert events == []


class TestDoubleMode:
    def test_first_tap_does_not_record(self, clock):
        manager, events = _make("double")
        manager._on_press(KEY)
        clock.advance(0.1)
        manager._on_release(KEY)
        assert events == []

    def test_second_press_after_tap_records(self, clock):
        manager, events = _make("double")
        manager._on_press(KEY)
        clock.advance(0.1)
        manager._on_release(KEY)
        clock.advance(0.2)
        manager._on_press(KEY)
        assert events == ["start"]
        clock.advance(1.5)
        manager._on_release(KEY)
        assert events == ["start", "stop"]

    def test_holding_first_press_does_not_arm(self, clock):
        manager, events = _make("double")
        manager._on_press(KEY)
        clock.advance(1.0)
        manager._on_release(KEY)
        clock.advance(0.1)
        manager._on_press(KEY)
        assert events == []

    def test_arm_expires(self, clock):
        manager, events = _make("double")
        manager._on_press(KEY)
        clock.advance(0.1)
        manager._on_release(KEY)
        clock.advance(hotkey_manager.ARM_SECONDS + 0.1)
        manager._on_press(KEY)
        assert events == []

    def test_release_after_recording_does_not_rearm(self, clock):
        manager, events = _make("double")
        manager._on_press(KEY)
        clock.advance(0.1)
        manager._on_release(KEY)
        clock.advance(0.2)
        manager._on_press(KEY)
        clock.advance(2.0)
        manager._on_release(KEY)
        assert manager._armed_at == 0.0
        clock.advance(0.1)
        manager._on_press(KEY)
        assert events == ["start", "stop"]

    def test_second_tap_without_hold_also_records_until_release(self, clock):
        manager, events = _make("double")
        manager._on_press(KEY)
        clock.advance(0.1)
        manager._on_release(KEY)
        clock.advance(0.2)
        manager._on_press(KEY)
        clock.advance(0.1)
        manager._on_release(KEY)
        assert events == ["start", "stop"]


class TestEnterIntercept:
    def test_enter_emits_once_until_release(self, clock):
        manager, _ = _make("single")
        submitted: list[int] = []
        manager.enter_pressed.connect(lambda: submitted.append(1))
        manager.set_intercept_enter(True)
        enter = _Key(name="enter")
        manager._on_press(enter)
        manager._on_press(enter)
        assert submitted == [1]
        manager._on_release(enter)
        manager._on_press(enter)
        assert submitted == [1, 1]

    def test_enter_ignored_when_not_intercepting(self, clock):
        manager, _ = _make("single")
        submitted: list[int] = []
        manager.enter_pressed.connect(lambda: submitted.append(1))
        manager._on_press(_Key(name="enter"))
        assert submitted == []


class TestKeyName:
    def test_char_takes_precedence_and_lowercases(self):
        assert HotkeyManager._key_name(_Key(name="a", char="A")) == "a"

    def test_special_key_uses_name(self):
        assert HotkeyManager._key_name(_Key(name="alt_l")) == "alt_l"

    def test_missing_fields_return_empty(self):
        class _Broken:
            @property
            def name(self):
                raise ValueError("no name")

            @property
            def char(self):
                raise ValueError("no char")

        assert HotkeyManager._key_name(_Broken()) == ""


class TestReload:
    """设置页保存后热键与模式立即生效。"""

    def test_reload_picks_up_new_hotkey(self, monkeypatch, clock):
        from pet.config import config
        manager, events = _make("single", hotkey="alt_l")
        monkeypatch.setattr(config, "VOICE_HOTKEY", "f8")
        monkeypatch.setattr(config, "VOICE_TRIGGER_MODE", "single")
        manager.reload()
        assert manager._hotkey == "f8"

        manager._on_press(_Key(name="alt_l"))
        clock.advance(0.5)
        manager._on_release(_Key(name="alt_l"))
        assert events == []

        manager._on_press(_Key(name="f8"))
        clock.advance(0.5)
        manager._on_release(_Key(name="f8"))
        assert events == ["start", "stop"]

    def test_reload_picks_up_new_mode(self, monkeypatch, clock):
        from pet.config import config
        manager, events = _make("single", hotkey="alt_l")
        monkeypatch.setattr(config, "VOICE_HOTKEY", "alt_l")
        monkeypatch.setattr(config, "VOICE_TRIGGER_MODE", "double")
        manager.reload()
        assert manager._mode == "double"

        manager._on_press(KEY)
        clock.advance(1.0)
        manager._on_release(KEY)
        assert events == []

    def test_reload_during_recording_stops_it(self, monkeypatch, clock):
        from pet.config import config
        manager, events = _make("single", hotkey="alt_l")
        manager._on_press(KEY)
        assert events == ["start"]

        monkeypatch.setattr(config, "VOICE_HOTKEY", "f8")
        monkeypatch.setattr(config, "VOICE_TRIGGER_MODE", "single")
        manager.reload()
        assert events == ["start", "stop"]
        assert manager._recording is False

    def test_reload_resets_armed_state(self, monkeypatch, clock):
        from pet.config import config
        manager, events = _make("double", hotkey="alt_l")
        manager._on_press(KEY)
        clock.advance(0.1)
        manager._on_release(KEY)
        assert manager._armed_at > 0

        monkeypatch.setattr(config, "VOICE_HOTKEY", "alt_l")
        monkeypatch.setattr(config, "VOICE_TRIGGER_MODE", "double")
        manager.reload()
        assert manager._armed_at == 0.0


class TestConfigDefaults:
    def test_default_hotkey_is_left_alt(self):
        from pet.config import _KEY_META
        assert _KEY_META["VOICE_HOTKEY"]["default"] == "alt_l"
    def test_trigger_modes(self):
        from pet.config import _KEY_META
        assert _KEY_META["VOICE_TRIGGER_MODE"]["enum"] == ["single", "double"]
        assert _KEY_META["VOICE_TRIGGER_MODE"]["default"] == "double"

    def test_spec_labels_match_config_enum(self):
        from pet.config import _KEY_META
        from pet.ui.settings_spec import VOICE_TRIGGER_CHOICES
        assert [v for _, v in VOICE_TRIGGER_CHOICES] == _KEY_META["VOICE_TRIGGER_MODE"]["enum"]
