"""与 AI 通信，解析响应为动作序列。"""

import queue
import random
import re
import time
from datetime import datetime
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

from openai import BadRequestError

from pet.brain.base import BrainMixin
from pet.brain.context_builder import ContextBuilder
from pet.brain.llm_client import LLMClient
from pet.brain.llm_stats import LlmStats
from pet.action.registry import ACTION_NAMES
from pet.config import config
from pet.brain.llm_retry import llm_retry
from pet.tools.registry import TOOL_REGISTRY

logger = logging.getLogger(__name__)

# 服务商是否不支持 thinking 参数（首次 400 后自动降级，后续请求不再携带）
_thinking_unsupported = False


@dataclass
class ActionStep:
    name: str
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)


@dataclass
class BehaviorOutput:
    actions: list = field(default_factory=list)
    speech: Optional[str] = None
    speech_parts: list = field(default_factory=list)
    speech_streamed: bool = False
    summary: Optional[str] = None
    memory_line: Optional[str] = None
    emotion: Optional[str] = None
    mood_deltas: Optional[dict] = None  # {"affection": ±值, "joy": ±值, "sanity": ±值}
    vitals_deltas: Optional[dict] = None  # {"satiety": ±值, "energy": ±值}


class Behavior(BrainMixin):

    def __init__(self, memory_store=None, screen_reader=None, vitals=None, mood=None):
        db_path = memory_store._db_path if memory_store else None
        super().__init__(db_path=db_path)
        self._llm = LLMClient()
        self._lock = threading.RLock()

        self._actions = ACTION_NAMES
        self.ctx = ContextBuilder(
            memory_store=memory_store, screen_reader=screen_reader,
            vitals=vitals, mood=mood, brain_mixin=self,
        )
        self.llm_stats = LlmStats()

        # 工具动态激活
        self._active_tool_groups: set[str] = {"default"}

        t = datetime.now().strftime("%H:%M:%S")
        client_type = "None (local)" if not self._llm else f"{type(self._llm.client).__name__}(model={self._llm.model})"
        logger.info(f"[{t}] [Behavior] init: {len(self._actions)} actions, client={client_type}")

    def rebuild_client(self):
        """运行时重建 LLM 客户端（设置界面修改连接配置后调用）。"""
        with self._lock:
            self._llm.rebuild()
        client_type = "None (local)" if not self._llm else f"{type(self._llm.client).__name__}(model={self._llm.model})"
        logger.info(f"[Behavior] rebuild_client: {client_type}")

    @property
    def has_vision(self) -> bool:
        return self._llm.has_vision

    def _build_tools_param(self):
        """根据当前已激活的分组构建 tools 参数，仅包含激活组中的工具。"""
        if not config.LLM_TOOLS_ENABLED:
            return None
        return TOOL_REGISTRY.to_openai_tools(groups=self._active_tool_groups)

    def _activate_tool_groups_from_search(self, matches: list[dict]):
        """根据搜索返回的匹配结果激活分组。"""
        for item in matches:
            grp = item.get("group")
            if grp and grp != "default" and grp not in self._active_tool_groups:
                self._active_tool_groups.add(grp)
                logger.info(f"[Behavior] activated tool group: {grp} (from search result)")

    def _activate_groups_from_keyword(self, keyword: str):
        """搜索关键词匹配分组名，自动激活命中的分组。"""
        kw = keyword.strip().lower() if keyword else ""
        if not kw:
            return
        for grp in TOOL_REGISTRY.get_groups():
            if grp == "default":
                continue
            if kw in grp.lower() and grp not in self._active_tool_groups:
                self._active_tool_groups.add(grp)
                logger.info(f"[Behavior] activated tool group: {grp} (keyword: {kw})")

    def reset_active_tool_groups(self):
        """互动结束时重置激活状态，仅保留 default 组。"""
        self._active_tool_groups = {"default"}
        logger.debug("[Behavior] reset active tool groups")
    
    def autonomous_decide(self, context: str = "", screenshot: bool = True) -> BehaviorOutput:
        t = datetime.now().strftime("%H:%M:%S")
        if not self._llm:
            return self._decide_local()

        messages = self.ctx.build_autonomous_decide(context, screenshot=screenshot)
        is_vision = isinstance(messages[1]["content"], list)
        tag = "autonomous_vision" if is_vision else "autonomous_non_vision"
        ctx_preview = context[:60] if context else "(empty)"
        logger.info(f"[{t}] [Behavior] === LLM REQUEST ({tag}) ===")
        logger.info(f"[{t}] [Behavior]   model: {self._llm.model}, context({len(context)} chars): \"{ctx_preview}\"")
        logger.info(f"[{t}] [Behavior]   history: {self.context_count()} entries")

        return self._retry_if_empty(self._call_llm_and_parse, messages, messages[0]["content"], tag, tag=tag, max_tokens=config.LLM_MAX_TOKENS_AUTONOMOUS)

    def autonomous_decide_stream(self, context: str = "", screenshot: bool = True,
                      on_chunk=None, on_stream_end=None) -> BehaviorOutput:
        if not self._llm:
            return self._decide_local()
        if not self._lock.acquire(timeout=0.5):
            logger.warning("[Behavior] autonomous_decide_stream: busy, skip")
            return self._decide_local()
        try:
            messages = self.ctx.build_autonomous_decide(context, screenshot=screenshot)
            is_vision = isinstance(messages[1]["content"], list)
            tag = "autonomous_decide_vision_stream" if is_vision else "autonomous_decide_stream"
            return self._retry_if_empty(self._stream_and_build_output, messages, tag=tag, on_chunk=on_chunk, on_stream_end=on_stream_end, max_tokens=config.LLM_MAX_TOKENS_AUTONOMOUS)
        finally:
            self._lock.release()

    def interact_decide(self, event_hint: str) -> BehaviorOutput:
        if not self._llm:
            return self._interact_decide_local(event_hint)
        messages = self.ctx.build_interact(event_hint)
        return self._retry_if_empty(self._call_llm_and_parse, messages, messages[0]["content"], "interact", tag="interact", max_tokens=config.LLM_MAX_TOKENS_INTERACT)

    def interact_decide_stream(self, event_hint: str,
                               on_chunk=None, on_stream_end=None) -> BehaviorOutput:
        if not self._llm:
            return self._interact_decide_local(event_hint)
        if not self._lock.acquire(timeout=2):
            logger.warning("[Behavior] interact_decide_stream: busy, skip")
            return self._interact_decide_local(event_hint)
        try:
            messages = self.ctx.build_interact(event_hint)
            return self._retry_if_empty(self._stream_and_build_output, messages, tag="interact", on_chunk=on_chunk, on_stream_end=on_stream_end, max_tokens=config.LLM_MAX_TOKENS_INTERACT)
        finally:
            self._lock.release()



    def chat_decide(self, user_message: str, context: str = "", screenshot: bool = True) -> BehaviorOutput:
        t = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{t}] [Behavior] chat_decide(msg={user_message[:50]}, ctx={context[:30]})")

        if not self._llm:
            return self._chat_decide_local(user_message)

        messages = self.ctx.build_chat_decide(user_message, context, screenshot=screenshot)
        is_vision = isinstance(messages[1]["content"], list)
        tag = "chat_vision" if is_vision else "chat_non_vision"
        logger.info(f"[{t}] [Behavior] === LLM REQUEST ({tag}) ===")
        logger.info(f"[{t}] [Behavior]   model: {self._llm.model}")
        logger.info(f"[{t}] [Behavior]   history: {self.context_count()} entries")

        return self._retry_if_empty(self._call_llm_and_parse, messages, messages[0]["content"], tag, tag=tag, max_tokens=config.LLM_MAX_TOKENS_CHAT)

    def chat_decide_stream(self, user_message: str, context: str, screenshot: bool = True,
                           on_chunk=None, on_stream_end=None) -> BehaviorOutput:
        if not self._llm:
            return self._chat_decide_local(user_message)
        if not self._lock.acquire(timeout=5):
            logger.warning("[Behavior] chat_decide_stream: busy, timeout")
            return BehaviorOutput(
                actions=[ActionStep("look_around", kwargs={"duration": 5})],
                speech="嚎……等一下，我还在想……",
            )
        try:
            messages = self.ctx.build_chat_decide(user_message, context, screenshot=screenshot)
            is_vision = isinstance(messages[1]["content"], list)
            tag = "chat_decide_vision_stream" if is_vision else "chat_decide_stream"
            return self._retry_if_empty(self._stream_and_build_output, messages, tag=tag, on_chunk=on_chunk, on_stream_end=on_stream_end, max_tokens=config.LLM_MAX_TOKENS_CHAT)
        finally:
            self._lock.release()

    def _retry_if_empty(self, fn, *args, tag="", **kwargs) -> BehaviorOutput:
        """调用 fn 并在结果为空时重试一次。"""
        result = fn(*args, **kwargs)
        if not result.actions and not result.speech:
            logger.warning(f"[Behavior] empty LLM response (no actions, no speech), retrying once ({tag})")
            result = fn(*args, **kwargs)
        return result

    @staticmethod
    def _apply_thinking_param(kwargs: dict):
        """按配置注入思考模式参数"""
        if _thinking_unsupported:
            return
        state = "disabled" if config.LLM_THINKING_DISABLED else "enabled"
        kwargs.setdefault("extra_body", {})["thinking"] = {"type": state}

    def _create_completion(self, kwargs: dict):
        """发起补全请求；若服务商不支持 thinking 参数（400），自动移除后重试一次。"""
        global _thinking_unsupported
        try:
            return self._llm.client.chat.completions.create(**kwargs)
        except BadRequestError:
            if "extra_body" not in kwargs:
                raise
            logger.warning("[Behavior] 请求被拒绝(400)，可能不支持 thinking 参数，自动降级重试")
            kwargs.pop("extra_body", None)
            resp = self._llm.client.chat.completions.create(**kwargs)
            _thinking_unsupported = True
            return resp

    @llm_retry(tag="Behavior")
    def _llm_call(self, messages: list, max_tokens: int = 4000, tools: list = None):
        self.llm_stats.increment()
        t0 = time.perf_counter()
        kwargs = {"model": self._llm.model, "messages": messages, "max_tokens": max_tokens, "temperature": config.LLM_TEMPERATURE}
        if tools:
            kwargs["tools"] = tools
        self._apply_thinking_param(kwargs)
        resp = self._create_completion(kwargs)
        elapsed = time.perf_counter() - t0
        usage = resp.usage
        if usage:
            logger.info(f"[Behavior] LLM call completed in {elapsed:.2f}s, "
                        f"tokens: prompt={usage.prompt_tokens}, completion={usage.completion_tokens}, total={usage.total_tokens}")
        else:
            logger.info(f"[Behavior] LLM call completed in {elapsed:.2f}s")
        return resp

    def _llm_call_stream(self, messages: list, max_tokens: int = 4000, tools: list = None):
        self.llm_stats.increment()
        from pet.brain.llm_retry import llm_stream_with_retry
        kwargs = {"model": self._llm.model, "messages": messages, "max_tokens": max_tokens,
                   "temperature": config.LLM_TEMPERATURE, "stream": True,
                   "stream_options": {"include_usage": True}}
        if tools:
            kwargs["tools"] = tools
        self._apply_thinking_param(kwargs)
        return llm_stream_with_retry(
            lambda: self._create_completion(kwargs),
            tag="Behavior.stream",
        )

    def _log_prompt_size(self, messages: list, tag: str):
        """计算并打印 prompt 规模：文本字符数 + 图片 base64 大小。"""
        text_chars = 0
        image_count = 0
        image_bytes = 0
        image_fmt = ""
        for m in messages:
            content = m["content"]
            if isinstance(content, str):
                text_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_chars += len(part.get("text", ""))
                    elif isinstance(part, dict) and part.get("type") == "image_url":
                        image_count += 1
                        url = part.get("image_url", {}).get("url", "")
                        if "," in url:
                            image_bytes += len(url.split(",", 1)[1])
                            # 从 data:image/jpeg;base64,... 中提取格式
                            if not image_fmt and "data:image/" in url:
                                fmt_start = url.find("data:image/") + len("data:image/")
                                fmt_end = url.find(";", fmt_start)
                                if fmt_end > fmt_start:
                                    image_fmt = url[fmt_start:fmt_end]
        t = datetime.now().strftime("%H:%M:%S")
        parts = [f"prompt_chars: {text_chars}"]
        if image_count:
            parts.append(f"images: {image_count}{' (' + image_fmt + ')' if image_fmt else ''} ({image_bytes // 1024}KB base64)")
        logger.info(f"[{t}] [Behavior]   {', '.join(parts)} ({tag})")

    def _call_llm_and_parse(self, messages: list, system_content: str, tag: str, max_tokens: int = 4000) -> BehaviorOutput:
        t = datetime.now().strftime("%H:%M:%S")
        self._apply_cache_control(messages)
        self._dump_context(tag, messages)
        self._log_prompt_size(messages, tag)
        try:
            tools_param = self._build_tools_param()
            resp = self._llm_call(messages, max_tokens=max_tokens, tools=tools_param)
            msg = resp.choices[0].message
            content = msg.content or ""
            logger.info(f"[{t}] [Behavior] === LLM RESPONSE ({tag}) ===")
            logger.info(f"[{t}] [Behavior]   finish_reason: {resp.choices[0].finish_reason}")
            logger.info(f"[{t}] [Behavior]   raw: {content}")

            # 处理 tool_calls
            if msg.tool_calls:
                tool_calls_map = {}
                for i, tc in enumerate(msg.tool_calls):
                    tool_calls_map[i] = {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                return self._handle_tool_calls(
                    messages, tool_calls_map, content,
                    tag=tag, max_tokens=max_tokens,
                    max_rounds=config.LLM_TOOL_MAX_ROUNDS,
                    speech_streamed=False,
                )

            result = self._parse_behavior(content)
            logger.info(f"[{t}] [Behavior]   parsed -> {result}")
            return result
        except Exception as e:
            logger.exception(f"[{t}] [Behavior]   {tag} LLM call failed: {type(e).__name__}: {e}")
            logger.warning(f"[{t}] [Behavior]   falling back to local")
            return self._decide_local()

    def _iterate_stream_with_timeout(self, stream, total_timeout: float):
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
                yield value
        finally:
            stop_event.set()
            t.join(timeout=3)

    def _stream_and_build_output(self, messages: list, on_chunk=None, on_stream_end=None, tag: str = "", max_tokens: int = 4000) -> BehaviorOutput:
        self._apply_cache_control(messages)
        self._dump_context(tag, messages)
        self._log_prompt_size(messages, tag)
        t0 = time.perf_counter()
        try:
            tools_param = self._build_tools_param()
            stream = self._llm_call_stream(messages, max_tokens=max_tokens, tools=tools_param)

            buffer = ""
            actions = []
            speech_parts = []
            summary_holder = []
            memory_holder = []
            emotion_holder = []
            mood_holder = []
            vitals_holder = []
            speech_streamed = False
            line_type = None
            speech_prefix_consumed = False
            accumulated_tool_calls = {}  # {index: {"id":..., "name":..., "arguments":...}}

            finish_reason = None
            stream_usage = None

            for chunk in self._iterate_stream_with_timeout(stream, config.LLM_STREAM_TIMEOUT):
                # usage-only chunk（choices 为空，仅含 usage）
                if not chunk.choices:
                    if hasattr(chunk, "usage") and chunk.usage:
                        stream_usage = chunk.usage
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta

                # 文本内容
                if delta.content:
                    delta_speech = ""
                    for char in delta.content:
                        if char in ("\n", "\r"):
                            if delta_speech and on_chunk:
                                on_chunk(delta_speech)
                                speech_streamed = True
                            delta_speech = ""
                            self._finish_line(buffer, actions, speech_parts, summary_holder, memory_holder, emotion_holder, mood_holder, vitals_holder)
                            buffer = ""
                            line_type = None
                            speech_prefix_consumed = False
                        else:
                            buffer += char
                            if line_type is None:
                                stripped = buffer.lstrip()
                                lower = stripped.lower()
                                if lower.startswith("speech:"):
                                    line_type = "speech"
                                    if speech_parts and on_stream_end:
                                        on_stream_end()
                                elif lower.startswith("action:"):
                                    line_type = "action"
                                elif lower.startswith("summary:"):
                                    line_type = "summary"
                                elif lower.startswith("memory:"):
                                    line_type = "memory"
                                elif lower.startswith("emotion:"):
                                    line_type = "emotion"
                                elif lower.startswith("mood:"):
                                    line_type = "mood"
                                elif lower.startswith("vitals:"):
                                    line_type = "vitals"
                                elif len(stripped) >= 8:
                                    line_type = "other"
                            if line_type == "speech":
                                stripped = buffer.lstrip()
                                if not speech_prefix_consumed:
                                    prefix = "Speech: "
                                    if len(stripped) > len(prefix):
                                        speech_prefix_consumed = True
                                        delta_speech += stripped[len(prefix):]
                                else:
                                    delta_speech += char
                    if delta_speech and on_chunk:
                        on_chunk(delta_speech)
                        speech_streamed = True

                # 工具调用增量
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in accumulated_tool_calls:
                            accumulated_tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc_delta.id:
                            accumulated_tool_calls[idx]["id"] = tc_delta.id
                        if tc_delta.function and tc_delta.function.name:
                            accumulated_tool_calls[idx]["name"] = tc_delta.function.name
                        if tc_delta.function and tc_delta.function.arguments:
                            accumulated_tool_calls[idx]["arguments"] += tc_delta.function.arguments

            if buffer.strip():
                self._finish_line(buffer, actions, speech_parts, summary_holder, memory_holder, emotion_holder, mood_holder, vitals_holder)

            elapsed = time.perf_counter() - t0
            usage_log = f", finish_reason: {finish_reason}"
            if stream_usage:
                usage_log += (f", tokens: prompt={stream_usage.prompt_tokens}, "
                              f"completion={stream_usage.completion_tokens}, total={stream_usage.total_tokens}")
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior] stream completed in {elapsed:.2f}s ({tag}){usage_log}")

            # 如果有 tool_calls，执行工具并循环
            if accumulated_tool_calls:
                # 构建第一轮的 content 文本
                first_content = "\n".join(
                    ([f"Summary: {summary_holder[0]}"] if summary_holder else []) +
                    ([f"Emotion: {emotion_holder[0]}"] if emotion_holder else []) +
                    [f"Speech: {s}" for s in speech_parts] +
                    [f"Action: {a.name} {' '.join(map(str, a.args))} {' '.join(f'{k}={v}' for k, v in a.kwargs.items())}".strip() for a in actions] +
                    ([f"Memory: {memory_holder[0]}"] if memory_holder else []) +
                    ([f"Mood: {mood_holder[0]}"] if mood_holder else []) +
                    ([f"Vitals: {vitals_holder[0]}"] if vitals_holder else [])
                )
                logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior]   tool_calls: {len(accumulated_tool_calls)}")
                # 不在此处调用 on_stream_end：保持气泡流不中断，
                # on_stream_end 仅用于 speech 中断（多行 Speech 分开显示），
                # 不在轮次间调用（_collect_stream_raw 末尾不再调 on_stream_end）。
                return self._handle_tool_calls(
                    messages, accumulated_tool_calls, first_content,
                    on_chunk=on_chunk, on_stream_end=on_stream_end, tag=tag,
                    max_tokens=max_tokens, max_rounds=config.LLM_TOOL_MAX_ROUNDS,
                    speech_streamed=speech_streamed,
                )

            raw = "\n".join(
                ([f"Summary: {summary_holder[0]}"] if summary_holder else []) +
                ([f"Emotion: {emotion_holder[0]}"] if emotion_holder else []) +
                [f"Speech: {s}" for s in speech_parts] +
                [f"Action: {a.name} {' '.join(map(str, a.args))} {' '.join(f'{k}={v}' for k, v in a.kwargs.items())}".strip() for a in actions] +
                ([f"Memory: {memory_holder[0]}"] if memory_holder else []) +
                ([f"Mood: {mood_holder[0]}"] if mood_holder else []) +
                ([f"Vitals: {vitals_holder[0]}"] if vitals_holder else [])
            )
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior] === LLM RESPONSE ({tag}) ===")
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior]   raw: {raw}")

            mood_deltas = self._parse_mood_line(mood_holder[0]) if mood_holder else None
            vitals_deltas = self._parse_vitals_line(vitals_holder[0]) if vitals_holder else None
            return BehaviorOutput(
                actions=actions,
                speech=" ".join(speech_parts),
                speech_parts=list(speech_parts),
                speech_streamed=speech_streamed,
                summary=summary_holder[0] if summary_holder else None,
                memory_line=memory_holder[0] if memory_holder else None,
                emotion=emotion_holder[0] if emotion_holder else None,
                mood_deltas=mood_deltas,
                vitals_deltas=vitals_deltas,
            )

        except Exception as e:
            logger.exception(f"[{tag}] stream failed: {type(e).__name__}: {e}")
            return self._decide_local()

    def _parse_behavior(self, content: str) -> BehaviorOutput:
        actions: list = []
        speech_parts = []
        summary = None
        memory_line = None
        emotion = None
        mood_line = None
        vitals_line = None
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            lower = line.lower()
            if lower.startswith("action:"):
                raw = line.split(":", 1)[1].strip()
                step = self._parse_action_line(raw)
                if step:
                    actions.append(step)
            elif lower.startswith("speech:"):
                raw = line.split(":", 1)[1].strip()
                if raw.lower() not in ("none", "", "null"):
                    speech_parts.append(raw)
            elif lower.startswith("summary:"):
                summary = line.split(":", 1)[1].strip()
            elif lower.startswith("memory:") and memory_line is None:
                memory_line = line.split(":", 1)[1].strip()
            elif lower.startswith("emotion:"):
                emotion = line.split(":", 1)[1].strip()
            elif lower.startswith("mood:") and mood_line is None:
                mood_line = line.split(":", 1)[1].strip()
            elif lower.startswith("vitals:") and vitals_line is None:
                vitals_line = line.split(":", 1)[1].strip()
        if not actions:
            actions.append(ActionStep("sit", kwargs={"duration": 5}))
        mood_deltas = self._parse_mood_line(mood_line) if mood_line else None
        vitals_deltas = self._parse_vitals_line(vitals_line) if vitals_line else None
        speech = " ".join(speech_parts) if speech_parts else None
        return BehaviorOutput(actions=actions, speech=speech, speech_parts=speech_parts, summary=summary, memory_line=memory_line, emotion=emotion, mood_deltas=mood_deltas, vitals_deltas=vitals_deltas)

    def _finish_line(self, buffer, actions, speech_parts,
                      summary_holder=None, memory_holder=None, emotion_holder=None,
                      mood_holder=None, vitals_holder=None):
        line = buffer.strip()
        if not line:
            return
        lower = line.lower()
        if lower.startswith("speech:"):
            speech_parts.append(line.split(":", 1)[1].strip())
        elif lower.startswith("action:"):
            raw = line.split(":", 1)[1].strip()
            step = self._parse_action_line(raw)
            if step:
                actions.append(step)
        elif lower.startswith("summary:"):
            if summary_holder is not None:
                summary_holder.append(line.split(":", 1)[1].strip())
        elif lower.startswith("memory:"):
            if memory_holder is not None:
                memory_holder.append(line.split(":", 1)[1].strip())
        elif lower.startswith("emotion:"):
            if emotion_holder is not None:
                emotion_holder.append(line.split(":", 1)[1].strip())
        elif lower.startswith("mood:"):
            if mood_holder is not None:
                mood_holder.append(line.split(":", 1)[1].strip())
        elif lower.startswith("vitals:"):
            if vitals_holder is not None:
                vitals_holder.append(line.split(":", 1)[1].strip())

    @staticmethod
    def _parse_mood_line(raw: str) -> dict | None:
        """解析 Mood 行，格式: affection+5 joy+3 sanity-2"""
        import re
        deltas = {}
        pattern = re.compile(r'(affection|joy|sanity)\s*([+-]\s*\d+)', re.IGNORECASE)
        for match in pattern.finditer(raw):
            key = match.group(1).lower()
            value = float(match.group(2).replace(" ", ""))
            deltas[key] = value
        return deltas if deltas else None

    @staticmethod
    def _parse_vitals_line(raw: str) -> dict | None:
        """解析 Vitals 行，格式: satiety+15 energy-3（仅生理参数）"""
        import re
        deltas = {}
        pattern = re.compile(r'(satiety|energy)\s*([+-]\s*\d+)', re.IGNORECASE)
        for match in pattern.finditer(raw):
            key = match.group(1).lower()
            value = float(match.group(2).replace(" ", ""))
            deltas[key] = value
        return deltas if deltas else None

    def _parse_action_line(self, raw: str) -> ActionStep | None:
        parts = raw.split()
        if not parts:
            return None
        name = parts[0].lower()
        if name not in self._actions:
            t = datetime.now().strftime("%H:%M:%S")
            logger.warning(f"[{t}] [Behavior]   ⚠ unknown action: {name!r}, skipped")
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

    # 元工具（工具发现类）不消耗实际工具调用轮次
    _META_TOOL_NAMES = frozenset({"tool_search__search", "tool_search__list_groups"})
    _META_TOOL_MAX_ROUNDS = 10  # 元工具调用安全上限，防止死循环

    def _handle_tool_calls(self, messages, tool_calls_map, first_content,
                            on_chunk=None, on_stream_end=None, tag="",
                            max_rounds=5, max_tokens: int = 4000,
                            speech_streamed: bool = False) -> BehaviorOutput:
        """执行 tool_calls 并循环直到 LLM 不再请求工具。

        tool_search / list_groups 等元工具不消耗 max_rounds 配额，
        仅当至少执行了一个非元工具时，才计入一轮。
        """
        import json as _json
        from pet.tools.executor import ToolExecutor, ToolCall

        executor = ToolExecutor()
        current_messages = list(messages)
        tool_log = []  # 记录工具调用摘要，用于写入上下文
        final_instruction_added = False  # 最终轮精简指令是否已追加

        # 追踪 on_chunk 是否在 _collect_stream_raw 中被真正调用（即检测到 Speech 内容）
        # 仅当 Speech 被实际流式发送时才标记 speech_streamed=True，
        # 避免非 Speech 文本（如 Summary/Action）导致误判
        _chunk_invoked = [False]
        _wrapped_chunk = None
        if on_chunk:
            def _wrapped_chunk(delta: str):
                _chunk_invoked[0] = True
                on_chunk(delta)

        real_round = 0  # 实际（非元工具）调用轮次计数
        meta_round = 0  # 元工具调用总轮次（安全防护）
        display_round = 0  # 仅用于日志展示

        while real_round < max_rounds:
            meta_round += 1
            display_round += 1
            if meta_round > self._META_TOOL_MAX_ROUNDS:
                logger.warning(f"[Behavior] reached META_MAX_ROUNDS={self._META_TOOL_MAX_ROUNDS}, force terminate")
                break

            # 构建 assistant 消息（含 tool_calls）
            openai_tool_calls = []
            for idx in sorted(tool_calls_map.keys()):
                tc = tool_calls_map[idx]
                # 清洗 arguments：解析后重新序列化，避免流式拼接残留导致 400
                try:
                    clean_args = _json.dumps(_json.loads(tc["arguments"] or "{}"), ensure_ascii=False)
                except _json.JSONDecodeError:
                    clean_args = "{}"
                tc["arguments"] = clean_args
                openai_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": clean_args},
                })
            assistant_msg = {"role": "assistant", "tool_calls": openai_tool_calls}
            if first_content.strip():
                assistant_msg["content"] = first_content
            current_messages.append(assistant_msg)

            # 执行工具调用（并行或串行）
            sorted_indices = sorted(tool_calls_map.keys())

            def _exec_tool(idx):
                """执行单个工具调用，返回 (idx, tc, result, tool_brief, result_text)。"""
                tc = tool_calls_map[idx]
                try:
                    args = _json.loads(tc["arguments"] or "{}")
                except _json.JSONDecodeError:
                    args = {}
                call = ToolCall(name=tc["name"], args=args)
                result = executor._execute_one(call)
                # 在 _normalize 之前提取摘要（_normalize 会 pop summary）
                tool_brief = ""
                if result.success and isinstance(result.data, dict):
                    tool_brief = result.data.get("summary", "")
                result_text = executor._normalize(result.data) if result.success else result.error
                return idx, tc, result, tool_brief, result_text

            tool_results = {}
            use_parallel = config.LLM_TOOL_PARALLEL and len(sorted_indices) > 1
            if use_parallel:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(sorted_indices)) as pool:
                    futures = {pool.submit(_exec_tool, idx): idx for idx in sorted_indices}
                    for future in concurrent.futures.as_completed(futures):
                        res = future.result()
                        tool_results[res[0]] = res
            else:
                for idx in sorted_indices:
                    res = _exec_tool(idx)
                    tool_results[res[0]] = res

            # 判断本轮是否全为元工具调用（不消耗实际轮次配额）
            all_meta = all(
                tool_calls_map[idx]["name"] in self._META_TOOL_NAMES
                for idx in sorted_indices
            )

            # 按 index 排序后依次 append（保持顺序一致性）
            for idx in sorted_indices:
                _, tc, result, tool_brief, result_text = tool_results[idx]
                current_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_text,
                })
                logger.info(f"[Behavior] tool_round_{display_round} {tc['name']} -> {'OK' if result.success else 'FAIL'}")
                tool_log.append(f"{tc['name']} → {result.context_brief or tool_brief or result_text[:200]}")

                # 搜索工具执行后自动激活匹配的分组
                if tc["name"] == "tool_search__search":
                    try:
                        args = _json.loads(tc["arguments"] or "{}")
                        keyword = args.get("keyword", "")
                        if result.success and isinstance(result.data, dict):
                            self._activate_tool_groups_from_search(result.data.get("matches", []))
                        # 兜底：keyword 直接匹配组名
                        self._activate_groups_from_keyword(keyword)
                    except Exception:
                        pass

            # 非元工具轮次才计数
            if not all_meta:
                real_round += 1

            # 最终轮精简指令：仅在至少执行过一个非元工具后追加
            if not all_meta and not final_instruction_added:
                remaining = max_rounds - real_round
                low_threshold = -(-max_rounds // 10)  # 10% 向上取整
                low = "，轮次不多了请尽快输出" if remaining <= low_threshold else ""
                current_messages.append({
                    "role": "user",
                    "content": f"工具已执行，可直接输出最终行为（Summary+Speech+Action），无需重复分析；有值得记忆的信息才输出 Memory（剩余工具轮次：{remaining}/{max_rounds}{low}）"
                })
                final_instruction_added = True

            # 再次调用 LLM（每轮重建 tools_param，包含新激活的分组）
            t0 = time.perf_counter()
            tools_param = self._build_tools_param()
            stream = self._llm_call_stream(current_messages, max_tokens=max_tokens, tools=tools_param)
            _chunk_invoked[0] = False
            content, new_tool_calls = self._collect_stream_raw(stream, on_chunk=_wrapped_chunk, on_stream_end=on_stream_end, tag=f"{tag}_round_{display_round}", t0=t0)
            if _chunk_invoked[0]:
                speech_streamed = True

            if not new_tool_calls:
                # LLM 不再请求工具，解析最终行为
                result = self._parse_behavior(content)
                result.speech_streamed = speech_streamed
                if tool_log:
                    self.add_context(role="assistant", content=f"[工具调用] {' | '.join(tool_log)}")
                return result

            # 准备下一轮
            first_content = content
            tool_calls_map = new_tool_calls

        logger.warning(f"[Behavior] reached MAX_ROUNDS={max_rounds} (real_rounds={real_round}, meta_rounds={meta_round}), force terminate tool loop")
        result = self._parse_behavior(first_content)
        result.speech_streamed = speech_streamed
        if tool_log:
            self.add_context(role="assistant", content=f"[工具调用] {' | '.join(tool_log)}")
        return result

    def _collect_stream_raw(self, stream, on_chunk=None, on_stream_end=None, tag="", t0=None):
        """消费流，返回 (content_text, tool_calls_map)。"""
        content = ""
        tool_calls_map = {}
        line_buffer = ""
        in_speech = False
        speech_count = 0
        prefix_consumed = False
        finish_reason = None
        stream_usage = None

        for chunk in self._iterate_stream_with_timeout(stream, config.LLM_STREAM_TIMEOUT):
            if not chunk.choices:
                if hasattr(chunk, "usage") and chunk.usage:
                    stream_usage = chunk.usage
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta

            if delta.content:
                content += delta.content
                delta_speech = ""
                for char in delta.content:
                    if char in ("\n", "\r"):
                        if delta_speech and on_chunk:
                            on_chunk(delta_speech)
                        delta_speech = ""
                        line_buffer = ""
                        in_speech = False
                        prefix_consumed = False
                    else:
                        line_buffer += char
                        if not in_speech:
                            stripped = line_buffer.lstrip()
                            if stripped.lower().startswith("speech:"):
                                in_speech = True
                                speech_count += 1
                                if speech_count > 1 and on_stream_end:
                                    on_stream_end()
                                prefix = "Speech: "
                                if len(stripped) > len(prefix):
                                    prefix_consumed = True
                                    delta_speech += stripped[len(prefix):]
                        else:
                            if not prefix_consumed:
                                stripped = line_buffer.lstrip()
                                prefix = "Speech: "
                                if len(stripped) > len(prefix):
                                    prefix_consumed = True
                                    delta_speech += stripped[len(prefix):]
                            else:
                                delta_speech += char
                if delta_speech and on_chunk:
                    on_chunk(delta_speech)

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

        if t0:
            elapsed = time.perf_counter() - t0
            usage_log = f", finish_reason: {finish_reason}"
            if stream_usage:
                usage_log += (f", tokens: prompt={stream_usage.prompt_tokens}, "
                              f"completion={stream_usage.completion_tokens}, total={stream_usage.total_tokens}")
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior] stream completed in {elapsed:.2f}s ({tag}){usage_log}")
        logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior] === LLM RESPONSE ({tag}) ===")
        logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior]   raw: {content}")
        return content, tool_calls_map


    def _flush_pending_summaries(self):
        """将上下文淘汰产生的待摘要条目队列统一处理。
        有 LLM 就用 LLM 总结，不可用时兜底拼接。
        """
        items = self.drain_pending_summaries()
        if not items:
            return

        logger.info(f"[Behavior] flushing pending summaries: {len(items)} items")
        summary = None
        if self._llm:
            try:
                summary = self._llm_summarize(items)
            except Exception:
                logger.warning("[Behavior] LLM summarization failed, using fallback")

        if not summary:
            summary = self._build_fallback_summary(items)

        if summary:
            self.add_context(role="system", content=f"[历史摘要] {summary}", is_summary=True)
            logger.info(f"[Behavior] flushed pending summaries: {len(items)} items → {summary[:50]}...")

    def _llm_summarize(self, items: list[str]) -> str | None:
        """用 LLM 将多条历史上下文压缩为一句简洁摘要。"""
        messages = self.ctx.build_summary_messages(items)
        resp = self._llm_call(messages, max_tokens=config.LLM_MAX_TOKENS_SUMMARY)
        raw = resp.choices[0].message.content
        result = (raw or "").strip()
        if not result:
            logger.warning(f"[Behavior] LLM summarize returned empty content, raw={raw!r}, finish_reason={resp.choices[0].finish_reason}")
            return None
        logger.info(f"[Behavior] LLM summarized {len(items)} items → {result}")
        return result

    _LOCAL_ACTIONS = [
        ("sit", "歇一会儿～"),
        ("drive", "骑上我心爱的小摩托～"),
        ("walk", "蹦蹦跳跳真开心！"),
        ("shake_arms", "耶！太好啦！"),
        ("look_around", "那边有什么好玩的？"),
        ("stretch", "唔…伸个懒腰舒服多了～"),
        ("sleep", "呼…呼… zzz…"),
        ("thinking", "让我想想…"),
        ("bathing", "洗个澡清爽一下～"),
    ]

    def _decide_local(self) -> BehaviorOutput:
        action, speech = random.choice(self._LOCAL_ACTIONS)
        t = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{t}] [Behavior] _decide_local → {action} / {speech}")

        # walk 类动作需要方向和距离参数
        if action in ("drive", "walk"):
            direction = random.choice(["left", "right"])
            distance = random.randint(300, 800)
            step = ActionStep(action, args=(direction, distance))
        else:
            step = ActionStep(action)

        return BehaviorOutput(
            actions=[step],
            speech=speech,
            emotion="happy" if action == "shake_arms" else None,
        )

    def _interact_decide_local(self, event_hint: str) -> BehaviorOutput:
        """本地模式下根据交互提示词生成响应。"""
        t = datetime.now().strftime("%H:%M:%S")

        # 检测投喂事件并提取食物名
        if "投喂" in event_hint:
            food_match = re.search(r"投喂了(.+)[。，,]", event_hint)
            food = food_match.group(1) if food_match else "好吃的"

            speeches = [
                f"嗷呜～{food}真好吃！谢谢！",
                f"嗯嗯，{food}好香呀～",
                f"嘿嘿，{food}太棒啦！",
                f"哇，{food}！好开心！",
                f"嚼嚼嚼…{food}美味！",
            ]
            speech = random.choice(speeches)
            logger.info(f"[{t}] [Behavior] _interact_decide_local(feed {food}) → {speech}")

            return BehaviorOutput(
                actions=[ActionStep("shake_arms", kwargs={"duration": 3})],
                speech=speech,
                emotion="love",
                vitals_deltas={"satiety": 1.5, "energy": 0.5},
                mood_deltas={"joy": 1.5, "affection": 1.0},
            )

        # 检测抓取事件
        if "抓起" in event_hint or "抓住" in event_hint:
            speech = random.choice([
                "哎哎？快放我下来～",
                "呜哇，被抓住了！",
                "诶诶诶？！",
            ])
            logger.info(f"[{t}] [Behavior] _interact_decide_local(grab) → {speech}")
            return BehaviorOutput(
                actions=[ActionStep("shake_arms", kwargs={"duration": 3})],
                speech=speech,
                emotion="grim",
            )

        # 检测释放事件
        if "放下" in event_hint or "释放" in event_hint:
            speech = random.choice([
                "呼…终于落地了。",
                "踏实的感觉真好～",
                "嗯哼，还是地上舒服。",
            ])
            logger.info(f"[{t}] [Behavior] _interact_decide_local(release) → {speech}")
            return BehaviorOutput(
                actions=[ActionStep("stretch", kwargs={"duration": 4})],
                speech=speech,
            )

        # 其他交互事件：通用兜底
        action, speech = random.choice(self._LOCAL_ACTIONS)
        logger.info(f"[{t}] [Behavior] _interact_decide_local(generic) → {action} / {speech}")
        step = ActionStep(action, args=(random.choice(["left", "right"]), random.randint(300, 800))) \
            if action in ("drive", "walk") else ActionStep(action)

        return BehaviorOutput(
            actions=[step],
            speech=speech,
            emotion="happy" if action == "shake_arms" else None,
        )

    def _chat_decide_local(self, user_message: str) -> BehaviorOutput:
        return BehaviorOutput(
            actions=[ActionStep("look_around", kwargs={"duration": 5})],
            speech=f"（听到了：{user_message[:10]}...但我还不会回应）",
        )

    def _apply_cache_control(self, messages: list):
        """为 system prompt 添加缓存标记（Anthropic 兼容 API 使用）。

        仅在 config.LLM_CACHE_PROMPT 启用时生效。
        将 system 消息的字符串 content 包装为带 cache_control 的结构化格式。
        OpenAI 原生 API 会忽略该字段，Anthropic 兼容端点则会缓存。
        """
        if not config.LLM_CACHE_PROMPT:
            return
        if not messages or messages[0]["role"] != "system":
            return
        content = messages[0]["content"]
        if not isinstance(content, str):
            return
        messages[0]["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]

    def _dump_context(self, tag: str, messages: list):
        t = datetime.now().strftime("%H:%M:%S")
        logger.debug(f"[{t}] [Behavior] ====== FULL CONTEXT ({tag}) ======")
        for i, m in enumerate(messages):
            if isinstance(m["content"], str):
                logger.debug(f"[{t}] [Behavior] --- msg[{i}] role={m['role']} ---\n{m['content']}")
            else:
                for j, part in enumerate(m["content"]):
                    if part["type"] == "text":
                        logger.debug(f"[{t}] [Behavior] --- msg[{i}] role={m['role']} part[{j}] text ---\n{part['text']}")
                    else:
                        logger.debug(f"[{t}] [Behavior] --- msg[{i}] role={m['role']} part[{j}] {part['type']} len={len(str(part))} --- (binary omitted)")
        logger.debug(f"[{t}] [Behavior] ====== END CONTEXT ({tag}) ======")
