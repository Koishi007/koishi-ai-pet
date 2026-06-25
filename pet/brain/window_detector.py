"""窗口枚举 — 根据平台分发到 Win32 / Quartz / X11 后端"""

import sys

if sys.platform == "darwin":
    from pet.brain.mac_detector import (       # noqa: F401
        MIN_WINDOW_SIZE,
        OCCLUSION_THRESHOLD,
        is_window_alive,
        get_window_rect,
        is_window_occluded,
        get_visible_windows,
    )
elif sys.platform.startswith("linux"):
    from pet.brain.linux_detector import (     # noqa: F401
        MIN_WINDOW_SIZE,
        OCCLUSION_THRESHOLD,
        is_window_alive,
        get_window_rect,
        is_window_occluded,
        get_visible_windows,
    )
else:
    from pet.brain.win_detector import (       # noqa: F401
        MIN_WINDOW_SIZE,
        OCCLUSION_THRESHOLD,
        is_window_alive,
        get_window_rect,
        is_window_occluded,
        get_visible_windows,
    )
