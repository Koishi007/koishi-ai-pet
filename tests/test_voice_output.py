"""语音输出：音节切分、波形合成与播放通道的状态机。

播放通道用桩替换 QAudioSink，不依赖真实声卡；合成与切分是纯函数，直接断言。
"""

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from pet.voice import animalese, phoneme
from pet.ui.audio.audio_out import AudioOut, _Feeder


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


class TestPresets:
    def test_all_four_presets_present(self):
        assert set(phoneme.PRESETS) == {"deep", "soft", "bright", "clear"}

    def test_ranges_are_ordered(self):
        for preset_id, (low, high) in phoneme.PRESETS.items():
            assert low < high, preset_id

    def test_default_is_in_table(self):
        assert phoneme.DEFAULT_PRESET in phoneme.PRESETS

    def test_config_enum_matches_presets(self):
        from pet.config import _KEY_META
        assert set(_KEY_META["VOICE_OUTPUT_PRESET"]["enum"]) == set(phoneme.PRESETS)

    def test_unknown_value_falls_back_to_default(self):
        assert phoneme.normalize_preset("nope") == phoneme.DEFAULT_PRESET
        assert phoneme.normalize_preset(None) == phoneme.DEFAULT_PRESET
        assert phoneme.normalize_preset("  SOFT ") == "soft"


class TestSplit:
    def test_pitch_is_stable(self):
        first = [s.pitch for s in phoneme.split("找到你啦")]
        for _ in range(3):
            assert [s.pitch for s in phoneme.split("找到你啦")] == first

    def test_different_chars_differ(self):
        pitches = {round(s.pitch) for s in phoneme.split("找到你啦")}
        assert len(pitches) > 1

    def test_preset_changes_pitch_within_range(self):
        chars = "找到你啦"
        for preset_id, (low, high) in phoneme.PRESETS.items():
            syllables = phoneme.split(chars, preset=preset_id)
            assert len(syllables) == len(chars)
            for syllable in syllables:
                assert low <= syllable.pitch <= high

    def test_preset_changes_actual_values(self):
        char = "找"
        values = {round(phoneme.split(char, preset=p)[0].pitch) for p in phoneme.PRESETS}
        assert len(values) == len(phoneme.PRESETS)

    def test_punctuation_is_unvoiced_pause(self):
        syllables = phoneme.split("好，吗")
        assert len(syllables) == 3
        assert syllables[1].voiced is False
        assert syllables[1].pitch == 0.0
        assert syllables[1].duration > syllables[0].duration

    def test_blank_and_space_are_skipped(self):
        assert len(phoneme.split("  ")) == 0
        assert len(phoneme.split("a b")) == 2
        assert len(phoneme.split("")) == 0

    def test_duration_clamped(self):
        assert phoneme.syllable_duration(1) == pytest.approx(0.030)
        assert phoneme.syllable_duration(9999) == pytest.approx(0.060)

    def test_duration_tracks_typing_interval(self):
        slow = phoneme.split("找", type_interval_ms=50)[0].duration
        fast = phoneme.split("找", type_interval_ms=34)[0].duration
        assert slow > fast


class TestRender:
    def test_length_matches_duration(self):
        syllables = phoneme.split("找到你啦")
        pcm = animalese.render(syllables, 0.5)
        expected = int(sum(s.duration for s in syllables) * animalese.SAMPLE_RATE)
        assert len(pcm) == pytest.approx(expected * 2, abs=8)

    def test_int16_and_peak_within_range(self):
        pcm = animalese.render(phoneme.split("找到你啦"), 1.0)
        samples = np.frombuffer(pcm, dtype=np.int16)
        assert samples.dtype == np.int16
        assert int(np.abs(samples).max()) <= 32767

    def test_silence_syllable_is_zero(self):
        pcm = animalese.render([phoneme.Syllable(0.0, 0.05, False)], 1.0)
        assert not np.frombuffer(pcm, dtype=np.int16).any()

    def test_volume_zero_is_silent(self):
        pcm = animalese.render(phoneme.split("找到你啦"), 0.0)
        assert not np.frombuffer(pcm, dtype=np.int16).any()

    def test_volume_scales_peak(self):
        syllables = phoneme.split("找")
        low = int(np.abs(np.frombuffer(animalese.render(syllables, 0.3), dtype=np.int16)).max())
        high = int(np.abs(np.frombuffer(animalese.render(syllables, 0.6), dtype=np.int16)).max())
        assert high == pytest.approx(low * 2, rel=0.05)

    def test_fade_in_and_out(self):
        pcm = animalese.render([phoneme.Syllable(400.0, 0.05, True)], 1.0)
        samples = np.frombuffer(pcm, dtype=np.int16)
        assert abs(int(samples[0])) < 200
        assert abs(int(samples[-1])) < 200
        assert int(np.abs(samples).max()) > 20000

    def test_empty_input(self):
        assert animalese.render([], 1.0) == b""


