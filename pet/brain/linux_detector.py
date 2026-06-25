"""Linux X11 窗口枚举 —— 基于 python-xlib + EWMH，与 Win32/macOS 版保持相同接口。

仅支持 X11 桌面环境（GNOME/KDE/XFCE on X11），不支持 Wayland。
坐标系为左上原点，与 Qt 一致，无需 Y 翻转。
"""

import logging
import threading

from Xlib import X, display

logger = logging.getLogger(__name__)

MIN_WINDOW_SIZE = 50
OCCLUSION_THRESHOLD = 0.8

# EWMH 原子常量
_NET_CLIENT_LIST = "_NET_CLIENT_LIST"
_NET_CLIENT_LIST_STACKING = "_NET_CLIENT_LIST_STACKING"
_NET_WM_NAME = "_NET_WM_NAME"
_NET_WM_STATE = "_NET_WM_STATE"
_NET_WM_STATE_HIDDEN = "_NET_WM_STATE_HIDDEN"
_NET_WM_WINDOW_TYPE = "_NET_WM_WINDOW_TYPE"
_NET_WM_WINDOW_TYPE_DESKTOP = "_NET_WM_WINDOW_TYPE_DESKTOP"
_NET_WM_WINDOW_TYPE_DOCK = "_NET_WM_WINDOW_TYPE_DOCK"
_NET_WM_WINDOW_TYPE_TOOLBAR = "_NET_WM_WINDOW_TYPE_TOOLBAR"

_dpy = None
_dpy_lock = threading.RLock()
_atom_cache: dict[str, int] = {}


def _get_display() -> display.Display:
    """获取或创建 X 连接，检测断连后自动重连。"""
    global _dpy
    if _dpy is not None and _dpy.socket_error is not None:
        logger.warning("[linux_detector] X connection lost, reconnecting")
        _dpy = None
    if _dpy is None:
        _dpy = display.Display()
    return _dpy


def _get_atom(name: str) -> int:
    """获取 EWMH atom，首次查询后缓存（atom 不可变）。"""
    if name not in _atom_cache:
        _atom_cache[name] = _get_display().intern_atom(name)
    return _atom_cache[name]


def _get_property(win, name: str):
    """读取窗口的 EWMH 属性，返回 list 或 None。"""
    try:
        atom = _get_atom(name)
        prop = win.get_full_property(atom, X.AnyPropertyType)
        if prop is None:
            return None
        return prop.value
    except Exception:
        return None


def _get_window_title(win) -> str:
    """优先用 _NET_WM_NAME (UTF-8)，fallback 到 XFetchName (Latin-1)。"""
    try:
        atom = _get_atom(_NET_WM_NAME)
        text = win.get_full_text_property(atom)
        if text:
            return text
    except Exception:
        pass
    try:
        return win.get_wm_name() or ""
    except Exception:
        return ""


def _is_hidden(win) -> bool:
    """检查窗口是否最小化（_NET_WM_STATE_HIDDEN）。"""
    states = _get_property(win, _NET_WM_STATE)
    if not states:
        return False
    hidden_atom = _get_atom(_NET_WM_STATE_HIDDEN)
    return hidden_atom in states


def _get_window_type(win) -> list:
    """返回 _NET_WM_WINDOW_TYPE 原子列表。"""
    return _get_property(win, _NET_WM_WINDOW_TYPE) or []


def _is_ignored_type(win) -> bool:
    """过滤桌面、dock、toolbar 等非普通窗口。"""
    win_types = _get_window_type(win)
    if not win_types:
        return False
    ignored = [_get_atom(t) for t in (
        _NET_WM_WINDOW_TYPE_DESKTOP,
        _NET_WM_WINDOW_TYPE_DOCK,
        _NET_WM_WINDOW_TYPE_TOOLBAR,
    )]
    return any(t in ignored for t in win_types)


def _get_geometry(win) -> tuple[int, int, int, int] | None:
    """用 XGetGeometry + translate_coords 获取窗口屏幕绝对矩形 (left, top, right, bottom)。"""
    try:
        geom = win.get_geometry()
        root = _get_display().screen().root
        # root.translate_coords(win, 0, 0) → 把 win 原点翻译到 root 坐标系
        translated = root.translate_coords(win, 0, 0)
        x = translated.x
        y = translated.y
        w = geom.width
        h = geom.height
        return (x, y, x + w, y + h)
    except Exception:
        return None


