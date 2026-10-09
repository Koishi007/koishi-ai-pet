"""按钮与提交的防抖：一次物理操作只产生一次请求。"""

import time


class Debounce:
    """窗口内的重复调用返回 False；每次放行都会刷新计时。

    clock 是给测试注入的时钟，默认 `time.monotonic`。
    """

    def __init__(self, window: float = 0.4, clock=time.monotonic):
        self._window = window
        self._clock = clock
        self._last = 0.0

    def ready(self) -> bool:
        now = self._clock()
        if now - self._last < self._window:
            return False
        self._last = now
        return True
