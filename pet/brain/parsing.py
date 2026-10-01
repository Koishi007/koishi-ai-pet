"""LLM 输出解析：把文本行 / 流式 chunk 收敛成 BehaviorOutput。

流式与非流式共用同一套行标签、字段解析与动作校验：改解析规则只需动这里。
"""

import queue
import threading
import time
from datetime import datetime
import logging
import re
from typing import Callable, Optional, Protocol

from pet.action.registry import ACTION_NAMES
from pet.brain.output import ActionStep, BehaviorOutput, CancelledError

logger = logging.getLogger(__name__)

# 行首标签真源：标签 -> 正则。断言式匹配（不吃分隔空白），
# 这样流式路径把标签后的首个空格当内容转发、整行路径取值时再统一去掉一个空格
_TAGS: dict[str, re.Pattern] = {
    "speech": re.compile(r"^\s*Speech:(?=\s|$)", re.IGNORECASE),
    "action": re.compile(r"^\s*Action:(?=\s|$)", re.IGNORECASE),
    "summary": re.compile(r"^\s*Summary:(?=\s|$)", re.IGNORECASE),
    "memory": re.compile(r"^\s*Memory:(?=\s|$)", re.IGNORECASE),
    "emotion": re.compile(r"^\s*Emotion:(?=\s|$)", re.IGNORECASE),
    "mood": re.compile(r"^\s*Mood:(?=\s|$)", re.IGNORECASE),
    "vitals": re.compile(r"^\s*Vitals:(?=\s|$)", re.IGNORECASE),
}

# 行首最多缓冲多少字符：超过仍未命中标签就按未知行处理，不再等标签
_TAG_SCAN_LIMIT = 8

# 静音标记：Speech 行取这些值时不产出语音
_SILENCE_MARKERS = ("none", "", "null", "无")

# 增量字段白名单：Mood / Vitals 各自可出现的键
_MOOD_FIELDS = ("affection", "joy", "sanity")
_VITALS_FIELDS = ("satiety", "energy")

# 「字段±值」增量：字段名后允许空格，数字必须带符号
_DELTA = re.compile(r"([A-Za-z]+)\s*([+-]\s*\d+)")


class BehaviorSink(Protocol):
    """解析依赖的编排方能力：进展上报。"""

    def note_progress(self) -> None: ...


def parse_deltas(raw: str, fields: tuple[str, ...]) -> Optional[dict]:
    """解析字段增量：白名单外的字段与不带符号的数字都忽略。"""
    deltas = {}
    for match in _DELTA.finditer(raw):
        key = match.group(1).lower()
        if key in fields:
            deltas[key] = float(match.group(2).replace(" ", ""))
    return deltas or None


def parse_mood_line(raw: str) -> Optional[dict]:
    """解析 Mood 行，格式: affection+5 joy+3 sanity-2"""
    return parse_deltas(raw, _MOOD_FIELDS)


def parse_vitals_line(raw: str) -> Optional[dict]:
    """解析 Vitals 行，格式: satiety+15 energy-3（仅生理参数）"""
    return parse_deltas(raw, _VITALS_FIELDS)


def parse_action_line(raw: str, resolve=ACTION_NAMES.__contains__) -> Optional[ActionStep]:
    """解析 Action 行；动作名不在白名单里返回 None。

    resolve 决定白名单来源（默认 ACTION_NAMES），便于按当前注册表校验。
    """
    parts = raw.split()
    if not parts:
        return None
    name = parts[0].lower()
    if not resolve(name):
        logger.warning(f"[Behavior]   ⚠ unknown action: {name!r}, skipped")
        return None
    args: list = []
    kwargs: dict = {}
    for token in parts[1:]:
        if "=" in token:
            k, v = token.split("=", 1)
            try:
                v = int(v)
            except ValueError:
                pass
            kwargs[k] = v
        else:
            try:
                token = int(token)
            except ValueError:
                pass
            args.append(token)
    return ActionStep(name, tuple(args), kwargs)


