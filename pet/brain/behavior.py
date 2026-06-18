"""与 AI 通信，解析响应为动作序列。"""

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

    def __init__(self, memory_store=None, screen_reader=None):
        super().__init__()
        self._client = None
        self._model = None
        self._lock = threading.RLock()
        self._memory_store = memory_store
        self._screen_reader = screen_reader
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
    
    def decide(self, context: str = "", screenshot: bool = True) -> BehaviorOutput:
        t = datetime.now().strftime("%H:%M:%S")
        if not self._client:
            return self._decide_local()

        base64_img = self._prepare_image() if screenshot else None
        vision = base64_img is not None
        tag = "vision" if vision else "non_vision"
        ctx_preview = context[:60] if context else "(empty)"
        logger.info(f"[{t}] [Behavior] === LLM REQUEST ({tag}) ===")
        logger.info(f"[{t}] [Behavior]   model: {self._model}, context({len(context)} chars): \"{ctx_preview}\"")
        logger.info(f"[{t}] [Behavior]   history: {len(self._context)} entries")

        messages = self._build_decide_messages(context, vision=vision, base64_img=base64_img)
        return self._call_llm_and_parse(messages, messages[0]["content"], tag)

    def decide_stream(self, context: str = "", screenshot: bool = True,
                      on_chunk=None, on_stream_end=None) -> BehaviorOutput:
        if not self._client:
            return self._decide_local()
        if not self._lock.acquire(timeout=0.5):
            logger.warning("[Behavior] decide_stream: busy, skip")
            return self._decide_local()
        try:
            base64_img = self._prepare_image() if screenshot else None
            vision = base64_img is not None
            tag = "decide_vision_stream" if vision else "decide_stream"
            messages = self._build_decide_messages(context, vision=vision, base64_img=base64_img)
            return self._stream_and_parse(messages, on_chunk=on_chunk, on_stream_end=on_stream_end, tag=tag)
        finally:
            self._lock.release()

    def interact_decide(self, event_hint: str) -> BehaviorOutput:
        if not self._client:
            return self._decide_local()
        system_content = prompts.interact_system_prompt()
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": event_hint},
        ]
        return self._call_llm_and_parse(messages, system_content, "interact")

    def interact_decide_stream(self, event_hint: str,
                               on_chunk=None, on_stream_end=None) -> BehaviorOutput:
        if not self._client:
            return self._decide_local()
        if not self._lock.acquire(timeout=2):
            logger.warning("[Behavior] interact_decide_stream: busy, skip")
            return self._decide_local()
        try:
            system_content = prompts.interact_system_prompt()
            messages = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": event_hint},
            ]
            return self._stream_and_parse(
                messages, on_chunk=on_chunk, on_stream_end=on_stream_end,
                tag="interact"
            )
        finally:
            self._lock.release()



    def chat_decide(self, user_message: str, context: str = "", screenshot: bool = True) -> BehaviorOutput:
        t = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{t}] [Behavior] chat_decide(msg={user_message[:50]}, ctx={context[:30]})")

        if not self._client:
            return self._chat_decide_local(user_message)

        base64_img = self._prepare_image() if screenshot else None
        logger.info(f"[{t}] [Behavior] === LLM REQUEST (chat_decide) ===")
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
            messages = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": [
                    {"type": "text", "text": user_content},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_img}"}},
                ]},
            ]
        else:
            messages = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ]

        self._dump_context("chat_decide", messages)
        try:
            resp = self._llm_call(messages)
            content = resp.choices[0].message.content or ""
            logger.info(f"[{t}] [Behavior] === LLM RESPONSE (chat_decide) ===")
            logger.info(f"[{t}] [Behavior]   raw: {content}")
            result = self._parse_behavior(content)
            logger.info(f"[{t}] [Behavior]   parsed → {result}")
            return result
        except Exception as e:
            logger.error(f"[{t}] [Behavior] chat_decide failed: {e}")
            return BehaviorOutput(
                actions=[ActionStep("look_around", kwargs={"duration": 5})],
                speech="喔...我好像没听清",
            )

    def chat_decide_stream(self, user_message: str, context: str, screenshot: bool = True,
                           on_chunk=None, on_stream_end=None) -> BehaviorOutput:
        if not self._client:
            return self._chat_decide_local(user_message)
        if not self._lock.acquire(timeout=5):
            logger.warning("[Behavior] chat_decide_stream: busy, timeout")
            return BehaviorOutput(
                actions=[ActionStep("look_around", kwargs={"duration": 5})],
                speech="嚎……等一下，我还在想……",
            )
        try:
            base64_img = self._prepare_image() if screenshot else None
            messages = self._build_chat_messages(user_message, context, base64_img=base64_img)
            self._dump_context("chat_stream", messages)
            stream = self._llm_call_stream(messages)
            full_content = ""
            line_buffer = ""
            in_speech = False
            prefix_consumed = False
            speech_streamed = False

            for chunk in stream:
                if not chunk.choices:
                    continue
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

            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior] === LLM RESPONSE (chat_stream) ===")
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior]   raw: {full_content}")

            if "skill:" in full_content.lower():
                if on_stream_end:
                    on_stream_end()
                system_content = messages[0]["content"]
                result = self._execute_with_skills(full_content, system_content, on_chunk=on_chunk, on_stream_end=on_stream_end)
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

    @llm_retry(tag="Behavior")
    def _llm_call(self, messages: list, max_tokens: int = 4000):
        return self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=max_tokens,
        )

    def _llm_call_stream(self, messages: list, max_tokens: int = 4000):
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
        t = datetime.now().strftime("%H:%M:%S")
        self._dump_context(tag, messages)
        try:
            resp = self._llm_call(messages)
            content = resp.choices[0].message.content or ""
            logger.info(f"[{t}] [Behavior] === LLM RESPONSE ({tag}) ===")
            logger.info(f"[{t}] [Behavior]   finish_reason: {resp.choices[0].finish_reason}")
            if hasattr(resp, 'usage') and resp.usage:
                logger.info(f"[{t}] [Behavior]   usage: {resp.usage}")
            logger.info(f"[{t}] [Behavior]   raw: {content}")
            result = self._execute_with_skills(content, system_content)
            logger.info(f"[{t}] [Behavior]   parsed → {result}")
            return result
        except Exception as e:
            logger.exception(f"[{t}] [Behavior]   {tag} LLM call failed: {type(e).__name__}: {e}")
            logger.warning(f"[{t}] [Behavior]   falling back to local")
            return self._decide_local()

    def _stream_and_parse(self, messages: list, on_chunk=None, on_stream_end=None, tag: str = "") -> BehaviorOutput:
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
                if not chunk.choices:
                    continue
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

            if buffer.strip():
                self._finish_line(buffer, actions, speech_parts, skill_lines, summary_holder, memory_holder)

            # Skill 调用 → 执行技能后二次 LLM 调用
            if skill_lines:
                if on_stream_end:
                    on_stream_end()
                full_content = "\n".join(
                    [f"Speech: {s}" for s in speech_parts] +
                    [f"Action: {a.name} {' '.join(map(str, a.args))}" for a in actions] +
                    skill_lines
                )
                logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior] === LLM RESPONSE ({tag}) ===")
                logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior]   raw: {full_content}")
                return self._execute_with_skills(full_content, messages[0]["content"], on_chunk=on_chunk, on_stream_end=on_stream_end)

            raw = "\n".join(
                ([f"Summary: {summary_holder[0]}"] if summary_holder else []) +
                [f"Speech: {s}" for s in speech_parts] +
                [f"Action: {a.name} {' '.join(map(str, a.args))} {' '.join(f'{k}={v}' for k, v in a.kwargs.items())}".strip() for a in actions] +
                skill_lines
            )
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior] === LLM RESPONSE ({tag}) ===")
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior]   raw: {raw}")

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

    def _execute_with_skills(self, first_content: str, system_content: str, on_chunk=None,
                             on_stream_end=None, max_rounds: int = 3) -> BehaviorOutput:
        from pet.skills.executor import SkillExecutor

        executor = SkillExecutor()
        current_content = first_content
        history = []
        speech_streamed = False
        # 第 1 轮用完整 system prompt，后续轮次用精简版
        personality = config.PET_PERSONALITY or ""
        short_system = prompts.skill_round_system_prompt(personality)

        for round_idx in range(max_rounds):
            tool_calls = executor.parse_skill_lines(current_content)
            if not tool_calls:
                result = self._parse_behavior(current_content)
                result.speech_streamed = speech_streamed
                return result

            results = executor.execute(tool_calls)
            result_text, images = executor.format_results(results)
            logger.info(f"[Behavior] skill_round_{round_idx+1} executed {len(tool_calls)} tool(s), images={len(images)}")

            history.append({"role": "assistant", "content": current_content})
            # 插件有图且模型支持视觉时，构建多模态消息
            if images and self.has_vision:
                user_content = [
                    {"type": "text", "text": prompts.skill_result_user_prompt(result_text)}
                ]
                for img_uri in images:
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": img_uri}
                    })
                history.append({"role": "user", "content": user_content})
            else:
                history.append({"role": "user", "content": prompts.skill_result_user_prompt(result_text)})

            sys = system_content if round_idx == 0 else short_system
            messages = [{"role": "system", "content": sys}] + history
            tag = f"skill_round_{round_idx+1}"
            self._dump_context(tag, messages)
            try:
                if on_chunk:
                    current_content = self._stream_text_raw(messages, on_chunk=on_chunk, tag=tag)
                    speech_streamed = True
                    if on_stream_end:
                        on_stream_end()
                else:
                    resp = self._llm_call(messages)
                    current_content = resp.choices[0].message.content or ""
                    logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior] === LLM RESPONSE ({tag}) ===")
                    logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior]   raw: {current_content}")
            except Exception as e:
                logger.error(f"[Behavior] {tag} failed: {e}")
                result = self._parse_behavior(current_content)
                result.speech_streamed = speech_streamed
                return result

        logger.warning(f"[Behavior] reached MAX_ROUNDS={max_rounds}, force terminate skill loop")
        result = self._parse_behavior(current_content)
        result.speech_streamed = speech_streamed
        return result

    def _stream_text_raw(self, messages: list, on_chunk=None, tag: str = "") -> str:
        """流式调用并返回原始文本，不做 Skill/Action 行解析，避免多轮 Skill 循环中递归触发。"""
        stream = self._llm_call_stream(messages)
        full_content = ""
        line_buffer = ""
        in_speech = False
        prefix_consumed = False

        for chunk in stream:
            if not chunk.choices:
                continue
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

        logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior] === LLM RESPONSE ({tag}) ===")
        logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior]   raw: {full_content}")
        return full_content

    _LOCAL_ACTIONS = [
        ("idle", "Just hanging out..."),
        ("driving", "Riding my little scooter!"),
        ("walk", "Bouncy bouncy!"),
        ("shake_arms", "Yay! So happy!"),
        ("look_around", "What's going on over there?"),
        ("stretch", "Ahh, that's better..."),
        ("sit", "Taking a little break."),
        ("sleep", "Getting sleepy... zzz..."),
        ("thinking", "Hmm..."),
    ]

    def _decide_local(self) -> BehaviorOutput:
        import random
        self._trim_history()
        action, speech = random.choice(self._LOCAL_ACTIONS)
        t = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{t}] [Behavior] _decide_local → {action} / {speech}")

        # walk 类动作需要方向和距离参数
        if action in ("driving", "walk"):
            direction = random.choice(["left", "right"])
            distance = random.randint(300, 800)
            step = ActionStep(action, args=(direction, distance))
        else:
            step = ActionStep(action)

        return BehaviorOutput(
            actions=[step],
            speech=speech,
        )

    def _chat_decide_local(self, user_message: str) -> BehaviorOutput:
        return BehaviorOutput(
            actions=[ActionStep("look_around", kwargs={"duration": 5})],
            speech=f"（听到了：{user_message[:10]}...但我还不会回应）",
        )

    def _prepare_image(self) -> Optional[str]:
        if not self.has_vision or not self._screen_reader:
            return None
        return self._screen_reader.prepare_image(vision_scale=config.VISION_SCALE)

    def _build_context_str(self, context: str) -> str:
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
