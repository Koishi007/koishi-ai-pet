"""pixel_art 工具 — 点阵绘画：有限色板、矩阵与算子落笔、落库与导出"""

from __future__ import annotations

import logging

from pet.tools.output import IMAGES_SCHEMA, PAGINATION_SCHEMA, ToolOutputDefinition
from pet.tools.pixel_art.canvas import PixelArtError
from pet.tools.pixel_art.core import (create, delete, list_canvases, open_canvas,
                                      paint, palette, postprocess, preview, refresh_view,
                                      rename, set_pixels)
from pet.tools.pixel_art.core import export as export_canvas
from pet.tools.pixel_art.ops import (ALL_OPS, DITHERS, POSTPROCESS_OPS, SHAPES,
                                     TILES)

logger = logging.getLogger(__name__)

TOOL_NAME = "pixel_art"
TOOL_DESCRIPTION = (
    "像素画/点阵画：新建、打开、列举、改名、删除画布；用像素矩阵或图元算子绘制，"
    "可后处理、预览、导出 PNG/SVG/JSON。画布按 id 存库，重启后仍可继续编辑。"
    "已有画布用 list 查看、open 按 id 继续；名字只作标识、允许重名。"
    "16 档整图给 rows，32/64 档用 paint 的 ops。"
)
TOOL_GROUP = "creative"

# paint 的 ops 元素结构：字段名平铺，具体取哪些由 op 决定（见方法描述与 OP_FIELDS）
OPS_ITEM_SCHEMA = {
    "type": "object",
    "required": ["op"],
    "properties": {
        "op": {"type": "string", "enum": list(ALL_OPS)},
        "x": {"type": "integer"}, "y": {"type": "integer"},
        "w": {"type": "integer"}, "h": {"type": "integer"},
        "x1": {"type": "integer"}, "y1": {"type": "integer"},
        "x2": {"type": "integer"}, "y2": {"type": "integer"},
        "c": {"description": "色索引字符 0-9a-v 或 #RRGGBB"},
        "fill": {"type": "boolean"},
        "points": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}},
                   "description": "[[x, y], …]"},
        "region": {"type": "array", "items": {"type": "integer"},
                   "description": "[x, y, w, h]"},
        "from": {"description": "起始色：色索引或 #RRGGBB"},
        "to": {"description": "结束色：色索引或 #RRGGBB"},
        "dither": {"type": "string", "enum": list(DITHERS)},
        "mask": {"type": "string", "enum": ["none", "circle"]},
        "ratio": {"type": "number"}, "seed": {"type": "integer"},
        "tile": {"type": "string", "enum": list(TILES)},
        "shape": {"type": "string", "enum": list(SHAPES)},
        "count": {"type": "integer"},
    },
}

SIMPLE_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "一句话结论"},
        "data": {"type": "object", "x-role": "data",
                 "description": "结构化明细：canvas_id、revision、bbox、变化格数等"},
    },
}

CREATE_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "新建画布的名字、尺寸与色数"},
        "data": {"type": "object", "x-role": "data",
                 "description": "canvas_id、name、revision、palette_size"},
    },
}

LIST_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "画布总数与页码"},
        "body": {"type": "string", "x-role": "body",
                 "description": "每行一张：id、名字、尺寸、revision、更新时间"},
        "pagination": PAGINATION_SCHEMA,
    },
}

PREVIEW_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "画布名、尺寸、revision 与填充度"},
        "body": {"type": "string", "x-role": "body",
                 "description": "16 列字符矩阵，行首两位行号；0-9a-v 为色索引，. 为透明"},
        "images": IMAGES_SCHEMA,
        "hint": {"type": "string", "x-role": "hint", "description": "下一步提示"},
    },
}

PALETTE_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "色板状态或重映射结果"},
        "body": {"type": "string", "x-role": "body",
                 "description": "get 时逐条列出 索引:十六进制值"},
        "data": {"type": "object", "x-role": "data",
                 "description": "palette_size 与 remapped_cells"},
    },
}

EXPORT_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "格式、尺寸与落点"},
        "data": {"type": "object", "x-role": "data",
                 "description": "path、width、height、bytes"},
    },
}


