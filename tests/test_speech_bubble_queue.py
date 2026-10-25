"""气泡队列：多轮工具调用下「一句一泡、不丢句、不叠句」。

这几个回归点不看成品 UI 也能判定：句子是否独立成泡、句子之间的过渡期是否会被抢占、
抢占时已输出的内容是否保留。宿主窗口用几何桩替代，不依赖真实桌面；
播放进度由测试手动驱动（打字、收尾、过渡定时器），不依赖真实时钟与事件循环调度。
"""

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
def bubble(qt_app):
    b = SpeechBubble()
    yield b
    b.flush_pending()


def _fire(timer):
    """手动触发单次定时器：先停表，等价于真实到期。"""
    timer.stop()
    timer.timeout.emit()


def _finish_typing(bubble):
    """手动打完当前这句话（含收尾），不等真实打字定时器。"""
    while bubble._char_queue or bubble._stream_ending:
        bubble._type_next_char()


def _advance(bubble):
    """播完下一句：收尾定时器 → 过渡定时器 → 取队首 → 打完。"""
    _fire(bubble._hide_timer)
    _fire(bubble._gap_timer)
    _finish_typing(bubble)


def _finished_sentences(bubble, monkeypatch) -> list:
    """记录每一句「打完字」的时刻所见文本：一句只记录一次。"""
    seen: list = []
    original = bubble._finish_stream

    def _recording():
        seen.append(bubble.text())
        original()

    monkeypatch.setattr(bubble, "_finish_stream", _recording)
    return seen


def test_missing_start_stream_starts_a_new_bubble(bubble, monkeypatch):
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

    _finish_typing(bubble)
    _advance(bubble)
    assert seen == ["第一轮的话", "第二轮的话"]


def test_append_alone_shows_and_then_hides(bubble):
    """完全没有 start_stream 时也能显示一句话（调用方忘发首帧也不丢输出）。"""
    bubble.append_stream("单独一句")
    bubble.end_stream(10)

    assert bubble.isVisible()
    _finish_typing(bubble)
    assert bubble.text() == "单独一句"
    _fire(bubble._hide_timer)
    assert not bubble.isVisible()


def test_queued_sentence_is_not_preempted(bubble, monkeypatch):
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

    _finish_typing(bubble)
    _fire(bubble._hide_timer)  # 收尾到期 → 进入过渡期
    assert bubble._gap_timer.isActive(), "过渡期应处于占用状态"

    bubble.show_text("第三句", 10)  # 过渡期到达 → 排在队尾

    _fire(bubble._gap_timer)  # 过渡结束 → 播第二句
    _finish_typing(bubble)
    _advance(bubble)
    assert played == ["第一句", "第二句", "第三句"]


def test_flush_pending_drops_queue_and_buffer(bubble):
    """窗口隐藏时必须连队列一起清：积压台词不能再弹出来。"""
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
    _fire(bubble._hide_timer)
    _fire(bubble._gap_timer)
    assert not bubble.isVisible()


def test_finish_pending_keeps_received_speech(bubble, monkeypatch):
    """抢占时已收到的内容必须完整播出，不能丢句。"""
    seen = _finished_sentences(bubble, monkeypatch)
    played: list = []
    original = bubble._play_text

    def _record(text, duration, parent_pos=None):
        played.append(text)
        original(text, duration, parent_pos)

    monkeypatch.setattr(bubble, "_play_text", _record)

    bubble.start_stream(_PARENT_POS)
    bubble.append_stream("已经输出的一句话")
    bubble.end_stream(10)
    bubble.show_text("已经输出的旁白", 10)  # 气泡忙 → 入队
    bubble.finish_pending()

    _finish_typing(bubble)
    _advance(bubble)
    assert seen == ["已经输出的一句话", "已经输出的旁白"]
    assert played == ["已经输出的旁白"]


