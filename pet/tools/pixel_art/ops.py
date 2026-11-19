"""像素画算子：图元栅格化、过程式填充与后处理。"""

from __future__ import annotations

import numpy as np

from pet.tools.pixel_art.canvas import (TRANSPARENT, PixelArtError, parse_color,
                                        parse_region)

PRIMITIVE_OPS = ("point", "line", "rect", "ellipse", "polygon", "path", "erase", "clear")
PROCEDURAL_OPS = ("gradient", "noise", "pattern", "mask", "scatter")
ALL_OPS = PRIMITIVE_OPS + PROCEDURAL_OPS

POSTPROCESS_OPS = ("outline", "dither", "mirror_x", "mirror_y", "rotate90",
                   "shade", "cleanup", "invert")

TILES = ("brick", "checker", "dots", "stripes", "grid")
SHAPES = ("circle", "ellipse", "rect")
DITHERS = ("none", "bayer4")

# 每个 op 的字段表：字段错误时原样回给模型，避免它靠试错猜字段名
OP_FIELDS = {
    "point": "x, y, c",
    "line": "x1, y1, x2, y2, c",
    "rect": "x, y, w, h, c, fill（默认 true）",
    "ellipse": "x, y, w, h, c, fill（默认 false）",
    "polygon": "points（[[x,y],…]）, c, fill（默认 true）",
    "path": "points（[[x,y],…]）, c",
    "erase": "x, y, w, h",
    "clear": "无字段",
    "gradient": "region, from, to, dither, mask",
    "noise": "region, c, ratio, seed",
    "pattern": "region, tile, c",
    "mask": "region, shape, c",
    "scatter": "region, c, count, seed",
}


def _fields_hint(op) -> str:
    """该 op 的字段表；未知 op 返回空，交给 bad_op 的可用列表提示。"""
    name = op.get("op") if isinstance(op, dict) else None
    fields = OP_FIELDS.get(name)
    return f"{name} 的字段：{fields}" if fields else ""

BAYER4 = ((0, 8, 2, 10),
          (12, 4, 14, 6),
          (3, 11, 1, 9),
          (15, 7, 13, 5))


def apply_op(index: np.ndarray, op: dict, palette, default_seed: int = 0) -> None:
    """按 op 原地绘制；字段非法时抛 PixelArtError。"""
    if not isinstance(op, dict):
        raise PixelArtError("bad_op", f"ops 元素必须是对象，收到 {type(op).__name__}")
    name = op.get("op")
    if name not in ALL_OPS:
        raise PixelArtError("bad_op", f"未知 op {name!r}", "可用：" + "、".join(ALL_OPS))

    size = index.shape[0]
    if name == "point":
        _set(index, _int(op, "x"), _int(op, "y"), _color(op, palette))
    elif name == "line":
        _line(index, _int(op, "x1"), _int(op, "y1"),
              _int(op, "x2"), _int(op, "y2"), _color(op, palette))
    elif name == "rect":
        _rect(index, _int(op, "x"), _int(op, "y"), _int(op, "w"), _int(op, "h"),
              _color(op, palette), bool(op.get("fill", True)))
    elif name == "ellipse":
        _ellipse(index, _int(op, "x"), _int(op, "y"), _int(op, "w"), _int(op, "h"),
                 _color(op, palette), bool(op.get("fill", False)))
    elif name == "polygon":
        _polygon(index, _points(op), _color(op, palette), bool(op.get("fill", True)))
    elif name == "path":
        _path(index, _points(op), _color(op, palette))
    elif name == "erase":
        _erase(index, _int(op, "x"), _int(op, "y"), _int(op, "w"), _int(op, "h"))
    elif name == "clear":
        index.fill(TRANSPARENT)
    elif name == "gradient":
        _gradient(index, op, palette)
    elif name == "noise":
        _noise(index, op, palette, default_seed)
    elif name == "pattern":
        _pattern(index, op, palette)
    elif name == "mask":
        _mask(index, op, palette)
    elif name == "scatter":
        _scatter(index, op, palette, default_seed)
    else:  # pragma: no cover - 上面的名字表已覆盖
        raise PixelArtError("bad_op", f"未实现的 op {name!r}")


def mirror_op(op: dict, size: int) -> dict | None:
    """返回按画布中线左右反射的副本；无坐标可镜像时返回 None。"""
    name = op.get("op")
    mirrored = dict(op)
    if name in PRIMITIVE_OPS:
        if name == "point":
            mirrored["x"] = size - 1 - _int(op, "x")
        elif name == "line":
            mirrored["x1"] = size - 1 - _int(op, "x1")
            mirrored["x2"] = size - 1 - _int(op, "x2")
        elif name in ("rect", "ellipse", "erase"):
            mirrored["x"] = size - _int(op, "x") - _int(op, "w")
        elif name in ("polygon", "path"):
            mirrored["points"] = [[size - 1 - x, y] for x, y in _points(op)]
        else:
            return None
        return mirrored
    if name in PROCEDURAL_OPS:
        region = op.get("region")
        if not isinstance(region, (list, tuple)) or len(region) != 4:
            return None
        x, y, w, h = region
        mirrored["region"] = [size - int(x) - int(w), int(y), int(w), int(h)]
        return mirrored
    return None


