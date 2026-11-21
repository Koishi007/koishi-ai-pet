"""像素画数据模型：色板、矩阵解析、RLE 编解码、文本预览。"""

from __future__ import annotations

import numpy as np

TRANSPARENT = 255
MAX_COLORS = 32
# 单字符色索引：0-9 与 a-v，共 32 个；. 透明，? 保留原值
INDEX_CHARS = "0123456789abcdefghijklmnopqrstuv"

PICO8 = [
    (0x00, 0x00, 0x00), (0x1D, 0x2B, 0x53), (0x7E, 0x25, 0x53), (0x00, 0x87, 0x51),
    (0xAB, 0x52, 0x36), (0x5F, 0x57, 0x4F), (0xC2, 0xC3, 0xC7), (0xFF, 0xF1, 0xE8),
    (0xFF, 0x00, 0x4D), (0xFF, 0xA3, 0x00), (0xFF, 0xEC, 0x27), (0x00, 0xE4, 0x36),
    (0x29, 0xAD, 0xFF), (0x83, 0x76, 0x9C), (0xFF, 0x77, 0xA8), (0xFF, 0xCC, 0xAA),
]
GAMEBOY = [(0x0F, 0x38, 0x0F), (0x30, 0x62, 0x30), (0x8B, 0xAC, 0x0F), (0x9B, 0xBC, 0x0F)]
NES = [
    (0x00, 0x00, 0x00), (0xFC, 0xFC, 0xFC), (0x7C, 0x7C, 0x7C), (0xBC, 0xBC, 0xBC),
    (0x00, 0x78, 0xF8), (0x3C, 0xBC, 0xFC), (0xA4, 0xE4, 0xFC), (0x00, 0x00, 0xFC),
    (0x00, 0x58, 0xF8), (0x68, 0x88, 0xFC), (0xB8, 0xB8, 0xF8), (0xE4, 0x00, 0x58),
    (0xF8, 0x78, 0xF8), (0xF8, 0xB8, 0xF8), (0xFC, 0xE4, 0xA0), (0xF8, 0xB8, 0x00),
]

# DawnBringer 32：32 色通用像素画板，相邻色阶跨度比 pico8 小
DB32 = [
    (0x00, 0x00, 0x00), (0x22, 0x20, 0x34), (0x45, 0x28, 0x3C), (0x66, 0x39, 0x31),
    (0x8F, 0x56, 0x3B), (0xDF, 0x71, 0x26), (0xD9, 0xA0, 0x66), (0xEE, 0xC3, 0x9A),
    (0xFB, 0xF2, 0x36), (0x99, 0xE5, 0x50), (0x6A, 0xBE, 0x30), (0x37, 0x94, 0x6E),
    (0x4B, 0x69, 0x2F), (0x52, 0x4B, 0x24), (0x32, 0x3C, 0x39), (0x3F, 0x3F, 0x74),
    (0x30, 0x60, 0x82), (0x5B, 0x6E, 0xE1), (0x63, 0x9B, 0xFF), (0x5F, 0xCD, 0xE4),
    (0xCB, 0xDB, 0xFC), (0xFF, 0xFF, 0xFF), (0x9B, 0xAD, 0xB7), (0x84, 0x7E, 0x87),
    (0x69, 0x6A, 0x6A), (0x59, 0x56, 0x52), (0x76, 0x42, 0x8A), (0xAC, 0x32, 0x32),
    (0xD9, 0x57, 0x63), (0xD7, 0x7B, 0xBA), (0x8F, 0x97, 0x4A), (0x8A, 0x6F, 0x30),
]

PRESETS = {"pico8": PICO8, "gameboy": GAMEBOY, "nes": NES, "db32": DB32}


class PixelArtError(Exception):
    """带稳定错误码的业务失败，由 handler 转成 {"error","code","hint"}。"""

    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


class Canvas:
    """一张画布的完整状态。index 为 uint8 矩阵，0-31 是色索引，255 透明。"""

    def __init__(self, canvas_id: str, name: str, size: int, preset: str,
                 palette: list[tuple[int, int, int]], index: np.ndarray,
                 revision: int = 0):
        self.id = canvas_id
        self.name = name
        self.size = size
        self.preset = preset
        self.palette = palette
        self.index = index
        self.revision = revision

    @property
    def filled_cells(self) -> int:
        return int(np.count_nonzero(self.index != TRANSPARENT))

    @property
    def palette_size(self) -> int:
        return len(self.palette)

    def copy_index(self) -> np.ndarray:
        return self.index.copy()


