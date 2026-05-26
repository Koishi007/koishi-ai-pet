import base64
from datetime import datetime
import io
import logging
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image
from openai import OpenAI
from pet.brain.base import BrainMixin
from pet.brain import prompts
from pet.action.registry import ACTION_NAMES
from config import config

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


class Behavior(BrainMixin):

    def __init__(self):
        super().__init__()
        self._client = None
        self._model = None
        self._setup()

        self._responses = {
            "idle": [
                "Just hanging out...",
                "What a nice day!",
                "Watching you work is fun!",
            ],
        }
        self._idx = 0

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
            )
            self._model = model or "llama3.2"
        elif brain == "llm" and key:
            self._client = OpenAI(
                api_key=key,
                base_url=url or "",
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

        if len(self._context) > 10:
            self._context[:] = self._context[-10:]

        system_content = prompts.vision_system_prompt()
        if config.PET_PERSONALITY:
            system_content += f"\n\n=== 你的性格 ===\n{config.PET_PERSONALITY}"

        context_str = self._build_context_str(context)
        text_prompt = prompts.vision_decide_prompt(context_str)
        if config.VISION_PROMPT_EXTRA:
            text_prompt += "\n\n" + config.VISION_PROMPT_EXTRA.replace("{context}", context_str)

        messages = [
            {"role": "system", "content": system_content},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{base64_img}"},
                    },
                ],
            },
        ]
        for i, m in enumerate(messages):
            if isinstance(m["content"], str):
                preview = m["content"][:120].replace("\n", "\\n")
                logger.debug(f"[{t}] [Behavior]   msg[{i}] role={m['role']}: \"{preview}...\"")
            else:
                parts_desc = ", ".join(p["type"] for p in m["content"])
                logger.debug(f"[{t}] [Behavior]   msg[{i}] role={m['role']}: [{parts_desc}]")

        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=4000,
            )
            content = resp.choices[0].message.content or ""
            logger.info(f"[{t}] [Behavior] === LLM RESPONSE (vision) ===")
            logger.info(f"[{t}] [Behavior]   finish_reason: {resp.choices[0].finish_reason}")
            if hasattr(resp, 'usage') and resp.usage:
                logger.info(f"[{t}] [Behavior]   usage: {resp.usage}")
            logger.debug(f"[{t}] [Behavior]   raw: {content}")
            result = self._parse_behavior(content)
            logger.info(f"[{t}] [Behavior]   parsed → {result}")
            return result
        except Exception as e:
            logger.error(f"[{t}] [Behavior]   vision LLM call failed: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            logger.warning(f"[{t}] [Behavior]   falling back to local")
            return self._decide_local()

    def _decide_non_vision(self, context: str) -> BehaviorOutput:
        t = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{t}] [Behavior] === LLM REQUEST (non_vision) ===")
        logger.info(f"[{t}] [Behavior]   model: {self._model}")
        logger.info(f"[{t}] [Behavior]   context({len(context)} chars): \"{context[:80]}\"")
        logger.info(f"[{t}] [Behavior]   history: {len(self._context)} entries")

        if len(self._context) > 10:
            self._context[:] = self._context[-10:]

        context_str = self._build_context_str(context)
        prompt = prompts.non_vision_decide_prompt(context_str)
        if config.NON_VISION_PROMPT_EXTRA:
            prompt += "\n\n" + config.NON_VISION_PROMPT_EXTRA.replace("{context}", context_str)
        system_content = prompts.non_vision_system_prompt()
        if config.PET_PERSONALITY:
            system_content += f"\n\n=== 你的性格 ===\n{config.PET_PERSONALITY}"
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt},
        ]
        for i, m in enumerate(messages):
            preview = m["content"][:120].replace("\n", "\\n")
            logger.debug(f"[{t}] [Behavior]   msg[{i}] role={m['role']}: \"{preview}...\"")
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=4000,
            )
            content = resp.choices[0].message.content or ""
            logger.info(f"[{t}] [Behavior] === LLM RESPONSE (non_vision) ===")
            logger.info(f"[{t}] [Behavior]   finish_reason: {resp.choices[0].finish_reason}")
            if hasattr(resp, 'usage') and resp.usage:
                logger.info(f"[{t}] [Behavior]   usage: {resp.usage}")
            logger.debug(f"[{t}] [Behavior]   raw: {content}")
            result = self._parse_behavior(content)
            logger.info(f"[{t}] [Behavior]   parsed → {result}")
            return result
        except Exception as e:
            logger.error(f"[{t}] [Behavior]   LLM call failed: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            logger.warning(f"[{t}] [Behavior]   falling back to local")
            return self._decide_local()

    def _parse_behavior(self, content: str) -> BehaviorOutput:
        actions: list = []
        speech = None
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
        if not actions:
            actions.append(ActionStep("idle"))
        return BehaviorOutput(actions=actions, speech=speech)

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

    def _decide_local(self) -> BehaviorOutput:
        import random
        if len(self._context) > 10:
            self._context[:] = self._context[-10:]
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

    def decide_action(self, context: str = "") -> str:
        result = self.decide(context)
        return result.actions[0].name if result.actions else "idle"

    def think(self, prompt: str) -> str:
        t = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{t}] [Behavior] think(prompt={prompt[:50]})")
        if self._client:
            reply = self._think_remote(prompt)
        else:
            reply = self._think_local(prompt)
        logger.info(f"[{t}] [Behavior] think → \"{reply[:60]}\"")
        return reply

    def _think_remote(self, prompt: str) -> str:
        t = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{t}] [Behavior] === LLM REQUEST (think) ===")
        logger.info(f"[{t}] [Behavior]   model: {self._model}, ctx_len={len(self._context)}")
        system_content = config.CHAT_PROMPT_SYSTEM
        if config.PET_PERSONALITY:
            system_content += f"\n\n=== 你的性格 ===\n{config.PET_PERSONALITY}"
        messages = [
            {"role": "system", "content": system_content},
        ]
        # 仅保留最近 10 条历史，避免上下文窗口溢出
        if len(self._context) > 10:
            self._context[:] = self._context[-10:]
        for ctx in self._context:
            messages.append({"role": "user", "content": ctx})
        messages.append({"role": "user", "content": prompt})
        for i, m in enumerate(messages):
            preview = m["content"][:120].replace("\n", "\\n")
            logger.debug(f"[{t}] [Behavior]   msg[{i}] role={m['role']}: \"{preview}...\"")

        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=4000,
        )
        content = response.choices[0].message.content or ""
        logger.info(f"[{t}] [Behavior] === LLM RESPONSE ===")
        logger.info(f"[{t}] [Behavior]   finish_reason: {response.choices[0].finish_reason}")
        if hasattr(response, 'usage') and response.usage:
            logger.info(f"[{t}] [Behavior]   usage: {response.usage}")
        logger.debug(f"[{t}] [Behavior]   raw: {content}")
        return content

    def _think_local(self, prompt: str) -> str:
        t = datetime.now().strftime("%H:%M:%S")
        reply = self._rotate("idle")
        logger.info(f"[{t}] [Behavior] _think_local → \"{reply}\"")
        return reply

    def _rotate(self, key: str) -> str:
        responses = self._responses.get(key, ["Hmm..."])
        resp = responses[self._idx % len(responses)]
        self._idx += 1
        return resp
