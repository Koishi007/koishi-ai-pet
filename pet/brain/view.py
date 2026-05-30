"""屏幕分析 brain —— 接收屏幕截图，base64 编码后调用视觉模型分析。"""

import base64
from datetime import datetime
import io
import logging
import traceback

from PIL import Image
from openai import OpenAI
from config import config
from pet.brain.llm_retry import llm_retry

logger = logging.getLogger(__name__)


class View:

    def __init__(self):
        brain = config.BRAIN or "local"
        logger.info(f"[View] __init__: BRAIN={brain}, KEY={'***' if config.LLM_KEY else 'EMPTY'}, URL={config.LLM_URL or '(empty)'}")
        if brain == "ollama":
            self._client = OpenAI(
                api_key="ollama",
                base_url=config.OLLAMA_BASE_URL,
                timeout=config.LLM_TIMEOUT,
            )
            self._model = config.LLM_MODEL or "llama3.2-vision"
        elif brain == "llm" and config.LLM_KEY:
            self._client = OpenAI(
                api_key=config.LLM_KEY,
                base_url=config.LLM_URL or "",
                timeout=config.LLM_TIMEOUT,
            )
            self._model = config.LLM_MODEL
        else:
            self._client = None

    def analyze(self, image: Image.Image, prompt: str = "") -> str:
        if not self._client:
            return ""
        logger.debug(f"[View.analyze] image={image.size}, prompt=\"{prompt[:50]}\"")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        base64_img = base64.b64encode(buf.getvalue()).decode("utf-8")
        logger.debug(f"[View.analyze] base64 encoded, length={len(base64_img)}")
        result = self._call_vision_api(base64_img, prompt)
        logger.debug(f"[View.analyze] result=\"{result[:100]}\"")
        return result

    def analyze_bytes(self, image_data: bytes, prompt: str = "") -> str:
        if not self._client:
            return ""
        try:
            image = Image.open(io.BytesIO(image_data))
            return self.analyze(image, prompt)
        except Exception:
            traceback.print_exc()
            return ""

    @llm_retry(tag="View")
    def _llm_call(self, messages: list):
        """带重试的非流式 LLM 调用（max_tokens=500）。"""
        return self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=500,
        )

    def _call_vision_api(self, base64_img: str, prompt: str) -> str:
        t = datetime.now().strftime("%H:%M:%S")
        try:
            messages = [
                {"role": "system", "content": config.VIEW_PROMPT_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt or config.VIEW_PROMPT_VISION},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{base64_img}"},
                        },
                    ],
                },
            ]
            logger.info(f"[{t}] [View] LLM REQ model={self._model} base64={len(base64_img)}b")
            logger.debug(f"[{t}] [View]   system: \"{config.VIEW_PROMPT_SYSTEM[:80]}...\"")
            logger.debug(f"[{t}] [View]   user.text: \"{(prompt or config.VIEW_PROMPT_VISION)[:80]}...\"")
            resp = self._llm_call(messages)
            logger.info(f"[{t}] [View] LLM RESP finish={resp.choices[0].finish_reason if resp.choices else 'N/A'} "
                        f"usage={resp.usage}")
            logger.debug(f"[{t}] [View]   id={resp.id} model={resp.model} created={resp.created}")
            if resp.choices:
                choice = resp.choices[0]
                content = choice.message.content
                logger.debug(f"[{t}] [View]   raw: {content}")
                if hasattr(choice.message, 'tool_calls') and choice.message.tool_calls:
                    logger.debug(f"[{t}] [View]   tool_calls: {choice.message.tool_calls}")

            content = resp.choices[0].message.content
            if content is None:
                logger.warning(f"[{t}] [View] WARNING: content is None, finish_reason={resp.choices[0].finish_reason}")
                return ""
            if resp.choices[0].finish_reason not in ("stop", "length", None):
                logger.warning(f"[{t}] [View] WARNING: unexpected finish_reason={resp.choices[0].finish_reason}")
            return content
        except Exception as e:
            logger.error(f"[{t}] [View] EXCEPTION: {type(e).__name__}: {e}")
            traceback.print_exc()
            return ""
