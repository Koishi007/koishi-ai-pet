"""气泡队列：多轮工具调用下「一句一泡、不丢句、不叠句」。

这几个回归点不看成品 UI 也能判定：句子是否独立成泡、句子之间的过渡期是否会被抢占、
抢占时是否真把队列清空。宿主窗口用几何桩替代，不依赖真实桌面。
"""

import time

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from pet.ui import speech_bubble as speech_bubble_mod
from pet.ui.speech_bubble import SpeechBubble

_PARENT_POS = QPoint(400, 400)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bubble(qt_app, monkeypatch):
    """过渡期压到 30ms、打字 1ms/字：保留真实的定时器时序，只是把尺度缩小。"""
    monkeypatch.setattr(speech_bubble_mod, "_GAP_MS", 30)
    b = SpeechBubble()
    b._type_interval = 1
    yield b
    b.flush_pending()


def _pump(qt_app, condition, timeout_s: float = 5.0) -> bool:
    """驱动事件循环直到条件成立或超时。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def _finished_sentences(bubble, monkeypatch) -> list:
    """记录每一句「打完字」的时刻所见文本：一句只记录一次。"""
    seen: list = []
    original = bubble._finish_stream

    def _recording():
        seen.append(bubble.text())
        original()

    monkeypatch.setattr(bubble, "_finish_stream", _recording)
    return seen


def test_missing_start_stream_starts_a_new_bubble(qt_app, bubble, monkeypatch):
    """调用方漏发 start（工具轮次之间常见）时，新句子必须另起一个气泡。

    修之前这里会变成一句：「第一轮的话第二轮的话」——下一轮的 chunk 直接
    续进了上一句的缓冲区。
    """
    seen = _finished_sentences(bubble, monkeypatch)

    bubble.start_stream(_PARENT_POS)
    bubble.append_stream("第一轮的话")
    bubble.end_stream(10)

    # 工具执行、下一轮回来：中间没有新的 start_stream
    bubble.append_stream("第二轮的话")
    bubble.end_stream(10)

    assert _pump(qt_app, lambda: len(seen) == 2), f"两句各自收尾，实际：{seen}"
    assert seen == ["第一轮的话", "第二轮的话"]


def test_append_alone_shows_and_then_hides(qt_app, bubble):
    """完全没有 start_stream 时也能显示一句话（调用方忘发首帧也不丢输出）。"""
    bubble.append_stream("单独一句")
    bubble.end_stream(10)

    assert bubble.isVisible()
    assert _pump(qt_app, lambda: bubble.text() == "单独一句")
    assert _pump(qt_app, lambda: not bubble.isVisible())


def test_queued_sentence_is_not_preempted(qt_app, bubble, monkeypatch):
    """句子之间的过渡期也算占用：晚到的句子不许插到队首。

    修之前这里会变成「第一句 → 第三句 → 第二句」：第三句在 300ms 的空档里
    以为气泡空闲而直接开播，随后排定的第二句又把它整体顶掉。
    """
    played: list = []
    original = bubble._play_text

    def _record(text, duration, parent_pos=None):
        played.append(text)
        original(text, duration, parent_pos)

    monkeypatch.setattr(bubble, "_play_text", _record)

    bubble.show_text("第一句", 10, _PARENT_POS)
    bubble.show_text("第二句", 10)  # 气泡忙 → 入队

    assert _pump(qt_app, lambda: bubble._gap_timer.isActive()), "过渡期应处于占用状态"

    bubble.show_text("第三句", 10)  # 过渡期到达 → 排在队尾

    assert _pump(qt_app, lambda: len(played) == 3), f"三句都应播出，实际：{played}"
    assert played == ["第一句", "第二句", "第三句"]


def test_flush_pending_drops_queue_and_buffer(qt_app, bubble):
    """抢占时必须连队列一起清：旧台词不能再弹出来压住新输出。"""
    bubble.start_stream(_PARENT_POS)
    bubble.append_stream("旧输出")
    bubble.end_stream(10)
    bubble.show_text("旧的 aside", 10)

    bubble.flush_pending()

    assert bubble._speech_queue == []
    assert bubble._incoming_chunks == []
    assert bubble._buffering is False
    assert bubble._stream_open is False
    assert bubble.text() == ""
    assert not bubble.isVisible()
    assert not bubble.is_busy()

    # 清空之后不该再被任何回调唤醒
    qt_app.processEvents()
    assert not bubble.isVisible()


def test_hide_clears_pending_so_nothing_pops_up_later(qt_app, bubble):
    """宠物窗口隐去后，积压的句子不能再把独立气泡弹出来。"""
    bubble.show_text("正在播", 500, _PARENT_POS)
    bubble.show_text("排队中", 500)

    bubble.hide()

    assert bubble._speech_queue == []
    bubble._hide_timer.timeout.emit()
    qt_app.processEvents()
    assert not bubble.isVisible()


def test_queue_is_capped_so_backlog_cannot_pile_up(qt_app, bubble):
    """多轮工具链会攒下大量 aside，超出上限丢最旧的而不是无限积压。"""
    bubble.show_text("占位", 1000, _PARENT_POS)  # 长时间占用，后续统统入队
    extra = 6
    for i in range(speech_bubble_mod._QUEUE_MAX + extra):
        bubble.show_text(f"台词{i}", 10)

    queued = [item["text"] for item in bubble._speech_queue]
    assert len(queued) == speech_bubble_mod._QUEUE_MAX
    assert queued[-1] == f"台词{speech_bubble_mod._QUEUE_MAX + extra - 1}"  # 保留最新的


def test_buffered_sentence_survives_queued_playback(qt_app, bubble, monkeypatch):
    """气泡忙时攒下的缓冲句，不能被随后播出的排队句抹掉。"""
    played: list = []
    original = bubble._play_text

    def _record(text, duration, parent_pos=None):
        played.append(text)
        original(text, duration, parent_pos)

    monkeypatch.setattr(bubble, "_play_text", _record)

    bubble.show_text("占位", 10, _PARENT_POS)  # 立即开播，占住气泡
    bubble.start_stream(_PARENT_POS)           # 气泡忙 → 进入缓冲
    bubble.append_stream("缓冲中的话")
    bubble.end_stream(10)                      # 缓冲句入队
    bubble.show_text("后来的静态句", 10)          # 排在缓冲句之后

    assert _pump(qt_app, lambda: len(played) == 3), f"三句都应播出，实际：{played}"
    assert played.index("缓冲中的话") < played.index("后来的静态句")


def test_parallel_tool_asides_play_in_order(qt_app, bubble, monkeypatch):
    """工具 aside 与流式台词混排时保持先后顺序，不被后来者顶替。"""
    played: list = []
    original = bubble._play_text

    def _record(text, duration, parent_pos=None):
        played.append(text)
        original(text, duration, parent_pos)

    monkeypatch.setattr(bubble, "_play_text", _record)

    bubble.show_text("第一句", 10, _PARENT_POS)
    bubble.show_text("aside 一", 10)
    bubble.show_text("aside 二", 10)

    assert _pump(qt_app, lambda: len(played) == 3), f"三句都应播出，实际：{played}"
    assert played == ["第一句", "aside 一", "aside 二"]
