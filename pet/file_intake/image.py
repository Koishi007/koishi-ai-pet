"""图片读取：先 draft 后判像素闸门，通过后再解码与缩放。"""

from __future__ import annotations

import logging

from PIL import Image

logger = logging.getLogger(__name__)

MAX_EDGE = 1024


def load_image(path: str, max_pixels: int) -> Image.Image | None:
    """返回可直接编码的 RGB 图片；闸门命中或解析失败返回 None。

    顺序固定：Image.open 只读文件头，draft 让支持降采样的格式在解码阶段就变小，
    闸门判的是 draft 之后的尺寸，因此 JPEG 直出照片不会被像素上限误伤。

    像素上限取闸门与 Pillow 解压炸弹阈值中较小者：超过两倍阈值时 Image.open 直接抛错，
    一倍到两倍之间 Pillow 只发警告并照常打开，这里自行拒绝，避免改动进程级的 warnings 过滤器。
    """
    try:
        bomb = Image.MAX_IMAGE_PIXELS
        image = Image.open(path)
        image.draft("RGB", (MAX_EDGE, MAX_EDGE))
        pixels = image.size[0] * image.size[1]
        if pixels >= max_pixels or (bomb and pixels > bomb):
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
