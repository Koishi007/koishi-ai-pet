"""图片读取：先 draft 后判像素闸门，通过后再解码与缩放。"""

from __future__ import annotations

import logging
import warnings

from PIL import Image

logger = logging.getLogger(__name__)

MAX_EDGE = 1024


def load_image(path: str, max_pixels: int) -> Image.Image | None:
    """返回可直接编码的 RGB 图片；闸门命中或解析失败返回 None。

    顺序固定：Image.open 只读文件头，draft 让支持降采样的格式在解码阶段就变小，
    闸门判的是 draft 之后的尺寸，因此 JPEG 直出照片不会被像素上限误伤。
    """
    try:
        with warnings.catch_warnings():
            # Pillow 在 1 倍阈值只发警告并照常解码，转成异常才能走降级路径
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(path)
            image.draft("RGB", (MAX_EDGE, MAX_EDGE))
            if image.size[0] * image.size[1] >= max_pixels:
                logger.info(f"[FileIntake] over pixel gate: {path} {image.size}")
                image.close()
                return None
            if image.mode != "RGB":
                converted = image.convert("RGB")
                image.close()
                image = converted
            image.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
            image.load()
            result = image.copy()
            image.close()
            return result
    except Exception as e:
        logger.info(f"[FileIntake] image load failed: {path} {type(e).__name__}: {e}")
        return None
