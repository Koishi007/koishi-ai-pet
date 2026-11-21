"""像素画工具核心：画布缓存、revision 校验与十一个方法的 handler。"""

from __future__ import annotations

import base64
import json
import logging
import threading
import zlib
from collections import OrderedDict

import numpy as np

from pet.tools.context import TOOL_CTX
from pet.tools.pixel_art.canvas import (MAX_COLORS, PRESETS, TRANSPARENT, Canvas,
                                        PixelArtError, changed_bbox, changed_cells,
                                        decode_rle, hex_of, nearest_index,
                                        new_index, parse_color, parse_hex,
                                        parse_region, parse_rows, render_text_matrix)
from pet.tools.pixel_art.export import default_scale, normalize_scale, save, to_png_bytes
from pet.tools.pixel_art.ops import apply_mirror_x, apply_op, apply_postprocess
from pet.tools.pixel_art.storage import PixelArtStorage

logger = logging.getLogger(__name__)

CACHE_SIZE = 4
NAME_LIMIT = 24
MAX_OPS = 200


class _Store:
    def __init__(self, storage: PixelArtStorage):
        self.storage = storage
        self.lock = threading.Lock()
        self.cache: OrderedDict[str, Canvas] = OrderedDict()
        self.current_id: str | None = None


class _Bootstrap:
    """单例的创建锁：类体执行不参与 ARCH009 的导入期扫描。"""

    guard = threading.Lock()
    instance: "_Store | None" = None


def _store() -> _Store:
    store = _Bootstrap.instance
    if store is not None:
        return store
    with _Bootstrap.guard:
        if _Bootstrap.instance is None:
            _Bootstrap.instance = _Store(PixelArtStorage())
        return _Bootstrap.instance


def reset_store(db_path: str | None = None) -> None:
    """重建单例；测试用临时库隔离。旧连接先关闭。"""
    with _Bootstrap.guard:
        old = _Bootstrap.instance
        _Bootstrap.instance = None
    if old is not None:
        try:
            old.storage.close()
        except Exception:
            pass
    if db_path:
        with _Bootstrap.guard:
            _Bootstrap.instance = _Store(PixelArtStorage(db_path))


