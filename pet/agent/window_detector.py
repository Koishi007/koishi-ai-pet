"""Win32 窗口枚举 —— 桌宠固有能力，用于检测可站立的窗口。"""

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
GW_HWNDPREV = 3
MIN_WINDOW_SIZE = 50
OCCLUSION_THRESHOLD = 0.8


def is_window_alive(hwnd: int) -> bool:
    """O(1) 检查窗口句柄是否仍然有效。"""
    return bool(user32.IsWindow(hwnd))


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """O(1) 获取单个窗口的屏幕矩形，不可见或失败返回 None。"""
    if not user32.IsWindowVisible(hwnd):
        return None
    rect = wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (rect.left, rect.top, rect.right, rect.bottom)
    return None


def is_window_occluded(hwnd: int, threshold: float = OCCLUSION_THRESHOLD, skip_hwnd: int = 0) -> bool:
    """沿 Z 序向上遍历，检查窗口是否被上层窗口遮挡超过 threshold 比例。
    
    skip_hwnd: 要跳过的窗口句柄（如宠物自身窗口），不计入遮挡面积。"""
    rect = get_window_rect(hwnd)
    if rect is None:
        return True

    target_area = (rect[2] - rect[0]) * (rect[3] - rect[1])
    if target_area <= 0:
        return True

    covered_area = 0
    current = user32.GetWindow(hwnd, GW_HWNDPREV)

    while current:
        if current != skip_hwnd and user32.IsWindowVisible(current) and not user32.IsIconic(current):
            above_rect = wintypes.RECT()
            if user32.GetWindowRect(current, ctypes.byref(above_rect)):
                ox1 = max(rect[0], above_rect.left)
                oy1 = max(rect[1], above_rect.top)
                ox2 = min(rect[2], above_rect.right)
                oy2 = min(rect[3], above_rect.bottom)
                if ox1 < ox2 and oy1 < oy2:
                    covered_area += (ox2 - ox1) * (oy2 - oy1)
                    if covered_area / target_area > threshold:
                        return True
        current = user32.GetWindow(current, GW_HWNDPREV)

    return False


def get_visible_windows() -> list[dict]:
    """返回所有可见顶层窗口，过滤掉工具窗口和极小窗口。"""
    windows = []

    def callback(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True

            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if (ex_style & WS_EX_TOOLWINDOW) and not (ex_style & WS_EX_APPWINDOW):
                return True

            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True

            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w < MIN_WINDOW_SIZE or h < MIN_WINDOW_SIZE:
                return True

            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)

            windows.append({
                "hwnd": hwnd,
                "title": title.value or "",
                "rect": (rect.left, rect.top, rect.right, rect.bottom),
            })
        except Exception:
            pass
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return windows