def test_finish_pending_flushes_buffered_sentence(bubble):
    """气泡忙时攒下的缓冲句，抢占后也要入队，不能丢。"""
    bubble.show_text("占位句", 500, _PARENT_POS)
    bubble.start_stream(_PARENT_POS)  # 忙 → 缓冲
    bubble.append_stream("缓冲中的输出")
    bubble.finish_pending()

    assert bubble._buffering is False
    assert [item["text"] for item in bubble._speech_queue] == ["缓冲中的输出"]

    _finish_typing(bubble)
    _advance(bubble)
    assert bubble.text() == "缓冲中的输出"


def test_new_stream_plays_after_pending_finish(bubble, monkeypatch):
    """抢占后的新回复排在旧内容之后：不打断旧句，也不丢新句。"""
    seen = _finished_sentences(bubble, monkeypatch)

    bubble.show_text("旧句", 10, _PARENT_POS)
    bubble.finish_pending()
    bubble.start_stream(_PARENT_POS)  # 新回复，气泡忙 → 缓冲
    bubble.append_stream("新回复")
    bubble.end_stream(10)

    _finish_typing(bubble)
    _advance(bubble)
    assert seen == ["旧句", "新回复"]


def test_hide_clears_pending_so_nothing_pops_up_later(bubble):
    """宠物窗口隐去后，积压的句子不能再把独立气泡弹出来。"""
    bubble.show_text("正在播", 500, _PARENT_POS)
    bubble.show_text("排队中", 500)

    bubble.hide()

    assert bubble._speech_queue == []
    _fire(bubble._hide_timer)
    assert not bubble.isVisible()


def test_queue_is_capped_so_backlog_cannot_pile_up(bubble):
    """多轮工具链会攒下大量 aside，超出上限丢最旧的而不是无限积压。"""
    bubble.show_text("占位", 1000, _PARENT_POS)  # 长时间占用，后续统统入队
    extra = 6
    for i in range(speech_bubble_mod._QUEUE_MAX + extra):
        bubble.show_text(f"台词{i}", 10)

    queued = [item["text"] for item in bubble._speech_queue]
    assert len(queued) == speech_bubble_mod._QUEUE_MAX
    assert queued[-1] == f"台词{speech_bubble_mod._QUEUE_MAX + extra - 1}"  # 保留最新的


def test_buffered_sentence_survives_queued_playback(bubble, monkeypatch):
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
    bubble.show_text("后来的静态句", 10)         # 排在缓冲句之后

    _finish_typing(bubble)
    _advance(bubble)
    _advance(bubble)
    assert played.index("缓冲中的话") < played.index("后来的静态句")
    assert len(played) == 3


def test_discard_stream_clears_partial_sentence(bubble):
    """修复轮整段重写：已流出的半句与未发送的 chunk 一并丢掉。"""
    bubble.start_stream(_PARENT_POS)
    bubble.append_stream("半句台词")

    bubble.discard_stream()

    assert not bubble.isVisible()
    assert bubble.text() == ""
    assert bubble._char_queue == []
    assert bubble._stream_open is False

    # 修复轮重新输出时另起一句，不接在半句后面
    bubble.start_stream(_PARENT_POS)
    bubble.append_stream("补写后的话")
    _finish_typing(bubble)
    assert bubble.text() == "补写后的话"


def test_discard_stream_keeps_earlier_sentence(bubble):
    """气泡在播更早的句子时只丢缓冲，不打断正在播的那句。"""
    bubble.show_text("更早的句子", 1000, _PARENT_POS)
    _finish_typing(bubble)
    bubble.start_stream(_PARENT_POS)  # 忙 → 缓冲模式
    bubble.append_stream("半句台词")

    bubble.discard_stream()

    assert bubble.isVisible()
    assert bubble.text() == "更早的句子"
    assert bubble._incoming_chunks == []
    assert bubble._buffering is False


def test_parallel_tool_asides_play_in_order(bubble, monkeypatch):
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

    _finish_typing(bubble)
    _advance(bubble)
    _advance(bubble)
    assert played == ["第一句", "aside 一", "aside 二"]
