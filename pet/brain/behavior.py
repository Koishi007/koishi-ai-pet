"""与 AI 通信，解析响应为动作序列。"""

import time
from datetime import datetime
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

from pet.brain.base import BrainMixin
from pet.brain.context_builder import ContextBuilder
from pet.brain.llm_client import LLMClient
from pet.brain.llm_stats import LlmStats
from pet.action.registry import ACTION_NAMES
from pet.config import config
from pet.brain.llm_retry import llm_retry

logger = logging.getLogger(__name__)


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
            return self._retry_if_empty(self._stream_and_parse, messages, tag=tag, on_chunk=on_chunk, on_stream_end=on_stream_end, max_tokens=config.LLM_MAX_TOKENS_AUTONOMOUS)
        finally:
            self._lock.release()

    def interact_decide(self, event_hint: str) -> BehaviorOutput:
        if not self._llm:
            return self._decide_local()
        messages = self.ctx.build_interact(event_hint)
        return self._retry_if_empty(self._call_llm_and_parse, messages, messages[0]["content"], "interact", tag="interact", max_tokens=config.LLM_MAX_TOKENS_INTERACT)

    def interact_decide_stream(self, event_hint: str,
                               on_chunk=None, on_stream_end=None) -> BehaviorOutput:
        if not self._llm:
            return self._decide_local()
        if not self._lock.acquire(timeout=2):
            logger.warning("[Behavior] interact_decide_stream: busy, skip")
            return self._decide_local()
        try:
            messages = self.ctx.build_interact(event_hint)
            return self._retry_if_empty(self._stream_and_parse, messages, tag="interact", on_chunk=on_chunk, on_stream_end=on_stream_end, max_tokens=config.LLM_MAX_TOKENS_INTERACT)
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
            return self._retry_if_empty(self._stream_and_parse, messages, tag=tag, on_chunk=on_chunk, on_stream_end=on_stream_end, max_tokens=config.LLM_MAX_TOKENS_CHAT)
        finally:
            self._lock.release()

    def _retry_if_empty(self, fn, *args, tag="", **kwargs) -> BehaviorOutput:
        """调用 fn 并在结果为空时重试一次。"""
        result = fn(*args, **kwargs)
        if not result.actions and not result.speech:
            logger.warning(f"[Behavior] empty LLM response (no actions, no speech), retrying once ({tag})")
            result = fn(*args, **kwargs)
        return result

    @llm_retry(tag="Behavior")
    def _llm_call(self, messages: list, max_tokens: int = 4000, tools: list = None):
        self.llm_stats.increment()
        t0 = time.perf_counter()
        kwargs = {"model": self._llm.model, "messages": messages, "max_tokens": max_tokens, "temperature": config.LLM_TEMPERATURE}
        if tools:
            kwargs["tools"] = tools
        resp = self._llm.client.chat.completions.create(**kwargs)
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
        return llm_stream_with_retry(
            lambda: self._llm.client.chat.completions.create(**kwargs),
            tag="Behavior.stream",
        )

    def _log_prompt_size(self, messages: list, tag: str):
        """计算并打印 prompt 规模：文本字符数 + 图片 base64 大小。"""
        text_chars = 0
        image_count = 0
        image_bytes = 0
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
        t = datetime.now().strftime("%H:%M:%S")
        parts = [f"prompt_chars: {text_chars}"]
        if image_count:
            parts.append(f"images: {image_count} ({image_bytes // 1024}KB base64)")
        logger.info(f"[{t}] [Behavior]   {', '.join(parts)} ({tag})")

    def _call_llm_and_parse(self, messages: list, system_content: str, tag: str, max_tokens: int = 4000) -> BehaviorOutput:
        t = datetime.now().strftime("%H:%M:%S")
        self._apply_cache_control(messages)
        self._dump_context(tag, messages)
        self._log_prompt_size(messages, tag)
        try:
            from pet.tools.registry import TOOL_REGISTRY
            tools_param = TOOL_REGISTRY.to_openai_tools()
            resp = self._llm_call(messages, max_tokens=max_tokens, tools=tools_param if tools_param else None)
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
                    tag=tag, tools_param=tools_param, max_tokens=max_tokens,
                    max_rounds=config.LLM_TOOL_MAX_ROUNDS,
                    speech_streamed=speech_streamed,
                )

            result = self._parse_behavior(content)
            logger.info(f"[{t}] [Behavior]   parsed -> {result}")
            return result
        except Exception as e:
            logger.exception(f"[{t}] [Behavior]   {tag} LLM call failed: {type(e).__name__}: {e}")
            logger.warning(f"[{t}] [Behavior]   falling back to local")
            return self._decide_local()

    def _stream_and_parse(self, messages: list, on_chunk=None, on_stream_end=None, tag: str = "", max_tokens: int = 4000) -> BehaviorOutput:
        self._apply_cache_control(messages)
        self._dump_context(tag, messages)
        self._log_prompt_size(messages, tag)
        t0 = time.perf_counter()
        try:
            from pet.tools.registry import TOOL_REGISTRY
            tools_param = TOOL_REGISTRY.to_openai_tools()
            stream = self._llm_call_stream(messages, max_tokens=max_tokens, tools=tools_param if tools_param else None)

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

            for chunk in stream:
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
                # 不在轮次间调用（_consume_stream 末尾不再调 on_stream_end）。
                return self._handle_tool_calls(
                    messages, accumulated_tool_calls, first_content,
                    on_chunk=on_chunk, on_stream_end=on_stream_end, tag=tag,
                    tools_param=tools_param, max_tokens=max_tokens,
                    max_rounds=config.LLM_TOOL_MAX_ROUNDS,
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

    def _handle_tool_calls(self, messages, tool_calls_map, first_content,
                            on_chunk=None, on_stream_end=None, tag="", tools_param=None,
                            max_rounds=5, max_tokens: int = 4000,
                            speech_streamed: bool = False) -> BehaviorOutput:
        """执行 tool_calls 并循环直到 LLM 不再请求工具。"""
        import json as _json
        from pet.tools.executor import ToolExecutor, ToolCall

        executor = ToolExecutor()
        current_messages = list(messages)
        tool_log = []  # 记录工具调用摘要，用于写入上下文
        final_instruction_added = False  # 最终轮精简指令是否已追加

        for round_idx in range(max_rounds):
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

            # 按 index 排序后依次 append（保持顺序一致性）
            for idx in sorted_indices:
                _, tc, result, tool_brief, result_text = tool_results[idx]
                current_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_text,
                })
                logger.info(f"[Behavior] tool_round_{round_idx+1} {tc['name']} -> {'OK' if result.success else 'FAIL'}")
                tool_log.append(f"{tc['name']} → {result.context_brief or tool_brief or result_text[:200]}")

            # 最终轮精简指令（仅追加一次，引导模型直接输出最终行为）
            if not final_instruction_added:
                current_messages.append({
                    "role": "user",
                    "content": "工具已执行完毕，直接输出最终行为（Summary+Speech+Action），无需重复分析；有值得记忆的信息才输出 Memory"
                })
                final_instruction_added = True

            # 再次调用 LLM
            t0 = time.perf_counter()
            stream = self._llm_call_stream(current_messages, max_tokens=max_tokens, tools=tools_param)
            content, new_tool_calls = self._consume_stream(stream, on_chunk=on_chunk, on_stream_end=on_stream_end, tag=f"{tag}_round_{round_idx+1}", t0=t0)
            if on_chunk:
                speech_streamed = speech_streamed or bool(content)

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

        logger.warning(f"[Behavior] reached MAX_ROUNDS={max_rounds}, force terminate tool loop")
        result = self._parse_behavior(first_content)
        result.speech_streamed = speech_streamed
        if tool_log:
            self.add_context(role="assistant", content=f"[工具调用] {' | '.join(tool_log)}")
        return result

    def _consume_stream(self, stream, on_chunk=None, on_stream_end=None, tag="", t0=None):
        """消费流，返回 (content_text, tool_calls_map)。"""
        content = ""
        tool_calls_map = {}
        line_buffer = ""
        in_speech = False
        speech_count = 0
        prefix_consumed = False
        finish_reason = None
        stream_usage = None

        for chunk in stream:
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

    _LOCAL_ACTIONS = [
        ("sit", "Taking a little break."),
        ("drive", "Riding my little scooter!"),
        ("walk", "Bouncy bouncy!"),
        ("shake_arms", "Yay! So happy!"),
        ("look_around", "What's going on over there?"),
        ("stretch", "Ahh, that's better..."),
        ("sleep", "Getting sleepy... zzz..."),
        ("thinking", "Hmm..."),
    ]

    def _decide_local(self) -> BehaviorOutput:
        import random
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
