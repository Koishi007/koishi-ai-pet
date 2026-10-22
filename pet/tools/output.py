"""工具输出的声明与投影：schema 约束规范值，render 产出模型可见的块。"""

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

# 渲染顺序固定：提示放在最后，图片插在数据之后
ROLE_ORDER = ("headline", "body", "data", "pagination", "images", "hint")
_ROLES = set(ROLE_ORDER) | {"suppress"}

_TYPE_CHECK = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}

RECORD_LIMIT = 120


@dataclass(frozen=True)
class TextBlock:
    """文本块；role 只在本层做排序与合并，不进入 wire 格式。"""
    text: str
    role: str = "primary"    # primary | data | hint


@dataclass(frozen=True)
class ImageBlock:
    """图片块；由编码层转成 user 消息里的 image_url，不进 tool 消息。"""
    base64: str
    mime: str
    caption: str = ""


ContentBlock = TextBlock | ImageBlock


@dataclass(frozen=True)
class ToolOutputDefinition:
    """一个方法的输出契约：schema 约束形状，renderer 只在需要特殊排版时提供。"""
    schema: dict
    renderer: Optional[Callable] = None


PAGINATION_SCHEMA = {
    "type": "object",
    "x-role": "pagination",
    "description": "分页信息",
    "properties": {
        "page":        {"type": "integer", "x-role": "suppress", "description": "当前页码"},
        "page_size":   {"type": "integer", "x-role": "suppress", "description": "每页条数"},
        "total_items": {"type": "integer", "x-role": "suppress", "description": "总条数"},
        "has_next":    {"type": "boolean", "x-role": "suppress", "description": "是否还有下一页"},
    },
}

IMAGES_SCHEMA = {
    "type": "array",
    "x-role": "images",
    "description": "图片列表",
    "items": {
        "type": "object",
        "required": ["base64", "mime"],
        "properties": {
            "base64":  {"type": "string", "x-role": "suppress", "description": "base64 图片数据"},
            "mime":    {"type": "string", "x-role": "suppress", "description": "图片 MIME"},
            "caption": {"type": "string", "x-role": "suppress", "description": "来源说明"},
        },
    },
}


def validate(definition: ToolOutputDefinition, value: Any) -> list[str]:
    """按 schema 校验规范值，返回违约路径；空表示通过。"""
    return _check_object(value, definition.schema, "")


def render_turn(definition: ToolOutputDefinition, value: Any) -> list[ContentBlock]:
    """当轮视图：按角色固定顺序展开，未声明字段归入 data。"""
    if definition.renderer is not None:
        return list(definition.renderer(value))
    if not isinstance(value, dict):
        return [TextBlock(text=str(value))] if value not in (None, "") else []

    fields = _collect_fields(definition.schema, value)
    blocks: list[ContentBlock] = []
    for role in ROLE_ORDER:
        for name, spec, item in fields.get(role, []):
            if item in (None, "", [], {}):
                continue
            if role == "images":
                blocks.extend(_image_blocks(item, name))
            elif role in ("headline", "body"):
                blocks.append(TextBlock(text=str(item)))
            elif role == "data":
                blocks.append(TextBlock(text=_flatten(item), role="data"))
            elif role == "pagination":
                hint = _pagination_hint(item)
                if hint:
                    blocks.append(TextBlock(text=hint, role="hint"))
            elif role == "hint":
                blocks.append(TextBlock(text=str(item), role="hint"))
    return blocks


def render_record(definition: ToolOutputDefinition, value: Any) -> str:
    """历史视图：只取 headline，附加翻页提示，截 120 字符。"""
    text = ""
    if isinstance(value, dict):
        for name, spec in _declared(definition.schema):
            if _role_of(spec) == "headline" and value.get(name):
                text = str(value[name]).strip()
                break
        pagination = _pagination_of(definition.schema, value)
        if text and pagination and pagination.get("has_next"):
            text = f"{text}，还有更多"
    return text[:RECORD_LIMIT]


