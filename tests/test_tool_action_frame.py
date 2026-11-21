"""工具调用动作帧：切帧契约、TOOL_CTX 转发、工具侧接入点。"""

import json
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from pet.tools.context import ToolContext
from pet.ui.pet_animations import PetAnimator


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _make_action(root, name: str, frames: int = 3, loop: bool = False):
    """在临时素材目录里造一个动作：若干张纯色 PNG + 配置。"""
    d = root / name
    d.mkdir(parents=True)
    for i in range(frames):
        img = QImage(8, 8, QImage.Format.Format_ARGB32)
        img.fill(0xFF000000 | (i * 0x203040))
        img.save(str(d / f"{i + 1}.png"))
    cfg = {"desc": name, "tick_counts": 30, "frame_ratios": [1 / frames] * frames,
           "loop": loop}
    (d / f"{name}.json").write_text(json.dumps(cfg), encoding="utf-8")


class TestPlayFrame:
    def test_holds_single_frame(self, qapp, tmp_path):
        _make_action(tmp_path, "read", frames=3)
        anim = PetAnimator(pet_dir=str(tmp_path))
        assert anim.play_frame("read", 1) is True
        assert anim.current_action == "read"
        assert anim.is_playing  # 计时器保持运行，外部逻辑仍视为在播
        assert len(anim._frames) == 1

    def test_index_out_of_range_uses_last_frame(self, qapp, tmp_path):
        _make_action(tmp_path, "write", frames=2)
        anim = PetAnimator(pet_dir=str(tmp_path))
        assert anim.play_frame("write", 99) is True
        assert len(anim._frames) == 1

    def test_missing_action_returns_false(self, qapp, tmp_path):
        anim = PetAnimator(pet_dir=str(tmp_path))
        assert anim.play_frame("nope", 0) is False

    def test_later_call_replaces_earlier(self, qapp, tmp_path):
        _make_action(tmp_path, "read", frames=2)
        _make_action(tmp_path, "operate", frames=2)
        anim = PetAnimator(pet_dir=str(tmp_path))
        assert anim.play_frame("read", 0) is True
        assert anim.play_frame("operate", 1) is True
        assert anim.current_action == "operate"


class TestToolContext:
    def test_forwards_to_agent(self):
        calls = []
        ctx = ToolContext()
        ctx.bind(SimpleNamespace(tool_frame_requested=SimpleNamespace(
            emit=lambda name, index: calls.append((name, index)))))
        ctx.play_action_frame("read", 2)
        assert calls == [("read", 2)]

    def test_index_coerced_to_int(self):
        calls = []
        ctx = ToolContext()
        ctx.bind(SimpleNamespace(tool_frame_requested=SimpleNamespace(
            emit=lambda name, index: calls.append((name, index)))))
        ctx.play_action_frame("write", 1.0)
        assert calls == [("write", 1)]

    def test_unbound_is_noop(self):
        ToolContext().play_action_frame("read", 0)

    def test_empty_name_is_ignored(self):
        calls = []
        ctx = ToolContext()
        ctx.bind(SimpleNamespace(tool_frame_requested=SimpleNamespace(
            emit=lambda name, index: calls.append((name, index)))))
        ctx.play_action_frame("", 0)
        assert calls == []

    def test_bad_index_is_ignored(self):
        calls = []
        ctx = ToolContext()
        ctx.bind(SimpleNamespace(tool_frame_requested=SimpleNamespace(
            emit=lambda name, index: calls.append((name, index)))))
        ctx.play_action_frame("read", "第二帧")
        assert calls == []


class TestConfirmFrame:
    def test_confirm_plays_waiting_first_frame(self):
        import threading

        frames = []
        ctx = ToolContext()
        ctx.bind(SimpleNamespace(tool_frame_requested=SimpleNamespace(
            emit=lambda name, index: frames.append((name, index)))))

        def sink(request):
            request.shown.set()
            request.event.set()

        ctx.set_confirm_sink(sink)
        request = SimpleNamespace(timeout_s=1, queue_grace_s=0,
                                  shown=threading.Event(), event=threading.Event(),
                                  result=True)
        assert ctx.confirm(request) is True
        assert frames == [("waiting", 0)]

    def test_no_sink_does_not_play_frame(self):
        frames = []
        ctx = ToolContext()
        ctx.bind(SimpleNamespace(tool_frame_requested=SimpleNamespace(
            emit=lambda name, index: frames.append((name, index)))))
        assert ctx.confirm(object()) is False
        assert frames == []


class TestToolWiring:
    def test_read_file_plays_read_frame(self, monkeypatch):
        from pet.tools import file_ops

        played = []
        monkeypatch.setattr(file_ops.TOOL_CTX, "play_action_frame",
                            lambda name, index=0: played.append(name))
        monkeypatch.setattr(file_ops, "_get_instance",
                            lambda: SimpleNamespace(read_file=lambda **kw: {"ok": True}))
        file_ops._read_file(path="x.txt")
        assert played == ["read"]

    def test_write_note_plays_write_frame(self, monkeypatch):
        from pet.tools import file_ops

        played = []
        monkeypatch.setattr(file_ops.TOOL_CTX, "play_action_frame",
                            lambda name, index=0: played.append(name))
        monkeypatch.setattr(file_ops, "_get_instance",
                            lambda: SimpleNamespace(write_note=lambda **kw: {"ok": True}))
        file_ops._write_note(filename="a.txt", content="x")
        assert played == ["write"]

    def test_pixel_art_wrapper_plays_frame(self, monkeypatch):
        from pet.tools import pixel_art

        played = []
        monkeypatch.setattr(pixel_art.TOOL_CTX, "play_action_frame",
                            lambda name, index=0: played.append(name))
        assert pixel_art.guarded(lambda **kw: {"ok": True})() == {"ok": True}
        assert played == ["operate"]