def guarded(handler):
    """把业务异常转成统一错误返回值。"""

    def wrapped(**kwargs):
        try:
            return handler(**kwargs)
        except PixelArtError as e:
            return {"error": e.message, "code": e.code, "hint": e.hint}

    return wrapped


def register(registry):
    registry.register(TOOL_NAME, TOOL_DESCRIPTION, group=TOOL_GROUP, side_effect=True)

    registry.add_method(
        TOOL_NAME, "create", "新建画布：命名、定尺寸与色板，立即落库并显示窗口",
        handler=guarded(create), timeout=15.0,
        args={
            "name": {"type": "str", "required": True,
                     "desc": "画布名，1-24 字符，可用中文；允许重名，标识以 id 为准"},
            "size": {"type": "str", "required": True, "enum": ["16", "32", "64"],
                     "desc": "正方形边长，三档之一"},
            "preset": {"type": "str", "enum": ["pico8", "gameboy", "nes", "db32"],
                       "default": "pico8",
                       "desc": "预设色板：pico8 16 色、gameboy 4 色、nes 16 色、db32 32 色；与 palette 互斥"},
            "palette": {"type": "list", "desc": "自定义色板，#RRGGBB 数组，最多 32 色"},
            "background": {"type": "str",
                           "desc": "底色：色索引字符 0-9a-v 或 #RRGGBB；透明请省略该参数，`.` 只是矩阵里的透明符，不是合法底色"},
        },
        output=ToolOutputDefinition(schema=CREATE_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "open", "按 id 把库内已有画布载入内存并设为当前，窗口切换显示",
        handler=guarded(open_canvas), timeout=15.0,
        args={
            "canvas_id": {"type": "str", "required": True, "desc": "画布 id，用 list 查"},
        },
        output=ToolOutputDefinition(schema=SIMPLE_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "list", "分页列举已有画布：id、名字、尺寸、revision、更新时间",
        handler=guarded(list_canvases), timeout=15.0,
        args={
            "page": {"type": "int", "default": 1, "desc": "页码，从 1 起"},
            "page_size": {"type": "int", "default": 20, "desc": "每页条数，1-50"},
        },
        output=ToolOutputDefinition(schema=LIST_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "rename", "修改画布名",
        handler=guarded(rename), timeout=15.0,
        args={
            "canvas_id": {"type": "str", "desc": "画布 id；省略时作用于当前画布"},
            "name": {"type": "str", "required": True, "desc": "新名字，1-24 字符"},
        },
        output=ToolOutputDefinition(schema=SIMPLE_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "delete", "删除画布，同时清除内存缓存",
        handler=guarded(delete), timeout=15.0,
        args={
            "canvas_id": {"type": "str", "desc": "画布 id；省略时作用于当前画布"},
        },
        output=ToolOutputDefinition(schema=SIMPLE_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "set",
        "提交像素矩阵：16 档可整图给 rows；32/64 档必须带 region（面积不超过 1024）。"
        "字符 0-9a-v 为色索引（最多 32 色），. 透明，? 保留原值；也可用 rle 整图覆盖",
        handler=guarded(set_pixels), timeout=15.0,
        args={
            "canvas_id": {"type": "str", "desc": "画布 id；省略时作用于当前画布"},
            "revision": {"type": "int", "desc": "乐观锁；省略则不校验"},
            "rows": {"type": "list", "desc": "每行一个字符串，长度必须等于区域宽"},
            "rle": {"type": "str", "desc": "整图游程，格式 <数量>:<字符>，如 12:.4:a"},
            "region": {"type": "list", "desc": "[x, y, w, h]，四整数，必须在画布内"},
            "mirror_x": {"type": "bool", "default": False,
                         "desc": "以画布中线镜像落笔"},
        },
        output=ToolOutputDefinition(schema=SIMPLE_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "paint",
        "批量落笔：ops 是对象数组，字段按 op 取。图元 point(x,y,c)、"
        "line(x1,y1,x2,y2,c)、rect/ellipse(x,y,w,h,c,fill)、polygon/path(points=[[x,y],…],c)、"
        "erase(x,y,w,h)、clear。过程式 gradient(region,from,to,dither,mask)、"
        "noise(region,c,ratio,seed)、pattern(region,tile,c)、mask(region,shape,c)、"
        "scatter(region,c,count,seed)。ellipse 用 x/y/w/h，没有 cx/cy/rx/ry；"
        "c 为色索引字符 0-9a-v 或 #RRGGBB，region 为 [x,y,w,h]。示例 "
        '[{"op":"rect","x":8,"y":4,"w":16,"h":12,"c":4},{"op":"line","x1":0,"y1":0,'
        '"x2":31,"y2":31,"c":1}]',
        handler=guarded(paint), timeout=15.0,
        args={
            "canvas_id": {"type": "str", "desc": "画布 id；省略时作用于当前画布"},
            "revision": {"type": "int", "desc": "乐观锁；省略则不校验"},
            "ops": {"type": "list", "required": True, "items": OPS_ITEM_SCHEMA,
                    "desc": "算子数组，最多 200 条；每项是对象，按 op 取字段"},
            "mirror_x": {"type": "bool", "default": False,
                         "desc": "每个 op 再按中线镜像执行一次"},
        },
        output=ToolOutputDefinition(schema=SIMPLE_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "postprocess", "整图或区域级变换",
        handler=guarded(postprocess), timeout=15.0,
        args={
            "canvas_id": {"type": "str", "desc": "画布 id；省略时作用于当前画布"},
            "revision": {"type": "int", "desc": "乐观锁；省略则不校验"},
            "op": {"type": "str", "required": True, "enum": list(POSTPROCESS_OPS),
                   "desc": "outline 描边、dither 抖动、mirror_x/y、rotate90、shade、cleanup、invert"},
            "region": {"type": "list", "desc": "[x, y, w, h]；省略为整图"},
            "params": {"type": "dict", "desc": "参数：c、steps 等，随 op 而定"},
        },
        output=ToolOutputDefinition(schema=SIMPLE_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "preview",
        "自检预览：默认返回 16 列字符矩阵，有视觉能力时可附最近邻放大的图片",
        handler=guarded(preview), timeout=15.0,
        args={
            "canvas_id": {"type": "str", "desc": "画布 id；省略时作用于当前画布"},
            "mode": {"type": "str", "enum": ["text", "image", "both"], "default": "text",
                     "desc": "text 文本矩阵、image 图片、both 两者"},
            "region": {"type": "list", "desc": "[x, y, w, h]；省略为整图"},
            "scale": {"type": "int", "desc": "图片放大倍数，上限 32"},
        },
        output=ToolOutputDefinition(schema=PREVIEW_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "export", "导出 PNG/SVG/JSON/RLE，默认落桌面",
        handler=guarded(export_canvas), timeout=15.0,
        args={
            "canvas_id": {"type": "str", "desc": "画布 id；省略时作用于当前画布"},
            "format": {"type": "str", "required": True,
                       "enum": ["png", "svg", "json", "rle"], "desc": "导出格式"},
            "path": {"type": "str", "desc": "文件名或绝对路径；只允许桌面与文档目录"},
            "scale": {"type": "int", "desc": "整数放大倍数，默认 256/size"},
            "transparent": {"type": "bool", "default": True, "desc": "保留透明背景"},
            "grid_overlay": {"type": "bool", "default": False, "desc": "叠加 1px 网格"},
        },
        output=ToolOutputDefinition(schema=EXPORT_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "palette", "查看或替换色板；quantize 按最近色重映射已有像素",
        handler=guarded(palette), timeout=15.0,
        args={
            "canvas_id": {"type": "str", "desc": "画布 id；省略时作用于当前画布"},
            "revision": {"type": "int", "desc": "乐观锁；省略则不校验"},
            "action": {"type": "str", "enum": ["get", "set", "quantize"], "default": "get",
                       "desc": "get 查询、set 换色值、quantize 重映射"},
            "colors": {"type": "list", "desc": "新色板，#RRGGBB 数组，最多 32 色"},
        },
        output=ToolOutputDefinition(schema=PALETTE_OUTPUT),
    )

    registry.add_menu_action(TOOL_NAME, "画布", refresh_view)