def apply_postprocess(index: np.ndarray, op: str, params: dict, palette) -> None:
    params = params or {}
    if op not in POSTPROCESS_OPS:
        raise PixelArtError("bad_op", f"未知后处理 {op!r}", "可用：" + "、".join(POSTPROCESS_OPS))
    size = index.shape[0]
    region = _optional_region(params.get("region"), size)

    if op == "outline":
        _outline(index, parse_color(params.get("c", 0), palette), region)
    elif op == "dither":
        _dither(index, region, parse_color(params.get("c", 0), palette))
    elif op == "mirror_x":
        _mirror(index, region, axis=1)
    elif op == "mirror_y":
        _mirror(index, region, axis=0)
    elif op == "rotate90":
        if region is not None:
            raise PixelArtError("bad_param", "rotate90 只支持整图，不能配合 region")
        index[:] = np.rot90(index, k=-1)
    elif op == "shade":
        steps = _int(params, "steps", 1)
        if steps not in (-2, -1, 1, 2):
            raise PixelArtError("bad_param", f"steps 需在 -2..2 且不为 0，收到 {steps}")
        _shade(index, region, steps, len(palette))
    elif op == "cleanup":
        _cleanup(index, region)
    elif op == "invert":
        _invert(index, region, len(palette))


def _int(op: dict, key: str, default=None) -> int:
    value = op.get(key, default) if isinstance(op, dict) else default
    if value is None:
        raise PixelArtError("bad_param", f"缺少整数参数 {key}", _fields_hint(op))
    try:
        return int(value)
    except (TypeError, ValueError):
        raise PixelArtError("bad_param", f"{key} 需要整数，收到 {value!r}",
                            _fields_hint(op)) from None


def _num(op: dict, key: str, default: float) -> float:
    value = op.get(key, default) if isinstance(op, dict) else default
    try:
        return float(value)
    except (TypeError, ValueError):
        raise PixelArtError("bad_param", f"{key} 需要数值，收到 {value!r}",
                            _fields_hint(op)) from None


def _color(op: dict, palette) -> int:
    if "c" not in op:
        raise PixelArtError("bad_param", f"{op.get('op')} 缺少颜色 c", _fields_hint(op))
    return parse_color(op["c"], palette)


def _points(op: dict) -> list[tuple[int, int]]:
    raw = op.get("points")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        raise PixelArtError("bad_param", f"{op.get('op')} 需要至少两个点", _fields_hint(op))
    points = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise PixelArtError("bad_param", f"{op.get('op')} 的 points 项必须是 [x, y]",
                                _fields_hint(op))
        try:
            points.append((int(item[0]), int(item[1])))
        except (TypeError, ValueError) as e:
            raise PixelArtError("bad_param", f"{op.get('op')} 的 points 含非整数",
                                _fields_hint(op)) from e
    return points


def _region(op: dict, size: int) -> tuple[int, int, int, int]:
    """过程式算子必须显式给 region，缺失时按整图会掩盖构图意图。"""
    if op.get("region") is None:
        raise PixelArtError("bad_param", f"{op.get('op')} 需要 region",
                            "给出 [x, y, w, h]，如 [0, 0, 64, 32]")
    return parse_region(op["region"], size)


def _optional_region(region, size: int):
    if region is None:
        return None
    return parse_region(region, size)


def _set(index: np.ndarray, x: int, y: int, color: int) -> None:
    size = index.shape[0]
    if 0 <= x < size and 0 <= y < size:
        index[y, x] = color


def _line(index: np.ndarray, x1: int, y1: int, x2: int, y2: int, color: int) -> None:
    dx = abs(x2 - x1)
    dy = -abs(y2 - y1)
    sx = 1 if x1 < x2 else -1
    sy = 1 if y1 < y2 else -1
    err = dx + dy
    while True:
        _set(index, x1, y1, color)
        if x1 == x2 and y1 == y2:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x1 += sx
        if e2 <= dx:
            err += dx
            y1 += sy