def _as_int(value, name: str, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise PixelArtError("bad_param", f"{name} 需要整数，收到 {value!r}") from None


def _put(store: _Store, canvas: Canvas) -> None:
    store.cache[canvas.id] = canvas
    store.cache.move_to_end(canvas.id)
    while len(store.cache) > CACHE_SIZE:
        store.cache.popitem(last=False)


def _resolve(store: _Store, canvas_id=None) -> Canvas:
    """按 id 取画布；省略时用 current_id，再退到库内最新一张。"""
    if canvas_id is not None and str(canvas_id).strip():
        key = str(canvas_id).strip()
        canvas = store.cache.get(key)
        if canvas is not None:
            store.cache.move_to_end(key)
            return canvas
        canvas = store.storage.get(key)
        if canvas is None:
            raise PixelArtError("no_canvas", f"找不到画布 {key}", _recent_hint(store))
        _put(store, canvas)
        return canvas

    if store.current_id:
        canvas = store.cache.get(store.current_id) or store.storage.get(store.current_id)
        if canvas is not None:
            _put(store, canvas)
            return canvas
        store.current_id = None

    canvas = store.storage.latest()
    if canvas is None:
        raise PixelArtError("no_canvas", "还没有画布", "先调 create 建一张")
    store.current_id = canvas.id
    _put(store, canvas)
    return canvas


def _recent_hint(store: _Store) -> str:
    rows, _ = store.storage.list(1, 5)
    if not rows:
        return "还没有画布，先调 create"
    return "可用：" + "、".join(f"{r['id']}={r['name']}" for r in rows)


def _check_revision(canvas: Canvas, revision) -> None:
    if revision is None:
        return
    try:
        value = int(revision)
    except (TypeError, ValueError) as e:
        raise PixelArtError("bad_param", f"revision 必须是整数，收到 {revision!r}") from e
    if value != canvas.revision:
        raise PixelArtError(
            "stale_revision",
            f"revision 过期：当前 {canvas.revision}，传入 {value}，未写入",
            "用当前 revision 重试，或省略 revision")


def _commit(store: _Store, canvas: Canvas, new_index: np.ndarray,
            extra: dict | None = None) -> dict:
    """落库后替换内存；无变化时不写库。"""
    old = canvas.index
    changes = changed_cells(old, new_index)
    bbox = changed_bbox(old, new_index)
    if changes:
        revision = canvas.revision + 1
        try:
            store.storage.save_pixels(canvas, new_index, revision)
        except PixelArtError:
            raise
        except Exception as e:
            raise PixelArtError("persist_failed", f"写入画布失败: {e}") from e
        canvas.index = new_index
        canvas.revision = revision
    _push(store, canvas, changes)
    data = {
        "revision": canvas.revision,
        "bbox": bbox,
        "changed_cells": changes,
        "filled_cells": canvas.filled_cells,
    }
    if extra:
        data.update(extra)
    return data


def _push(store: _Store, canvas: Canvas, changed: int = 0,
          force_show: bool = False) -> None:
    try:
        _emit({
            "canvas_id": canvas.id,
            "name": canvas.name,
            "size": canvas.size,
            "revision": canvas.revision,
            "changed_cells": changed,
            "palette_size": canvas.palette_size,
            "preset": canvas.preset,
            "force_show": force_show,
            "png": to_png_bytes(canvas.index, canvas.palette,
                                scale=default_scale(canvas.size)),
        })
    except Exception:
        logger.debug("[pixel_art] canvas push failed", exc_info=True)


def _emit(payload: dict) -> None:
    """画面交给面板。panel 是 Qt 重依赖且回指 core，import 延迟到调用时。"""
    from pet.tools.pixel_art.panel import push_view
    push_view(payload)


def _clean_name(name) -> str:
    if not isinstance(name, str):
        raise PixelArtError("bad_param", "name 必须是字符串")
    text = name.strip()
    if not text:
        raise PixelArtError("bad_param", "name 不能为空")
    if len(text) > NAME_LIMIT:
        raise PixelArtError("bad_param", f"name 最长 {NAME_LIMIT} 字符，收到 {len(text)}")
    return text


def _parse_size(size) -> int:
    text = str(size).strip()
    if text not in ("16", "32", "64"):
        raise PixelArtError("bad_param", f"size 只能是 16 / 32 / 64，收到 {size!r}")
    return int(text)


def _build_palette(preset, palette) -> tuple[str, list[tuple[int, int, int]]]:
    if palette:
        if not isinstance(palette, (list, tuple)):
            raise PixelArtError("bad_param", "palette 必须是 #RRGGBB 数组")
        if len(palette) > MAX_COLORS:
            raise PixelArtError("palette_full", f"色板最多 {MAX_COLORS} 色，收到 {len(palette)}")
        if not palette:
            raise PixelArtError("bad_param", "palette 不能为空")
        return "custom", [parse_hex(str(item)) for item in palette]
    name = str(preset or "pico8").strip().lower()
    colors = PRESETS.get(name)
    if colors is None:
        raise PixelArtError("bad_param", f"未知色板 {preset!r}",
                            "可用：" + "、".join(PRESETS))
    return name, list(colors)


def create(name, size, preset=None, palette=None, background=None):
    size_i = _parse_size(size)
    canvas_name = _clean_name(name)
    preset_name, colors = _build_palette(preset, palette)
    background_index = (parse_color(background, colors, "background")
                        if background is not None else TRANSPARENT)
    store = _store()
    with store.lock:
        canvas = store.storage.create(canvas_name, size_i, preset_name, colors,
                                      new_index(size_i, background_index))
        store.current_id = canvas.id
        _put(store, canvas)
    _push(store, canvas, canvas.filled_cells, force_show=True)
    return {
        "headline": (f"新建画布「{canvas_name}」{size_i}×{size_i}，id {canvas.id}，"
                     f"{len(colors)} 色，{'透明' if background_index == TRANSPARENT else hex_of(colors[background_index])}"),
        "data": {"canvas_id": canvas.id, "name": canvas_name,
                 "revision": canvas.revision, "palette_size": len(colors)},
    }


def open_canvas(canvas_id=None):
    if not canvas_id:
        raise PixelArtError("bad_param", "需要 canvas_id", _recent_hint(_store()))
    store = _store()
    with store.lock:
        canvas = store.storage.get(str(canvas_id).strip())
        if canvas is None:
            raise PixelArtError("no_canvas", "找不到目标画布", _recent_hint(store))
        store.current_id = canvas.id
        _put(store, canvas)
    _push(store, canvas, force_show=True)
    return {
        "headline": f"已打开画布 {canvas.id}「{canvas.name}」{canvas.size}×{canvas.size}，"
                    f"revision {canvas.revision}，画面 {canvas.filled_cells}/{canvas.size ** 2}",
        "data": {"canvas_id": canvas.id, "name": canvas.name, "size": canvas.size,
                 "revision": canvas.revision, "filled_cells": canvas.filled_cells},
    }


def list_canvases(page=1, page_size=20):
    store = _store()
    page = max(1, _as_int(page, "page", 1))
    page_size = min(50, max(1, _as_int(page_size, "page_size", 20)))
    with store.lock:
        rows, total = store.storage.list(page, page_size)
        pages = max(1, -(-total // page_size))
        if page > pages:
            page = pages
            rows, total = store.storage.list(page, page_size)
    body = "\n".join(
        f"#{r['id']} {r['name']} · {r['size']}×{r['size']} · rev {r['revision']} · "
        f"{str(r['updated_at'])[:16].replace('T', ' ')}"
        for r in rows)
    return {
        "headline": f"共 {total} 张画布，第 {page}/{pages} 页",
        "body": body or "（还没有画布）",
        "pagination": {"page": page, "page_size": page_size,
                       "total_items": total, "has_next": page < pages},
    }


def rename(canvas_id=None, name=None):
    new_name = _clean_name(name)
    store = _store()
    with store.lock:
        canvas = _resolve(store, canvas_id)
        store.storage.rename(canvas.id, new_name)
        canvas.name = new_name
    _push(store, canvas)
    return {"headline": f"画布 {canvas.id} 已改名为「{new_name}」",
            "data": {"canvas_id": canvas.id, "name": new_name}}


def delete(canvas_id=None):
    store = _store()
    with store.lock:
        canvas = _resolve(store, canvas_id)
        removed = store.storage.delete(canvas.id)
        store.cache.pop(canvas.id, None)
        if store.current_id == canvas.id:
            store.current_id = None
    if not removed:
        raise PixelArtError("no_canvas", f"画布 {canvas.id} 已不存在")
    return {"headline": f"已删除画布 {canvas.id}「{canvas.name}」",
            "data": {"deleted": True}}


def set_pixels(rows=None, rle=None, region=None, mirror_x=False,
               revision=None, canvas_id=None):
    if rows is not None and rle is not None:
        raise PixelArtError("bad_param", "rows 与 rle 只能给一个")
    if rows is None and rle is None:
        raise PixelArtError("bad_param", "需要 rows 或 rle")
    store = _store()
    with store.lock:
        canvas = _resolve(store, canvas_id)
        _check_revision(canvas, revision)
        new_index = canvas.copy_index()
        if rle is not None:
            if region is not None:
                raise PixelArtError("bad_param", "rle 只支持整图，不能配合 region")
            new_index = decode_rle(rle, canvas.size, canvas.palette_size)
            if mirror_x:
                new_index = np.fliplr(new_index)
        else:
            if region is None and canvas.size != 16:
                raise PixelArtError("region_too_large",
                                    f"{canvas.size} 档不支持整图 rows",
                                    "带 region 做局部写入，或用 paint；整图也可用 rle")
            x, y, w, h = parse_region(region, canvas.size)
            base = canvas.index[y:y + h, x:x + w]
            block = parse_rows(rows, w, h, base=base, palette_size=canvas.palette_size)
            new_index[y:y + h, x:x + w] = block
            if mirror_x:
                for row in range(h):
                    for col in range(w):
                        new_index[y + row, canvas.size - 1 - (x + col)] = block[row, col]
        data = _commit(store, canvas, new_index)
        return {"headline": _write_headline(canvas, data), "data": data}


def paint(ops=None, mirror_x=False, revision=None, canvas_id=None):
    if not isinstance(ops, list) or not ops:
        raise PixelArtError("bad_param", "ops 必须是非空数组")
    if len(ops) > MAX_OPS:
        raise PixelArtError("bad_param", f"单次最多 {MAX_OPS} 条 op，收到 {len(ops)}",
                            "拆成多次调用")
    store = _store()
    with store.lock:
        canvas = _resolve(store, canvas_id)
        _check_revision(canvas, revision)
        new_index = canvas.copy_index()
        for op in ops:
            seed = zlib.crc32(json.dumps(op, sort_keys=True, default=str).encode())
            apply_op(new_index, op, canvas.palette, default_seed=seed)
            if mirror_x:
                apply_mirror_x(new_index, op, canvas.palette, default_seed=seed)
        data = _commit(store, canvas, new_index)
        return {"headline": _write_headline(canvas, data), "data": data}


def postprocess(op=None, region=None, params=None, revision=None, canvas_id=None):
    if not op:
        raise PixelArtError("bad_param", "需要 op")
    store = _store()
    with store.lock:
        canvas = _resolve(store, canvas_id)
        _check_revision(canvas, revision)
        merged = dict(params or {})
        if region is not None:
            merged["region"] = region
        new_index = canvas.copy_index()
        apply_postprocess(new_index, str(op), merged, canvas.palette)
        data = _commit(store, canvas, new_index)
        return {"headline": _write_headline(canvas, data), "data": data}


def preview(mode="text", region=None, scale=None, canvas_id=None):
    store = _store()
    with store.lock:
        canvas = _resolve(store, canvas_id)
        parsed = parse_region(region, canvas.size) if region is not None else None
        # 一次取全字段快照：矩阵、标题与 PNG 必须来自同一版本
        index = canvas.copy_index()
        palette = list(canvas.palette)
        size = canvas.size
        name = canvas.name
        revision = canvas.revision
        filled = canvas.filled_cells
        matrix = render_text_matrix(index, parsed)
        wants_image = mode in ("image", "both")
        png = None
        if wants_image and TOOL_CTX.has_vision():
            png = to_png_bytes(index, palette,
                               scale=normalize_scale(scale, size, 32))

    headline = (f"「{name}」{size}×{size}，revision {revision}，"
                f"画面 {filled}/{size * size}")
    result = {"headline": headline}
    if png is not None:
        result["images"] = [{
            "base64": base64.b64encode(png).decode("ascii"),
            "mime": "image/png",
            "caption": f"画布「{name}」{size}×{size}",
        }]
        if mode == "both":
            result["body"] = matrix
    else:
        result["body"] = matrix
        if wants_image:
            result["hint"] = "当前模型无视觉能力，已退化为文本矩阵"
    return result


def export(format=None, path=None, scale=None, transparent=True,
           grid_overlay=False, canvas_id=None):
    if not format:
        raise PixelArtError("bad_param", "需要 format", "可用：png、svg、json、rle")
    store = _store()
    with store.lock:
        canvas = _resolve(store, canvas_id)
        result = save(canvas.index, canvas.palette, canvas.size, canvas.preset,
                      canvas.revision, str(format).lower(), path,
                      scale, bool(transparent), bool(grid_overlay))
    return {
        "headline": f"导出 {str(format).upper()} {result['width']}×{result['height']} "
                    f"至 {result['path']}",
        "data": result,
    }


def palette(action="get", colors=None, revision=None, canvas_id=None):
    store = _store()
    with store.lock:
        canvas = _resolve(store, canvas_id)
        if action == "get":
            body = " ".join(f"{i}:{hex_of(rgb)}" for i, rgb in enumerate(canvas.palette))
            return {
                "headline": f"「{canvas.name}」{canvas.preset}，{canvas.palette_size} 色",
                "body": body,
            }
        if action not in ("set", "quantize"):
            raise PixelArtError("bad_param", f"未知 action {action!r}",
                                "可用：get、set、quantize")
        _check_revision(canvas, revision)
        if not colors:
            raise PixelArtError("bad_param", f"{action} 需要 colors")
        new_palette = _build_palette(None, colors)[1]
        old_palette = list(canvas.palette)
        old_preset = canvas.preset
        new_index = canvas.copy_index()

        if action == "set":
            visible = new_index != TRANSPARENT
            used = int(new_index[visible].max()) if visible.any() else 0
            if used >= len(new_palette):
                raise PixelArtError("bad_color",
                                    f"已有像素用到索引 {used}，新色板只有 {len(new_palette)} 色",
                                    "改用 quantize，或补足色数")
        else:
            mapping = [nearest_index(rgb, new_palette) for rgb in old_palette]
            visible = new_index != TRANSPARENT
            new_index[visible] = np.array(mapping, dtype=np.uint8)[new_index[visible]]

        old_index = canvas.index
        changes = changed_cells(old_index, new_index)
        canvas.palette = new_palette
        canvas.preset = "custom"
        revision_now = canvas.revision + 1
        try:
            store.storage.save_pixels(canvas, new_index, revision_now)
        except PixelArtError:
            canvas.palette, canvas.preset = old_palette, old_preset
            raise
        except Exception as e:
            canvas.palette, canvas.preset = old_palette, old_preset
            raise PixelArtError("persist_failed", f"写入画布失败: {e}") from e

        canvas.index = new_index
        canvas.revision = revision_now
        _push(store, canvas, changes)
        data = {
            "revision": canvas.revision,
            "bbox": changed_bbox(old_index, new_index),
            "changed_cells": changes,
            "filled_cells": canvas.filled_cells,
            "palette_size": len(new_palette),
        }
        if action == "quantize":
            data["remapped_cells"] = changes
        return {"headline": _write_headline(canvas, data), "data": data}


def _push_empty() -> None:
    """没有画布时推空态：窗口显示占位文案并禁用导出。"""
    try:
        _emit({"empty": True, "force_show": True})
    except Exception:
        logger.debug("[pixel_art] canvas push failed", exc_info=True)


def refresh_view() -> None:
    """右键菜单入口：把当前画布重新推给窗口；没有画布时推空态。"""
    store = _store()
    with store.lock:
        try:
            canvas = _resolve(store, None)
        except PixelArtError:
            canvas = None
    if canvas is None:
        _push_empty()
        return
    _push(store, canvas, force_show=True)


def _write_headline(canvas: Canvas, data: dict) -> str:
    return (f"「{canvas.name}」revision {data['revision']}，本次 {data['changed_cells']} 格，"
            f"画面 {data['filled_cells']}/{canvas.size ** 2}")
