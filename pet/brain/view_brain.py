"""屏幕分析 brain —— 接收屏幕截图，base64 编码后调用视觉模型分析。"""

import base64
import io
import traceback

from PIL import Image
from openai import OpenAI
from config import config


class ViewBrain:
    """屏幕分析 brain。

    接收 PIL Image 截图，base64 编码后发送给视觉模型。
    BRAIN=llm 时调用 API，否则返回空字符串。
    """

    def __init__(self):
        brain = config.VIEW_BRAIN or "local"
        print(f"[ViewBrain] __init__: BRAIN={brain}, KEY={'***' if config.VIEW_MODEL_KEY else 'EMPTY'}, URL={config.VIEW_MODEL_URL or '(empty)'}")
        if brain == "llm" and config.VIEW_MODEL_KEY:
            self._client = OpenAI(
                api_key=config.VIEW_MODEL_KEY,
                base_url=config.VIEW_MODEL_URL or "",
            )
            self._model = config.VIEW_MODEL or config.CHAT_MODEL
            print(f"[ViewBrain] Client created, model={self._model}")
        else:
            self._client = None
            print(f"[ViewBrain] Client NOT created (BRAIN={brain}, KEY empty={not config.VIEW_MODEL_KEY})")


    def analyze(self, image: Image.Image, prompt: str = "") -> str:
        """分析屏幕截图。"""
        if not self._client:
            print("[ViewBrain.analyze] SKIP: client is None")
            return ""
        print(f"[ViewBrain.analyze] image={image.size}, prompt=\"{prompt[:50]}\"")
        base64_img = self._encode_base64(image)
        print(f"[ViewBrain.analyze] base64 encoded, length={len(base64_img)}")
        result = self._call_vision_api(base64_img, prompt)
        print(f"[ViewBrain.analyze] result=\"{result[:100]}\"")
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


    def _encode_base64(self, image: Image.Image) -> str:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def _call_vision_api(self, base64_img: str, prompt: str) -> str:
        """调用视觉模型 API 分析截图。"""
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
            print(f"[ViewBrain._call_vision_api] model={self._model}, prompt_len={len(prompt or config.VIEW_PROMPT_VISION)}")
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=500,
            )
            # ── 全量打印响应 ──
            print(f"[ViewBrain._call_vision_api] ===== RESPONSE FULL DUMP =====")
            print(f"  id: {resp.id}")
            print(f"  model: {resp.model}")
            print(f"  created: {resp.created}")
            print(f"  usage: {resp.usage}")
            print(f"  choices count: {len(resp.choices)}")
            if resp.choices:
                choice = resp.choices[0]
                print(f"  finish_reason: {choice.finish_reason}")
                print(f"  message.role: {choice.message.role}")
                print(f"  message.content: {repr(choice.message.content)}")
                # 检查是否有 tool_calls、function_call 等其他字段
                if hasattr(choice.message, 'tool_calls') and choice.message.tool_calls:
                    print(f"  tool_calls: {choice.message.tool_calls}")
            print(f"[ViewBrain._call_vision_api] ===== END FULL DUMP =====")

            content = resp.choices[0].message.content
            # ── 校验内容是否为业务错误 ──
            if content is None:
                print(f"[ViewBrain._call_vision_api] WARNING: content is None, finish_reason={resp.choices[0].finish_reason}")
                return ""
            if resp.choices[0].finish_reason not in ("stop", "length", None):
                print(f"[ViewBrain._call_vision_api] WARNING: unexpected finish_reason={resp.choices[0].finish_reason}")
            return content
        except Exception as e:
            print(f"[ViewBrain._call_vision_api] EXCEPTION: {type(e).__name__}: {e}")
            traceback.print_exc()
            return ""
