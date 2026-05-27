"""工具执行器 — 解析 LLM 输出中的 Tool JSON，路由执行，返回结果。"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from pet.skills.registry import TOOL_REGISTRY

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class ToolResult:
    name: str
    success: bool
    data: Any = None
    error: str = ""


class ToolExecutor:
    """串行执行工具调用，架构可扩展为并行。"""

    def execute(self, calls: list[ToolCall]) -> list[ToolResult]:
        results = []
        for call in calls:
            results.append(self._execute_one(call))
        return results

    def _execute_one(self, call: ToolCall) -> ToolResult:
        handler = TOOL_REGISTRY.get_handler(call.name)
        if handler is None:
            logger.warning(f"[ToolExecutor] unknown tool: {call.name}")
            return ToolResult(name=call.name, success=False, error="unknown tool")
        try:
            data = handler(**call.args)
            logger.info(f"[ToolExecutor] \u2713 {call.name} \u2192 {str(data)[:100]}")
            return ToolResult(name=call.name, success=True, data=data)
        except Exception as e:
            logger.error(f"[ToolExecutor] \u2717 {call.name} failed: {e}")
            return ToolResult(name=call.name, success=False, error=str(e))

    @staticmethod
    def parse_tool_lines(content: str) -> list[ToolCall]:
        calls = []
        for line in content.split("\n"):
            line = line.strip()
            if line.lower().startswith("tool:"):
                raw = line.split(":", 1)[1].strip()
                try:
                    obj = json.loads(raw)
                    calls.append(ToolCall(
                        name=obj.get("name", ""),
                        args=obj.get("args", {}),
                    ))
                except json.JSONDecodeError:
                    logger.warning(f"[ToolExecutor] invalid JSON: {raw[:80]}")
        return calls

    @staticmethod
    def _normalize(data: Any) -> str:
        """统一格式化工具返回值：dict 支持 summary 键，兼容 str/基本类型。"""
        if isinstance(data, dict):
            summary = data.pop("summary", None)
            json_str = json.dumps(data, ensure_ascii=False)
            return f"{summary}\n{json_str}" if summary else json_str
        elif isinstance(data, str):
            return data
        return str(data)

    @classmethod
    def format_results(cls, results: list[ToolResult]) -> str:
        lines = []
        for r in results:
            if r.success:
                lines.append(f"[\u2713 {r.name}]\n{cls._normalize(r.data)}")
            else:
                lines.append(f"[\u2717 {r.name}] \u5931\u8d25: {r.error}")
        return "\n\n".join(lines)