class LineTagger:
    """行标签状态机，两种用法共用同一套标签规则。

    - `feed()` 逐字符判行首（流式实时），命中 `Speech:` 时把该行语音交给 on_chunk；
    - `finish()` 判整行（非流式与流式收尾），再由 `raw_value()` 取值。
    """

    def __init__(self, on_chunk=None, on_stream_end=None):
        self._on_chunk = on_chunk
        self._on_stream_end = on_stream_end
        self._speech_started = False  # 本轮已发过 Speech，再遇 Speech 即分行
        self.reset()

    def reset(self):
        """换行或取消后复位，下一行重新识别标签。"""
        self._buffer = ""
        self._tag: Optional[str] = None
        self._eat_sep = False  # 下一个字符若是空白，则是标签后的分隔空格

    @property
    def tag(self) -> Optional[str]:
        """当前行的标签；尚未判定为 None，"other" 表示未知行。"""
        return self._tag

    @property
    def line(self) -> str:
        """当前行已缓冲的内容。"""
        return self._buffer

    def feed(self, char: str) -> bool:
        """喂入一个字符；正在流式播报语音时返回 False，由调用方停止转发。"""
        self._buffer += char
        if self._tag is None:
            return self._scan()
        if self._tag == "speech":
            # 标签命中时若还没见到分隔空格，它会在这一字符位到达
            if self._eat_sep:
                self._eat_sep = False
                if char in (" ", "\t"):
                    return False
            self._emit(char)
        return False

    def finish(self, line: str) -> Optional[str]:
        """整行判定：命中标签返回标签名，未命中返回 None。"""
        self._buffer = line
        self._tag = None
        stripped = line.strip()
        if stripped:
            for tag, pattern in _TAGS.items():
                if pattern.match(stripped):
                    self._tag = tag
                    break
        return self._tag

    def raw_value(self, line: str) -> str:
        """取标签后的值：按当前标签的正则切分整行，再去掉一个分隔空格。

        标签正则不吃空白，`Speech: 你好` 切出 `' 你好'`，去掉首个空格即值本身；
        值里多出来的空格保留原样。
        """
        pattern = _TAGS.get(self._tag or "")
        if pattern is None:
            return ""
        parts = pattern.split(line.strip(), maxsplit=1)
        if len(parts) < 2:
            return ""
        return parts[1][1:] if parts[1].startswith((" ", "\t")) else parts[1]

    def _scan(self) -> bool:
        """行首识别标签；返回是否已接管（True 表示调用方不用再转发字符）。"""
        stripped = self._buffer.lstrip()
        for tag, pattern in _TAGS.items():
            match = pattern.match(stripped)
            if not match:
                continue
            self._tag = tag
            piece = stripped[match.end():]
            if tag == "speech":
                if self._speech_started and self._on_stream_end:
                    self._on_stream_end()
                self._speech_started = True
                # 分隔空格：随标签后的剩余文本一起到达就当场丢掉，
                # 尚未到达（命中处正好是行尾）就留给下一个字符丢掉
                if piece[:1] in (" ", "\t"):
                    piece = piece[1:]
                    self._eat_sep = False
                else:
                    self._eat_sep = not piece
                self._emit(piece)
                return False
            # 其余字段不转发内容，只吃掉标签本身与紧跟的一个分隔空格
            if piece[:1] in (" ", "\t"):
                piece = piece[1:]
            return not piece
        # 长到该作罢就按未知行处理，不再等标签
        if len(stripped) >= _TAG_SCAN_LIMIT:
            self._tag = "other"
        return True

    def _emit(self, text: str) -> None:
        """把语音片段交给 on_chunk。"""
        if not text:
            return
        if self._tag == "speech" and self._on_chunk:
            self._on_chunk(text)


