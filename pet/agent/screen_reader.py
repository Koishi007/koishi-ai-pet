"""屏幕截图能力 —— 桌宠固有能力，供视觉 AI 分析。"""

import base64
import io
import logging
from typing import Optional

from PIL import Image
import mss

from config import config

logger = logging.getLogger(__name__)


class ScreenReader:
    def __init__(self):
        self._enabled = False
        self._sct: Optional[mss.mss] = None

    # ══ 截图 ══

    def enable(self):
        self._enabled = True
        logger.info("屏幕截图已启用")

    def disable(self):
        self._enabled = False
        if self._sct:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None
        logger.info("屏幕截图已禁用")

    def capture_fullscreen(self, all_screens: bool = False) -> Optional[Image.Image]:
        if not self._enabled:
            logger.warning("屏幕截图已禁用，无法截图")
            return None
        try:
            sct = self._get_sct()
            monitor_index = 0 if all_screens else 1
            monitor = sct.monitors[monitor_index]
            sct_img = sct.grab(monitor)
            return Image.frombytes(
                "RGB", sct_img.size, sct_img.bgra, "raw", "BGRX"
            )
        except Exception as e:
            logger.error(f"截图失败：{e}")
            return None

    def capture_area(self, x: int, y: int, width: int, height: int) -> Optional[Image.Image]:
        if not self._enabled:
            return None
        try:
            sct = self._get_sct()
            monitor = {"top": y, "left": x, "width": width, "height": height}
            sct_img = sct.grab(monitor)
            return Image.frombytes(
                "RGB", sct_img.size, sct_img.bgra, "raw", "BGRX"
            )
        except Exception as e:
            logger.error(f"区域截图失败：{e}")
            return None

    def _get_sct(self) -> mss.mss:
        """延迟初始化 mss 实例。"""
        if self._sct is None:
            self._sct = mss.mss()
        return self._sct

    # ══ 图片预处理 ══

    def prepare_image(
        self,
        image: Optional[Image.Image] = None,
        vision_scale: float = 1.0,
        min_px: int = 1536,
    ) -> Optional[str]:
        """截图（可选）+ 缩放 + base64 编码，为 LLM 多模态调用准备。

        Args:
            image:        已有 PIL Image；为 None 时自动截取全屏
            vision_scale: 缩放比例（1.0 = 不缩放）
            min_px:       缩放后最长边下限（px）
        Returns:
            base64 编码字符串，截图失败时返回 None
        """
        if image is None:
            image = self.capture_fullscreen()
        if image is None:
            return None
        if vision_scale < 1.0:
            w, h = image.size
            new_w, new_h = int(w * vision_scale), int(h * vision_scale)
            if max(new_w, new_h) < min_px:
                ratio = min_px / max(new_w, new_h)
                new_w, new_h = int(new_w * ratio), int(new_h * ratio)
            logger.info(f"[ScreenReader] resize {w}x{h} (scale={vision_scale}) \u2192 {new_w}x{new_h}")
            image = image.resize((new_w, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")
