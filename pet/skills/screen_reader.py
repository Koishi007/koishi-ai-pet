"""屏幕阅读技能 —— 截取屏幕截图，供视觉 AI 分析。"""

import logging
from typing import Optional

from PIL import Image
import mss

logger = logging.getLogger(__name__)


class ScreenReader:
    def __init__(self):
        self._enabled = False
        self._sct: Optional[mss.mss] = None

    def enable(self):
        self._enabled = True
        logger.info("屏幕阅读已启用")

    def disable(self):
        self._enabled = False
        logger.info("屏幕阅读已禁用")

    # ── 截图 ──

    def capture_fullscreen(self, all_screens: bool = False) -> Optional[Image.Image]:
        """截取全屏并返回 PIL Image。

        参数:
            all_screens: True 捕获所有显示器（虚拟桌面），False 仅主显示器。

        返回:
            PIL Image 对象；失败或未启用时返回 None。
        """
        if not self._enabled:
            logger.warning("屏幕阅读已禁用，无法截图")
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

    def capture_area(
        self, x: int, y: int, width: int, height: int
    ) -> Optional[Image.Image]:
        """截取屏幕指定区域。

        参数:
            x, y: 区域左上角坐标。
            width, height: 区域宽高。

        返回:
            PIL Image 对象；失败或未启用时返回 None。
        """
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

    # ── 内部 ──

    def _get_sct(self) -> mss.mss:
        """延迟初始化 mss 实例。"""
        if self._sct is None:
            self._sct = mss.mss()
        return self._sct