def _rect(index, x, y, w, h, color, fill: bool) -> None:
    size = index.shape[0]
    if w <= 0 or h <= 0:
        raise PixelArtError("bad_param", f"rect 宽高必须为正，收到 {w}×{h}")
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(size, x + w), min(size, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    if fill:
        index[y0:y1, x0:x1] = color
        return
    for px in range(x0, x1):
        _set(index, px, y, color)
        _set(index, px, y + h - 1, color)
    for py in range(y0, y1):
        _set(index, x, py, color)
        _set(index, x + w - 1, py, color)


def _ellipse(index, x, y, w, h, color, fill: bool) -> None:
    if w <= 0 or h <= 0:
        raise PixelArtError("bad_param", f"ellipse 宽高必须为正，收到 {w}×{h}")
    size = index.shape[0]
    cx, cy = x + w / 2.0, y + h / 2.0
    rx, ry = max(w / 2.0, 0.5), max(h / 2.0, 0.5)
    inner_rx, inner_ry = max(rx - 1, 0.01), max(ry - 1, 0.01)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(size, x + w), min(size, y + h)
    for py in range(y0, y1):
        for px in range(x0, x1):
            nx = (px + 0.5 - cx) / rx
            ny = (py + 0.5 - cy) / ry
            if nx * nx + ny * ny > 1.0:
                continue
            if not fill:
                ix = (px + 0.5 - cx) / inner_rx
                iy = (py + 0.5 - cy) / inner_ry
                if ix * ix + iy * iy <= 1.0:
                    continue
            index[py, px] = color


def _polygon(index, points, color, fill: bool) -> None:
    if fill:
        _fill_polygon(index, points, color)
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        _line(index, x1, y1, x2, y2, color)


def _fill_polygon(index, points, color) -> None:
    size = index.shape[0]
    ys = [p[1] for p in points]
    for py in range(max(0, min(ys)), min(size - 1, max(ys)) + 1):
        crossings = []
        for i in range(len(points)):
            x1, y1 = points[i]
            x2, y2 = points[(i + 1) % len(points)]
            if y1 == y2:
                continue
            if min(y1, y2) <= py < max(y1, y2):
                t = (py - y1) / (y2 - y1)
                crossings.append(x1 + t * (x2 - x1))
        crossings.sort()
        for i in range(0, len(crossings) - 1, 2):
            start = max(0, int(round(crossings[i])))
            end = min(size - 1, int(round(crossings[i + 1])))
            if end >= start:
                index[py, start:end + 1] = color


def _path(index, points, color) -> None:
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        _line(index, x1, y1, x2, y2, color)


def _erase(index, x, y, w, h) -> None:
    _rect(index, x, y, w, h, TRANSPARENT, True)


def _gradient(index, op: dict, palette) -> None:
    x, y, w, h = _region(op, index.shape[0])
    start = parse_color(op.get("from"), palette, "from")
    end = parse_color(op.get("to"), palette, "to")
    dither = op.get("dither", "none")
    mask = op.get("mask", "none")
    if dither not in DITHERS:
        raise PixelArtError("bad_param", f"gradient.dither 取值 {dither!r} 非法",
                            "可用：none、bayer4")
    if mask not in ("none", "circle"):
        raise PixelArtError("bad_param", f"gradient.mask 取值 {mask!r} 非法", "可用：none、circle")

    for py in range(h):
        t = (py / max(1, h - 1)) * (end - start)
        low = int(np.floor(start + t))
        high = int(np.ceil(start + t))
        for px in range(w):
            if mask == "circle" and not _in_circle(px, py, w, h):
                continue
            color = low
            if dither == "bayer4" and low != high:
                frac = (start + t) - low
                color = high if BAYER4[py % 4][px % 4] < frac * 16 else low
            index[y + py, x + px] = max(0, min(len(palette) - 1, color))


def _in_circle(px: int, py: int, w: int, h: int) -> bool:
    cx, cy = w / 2.0, h / 2.0
    rx, ry = max(w / 2.0, 0.5), max(h / 2.0, 0.5)
    nx = (px + 0.5 - cx) / rx
    ny = (py + 0.5 - cy) / ry
    return nx * nx + ny * ny <= 1.0


def _seed(op: dict, default: int) -> int:
    value = op.get("seed", default)
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise PixelArtError("bad_param", f"seed 必须是整数，收到 {value!r}") from e


def _noise(index, op: dict, palette, default_seed: int) -> None:
    x, y, w, h = _region(op, index.shape[0])
    color = _color(op, palette)
    ratio = _num(op, "ratio", 0.3)
    if not 0.0 <= ratio <= 1.0:
        raise PixelArtError("bad_param", f"noise.ratio 必须在 0-1，收到 {ratio}")
    rng = np.random.default_rng(_seed(op, default_seed))
    hits = rng.random((h, w)) < ratio
    block = index[y:y + h, x:x + w]
    block[hits] = color


def _pattern(index, op: dict, palette) -> None:
    x, y, w, h = _region(op, index.shape[0])
    color = _color(op, palette)
    tile = op.get("tile", "checker")
    if tile not in TILES:
        raise PixelArtError("bad_param", f"pattern.tile 取值 {tile!r} 非法",
                            "可用：" + "、".join(TILES))
    for py in range(h):
        for px in range(w):
            gx, gy = x + px, y + py
            if _tile_hit(tile, gx, gy):
                index[y + py, x + px] = color


def _tile_hit(tile: str, x: int, y: int) -> bool:
    if tile == "checker":
        return (x + y) % 2 == 0
    if tile == "dots":
        return x % 4 == 0 and y % 4 == 0
    if tile == "stripes":
        return x % 4 < 2
    if tile == "grid":
        return x % 4 == 0 or y % 4 == 0
    # brick：每行错开半块
    offset = 4 if (y // 4) % 2 else 0
    return y % 4 == 0 or (x + offset) % 8 == 0


def _mask(index, op: dict, palette) -> None:
    x, y, w, h = _region(op, index.shape[0])
    shape = op.get("shape", "circle")
    if shape not in SHAPES:
        raise PixelArtError("bad_param", f"mask.shape 取值 {shape!r} 非法",
                            "可用：" + "、".join(SHAPES))
    color = parse_color(op["c"], palette) if "c" in op else None
    for py in range(index.shape[0]):
        for px in range(index.shape[0]):
            inside = (x <= px < x + w and y <= py < y + h
                      and _shape_hit(shape, px - x, py - y, w, h))
            if not inside:
                index[py, px] = TRANSPARENT
            elif color is not None:
                index[py, px] = color


def _shape_hit(shape: str, px: int, py: int, w: int, h: int) -> bool:
    if shape == "rect":
        return True
    return _in_circle(px, py, w, h)


def _scatter(index, op: dict, palette, default_seed: int) -> None:
    x, y, w, h = _region(op, index.shape[0])
    color = _color(op, palette)
    count = _int(op, "count", 20)
    if count <= 0:
        raise PixelArtError("bad_param", f"scatter.count 必须为正，收到 {count}")
    if count > w * h:
        raise PixelArtError("bad_param", f"scatter.count {count} 超过区域格数 {w * h}")
    rng = np.random.default_rng(_seed(op, default_seed))
    ys = rng.integers(0, h, size=count)
    xs = rng.integers(0, w, size=count)
    index[y + ys, x + xs] = color


def _outline(index, color: int, region=None) -> None:
    mask = index != TRANSPARENT
    neighbor = np.zeros_like(mask)
    neighbor[1:, :] |= mask[:-1, :]
    neighbor[:-1, :] |= mask[1:, :]
    neighbor[:, 1:] |= mask[:, :-1]
    neighbor[:, :-1] |= mask[:, 1:]
    targets = (~mask) & neighbor
    if region is not None:
        x, y, w, h = region
        area = np.zeros_like(mask)
        area[y:y + h, x:x + w] = True
        targets &= area
    index[targets] = color


def _dither(index, region, color: int) -> None:
    size = index.shape[0]
    x, y, w, h = region or (0, 0, size, size)
    for py in range(y, y + h):
        for px in range(x, x + w):
            if index[py, px] == TRANSPARENT and BAYER4[py % 4][px % 4] < 8:
                index[py, px] = color


def _mirror(index, region, axis: int) -> None:
    size = index.shape[0]
    x, y, w, h = region or (0, 0, size, size)
    block = index[y:y + h, x:x + w]
    flipped = np.fliplr(block) if axis == 1 else np.flipud(block)
    index[y:y + h, x:x + w] = flipped


def _shade(index, region, steps: int, palette_size: int) -> None:
    size = index.shape[0]
    x, y, w, h = region or (0, 0, size, size)
    block = index[y:y + h, x:x + w]
    visible = block != TRANSPARENT
    shifted = np.clip(block.astype(np.int16) + steps, 0, palette_size - 1)
    block[visible] = shifted[visible].astype(np.uint8)


def _cleanup(index, region=None) -> None:
    mask = index != TRANSPARENT
    neighbors = np.zeros(index.shape, dtype=np.int16)
    neighbors[1:, :] += mask[:-1, :]
    neighbors[:-1, :] += mask[1:, :]
    neighbors[:, 1:] += mask[:, :-1]
    neighbors[:, :-1] += mask[:, 1:]
    targets = mask & (neighbors == 0)
    if region is not None:
        x, y, w, h = region
        area = np.zeros_like(mask)
        area[y:y + h, x:x + w] = True
        targets &= area
    index[targets] = TRANSPARENT


def _invert(index, region, palette_size: int) -> None:
    size = index.shape[0]
    x, y, w, h = region or (0, 0, size, size)
    block = index[y:y + h, x:x + w]
    visible = block != TRANSPARENT
    block[visible] = (palette_size - 1) - block[visible].astype(np.int16)