class TestFeeder:
    def test_returns_silence_when_empty(self):
        feeder = _Feeder()
        chunk = feeder.readData(100)
        assert chunk == b"\x00\x00" * 50

    def test_drains_in_order(self):
        feeder = _Feeder()
        feeder.feed(b"\x01\x00\x02\x00")
        assert feeder.readData(4) == b"\x01\x00\x02\x00"
        assert feeder.pending_bytes() == 0

    def test_clear_empties_queue(self):
        feeder = _Feeder()
        feeder.feed(b"\x01\x00" * 10)
        feeder.clear()
        assert feeder.pending_bytes() == 0


class _RecordingAudioOut(AudioOut):
    """记录喂入内容，不创建播放线程。"""

    def __init__(self):
        super().__init__(True)
        self.fed: list[bytes] = []
        self.cleared = 0

    def feed(self, pcm: bytes) -> None:
        if self.enabled and pcm:
            self.fed.append(pcm)

    def clear(self) -> None:
        self.cleared += 1


class TestVocalizer:
    def _make(self):
        from pet.ui.audio.vocalizer import Vocalizer
        return Vocalizer(audio=_RecordingAudioOut())

    def test_speak_feeds_pcm(self, monkeypatch):
        from pet.config import config
        monkeypatch.setattr(config, "VOICE_OUTPUT_ENABLED", True)
        monkeypatch.setattr(config, "VOICE_OUTPUT_VOLUME", 50)
        vocalizer = self._make()
        vocalizer.speak("找")
        assert len(vocalizer.audio.fed) == 1

    def test_blank_text_does_nothing(self, monkeypatch):
        from pet.config import config
        monkeypatch.setattr(config, "VOICE_OUTPUT_ENABLED", True)
        vocalizer = self._make()
        vocalizer.speak("  ")
        assert vocalizer.audio.fed == []

    def test_disabled_clears_queue(self, monkeypatch):
        from pet.config import config
        monkeypatch.setattr(config, "VOICE_OUTPUT_ENABLED", False)
        vocalizer = self._make()
        vocalizer.speak("找")
        assert vocalizer.audio.fed == []
        assert vocalizer.audio.cleared == 1


