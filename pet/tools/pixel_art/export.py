"""像素画导出：PNG / SVG / JSON / RLE 渲染与落点处理。"""

from __future__ import annotations

import io
import json
import os
from datetime import datetime

import numpy as np
from PIL import Image

from pet.file_intake.paths import check_write_path, desktop_dir
from pet.tools.pixel_art.canvas import (TRANSPARENT, PixelArtError, char_of,
                                        encode_rle, hex_of, render_text_matrix)

FORMATS = ("png", "svg", "json", "rle")
_EXTENSIONS = {"png": ".png", "svg": ".svg", "json": ".json", "rle": ".txt"}
_OUTPUT_SIZE = 256


def default_scale(size: int) -> int:
    return max(1, _OUTPUT_SIZE // size)


def _rgba(index: np.ndarray, palette, transparent: bool) -> np.ndarray:
    size = index.shape[0]
    canvas = np.zeros((size, size, 4), dtype=np.uint8)
    if not transparent:
        canvas[:, :, :3] = 255
        canvas[:, :, 3] = 255
    for i, (r, g, b) in enumerate(palette):
        canvas[index == i] = (r, g, b, 255)
    return canvas


def to_png_bytes(index: np.ndarray, palette, scale: int = None,
                 transparent: bool = True, grid_overlay: bool = False) -> bytes:
    size = index.shape[0]
    scale = scale or default_scale(size)
    image = Image.fromarray(_rgba(index, palette, transparent), mode="RGBA")
    image = image.resize((size * scale, size * scale), Image.NEAREST)
    if grid_overlay:
        arr = np.array(image)
        arr[::scale, :, :3] = 255 - arr[::scale, :, :3]
        arr[:, ::scale, :3] = 255 - arr[:, ::scale, :3]
        image = Image.fromarray(arr, mode="RGBA")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def to_svg(index: np.ndarray, palette, scale: int = None) -> str:
    size = index.shape[0]
    scale = scale or default_scale(size)
    width = height = size * scale
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {size} {size}" shape-rendering="crispEdges">'
    ]
    for y in range(size):
        x = 0
        while x < size:
            color = int(index[y, x])
            end = x
            while end + 1 < size and int(index[y, end + 1]) == color:
                end += 1
            if color != TRANSPARENT:
                parts.append(
                    f'<rect x="{x}" y="{y}" width="{end - x + 1}" height="1" '
                    f'fill="{hex_of(palette[color])}"/>')
            x = end + 1
    parts.append("</svg>")
    return "".join(parts)


def to_json(index: np.ndarray, palette, size: int, preset: str, revision: int) -> str:
    rows = []
    for y in range(size):
        rows.append("".join(char_of(int(v)) for v in index[y]))
    payload = {
        "size": size,
        "preset": preset,
        "revision": revision,
        "palette": [hex_of(rgb) for rgb in palette],
        "rows": rows,
    }
    return json.dumps(payload, ensure_ascii=False)


def to_rle(index: np.ndarray) -> str:
    return encode_rle(index)


def resolve_path(path: str | None, fmt: str, size: int) -> str:
    """省略 path 落桌面；给了 path 走白名单校验。"""
    extension = _EXTENSIONS[fmt]
    if path:
        candidate = path.strip()
        if not candidate.lower().endswith(extension):
            raise PixelArtError("unsupported_format",
                                f"扩展名与 format={fmt} 不一致: {candidate}",
                                f"用 {extension} 结尾")
        if not os.path.isabs(candidate):
            candidate = os.path.join(desktop_dir(), candidate)
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = os.path.join(desktop_dir(), f"pixel_{size}_{stamp}{extension}")
    try:
        return check_write_path(candidate)
    except PermissionError as e:
        raise PixelArtError("export_failed", str(e), "只允许写入桌面与文档目录") from e


def normalize_scale(scale, size: int, limit: int = 64) -> int:
    """scale 限定为 1..limit 的整数，省略时取 256/size。"""
    if scale is None:
        return default_scale(size)
    try:
        value = int(scale)
    except (TypeError, ValueError):
        raise PixelArtError("bad_param", f"scale 需要整数，收到 {scale!r}") from None
    if value < 1 or value > limit:
        raise PixelArtError("bad_param", f"scale 需在 1-{limit} 之间，收到 {value}")
    return value


def _unique(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, extension = os.path.splitext(path)
    index = 2
    while os.path.exists(f"{stem}-{index}{extension}"):
        index += 1
    return f"{stem}-{index}{extension}"


def save(index: np.ndarray, palette, size: int, preset: str, revision: int,
         fmt: str, path: str | None = None, scale: int | None = None,
         transparent: bool = True, grid_overlay: bool = False) -> dict:
    """按格式渲染并写入磁盘，返回 path / width / height / bytes。"""
    if fmt not in FORMATS:
        raise PixelArtError("unsupported_format", f"不支持的格式 {fmt!r}",
                            "可用：" + "、".join(FORMATS))
    scale = normalize_scale(scale, size)
    try:
        target = check_write_path(_unique(resolve_path(path, fmt, size)))
    except PermissionError as e:
        raise PixelArtError("export_failed", str(e), "只允许写入桌面与文档目录") from e

    if fmt == "png":
        data = to_png_bytes(index, palette, scale, transparent, grid_overlay)
    elif fmt == "svg":
        data = to_svg(index, palette, scale).encode("utf-8")
    elif fmt == "json":
        data = to_json(index, palette, size, preset, revision).encode("utf-8")
    else:
        data = to_rle(index).encode("utf-8")

    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(data)
    except OSError as e:
        raise PixelArtError("export_failed", f"写入失败: {e}") from e

    if fmt == "png":
        width = height = size * scale
    elif fmt == "svg":
        width = height = size * scale
    else:
        width = height = size
    return {"path": target, "width": width, "height": height, "bytes": len(data)}


def preview_matrix(index: np.ndarray, region=None) -> str:
    return render_text_matrix(index, region)
