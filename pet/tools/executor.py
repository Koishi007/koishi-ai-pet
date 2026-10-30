"""工具执行器 — 解析 LLM 输出中的 Tool JSON，路由执行，返回结果。"""

import logging
import math
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


_TYPE_MAP = {
    "int": int,
    "float": (int, float),
    "str": str,
    "bool": bool,
    "list": list,
    "dict": dict,
    "any": object,
}

_TRUE_WORDS = {"true", "1", "yes", "y", "on", "是"}
_FALSE_WORDS = {"false", "0", "no", "n", "off", "否"}


def _match_type(value: Any, expected_type: Any, type_name: str) -> bool:
    """类型匹配判定：bool 不算 int/float，避免 true 静默当数值。"""
    if isinstance(value, bool) and type_name in ("int", "float"):
        return False
    return isinstance(value, expected_type)


def _coerce_arg(value: Any, type_name: str) -> tuple[Any, bool]:
    """把模型常见的错型参数转成声明类型；转不动返回 (原值, False)。"""
    if type_name == "int":
        if isinstance(value, bool):
            return int(value), True
        if isinstance(value, (int, float, str)):
            try:
                number = float(value.strip() if isinstance(value, str) else value)
            except (ValueError, OverflowError):
                return value, False
            # 非整数不静默截断，交给上层报错或由模型重传
            if math.isfinite(number) and number.is_integer():
                return int(number), True
            return value, False
    elif type_name == "float":
        if isinstance(value, bool):
            return float(value), True
        if isinstance(value, int):
            return float(value), True
        if isinstance(value, str):
            try:
                number = float(value.strip())
            except ValueError:
                return value, False
            if math.isfinite(number):
                return number, True
            return value, False
    elif type_name == "bool":
        if isinstance(value, str):
            text = value.strip().lower()
            if text in _TRUE_WORDS:
                return True, True
            if text in _FALSE_WORDS:
                return False, True
            return value, False
        if isinstance(value, (int, float)):
            return bool(value), True
    elif type_name == "str":
        if isinstance(value, bool):
            return ("true" if value else "false"), True
        if isinstance(value, (int, float)):
            return str(value), True
    return value, False


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class ToolResult:
    name: str
    output: Any = None                    # 规范值，符合方法声明的 output.schema
    error_code: str = ""                  # 空即成功
    error_message: str = ""
    hint: str = ""                         # 工具自带的下一步提示，只进当轮视图
    meta: dict = field(default_factory=dict)
    output_schema: Any = None             # 执行时取得的输出契约，不再回查注册表

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
            # 业务失败沿用 {"error": "..."} 的写法：转成错误通道，交给投影层统一渲染
            if isinstance(data, dict) and data.get("error") and not data.get("headline"):
                return ToolResult(name=call.name,
                                  error_code=str(data.get("code") or "tool_error"),
                                  error_message=str(data["error"]),
                                  hint=str(data.get("hint") or ""))
            return ToolResult(name=call.name, output=data, output_schema=method.output)
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
            value = provided.get(key)

            if value is None:
                # 显式 null 与缺参同义：必需报错，否则走默认
                if required:
                    return {}, f"缺少必需参数 {key!r} ({type_name})"
                if default is not None:
                    validated[key] = default
                continue

            expected_type = _TYPE_MAP.get(type_name, object)
            if expected_type is not object and not _match_type(value, expected_type, type_name):
                coerced, ok = _coerce_arg(value, type_name)
                if not ok:
                    return {}, f"参数 {key!r} 类型错误，期望 {type_name}，实际 {type(value).__name__}"
                logger.info(f"[ToolExecutor] coerced arg {key!r}: {type(value).__name__} "
                            f"{str(value)[:_MAX_LOG_VALUE_LEN]!r} -> {type_name}")
                value = coerced
            enum_values = spec.get("enum")
            if enum_values and value not in enum_values:
                return {}, f"参数 {key!r} 值 {value!r} 不在允许范围 {enum_values} 内"
            validated[key] = value

        for key, value in provided.items():
            if key not in schema and key not in validated:
                validated[key] = value

        return validated, ""