def is_window_alive(window_id: int) -> bool:
    """O(1) 检查窗口 ID 是否仍然有效。"""
    with _dpy_lock:
        try:
            win = _get_display().create_resource_object("window", window_id)
            win.get_geometry()
            return True
        except Exception:
            return False


def get_window_rect(window_id: int) -> tuple[int, int, int, int] | None:
    """获取单个窗口的屏幕矩形，失败返回 None。"""
    with _dpy_lock:
        try:
            win = _get_display().create_resource_object("window", window_id)
            return _get_geometry(win)
        except Exception:
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


def _get_stacking_list() -> list[int]:
    """返回 _NET_CLIENT_LIST_STACKING（底→顶 Z 序）。

    回退到 _NET_CLIENT_LIST 时返回的是映射顺序而非 Z 序，遮挡检测可能不准确。
    """
    dpy = _get_display()
    root = dpy.screen().root
    stacking = _get_property(root, _NET_CLIENT_LIST_STACKING)
    if stacking:
        return list(stacking)
    client_list = _get_property(root, _NET_CLIENT_LIST)
    if client_list:
        logger.debug("[linux_detector] _NET_CLIENT_LIST_STACKING unavailable, "
                     "falling back to _NET_CLIENT_LIST (mapping order, not Z-order)")
    return list(client_list) if client_list else []


def is_window_occluded(window_id: int, threshold: float = OCCLUSION_THRESHOLD,
                       skip_hwnd: int = 0) -> bool:
    """基于 Z 序的遮挡检测：收集目标窗口之上的所有可见窗口矩形，计算遮挡面积比例。

    skip_hwnd: 要跳过的窗口 ID（如宠物自身窗口）。
    """
    with _dpy_lock:
        rect = get_window_rect(window_id)
        if rect is None:
            return True

        target_area = (rect[2] - rect[0]) * (rect[3] - rect[1])
        if target_area <= 0:
            return True

        # 获取 Z 序（底→顶），找到目标窗口位置，收集其上方的窗口
        stacking = _get_stacking_list()
        try:
            target_idx = stacking.index(window_id)
        except ValueError:
            return False

        dpy = _get_display()
        above_rects: list[tuple[int, int, int, int]] = []
        for wid in stacking[target_idx + 1:]:
            if wid == skip_hwnd:
                continue
            try:
                win = dpy.create_resource_object("window", wid)
            except Exception:
                continue
            if _is_hidden(win):
                continue
            above_rect = _get_geometry(win)
            if above_rect is None:
                continue
            # 计算交集
            ox1 = max(rect[0], above_rect[0])
            oy1 = max(rect[1], above_rect[1])
            ox2 = min(rect[2], above_rect[2])
            oy2 = min(rect[3], above_rect[3])
            if ox1 < ox2 and oy1 < oy2:
                above_rects.append((ox1, oy1, ox2, oy2))

        covered = _compute_occluded_area(rect, above_rects)
        return covered / target_area > threshold


def get_visible_windows() -> list[dict]:
    """返回所有可见顶层窗口，过滤最小化、桌面、dock 等非普通窗口和极小窗口。"""
    with _dpy_lock:
        dpy = _get_display()
        stacking = _get_stacking_list()
        if not stacking:
            logger.warning("[linux_detector] _NET_CLIENT_LIST 不可用，窗口枚举失败")
            return []

        windows = []
        for wid in stacking:
            try:
                win = dpy.create_resource_object("window", wid)
            except Exception:
                continue

            if _is_hidden(win):
                continue
            if _is_ignored_type(win):
                continue

            rect = _get_geometry(win)
            if rect is None:
                continue

            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            if w < MIN_WINDOW_SIZE or h < MIN_WINDOW_SIZE:
                continue

            title = _get_window_title(win)
            if not title:
                continue

            windows.append({
                "hwnd": wid,
                "title": title,
                "rect": rect,
            })

        return windows
