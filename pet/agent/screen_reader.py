"""屏幕截图"""

import base64
import io
import logging
from typing import Optional

from PIL import Image
import mss

from pet.config import config

logger = logging.getLogger(__name__)


class ScreenReader:
    def __init__(self):
        self._enabled = False

    def enable(self):
        self._enabled = True
        logger.info("屏幕截图已启用")

    def disable(self):
        self._enabled = False
        logger.info("屏幕截图已禁用")

    def capture_fullscreen(self, all_screens: bool = False) -> Optional[Image.Image]:
        if not self._enabled:
            logger.warning("屏幕截图已禁用，无法截图")
            return None
        sct = mss.mss()
        try:
            monitor_index = 0 if all_screens else 1
            sct_img = sct.grab(sct.monitors[monitor_index])
            return Image.frombytes(
                "RGB", sct_img.size, sct_img.bgra, "raw", "BGRX"
            )
        except Exception as e:
            logger.error(f"截图失败：{e}")
            return None
        finally:
            sct.close()

    def capture_area(self, x: int, y: int, width: int, height: int) -> Optional[Image.Image]:
        if not self._enabled:
            return None
        sct = mss.mss()
        try:
            sct_img = sct.grab({"top": y, "left": x, "width": width, "height": height})
            return Image.frombytes(
                "RGB", sct_img.size, sct_img.bgra, "raw", "BGRX"
            )
        except Exception as e:
            logger.error(f"区域截图失败：{e}")
            return None
        finally:
            sct.close()

    def prepare_image(
        self,
        image: Optional[Image.Image] = None,
        vision_scale: float = 1.0,
        min_px: int = 1536,
    ) -> Optional[str]:
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
            logger.info(f"[ScreenReader] resize {w}x{h} (scale={vision_scale}) → {new_w}x{new_h}")
            image = image.resize((new_w, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")