def _declared(schema: dict):
    """按声明顺序产出 (字段名, 字段 schema)。"""
    for name, spec in (schema.get("properties") or {}).items():
        if isinstance(spec, dict):
            yield name, spec


def _role_of(spec: dict) -> str:
    role = spec.get("x-role") or "data"
    return role if role in _ROLES else "data"


def _collect_fields(schema: dict, value: dict) -> dict:
    """把返回值按角色分组；schema 未声明的字段按 data 处理。"""
    grouped: dict[str, list] = {}
    known = set()
    for name, spec in _declared(schema):
        known.add(name)
        if name in value:
            grouped.setdefault(_role_of(spec), []).append((name, spec, value[name]))
    extra = {k: v for k, v in value.items() if k not in known}
    if extra:
        grouped.setdefault("data", []).append(("extra", {"type": "object"}, extra))
    return grouped


def _flatten(value: Any, prefix: str = "") -> str:
    """把嵌套结构压成 key: value 行。"""
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(item, dict):
                lines.append(_flatten(item, name))
            elif isinstance(item, list):
                lines.append(f"{name}: {json.dumps(item, ensure_ascii=False)}")
            else:
                lines.append(f"{name}: {item}")
        return "\n".join(line for line in lines if line)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return f"{prefix}: {value}" if prefix else str(value)


def _image_blocks(items: Any, caption_fallback: str) -> list[ImageBlock]:
    blocks = []
    if not isinstance(items, list):
        return blocks
    for item in items:
        if not isinstance(item, dict) or not item.get("base64"):
            continue
        blocks.append(ImageBlock(
            base64=str(item["base64"]),
            mime=str(item.get("mime") or "image/png"),
            caption=str(item.get("caption") or caption_fallback),
        ))
    return blocks


def _pagination_of(schema: dict, value: dict) -> Optional[dict]:
    for name, spec in _declared(schema):
        if _role_of(spec) == "pagination" and isinstance(value.get(name), dict):
            return value[name]
    return None


def _pagination_hint(pagination: dict) -> str:
    if not pagination.get("has_next"):
        return ""
    page = pagination.get("page")
    total = pagination.get("total_items")
    next_page = page + 1 if isinstance(page, int) else "下一页"
    if page and total:
        return f"第 {page} 页，共 {total} 条，用 page={next_page} 继续"
    if page:
        return f"第 {page} 页，还有更多，用 page={next_page} 继续"
    return "还有更多"


def _check_object(value: Any, schema: dict, path: str) -> list[str]:
    problems: list[str] = []
    expected = schema.get("type")
    if expected and not _TYPE_CHECK.get(expected, lambda v: True)(value):
        return [f"{path or '$'}: 期望 {expected}"]

    if expected == "object" and isinstance(value, dict):
        for name in schema.get("required") or []:
            if name not in value:
                problems.append(f"{name}: 缺少必填字段")
        properties = schema.get("properties") or {}
        for name, item in value.items():
            spec = properties.get(name)
            if spec is None:
                if schema.get("additionalProperties") is False:
                    problems.append(f"{name}: 未在 schema 中声明")
                continue
            problems.extend(_check_value(item, spec, f"{path}.{name}" if path else name))
    elif expected == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                problems.extend(_check_value(item, item_schema, f"{path}[{index}]"))
    return problems


def _check_value(value: Any, spec: dict, path: str) -> list[str]:
    problems: list[str] = []
    expected = spec.get("type")
    if expected and not _TYPE_CHECK.get(expected, lambda v: True)(value):
        return [f"{path}: 期望 {expected}"]
    max_len = spec.get("maxLength")
    if max_len is not None and isinstance(value, str) and len(value) > int(max_len):
        problems.append(f"{path}: 超过 {max_len} 字符")
    enum = spec.get("enum")
    if isinstance(enum, list) and value not in enum:
        problems.append(f"{path}: 不在枚举内")
    if expected == "object" and isinstance(value, dict):
        problems.extend(_check_object(value, spec, path))
    elif expected == "array" and isinstance(value, list):
        item_schema = spec.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                problems.extend(_check_value(item, item_schema, f"{path}[{index}]"))
    return problems
