"""屏幕边缘贴边的纯几何与策略计算。

窗口坐标与屏幕可用区坐标均为 Qt 逻辑像素；右侧约定与 `GravitySystem` 的
`right_lim = availableGeometry().right() - width` 一致。
"""

# 贴边后保留的可交互宽度占比；
EDGE_VISIBLE_RATIO = 0.70

_SIDES = ("left", "right")


def pick_dock_side(x: int, width: int,
                   screen_left: int, screen_right: int,
                   snap_px: int) -> str | None:
    """松手位置是否落在左/右边缘的吸附阈值内；同时命中时取更近的一侧。"""
    if snap_px < 0:
        return None
    candidates: list[tuple[int, str]] = []
    left_gap = x - screen_left
    right_gap = screen_right - (x + width)
    if left_gap <= snap_px:
        candidates.append((left_gap, "left"))
    if right_gap <= snap_px:
        candidates.append((right_gap, "right"))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def dock_x(side: str, screen_left: int, screen_right: int, width: int) -> int:
    """贴边后的窗口 x：左缘对齐或用可用区右缘减窗口宽度。"""
    if side not in _SIDES:
        raise ValueError(f"Unknown dock side: {side!r}")
    return screen_left if side == "left" else screen_right - width


def edge_mask_rect(side: str, width: int, height: int) -> tuple[int, int, int, int]:
    """贴边后保留的可见侧矩形 (x, y, w, h)。"""
    if side not in _SIDES:
        raise ValueError(f"Unknown dock side: {side!r}")
    visible = max(1, min(width, round(width * EDGE_VISIBLE_RATIO)))
    x = 0 if side == "left" else width - visible
    return x, 0, visible, height