class BehaviorParser:
    """行为解析器：整段解析与流式解析共用同一套标签与字段规则。"""

    def __init__(self, sink: BehaviorSink, resolve=ACTION_NAMES.__contains__):
        self._sink = sink
        self._resolve = resolve

    def parse_behavior(self, content: str) -> BehaviorOutput:
        """整段解析（非流式路径）。"""
        acc = _new_acc()
        tagger = LineTagger()
        for line in content.split("\n"):
            if not line.strip():
                continue
            tagger.finish(line)
            self.consume_line(tagger, line, acc)

        if not acc["actions"]:
            acc["actions"].append(ActionStep("sit", kwargs={"duration": 5}))
        return _build_output(acc, speech_streamed=False)

    def consume_line(self, tagger: LineTagger, line: str, acc: dict):
        """收下一整行：按标签把值写进累加器。"""
        tag = tagger.tag
        if tag is None:
            return
        value = tagger.raw_value(line)
        if not value and tag not in ("action", "speech"):
            return
        if tag == "action":
            step = parse_action_line(value, self._resolve)
            if step:
                acc["actions"].append(step)
        elif tag == "speech":
            if value.lower() not in _SILENCE_MARKERS:
                acc["speech_parts"].append(value)
        else:
            acc[tag].append(value)

    def collect_stream(self, stream, total_timeout: float, tag: str = "",
                       cancel_check: Optional[Callable[[], bool]] = None,
                       on_chunk=None, on_stream_end=None) -> tuple[str, dict]:
        """消费流，返回 (还原后的原始输出文本, tool_calls 表)。

        语音片段在识别到 `Speech:` 时立刻转给 on_chunk，
        同一轮里再次出现 `Speech:` 时触发 on_stream_end（让气泡分行显示）。
        """
        t0 = time.perf_counter()
        tagger = LineTagger(on_chunk=on_chunk, on_stream_end=on_stream_end)
        acc = _new_acc()
        tool_calls_map: dict = {}
        finish_reason = None
        stream_usage = None

        for chunk in self.iter_stream_with_timeout(stream, total_timeout, cancel_check=cancel_check):
            # usage-only chunk（choices 为空，仅含 usage）
            if not chunk.choices:
                if hasattr(chunk, "usage") and chunk.usage:
                    stream_usage = chunk.usage
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta

            if delta.content:
                for char in delta.content:
                    if char in ("\n", "\r"):
                        self.consume_line(tagger, tagger.line, acc)
                        tagger.reset()
                    else:
                        tagger.feed(char)

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc_delta.id:
                        tool_calls_map[idx]["id"] = tc_delta.id
                    if tc_delta.function and tc_delta.function.name:
                        tool_calls_map[idx]["name"] = tc_delta.function.name
                    if tc_delta.function and tc_delta.function.arguments:
                        tool_calls_map[idx]["arguments"] += tc_delta.function.arguments

        if tagger.line.strip():
            self.consume_line(tagger, tagger.line, acc)

        _log_stream_done(tag, time.perf_counter() - t0, finish_reason, stream_usage)
        raw = build_raw_text(acc)
        logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior] === LLM RESPONSE ({tag}) ===")
        logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior]   raw: {raw}")
        return raw, tool_calls_map

    def iter_stream_with_timeout(self, stream, total_timeout: float,
                                 cancel_check: Optional[Callable[[], bool]] = None):
        """读流并逐 chunk 产出；总超时到点或取消时抛错。

        迭代放在单独线程里：SDK 的读超时只覆盖单次 recv，
        服务端持续发心跳时整体仍可能无限拖长。
        """
        chunk_queue: queue.Queue = queue.Queue()
        stop_event = threading.Event()
        exception_holder: list = [None]

        def _iter_thread():
            try:
                for chunk in stream:
                    if stop_event.is_set():
                        return
                    chunk_queue.put(('chunk', chunk))
                chunk_queue.put(('done', None))
            except Exception as e:
                exception_holder[0] = e
                try:
                    chunk_queue.put(('error', None))
                except Exception:
                    pass

        t = threading.Thread(target=_iter_thread, daemon=True, name="stream-iter")
        t.start()

        deadline = time.monotonic() + total_timeout
        try:
            while True:
                # 协作式取消：被新请求抢占时尽快退出，释放资源
                if cancel_check and cancel_check():
                    stop_event.set()
                    raise CancelledError("流式调用被新请求取消")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stop_event.set()
                    raise TimeoutError(f"流式调用总超时 ({total_timeout}s)，已放弃等待")
                try:
                    kind, value = chunk_queue.get(timeout=min(remaining, 1.0))
                except queue.Empty:
                    continue
                if kind == 'done':
                    break
                if kind == 'error':
                    if exception_holder[0]:
                        raise exception_holder[0]
                    break
                self._sink.note_progress()
                yield value
        finally:
            stop_event.set()
            t.join(timeout=3)
            # 关闭被放弃的流式响应，中断挂住的 HTTP 读，释放连接
            close = getattr(stream, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass


def _new_acc() -> dict:
    """新建字段累加器：各字段都按出现顺序累积。"""
    return {"actions": [], "speech_parts": [], "summary": [],
            "memory": [], "emotion": [], "mood": [], "vitals": []}


class _NullSink:
    """不需要进展上报的调用方（整段解析）用的空实现。"""

    def note_progress(self) -> None:
        pass


def parse_behavior(content: str, resolve=ACTION_NAMES.__contains__) -> BehaviorOutput:
    """整段解析一次 LLM 输出（非流式路径）。"""
    return BehaviorParser(_NullSink(), resolve).parse_behavior(content)


def parse_stream_chunks(stream, total_timeout: float, sink: BehaviorSink, tag: str = "",
                        cancel_check: Optional[Callable[[], bool]] = None,
                        on_chunk=None, on_stream_end=None) -> tuple[str, dict]:
    """消费一条流式响应（流式路径），返回 (原始输出文本, tool_calls 表)。"""
    return BehaviorParser(sink).collect_stream(
        stream, total_timeout, tag=tag, cancel_check=cancel_check,
        on_chunk=on_chunk, on_stream_end=on_stream_end)


def _build_output(acc: dict, speech_streamed: bool) -> BehaviorOutput:
    return BehaviorOutput(
        actions=acc["actions"],
        speech=" ".join(acc["speech_parts"]) if acc["speech_parts"] else None,
        speech_parts=list(acc["speech_parts"]),
        speech_streamed=speech_streamed,
        summary=acc["summary"][0] if acc["summary"] else None,
        memory_line=acc["memory"][0] if acc["memory"] else None,
        emotion=", ".join(acc["emotion"]) if acc["emotion"] else None,
        mood_deltas=parse_mood_line(acc["mood"][0]) if acc["mood"] else None,
        vitals_deltas=parse_vitals_line(acc["vitals"][0]) if acc["vitals"] else None,
    )


def build_raw_text(acc: dict) -> str:
    """把累加器还原成原始输出文本，用于日志与工具轮次的中间内容。"""
    lines = []
    if acc["summary"]:
        lines.append(f"Summary: {acc['summary'][0]}")
    if acc["emotion"]:
        lines.append(f"Emotion: {', '.join(acc['emotion'])}")
    lines += [f"Speech: {s}" for s in acc["speech_parts"]]
    lines += [
        f"Action: {a.name} {' '.join(map(str, a.args))} {' '.join(f'{k}={v}' for k, v in a.kwargs.items())}".strip()
        for a in acc["actions"]
    ]
    if acc["memory"]:
        lines.append(f"Memory: {acc['memory'][0]}")
    if acc["mood"]:
        lines.append(f"Mood: {acc['mood'][0]}")
    if acc["vitals"]:
        lines.append(f"Vitals: {acc['vitals'][0]}")
    return "\n".join(lines)


def _log_stream_done(tag: str, elapsed: float, finish_reason, stream_usage) -> None:
    usage_log = f", finish_reason: {finish_reason}"
    if stream_usage:
        usage_log += (f", tokens: prompt={stream_usage.prompt_tokens}, "
                      f"completion={stream_usage.completion_tokens}, total={stream_usage.total_tokens}")
    logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior] stream completed in {elapsed:.2f}s ({tag}){usage_log}")