class TestMute:
    """托盘静音只存内存，初值取自磁盘，之后内存态覆盖磁盘。"""

    def _make(self, monkeypatch, enabled=True):
        from pet.config import config
        from pet.ui.audio.vocalizer import Vocalizer
        monkeypatch.setattr(config, "VOICE_OUTPUT_ENABLED", enabled)
        return Vocalizer(audio=_RecordingAudioOut())

    def test_default_unmuted_when_enabled(self, monkeypatch):
        assert self._make(monkeypatch, enabled=True).muted is False

    def test_default_muted_when_disabled_on_disk(self, monkeypatch):
        """磁盘关闭「允许发出声音」时，初始状态即静音。"""
        assert self._make(monkeypatch, enabled=False).muted is True

    def test_memory_unmute_overrides_disk_disabled(self, monkeypatch):
        """磁盘关着，菜单取消静音后仍能发声。"""
        vocalizer = self._make(monkeypatch, enabled=False)
        assert vocalizer.muted is True
        vocalizer.set_muted(False)
        vocalizer.speak("找")
        assert len(vocalizer.audio.fed) == 1

    def test_config_change_syncs_mute_state(self, monkeypatch):
        """设置页改成允许时，内存静音态跟着解除。"""
        from pet.config import config
        vocalizer = self._make(monkeypatch, enabled=False)
        assert vocalizer.muted is True

        monkeypatch.setattr(config, "VOICE_OUTPUT_ENABLED", True)
        assert vocalizer.muted is False

        monkeypatch.setattr(config, "VOICE_OUTPUT_ENABLED", False)
        assert vocalizer.muted is True

    def test_enabling_config_allows_speaking(self, monkeypatch):
        from pet.config import config
        vocalizer = self._make(monkeypatch, enabled=False)
        vocalizer.speak("找")
        assert vocalizer.audio.fed == []

        monkeypatch.setattr(config, "VOICE_OUTPUT_ENABLED", True)
        vocalizer.speak("找")
        assert len(vocalizer.audio.fed) == 1

    def test_manual_mute_survives_until_config_changes(self, monkeypatch):
        """设置不变时，手动切换的静音态不被回滚。"""
        vocalizer = self._make(monkeypatch, enabled=True)
        vocalizer.set_muted(True)
        assert vocalizer.muted is True
        assert vocalizer.muted is True

    def test_muted_stops_feeding(self, monkeypatch):
        vocalizer = self._make(monkeypatch)
        vocalizer.set_muted(True)
        vocalizer.speak("找")
        assert vocalizer.audio.fed == []
        assert vocalizer.audio.cleared >= 1

    def test_unmute_resumes_feeding(self, monkeypatch):
        vocalizer = self._make(monkeypatch)
        vocalizer.set_muted(True)
        vocalizer.speak("找")
        vocalizer.set_muted(False)
        vocalizer.speak("你")
        assert len(vocalizer.audio.fed) == 1

    def test_toggle_flips_state(self, monkeypatch):
        vocalizer = self._make(monkeypatch)
        vocalizer.set_muted(not vocalizer.muted)
        assert vocalizer.muted is True
        vocalizer.set_muted(not vocalizer.muted)
        assert vocalizer.muted is False

    def test_mute_wins_over_config_enabled(self, monkeypatch):
        vocalizer = self._make(monkeypatch, enabled=True)
        vocalizer.set_muted(True)
        vocalizer.speak("找")
        assert vocalizer.audio.fed == []

    def test_mute_is_not_a_config_key(self):
        from pet.config import _KEY_META
        assert "VOICE_MUTED" not in _KEY_META

    def test_mute_not_persisted(self, monkeypatch, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("{}", encoding="utf-8")
        vocalizer = self._make(monkeypatch)
        vocalizer.set_muted(True)
        assert "mute" not in path.read_text(encoding="utf-8").lower()


class TestPetMenuMute:
    """静音项挂在宠物本体右键菜单，不挂在托盘菜单。"""

    @pytest.fixture
    def window_factory(self, qt_app, monkeypatch):
        """构造 PetWindow 并在用例结束时显式关闭，避免退出阶段回收崩溃。"""
        from pet.config import config
        from pet.ui.audio.vocalizer import Vocalizer
        from pet.ui.pet_window import PetWindow
        created = []

        def make(enabled=True):
            monkeypatch.setattr(config, "VOICE_OUTPUT_ENABLED", enabled)
            window = PetWindow()
            vocalizer = Vocalizer(audio=_RecordingAudioOut())
            window.set_vocalizer(vocalizer)
            created.append(window)
            return window, vocalizer

        yield make
        for window in created:
            window.close()
            window.deleteLater()
        qt_app.processEvents()

    def test_menu_label_follows_muted(self, window_factory):
        window, vocalizer = window_factory(enabled=True)
        assert vocalizer.muted is False
        window._toggle_mute()
        assert vocalizer.muted is True
        window._toggle_mute()
        assert vocalizer.muted is False

    def test_initial_state_mirrors_disk_setting(self, window_factory):
        window, vocalizer = window_factory(enabled=False)
        assert vocalizer.muted is True

    def test_toggle_without_vocalizer_is_safe(self, window_factory):
        from pet.ui.pet_window import PetWindow
        window = PetWindow()
        window._toggle_mute()
        window.close()

    def test_tray_has_no_mute_entry(self):
        """托盘菜单不承载静音项。"""
        import inspect
        from pet.ui import system_tray
        source = inspect.getsource(system_tray)
        assert "_toggle_mute" not in source
        assert "静音" not in source


class TestAudioOutDisabled:
    def test_disabled_never_creates_worker(self):
        audio = AudioOut(False)
        audio.feed(b"\x01\x00")
        assert audio._worker is None
        assert audio._thread is None

    def test_empty_pcm_does_nothing(self):
        audio = AudioOut(True)
        audio.feed(b"")
        assert audio._worker is None


class _FakeSink:
    """记录状态转换的 sink 桩，不碰声卡。"""

    def __init__(self, *args, **kwargs):
        self.calls = []
        self.state = "stopped"

    def setBufferSize(self, size):
        self.calls.append("setBufferSize")

    def start(self, device):
        self.calls.append("start")
        self.state = "active"

    def suspend(self):
        self.calls.append("suspend")
        self.state = "suspended"

    def resume(self):
        self.calls.append("resume")
        self.state = "active"

    def stop(self):
        self.calls.append("stop")
        self.state = "stopped"


class _FakeDevices:
    class _Dev:
        def isNull(self):
            return False

    @staticmethod
    def defaultAudioOutput():
        return _FakeDevices._Dev()


class _FakeFormat:
    class SampleFormat:
        Int16 = "int16"

    def setSampleRate(self, rate): pass
    def setChannelCount(self, count): pass
    def setSampleFormat(self, fmt): pass


@pytest.fixture
def stub_multimedia(monkeypatch):
    """把 boot() 内的延迟 import 换成桩。"""
    import sys
    import types

    module = types.ModuleType("PySide6.QtMultimedia")
    module.QAudioSink = _FakeSink
    module.QAudioFormat = _FakeFormat
    module.QMediaDevices = _FakeDevices
    monkeypatch.setitem(sys.modules, "PySide6.QtMultimedia", module)
    monkeypatch.setattr("PySide6.QtMultimedia", module, raising=False)
    return module


class TestWorkerResumesPlayback:
    """喂入 PCM 后必须恢复播放：boot 时的挂起不能把后续 feed 卡在队列里。"""

    def _worker(self):
        from pet.ui.audio.audio_out import _AudioWorker
        return _AudioWorker()

    def test_feed_after_boot_resumes(self, stub_multimedia):
        worker = self._worker()
        worker.boot()
        assert worker._sink.state == "suspended"
        assert worker._active is False

        worker._on_feed(b"\x01\x00" * 100)
        assert worker._sink.state == "active"
        assert worker._active is True

    def test_boot_suspends_and_marks_inactive(self, stub_multimedia):
        worker = self._worker()
        worker.boot()
        assert "suspend" in worker._sink.calls
        assert worker._active is False

    def test_feed_before_boot_is_kept_and_resumed(self, stub_multimedia):
        worker = self._worker()
        worker._on_feed(b"\x01\x00" * 100)
        assert worker._feeder.pending_bytes() == 200
        assert worker._feed_requested is True

        worker.boot()
        assert worker._sink.state == "active"
        assert worker._active is True
        assert worker._feed_requested is False
        assert worker._feeder.pending_bytes() == 200

    def test_clear_suspends_again(self, stub_multimedia):
        worker = self._worker()
        worker.boot()
        worker._on_feed(b"\x01\x00" * 100)
        assert worker._active is True

        worker._on_clear()
        assert worker._sink.state == "suspended"
        assert worker._active is False
        assert worker._feeder.pending_bytes() == 0

    def test_resume_again_after_clear(self, stub_multimedia):
        worker = self._worker()
        worker.boot()
        worker._on_feed(b"\x01\x00" * 100)
        worker._on_clear()
        worker._on_feed(b"\x02\x00" * 100)
        assert worker._sink.state == "active"
        assert worker._active is True


class TestInkButtonTextResize:
    """文字变长时外层按钮必须重新量宽度，否则「录制中...」被截断。"""

    def test_set_text_updates_size_hint(self, qt_app):
        from pet.ui.ink_button import InkButton
        button = InkButton("录制")
        short = button.sizeHint().width()
        button.setText("录制中...")
        assert button.sizeHint().width() > short


class TestVolumeSlider:
    """音量用滑块呈现，取值仍直接写回配置。"""

    @pytest.fixture
    def form(self, qt_app):
        from pet.ui.settings_fields import SettingsForm
        from pet.ui.settings_tabs import SettingsTabs
        built = SettingsForm()
        tabs = SettingsTabs(built)
        pages = tabs.build()
        # 页面没有父对象，必须与 tabs 一起持有，否则控件随 Python 回收被删除
        owner = type("_Owner", (), {})()
        owner.tabs = tabs
        owner.pages = pages
        built._owner = owner
        yield built
        for _label, page in pages:
            page.close()
            page.deleteLater()
        qt_app.processEvents()

    def test_volume_is_slider(self, form):
        from PySide6.QtWidgets import QSlider
        assert isinstance(form.fields["VOICE_OUTPUT_VOLUME"], QSlider)

    def test_slider_range_matches_config(self, form):
        from pet.config import _KEY_META
        slider = form.fields["VOICE_OUTPUT_VOLUME"]
        meta = _KEY_META["VOICE_OUTPUT_VOLUME"]
        assert slider.minimum() == meta["minimum"]
        assert slider.maximum() == meta["maximum"]

    def test_load_sets_value(self, form):
        config = type("C", (), {"VOICE_OUTPUT_VOLUME": 42})()
        form.load(config)
        assert form.fields["VOICE_OUTPUT_VOLUME"].value() == 42

    def test_collect_returns_int(self, form):
        form.fields["VOICE_OUTPUT_VOLUME"].setValue(75)
        values, _ = form.collect()
        assert values["VOICE_OUTPUT_VOLUME"] == 75
        assert isinstance(values["VOICE_OUTPUT_VOLUME"], int)

    def test_label_tracks_value(self, form):
        form.fields["VOICE_OUTPUT_VOLUME"].setValue(88)
        assert form.VOICE_OUTPUT_VOLUME_value_label.text() == "88"

    def test_label_tracks_load(self, form):
        config = type("C", (), {"VOICE_OUTPUT_VOLUME": 31})()
        form.load(config)
        assert form.VOICE_OUTPUT_VOLUME_value_label.text() == "31"
