import base64
from datetime import datetime
import io
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image
from openai import OpenAI
from pet.brain.base import BrainMixin
from pet.brain import prompts
from pet.action.registry import ACTION_NAMES
from config import config
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
    speech_streamed: bool = False
    summary: Optional[str] = None
    memory_line: Optional[str] = None


class Behavior(BrainMixin):

    def __init__(self, memory_store=None):
        super().__init__()
        self._client = None
        self._model = None
        self._lock = threading.RLock()
        self._memory_store = memory_store
        self._setup()

        self._actions = ACTION_NAMES

        t = datetime.now().strftime("%H:%M:%S")
        client_type = "None (local)" if self._client is None else f"{type(self._client).__name__}(model={self._model})"
        logger.info(f"[{t}] [Behavior] init: {len(self._actions)} actions, client={client_type}")

    def _setup(self):
        brain = config.BRAIN or "local"
        key = config.LLM_KEY
        url = config.LLM_URL
        model = config.LLM_MODEL

        logger.debug(f"[Behavior] _setup: BRAIN={brain}, model={model}, "
                     f"key={'***' if key else 'EMPTY'}, URL={url or '(empty)'}")

        if brain == "ollama":
            self._client = OpenAI(
                api_key="ollama",
                base_url=config.OLLAMA_BASE_URL,
                timeout=config.LLM_TIMEOUT,
            )
            self._model = model or "llama3.2"
        elif brain == "llm" and key:
            self._client = OpenAI(
                api_key=key,
                base_url=url or "",
                timeout=config.LLM_TIMEOUT,
            )
            self._model = model
        else:
            self._client = None
            logger.warning(f"[Behavior] No client (BRAIN={brain}, key empty={not bool(key)}) → local fallback")


    @property
    def has_vision(self) -> bool:
        return self._client is not None and config.VISION_ENABLED

    def _encode_base64(self, image: Image.Image) -> str:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def _build_context_str(self, context: str) -> str:
        """合并 OCR 上下文 + 近期行为历史（排除最近一条）。"""
        ctx = context or "no context"
        if len(self._context) > 1:
            ctx += f"\nRecent: {', '.join(self._context[-6:-1])}"
        return ctx

    def _trim_history(self):
        if len(self._context) > 10:
            self._context[:] = self._context[-10:]

    def _append_personality(self, system_content: str) -> str:
        if config.PET_PERSONALITY:
            return system_content + f"\n\n=== 你的性格 ===\n{config.PET_PERSONALITY}"
        return system_content

    def _dump_context(self, tag: str, messages: list):
        """输出完整上下文到日志（调试用）。"""
        t = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{t}] [Behavior] ====== FULL CONTEXT ({tag}) ======")
        for i, m in enumerate(messages):
            if isinstance(m["content"], str):
                logger.info(f"[{t}] [Behavior] --- msg[{i}] role={m['role']} ---\n{m['content']}")
            else:
                for j, part in enumerate(m["content"]):
                    if part["type"] == "text":
                        logger.info(f"[{t}] [Behavior] --- msg[{i}] role={m['role']} part[{j}] text ---\n{part['text']}")
                    else:
                        logger.info(f"[{t}] [Behavior] --- msg[{i}] role={m['role']} part[{j}] {part['type']} len={len(str(part))} --- (binary omitted)")
        logger.info(f"[{t}] [Behavior] ====== END CONTEXT ({tag}) ======")

    @llm_retry(tag="Behavior")
    def _llm_call(self, messages: list, max_tokens: int = 4000):
        """带重试的非流式 LLM 调用。"""
        return self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=max_tokens,
        )

    def _llm_call_stream(self, messages: list, max_tokens: int = 4000):
        """带重试的流式 LLM 调用（仅连接阶段重试）。"""
        from pet.brain.llm_retry import llm_stream_with_retry
        return llm_stream_with_retry(
            lambda: self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                stream=True,
            ),
            tag="Behavior.stream",
        )

    def _call_llm_and_parse(self, messages: list, system_content: str, tag: str) -> BehaviorOutput:
        """统一的 LLM 调用 + 响应日志 + Skill 解析 + 异常 fallback。"""
        t = datetime.now().strftime("%H:%M:%S")
        self._dump_context(tag, messages)
        try:
            resp = self._llm_call(messages)
            content = resp.choices[0].message.content or ""
            logger.info(f"[{t}] [Behavior] === LLM RESPONSE ({tag}) ===")
            logger.info(f"[{t}] [Behavior]   finish_reason: {resp.choices[0].finish_reason}")
            if hasattr(resp, 'usage') and resp.usage:
                logger.info(f"[{t}] [Behavior]   usage: {resp.usage}")
            logger.debug(f"[{t}] [Behavior]   raw: {content}")
            result = self._execute_with_skills(content, system_content)
            logger.info(f"[{t}] [Behavior]   parsed → {result}")
            return result
        except Exception as e:
            logger.exception(f"[{t}] [Behavior]   {tag} LLM call failed: {type(e).__name__}: {e}")
            logger.warning(f"[{t}] [Behavior]   falling back to local")
            return self._decide_local()

    def decide(self, context: str = "") -> BehaviorOutput:
        t = datetime.now().strftime("%H:%M:%S")
        ctx_preview = context[:60] if context else "(empty)"
        logger.info(f"[{t}] [Behavior] decide(context={ctx_preview})")
        if self._client:
            result = self._decide_non_vision(context)
        else:
            result = self._decide_local()
        logger.info(f"[{t}] [Behavior] decide → {result}")
        return result

    def decide_with_vision(self, image: Image.Image, context: str = "") -> BehaviorOutput:
        t = datetime.now().strftime("%H:%M:%S")
        # 截图按比例缩放，下限锁 1536px
        scale = config.VISION_SCALE
        if scale < 1.0:
            w, h = image.size
            new_w, new_h = int(w * scale), int(h * scale)
            MIN_PX = 1536
            if max(new_w, new_h) < MIN_PX:
                ratio = MIN_PX / max(new_w, new_h)
                new_w, new_h = int(new_w * ratio), int(new_h * ratio)
            logger.info(f"[{t}] [Behavior] resize image {w}x{h} (scale={scale}) → {new_w}x{new_h}")
            image = image.resize((new_w, new_h), Image.LANCZOS)
        ctx_preview = context[:60] if context else "(empty)"
        logger.info(f"[{t}] [Behavior] decide_with_vision(context={ctx_preview}, image={image.size})")
        if not self.has_vision:
            logger.info(f"[{t}] [Behavior]   no vision client → fallback to decide()")
            return self.decide(context)
        base64_img = self._encode_base64(image)
        logger.info(f"[{t}] [Behavior]   base64 encoded, length={len(base64_img)}")
        return self._decide_vision(base64_img, context)

    def _decide_vision(self, base64_img: str, context: str) -> BehaviorOutput:
        t = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{t}] [Behavior] === LLM REQUEST (vision) ===")
        logger.info(f"[{t}] [Behavior]   model: {self._model}")
        logger.info(f"[{t}] [Behavior]   context({len(context)} chars): \"{context[:80]}\"")
        logger.info(f"[{t}] [Behavior]   history: {len(self._context)} entries")
        messages = self._build_decide_messages(context, vision=True, base64_img=base64_img)
        return self._call_llm_and_parse(messages, messages[0]["content"], "vision")

    def _decide_non_vision(self, context: str) -> BehaviorOutput:
        t = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{t}] [Behavior] === LLM REQUEST (non_vision) ===")
        logger.info(f"[{t}] [Behavior]   model: {self._model}")
        logger.info(f"[{t}] [Behavior]   context({len(context)} chars): \"{context[:80]}\"")
        logger.info(f"[{t}] [Behavior]   history: {len(self._context)} entries")
        messages = self._build_decide_messages(context, vision=False)
        return self._call_llm_and_parse(messages, messages[0]["content"], "non_vision")

    def _parse_behavior(self, content: str) -> BehaviorOutput:
        actions: list = []
        speech = None
        summary = None
        memory_line = None
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
                    speech = raw
            elif lower.startswith("summary:"):
                summary = line.split(":", 1)[1].strip()
            elif lower.startswith("memory:") and memory_line is None:
                memory_line = line.split(":", 1)[1].strip()
        if not actions:
            actions.append(ActionStep("idle"))
        return BehaviorOutput(actions=actions, speech=speech, summary=summary, memory_line=memory_line)

    def _finish_line(self, buffer, actions, speech_parts, skill_lines, summary_holder=None, memory_holder=None):
        """归档一个完成的行。"""
        line = buffer.strip()
        if not line:
            return
        lower = line.lower()
        if lower.startswith("speech:"):
            speech_parts.append(line.split(":", 1)[1].strip())
        elif lower.startswith("action:"):
            step = self._parse_action_line(line.split(":", 1)[1].strip())
            if step:
                actions.append(step)
        elif lower.startswith("skill:"):
            skill_lines.append(line)
        elif lower.startswith("summary:"):
            if summary_holder is not None:
                summary_holder.append(line.split(":", 1)[1].strip())
        elif lower.startswith("memory:"):
            if memory_holder is not None:
                memory_holder.append(line.split(":", 1)[1].strip())

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

    def _execute_with_skills(self, first_content: str, system_content: str, on_chunk=None) -> BehaviorOutput:
        """解析 LLM 输出，若含 Skill 调用则执行技能并进行二次调用。"""
        from pet.skills.executor import SkillExecutor

        executor = SkillExecutor()
        tool_calls = executor.parse_skill_lines(first_content)

        if not tool_calls:
            return self._parse_behavior(first_content)

        # 执行工具
        results = executor.execute(tool_calls)
        result_text = executor.format_results(results)

        # 二次调用 LLM（带工具结果）
        messages = [
            {"role": "system", "content": system_content},
            {"role": "assistant", "content": first_content},
            {"role": "user", "content": prompts.skill_result_user_prompt(result_text)},
        ]
        self._dump_context("skill_pass2", messages)
        try:
            if on_chunk:
                # 流式二次调用，speech 逐字推送到气泡
                return self._stream_and_parse(messages, on_chunk=on_chunk, tag="skill_pass2")
            else:
                resp = self._llm_call(messages)
                final_content = resp.choices[0].message.content or ""
                return self._parse_behavior(final_content)
        except Exception as e:
            logger.error(f"[Behavior] skill second-pass failed: {e}")
            return self._parse_behavior(first_content)

    def _decide_local(self) -> BehaviorOutput:
        import random
        self._trim_history()
        action = random.choice(self._actions)
        action_speech = {
            "idle": "Just hanging out...",
            "bounce": "Boing boing!",
            "walk": "Time to stretch my legs!",
            "look_around": "What's going on over there?",
            "stretch": "Ahh, that's better...",
            "sit": "Taking a little break.",
            "sleep": "Getting sleepy... zzz...",
        }
        speech = action_speech.get(action, "...")
        t = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{t}] [Behavior] _decide_local → {action} / {speech}")
        return BehaviorOutput(
            actions=[ActionStep(action)],
            speech=speech,
        )

    # ── 流式调用方法 ──

    def _build_decide_messages(self, context: str, vision: bool = False, base64_img: str = None) -> list:
        self._trim_history()
        context_str = self._build_context_str(context)
        if vision:
            system_content = self._append_personality(prompts.vision_system_prompt())
            if self._memory_store:
                memory_text = self._memory_store.retrieve_context("")
                if memory_text:
                    system_content += f"\n\n[你对主人的记忆]\n{memory_text}"
            text_prompt = prompts.vision_decide_prompt(context_str)
            if config.VISION_PROMPT_EXTRA:
                text_prompt += "\n\n" + config.VISION_PROMPT_EXTRA.replace("{context}", context_str)
            return [
                {"role": "system", "content": system_content},
                {"role": "user", "content": [
                    {"type": "text", "text": text_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_img}"}},
                ]},
            ]
        else:
            system_content = self._append_personality(prompts.non_vision_system_prompt())
            if self._memory_store:
                memory_text = self._memory_store.retrieve_context("")
                if memory_text:
                    system_content += f"\n\n[你对主人的记忆]\n{memory_text}"
            prompt = prompts.non_vision_decide_prompt(context_str)
            if config.NON_VISION_PROMPT_EXTRA:
                prompt += "\n\n" + config.NON_VISION_PROMPT_EXTRA.replace("{context}", context_str)
            return [
                {"role": "system", "content": system_content},
                {"role": "user", "content": prompt},
            ]

    def _build_chat_messages(self, user_message: str, context: str, base64_img: str = None) -> list:
        self._trim_history()
        system_content = self._append_personality(prompts.chat_decide_system_prompt())
        if self._memory_store:
            memory_text = self._memory_store.retrieve_context(user_message)
            if memory_text:
                system_content += f"\n\n[你对主人的记忆]\n{memory_text}"
        history = ""
        if self._context:
            recent = self._context[-9:-1] if len(self._context) > 1 else []
            if recent:
                history = "\n\n=== 近期对话/行为记录 ===\n" + "\n".join(recent)
        user_content = prompts.chat_decide_user_prompt(user_message, context + history)
        if base64_img:
            return [
                {"role": "system", "content": system_content},
                {"role": "user", "content": [
                    {"type": "text", "text": user_content},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_img}"}},
                ]},
            ]
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

    def _stream_and_parse(self, messages: list, on_chunk=None, on_stream_end=None, tag: str = "") -> BehaviorOutput:
        """流式 LLM 调用的核心：逐行状态机解析，Speech 行逐字推送。"""
        self._dump_context(tag, messages)
        try:
            stream = self._llm_call_stream(messages)

            buffer = ""
            actions = []
            speech_parts = []
            skill_lines = []
            summary_holder = []
            memory_holder = []
            speech_streamed = False
            line_type = None
            speech_prefix_consumed = False

            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta is None:
                    continue

                delta_speech = ""
                for char in delta:
                    if char == "\n":
                        self._finish_line(buffer, actions, speech_parts, skill_lines, summary_holder, memory_holder)
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
                            elif lower.startswith("action:"):
                                line_type = "action"
                            elif lower.startswith("skill:"):
                                line_type = "skill"
                            elif lower.startswith("summary:"):
                                line_type = "summary"
                            elif lower.startswith("memory:"):
                                line_type = "memory"
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

            # 最后一行
            if buffer.strip():
                self._finish_line(buffer, actions, speech_parts, skill_lines, summary_holder, memory_holder)

            # Skill 调用 → 二次非流式调用
            if skill_lines:
                if on_stream_end:
                    on_stream_end()
                full_content = "\n".join(
                    [f"Speech: {s}" for s in speech_parts] +
                    [f"Action: {a.name} {' '.join(map(str, a.args))}" for a in actions] +
                    skill_lines
                )
                logger.debug(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior] === LLM RESPONSE ({tag}) ===")
                logger.debug(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior]   raw: {full_content}")
                return self._execute_with_skills(full_content, messages[0]["content"], on_chunk=on_chunk)

            raw = "\n".join(
                ([f"Summary: {summary_holder[0]}"] if summary_holder else []) +
                [f"Speech: {s}" for s in speech_parts] +
                [f"Action: {a.name} {' '.join(map(str, a.args))}" for a in actions] +
                skill_lines
            )
            logger.debug(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior] === LLM RESPONSE ({tag}) ===")
            logger.debug(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior]   raw: {raw}")

            return BehaviorOutput(
                actions=actions,
                speech=" ".join(speech_parts),
                speech_streamed=speech_streamed,
                summary=summary_holder[0] if summary_holder else None,
                memory_line=memory_holder[0] if memory_holder else None,
            )

        except Exception as e:
            logger.exception(f"[{tag}] stream failed: {type(e).__name__}: {e}")
            return self._decide_local()

    def decide_stream(self, context: str, on_chunk=None, on_stream_end=None) -> BehaviorOutput:
        """流式决策（无视觉）。"""
        if not self._client:
            return self._decide_local()
        if not self._lock.acquire(timeout=0.5):
            logger.warning("[Behavior] decide_stream: busy, skip")
            return self._decide_local()
        try:
            messages = self._build_decide_messages(context, vision=False)
            return self._stream_and_parse(messages, on_chunk=on_chunk, on_stream_end=on_stream_end, tag="decide_stream")
        finally:
            self._lock.release()

    def decide_with_vision_stream(self, image: Image.Image, context: str, on_chunk=None, on_stream_end=None) -> BehaviorOutput:
        """流式决策（含视觉截图）。"""
        if not self.has_vision:
            return self.decide_stream(context, on_chunk=on_chunk, on_stream_end=on_stream_end)
        scale = config.VISION_SCALE
        if scale < 1.0:
            w, h = image.size
            new_w, new_h = int(w * scale), int(h * scale)
            MIN_PX = 1536
            if max(new_w, new_h) < MIN_PX:
                ratio = MIN_PX / max(new_w, new_h)
                new_w, new_h = int(new_w * ratio), int(new_h * ratio)
            image = image.resize((new_w, new_h), Image.LANCZOS)
        base64_img = self._encode_base64(image)
        messages = self._build_decide_messages(context, vision=True, base64_img=base64_img)
        return self._stream_and_parse(messages, on_chunk=on_chunk, on_stream_end=on_stream_end, tag="decide_vision_stream")

    def _chat_decide_local(self, user_message: str) -> BehaviorOutput:
        return BehaviorOutput(
            actions=[ActionStep("look_around", kwargs={"duration": 5})],
            speech=f"（听到了：{user_message[:10]}...但我还不会回应）",
        )

    def chat_decide_stream(self, user_message: str, context: str, image=None, on_chunk=None, on_stream_end=None) -> BehaviorOutput:
        """流式对话决策（可选视觉截图）。"""
        if not self._client:
            return self._chat_decide_local(user_message)
        if not self._lock.acquire(timeout=5):
            logger.warning("[Behavior] chat_decide_stream: busy, timeout")
            return BehaviorOutput(
                actions=[ActionStep("look_around", kwargs={"duration": 5})],
                speech="\u5514\u2026\u2026\u7b49\u4e00\u4e0b\uff0c\u6211\u8fd8\u5728\u60f3\u2026\u2026",
            )
        try:
            # 处理截图：缩放 + 编码
            base64_img = None
            if image is not None and self.has_vision:
                scale = config.VISION_SCALE
                if scale < 1.0:
                    w, h = image.size
                    new_w, new_h = int(w * scale), int(h * scale)
                    MIN_PX = 1536
                    if max(new_w, new_h) < MIN_PX:
                        ratio = MIN_PX / max(new_w, new_h)
                        new_w, new_h = int(new_w * ratio), int(new_h * ratio)
                    image = image.resize((new_w, new_h), Image.LANCZOS)
                base64_img = self._encode_base64(image)
            messages = self._build_chat_messages(user_message, context, base64_img=base64_img)
            self._dump_context("chat_stream", messages)
            stream = self._llm_call_stream(messages)
            full_content = ""
            line_buffer = ""
            in_speech = False
            prefix_consumed = False
            speech_streamed = False

            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta is None:
                    continue
                full_content += delta

                delta_speech = ""
                for char in delta:
                    if char == "\n":
                        line_buffer = ""
                        in_speech = False
                        prefix_consumed = False
                    else:
                        line_buffer += char
                        if not in_speech:
                            stripped = line_buffer.lstrip()
                            if stripped.lower().startswith("speech:"):
                                in_speech = True
                                prefix = "Speech: "
                                if len(stripped) > len(prefix):
                                    prefix_consumed = True
                                    delta_speech += stripped[len(prefix):]
                            elif len(stripped) >= 8:
                                pass
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
                    speech_streamed = True

            logger.debug(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior] === LLM RESPONSE (chat_stream) ===")
            logger.debug(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior]   raw: {full_content}")

            if "skill:" in full_content.lower():
                if on_stream_end:
                    on_stream_end()
                system_content = messages[0]["content"]
                result = self._execute_with_skills(full_content, system_content, on_chunk=on_chunk)
                return result

            result = self._parse_behavior(full_content)
            result.speech_streamed = speech_streamed
            return result
        except Exception as e:
            logger.error(f"[Behavior] chat_decide_stream failed: {type(e).__name__}: {e}")
            return BehaviorOutput(
                actions=[ActionStep("look_around", kwargs={"duration": 5})],
                speech="喔...我好像没听清",
            )
        finally:
            self._lock.release()

    def chat_decide(self, user_message: str, context: str = "") -> BehaviorOutput:
        """用户对话模式：接收用户消息，结合屏幕上下文，输出动作+语音。"""
        t = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{t}] [Behavior] chat_decide(msg={user_message[:50]}, ctx={context[:30]})")

        if not self._client:
            return BehaviorOutput(
                actions=[ActionStep("look_around", kwargs={"duration": 5})],
                speech=f"（听到了：{user_message[:10]}...但我还不会回应）",
            )

        return self._chat_decide_remote(user_message, context)

    def _chat_decide_remote(self, user_message: str, context: str) -> BehaviorOutput:
        t = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{t}] [Behavior] === LLM REQUEST (chat_decide) ===")

        self._trim_history()
        system_content = self._append_personality(prompts.chat_decide_system_prompt())

        # 构建对话历史（包含最近条目，不跳过最后一条）
        history = ""
        if self._context:
            recent = self._context[-9:-1] if len(self._context) > 1 else []
            if recent:
                history = "\n\n=== 近期对话/行为记录 ===\n" + "\n".join(recent)

        user_content = prompts.chat_decide_user_prompt(user_message, context + history)

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

        self._dump_context("chat_decide", messages)
        try:
            resp = self._llm_call(messages)
            content = resp.choices[0].message.content or ""
            logger.info(f"[{t}] [Behavior] === LLM RESPONSE (chat_decide) ===")
            logger.debug(f"[{t}] [Behavior]   raw: {content}")
            result = self._parse_behavior(content)
            logger.info(f"[{t}] [Behavior]   parsed → {result}")
            return result
        except Exception as e:
            logger.error(f"[{t}] [Behavior] chat_decide failed: {e}")
            return BehaviorOutput(
                actions=[ActionStep("look_around", kwargs={"duration": 5})],
                speech="喔...我好像没听清",
            )
