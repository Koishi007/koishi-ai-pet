"""工具执行器 — 解析 LLM 输出中的 Tool JSON，路由执行，返回结果。"""

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from pet.tools.registry import TOOL_REGISTRY

logger = logging.getLogger(__name__)


_MAX_LOG_VALUE_LEN = 300    # 单个字段值最多显示的字符数
_MAX_LOG_LINE_LEN = 4000    # 整行日志最多显示的字符数


def _log_preview(data: Any) -> str:
    """工具返回数据的日志预览：超长字段截断并标注溢出量"""
    if isinstance(data, dict):
        parts = []
        for key, value in data.items():
            text = str(value)
            if len(text) > _MAX_LOG_VALUE_LEN:
                text = text[:_MAX_LOG_VALUE_LEN] + f"...<+{len(text) - _MAX_LOG_VALUE_LEN}字>"
            parts.append(f"{key}={text}")
        preview = "{ " + ", ".join(parts) + " }"
    else:
        preview = str(data)
    if len(preview) > _MAX_LOG_LINE_LEN:
        preview = preview[:_MAX_LOG_LINE_LEN] + f"...<+{len(preview) - _MAX_LOG_LINE_LEN}字>"
    return preview


def _split_legacy_keys(data: Any) -> dict:
    """把旧工具的魔法键拆进 meta；工具声明 output 后不再产生，阶段 5 删除。"""
    if not isinstance(data, dict):
        return {}
    image = data.pop("__image__", None)
    mime = data.pop("__image_mime__", "image/png")
    context = data.pop("__context__", "")
    meta: dict = {}
    if context:
        meta["context_brief"] = context
    if image:
        meta["images"] = [{"base64": image, "mime": mime, "caption": context}]
    return meta


_TYPE_MAP = {
    "int": int,
    "float": (int, float),
    "str": str,
    "bool": bool,
    "list": list,
    "dict": dict,
    "any": object,
}


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class ToolResult:
    name: str
    output: Any = None                    # 规范值；未声明 output 的方法为原始返回值
    error_code: str = ""                  # 空即成功
    error_message: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error_code


class ToolExecutor:

    def execute(self, calls: list[ToolCall]) -> list[ToolResult]:
        results = []
        for call in calls:
            results.append(self._execute_one(call))
        return results

    def execute_one(self, call: ToolCall) -> ToolResult:
        """执行单个工具调用（带超时与参数校验）。"""
        return self._execute_one(call)

    @classmethod
    def normalize(cls, data: Any) -> str:
        """把工具返回的数据整理成给模型看的文本。"""
        return cls._normalize(data)

    def _execute_one(self, call: ToolCall) -> ToolResult:
        method = self._lookup_method(call.name)
        if method is None:
            logger.warning(f"[ToolExecutor] unknown tool: {call.name}")
            return ToolResult(name=call.name, error_code="unknown_tool",
                              error_message=f"unknown tool: {call.name}")

        validated_args, err = self._validate_args(call.args, method.args)
        if err:
            logger.warning(f"[ToolExecutor] arg validation failed: {call.name}: {err}")
            return ToolResult(name=call.name, error_code="bad_arguments", error_message=err)

        try:
            box: dict[str, Any] = {}

            def _runner() -> None:
                # 捕获 BaseException 以匹配原 ThreadPoolExecutor future 的语义
                try:
                    box["data"] = method.handler(**validated_args)
                except BaseException as exc:
                    box["error"] = exc

            worker = threading.Thread(target=_runner, daemon=True)
            worker.start()
            worker.join(timeout=method.timeout)

            if worker.is_alive():
                # 超时：守护线程仍在后台运行，不阻塞返回
                logger.warning(f"[ToolExecutor] {call.name} timed out after {method.timeout}s")
                return ToolResult(name=call.name, error_code="timeout",
                                  error_message=f"工具执行超时（{method.timeout}s）")

            if "error" in box:
                raise box["error"]

            data = box.get("data")
            logger.info(f"[ToolExecutor] {call.name} -> {_log_preview(data)}")
            return ToolResult(name=call.name, output=data, meta=_split_legacy_keys(data))
        except TypeError as e:
            logger.error(f"[ToolExecutor] {call.name} TypeError: {e}")
            return ToolResult(name=call.name, error_code="handler_error",
                              error_message=f"参数不匹配: {e}")
        except Exception as e:
            logger.error(f"[ToolExecutor] {call.name} failed: {e}")
            return ToolResult(name=call.name, error_code="handler_error",
                              error_message=str(e))

    @staticmethod
    def _lookup_method(full_name: str):
        return TOOL_REGISTRY.get_method(full_name)

    @staticmethod
    def _validate_args(provided: dict, schema: dict) -> tuple[dict, str]:
        validated = {}
        provided = provided or {}

        for key, spec in schema.items():
            type_name = spec.get("type", "any")
            required = spec.get("required", False)
            default = spec.get("default")

            if key in provided:
                value = provided[key]
                expected_type = _TYPE_MAP.get(type_name, object)
                if expected_type is not object and not isinstance(value, expected_type):
                    return {}, f"参数 {key!r} 类型错误，期望 {type_name}，实际 {type(value).__name__}"
                enum_values = spec.get("enum")
                if enum_values and value not in enum_values:
                    return {}, f"参数 {key!r} 值 {value!r} 不在允许范围 {enum_values} 内"
                validated[key] = value
            elif required:
                return {}, f"缺少必需参数 {key!r} ({type_name})"
            elif default is not None:
                validated[key] = default

        for key, value in provided.items():
            if key not in validated:
                validated[key] = value

        return validated, ""

    @staticmethod
    def _normalize(data: Any) -> str:
        if isinstance(data, dict):
            head = [str(data[key]) for key in ("summary", "model_hint") if data.get(key)]
            clean = {k: v for k, v in data.items() if k not in ("summary", "model_hint")}
            json_str = json.dumps(clean, ensure_ascii=False)
            return "\n".join(head + [json_str])
        elif isinstance(data, str):
            return data
        return str(data)

    @classmethod
    def format_results(cls, results: list[ToolResult]) -> tuple[str, list[str]]:
        lines = []
        image_uris = []
        for r in results:
            if r.ok:
                lines.append(f"[OK {r.name}]\n{cls._normalize(r.output)}")
                for image in r.meta.get("images") or []:
                    image_uris.append(
                        f"data:{image.get('mime', 'image/png')};base64,{image.get('base64', '')}")
                    lines.append("(附图: 已提供截图，可直接观察内容)")
            else:
                lines.append(f"[FAIL {r.name}] {r.error_code}: {r.error_message}")
        return "\n\n".join(lines), image_uris