def new_index(size: int, background: int = TRANSPARENT) -> np.ndarray:
    return np.full((size, size), background, dtype=np.uint8)


def rgb_of(palette: list[tuple[int, int, int]], color: int) -> tuple[int, int, int]:
    return palette[color]


def hex_of(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def parse_hex(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        raise PixelArtError("bad_color", f"颜色 {value!r} 不是 #RRGGBB 形式")
    if any(ch not in _HEX_DIGITS for ch in text):
        raise PixelArtError("bad_color", f"颜色 {value!r} 含非法十六进制位")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def nearest_index(rgb: tuple[int, int, int], palette: list[tuple[int, int, int]]) -> int:
    best, best_d = 0, None
    for i, ref in enumerate(palette):
        d = sum((a - b) ** 2 for a, b in zip(rgb, ref))
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


def parse_color(value, palette: list[tuple[int, int, int]], field: str = "c") -> int:
    """索引（0-31 的整数或单字符）或 #RRGGBB；板外颜色拒绝并给最近索引。"""
    if isinstance(value, bool) or value is None:
        raise PixelArtError("bad_color", f"{field} 必须是色索引或 #RRGGBB")
    if isinstance(value, int):
        idx = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise PixelArtError("bad_color", f"{field} 为空", "用 0-9a-v 或 #RRGGBB")
        if text.startswith("#"):
            rgb = parse_hex(text)
            idx = nearest_index(rgb, palette)
            if palette[idx] != rgb:
                raise PixelArtError(
                    "bad_color",
                    f"颜色 {text} 不在色板，最近为 {idx} ({hex_of(palette[idx])})",
                    "先调 palette 的 quantize，或改用色板内的颜色")
            return idx
        if len(text) == 1:
            ch = text.lower()
            if ch not in INDEX_CHARS:
                raise PixelArtError("bad_color", f"{field} 字符 {value!r} 非法", "只允许 0-9a-v")
            idx = INDEX_CHARS.index(ch)
        elif text.isdigit():
            idx = int(text)
        else:
            raise PixelArtError("bad_color", f"{field} 值 {value!r} 无法解析", "用 0-9a-v 或 #RRGGBB")
    else:
        raise PixelArtError("bad_color", f"{field} 类型不支持: {type(value).__name__}")

    if not 0 <= idx < len(palette):
        raise PixelArtError("bad_color", f"色索引 {idx} 超出范围 0-{len(palette) - 1}",
                            "用 palette 查看当前色板")
    return idx


def parse_region(region, size: int) -> tuple[int, int, int, int]:
    if region is None:
        return 0, 0, size, size
    if not isinstance(region, (list, tuple)) or len(region) != 4:
        raise PixelArtError("bad_param", "region 必须是 [x, y, w, h] 四个整数")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in region):
        raise PixelArtError("bad_param", f"region 必须是四个整数，收到 {region!r}")
    x, y, w, h = region
    if w <= 0 or h <= 0:
        raise PixelArtError("bad_region", f"region 宽高必须为正，收到 {w}×{h}")
    if x < 0 or y < 0 or x + w > size or y + h > size:
        raise PixelArtError("bad_region",
                            f"region {region} 超出 {size}×{size} 画布")
    if w * h > 1024:
        raise PixelArtError("region_too_large",
                            f"region 面积 {w * h} 格超出上限 1024",
                            "拆成多次调用，或改用 paint 的算子")
    return x, y, w, h


def parse_rows(rows, width: int, height: int,
               base: np.ndarray | None = None,
               palette_size: int = MAX_COLORS) -> np.ndarray:
    """rows 逐行解析；`?` 取 base 对应格的原值，索引按当前色板长度校验。"""
    if not isinstance(rows, (list, tuple)):
        raise PixelArtError("bad_rows", "rows 必须是字符串数组")
    if len(rows) != height:
        raise PixelArtError("bad_rows", f"需要 {height} 行，收到 {len(rows)} 行")

    out = np.full((height, width), TRANSPARENT, dtype=np.uint8)
    for y, row in enumerate(rows):
        if not isinstance(row, str):
            raise PixelArtError("bad_rows", f"第 {y + 1} 行不是字符串")
        if len(row) != width:
            raise PixelArtError("bad_rows",
                                f"第 {y + 1} 行长度 {len(row)}，期望 {width}")
        for x, raw in enumerate(row):
            ch = raw.lower()
            if ch == "?":
                out[y, x] = base[y, x] if base is not None else TRANSPARENT
            elif ch == ".":
                out[y, x] = TRANSPARENT
            elif ch in INDEX_CHARS:
                idx = INDEX_CHARS.index(ch)
                if idx >= palette_size:
                    raise PixelArtError("bad_rows", f"第 {y + 1} 行第 {x + 1} 列色索引越界",
                                        f"当前色板 {palette_size} 色，索引用 0-{palette_size - 1}")
                out[y, x] = idx
            else:
                raise PixelArtError("bad_rows",
                                    f"第 {y + 1} 行第 {x + 1} 列字符 {raw!r} 非法",
                                    "只允许 0-9a-v、. 与 ?")
    return out


def encode_rle(index: np.ndarray) -> str:
    flat = index.ravel()
    if flat.size == 0:
        return ""
    parts: list[str] = []
    prev = int(flat[0])
    count = 1
    for raw in flat[1:]:
        value = int(raw)
        if value == prev:
            count += 1
            continue
        parts.append(f"{count}:{char_of(prev)}")
        prev, count = value, 1
    parts.append(f"{count}:{char_of(prev)}")
    return "".join(parts)


def char_of(value: int) -> str:
    """色索引转单字符；透明为 `.`。"""
    return "." if value == TRANSPARENT else INDEX_CHARS[value]


def decode_rle(text, size: int, palette_size: int = MAX_COLORS) -> np.ndarray:
    if not isinstance(text, str) or not text.strip():
        raise PixelArtError("bad_rle", "rle 为空")
    total = size * size
    out = np.empty(total, dtype=np.uint8)
    pos = 0
    i = 0
    n = len(text)
    while i < n:
        j = i
        while j < n and text[j].isdigit():
            j += 1
        if j == i:
            raise PixelArtError("bad_rle", f"位置 {i} 缺少数量", "格式为 <数量>:<字符>")
        count = int(text[i:j])
        if count <= 0:
            raise PixelArtError("bad_rle", f"位置 {i} 的数量必须为正")
        if j >= n or text[j] != ":":
            raise PixelArtError("bad_rle", f"数量 {count} 后缺少分隔符 :",
                                "格式为 <数量>:<字符>")
        j += 1
        if j >= n:
            raise PixelArtError("bad_rle", "最后一个数量后缺少字符")
        ch = text[j].lower()
        j += 1
        if ch == ".":
            value = TRANSPARENT
        elif ch in INDEX_CHARS:
            value = INDEX_CHARS.index(ch)
            if value >= palette_size:
                raise PixelArtError("bad_rle", f"字符 {ch!r} 超出当前色板（{palette_size} 色）",
                                    f"索引用 0-{palette_size - 1}")
        else:
            raise PixelArtError("bad_rle", f"字符 {ch!r} 非法", "只允许 0-9a-v 与 .")
        if pos + count > total:
            raise PixelArtError("bad_rle",
                                f"格数超出：期望 {total}，实际至少 {pos + count}")
        out[pos:pos + count] = value
        pos += count
        i = j
    if pos != total:
        raise PixelArtError("bad_rle", f"格数不符：期望 {total}，实际 {pos}")
    return out.reshape(size, size)


def changed_bbox(old: np.ndarray, new: np.ndarray) -> list[int] | None:
    mask = old != new
    if not mask.any():
        return None
    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def changed_cells(old: np.ndarray, new: np.ndarray) -> int:
    return int(np.count_nonzero(old != new))


def render_text_matrix(index: np.ndarray, region=None) -> str:
    """16 列字符矩阵，行首两位行号；64 档按块取众数。"""
    size = index.shape[0]
    if region is None:
        x, y, w, h = 0, 0, size, size
    else:
        x, y, w, h = region

    sub = index[y:y + h, x:x + w]
    if w <= 16 and h <= 16:
        step = 1
        block = sub
    else:
        step = max(1, -(-max(w, h) // 16))
        block = _aggregate(sub, step)

    lines = []
    for row_idx in range(block.shape[0]):
        chars = "".join(char_of(int(v)) for v in block[row_idx])
        lines.append(f"{y + row_idx * step:02d} {chars}")
    return "\n".join(lines)


def _aggregate(block: np.ndarray, step: int) -> np.ndarray:
    height, width = block.shape
    rows = []
    for y in range(0, height, step):
        row = []
        for x in range(0, width, step):
            chunk = block[y:y + step, x:x + step].ravel()
            visible = chunk[chunk != TRANSPARENT]
            if visible.size == 0:
                row.append(TRANSPARENT)
            else:
                counts = np.bincount(visible, minlength=MAX_COLORS)
                row.append(int(counts.argmax()))
        rows.append(row)
    return np.array(rows, dtype=np.uint8)
