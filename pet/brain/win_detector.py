"""Win32 窗口枚举"""

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
GW_HWNDPREV = 3
MIN_WINDOW_SIZE = 50
OCCLUSION_THRESHOLD = 0.8
DWMWA_CLOAKED = 14


def _is_cloaked(hwnd: int) -> bool:
    """检查窗口是否被系统隐身（UWP 后台应用等）。"""
    try:
        cloaked = ctypes.c_uint(0)
        hr = dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED,
            ctypes.byref(cloaked), ctypes.sizeof(cloaked),
        )
        return hr == 0 and cloaked.value != 0
    except Exception:
        return False


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


def _compute_occluded_area(
    target_rect: tuple[int, int, int, int],
    above_rects: list[tuple[int, int, int, int]],
) -> int:
    """坐标压缩网格扫描法：计算遮挡矩形的精确并集面积。"""
    if not above_rects:
        return 0

    clipped = []
    for r in above_rects:
        cx1 = max(target_rect[0], r[0])
        cy1 = max(target_rect[1], r[1])
        cx2 = min(target_rect[2], r[2])
        cy2 = min(target_rect[3], r[3])
        if cx1 < cx2 and cy1 < cy2:
            clipped.append((cx1, cy1, cx2, cy2))
    if not clipped:
        return 0

    xs: set[int] = set()
    ys: set[int] = set()
    for r in clipped:
        xs.add(r[0]); xs.add(r[2])
        ys.add(r[1]); ys.add(r[3])
    xs_sorted = sorted(xs)
    ys_sorted = sorted(ys)

    x_idx = {v: i for i, v in enumerate(xs_sorted)}
    y_idx = {v: i for i, v in enumerate(ys_sorted)}

    grid = [[False] * (len(ys_sorted) - 1) for _ in range(len(xs_sorted) - 1)]
    for r in clipped:
        for i in range(x_idx[r[0]], x_idx[r[2]]):
            for j in range(y_idx[r[1]], y_idx[r[3]]):
                grid[i][j] = True

    area = 0
    for i in range(len(xs_sorted) - 1):
        for j in range(len(ys_sorted) - 1):
            if grid[i][j]:
                area += (xs_sorted[i + 1] - xs_sorted[i]) * (ys_sorted[j + 1] - ys_sorted[j])
    return area


def is_window_occluded(hwnd: int, threshold: float = OCCLUSION_THRESHOLD, skip_hwnd: int = 0) -> bool:
    """两阶段遮挡检测：快速上界 → 精确网格扫描。

    skip_hwnd: 要跳过的窗口句柄（如宠物自身窗口）。
    """
    rect = get_window_rect(hwnd)
    if rect is None:
        return True

    target_area = (rect[2] - rect[0]) * (rect[3] - rect[1])
    if target_area <= 0:
        return True

    covered_upper_bound = 0
    above_rects: list[tuple[int, int, int, int]] = []
    current = user32.GetWindow(hwnd, GW_HWNDPREV)

    while current:
        if (current != skip_hwnd
                and user32.IsWindowVisible(current)
                and not user32.IsIconic(current)
                and not _is_cloaked(current)):
            above_rect = wintypes.RECT()
            if user32.GetWindowRect(current, ctypes.byref(above_rect)):
                ox1 = max(rect[0], above_rect.left)
                oy1 = max(rect[1], above_rect.top)
                ox2 = min(rect[2], above_rect.right)
                oy2 = min(rect[3], above_rect.bottom)
                if ox1 < ox2 and oy1 < oy2:
                    overlap = (ox2 - ox1) * (oy2 - oy1)
                    covered_upper_bound += overlap
                    above_rects.append((ox1, oy1, ox2, oy2))
        current = user32.GetWindow(current, GW_HWNDPREV)

    # 上界未超阈值 → 一定没被遮挡
    if covered_upper_bound <= target_area * threshold:
        return False

    exact_covered = _compute_occluded_area(rect, above_rects)
    return exact_covered / target_area > threshold


def get_visible_windows() -> list[dict]:
    """返回所有可见顶层窗口，过滤掉工具窗口、最小化窗口、隐身窗口和极小窗口。"""
    windows = []

    def callback(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.IsIconic(hwnd):
                return True
            if _is_cloaked(hwnd):
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
    proc = WNDENUMPROC(callback)
    user32.EnumWindows(proc, 0)
    return windows
