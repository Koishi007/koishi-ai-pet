"""命令分发：冒号前缀判定，以及内建与工具命令的两条执行路径。"""

from __future__ import annotations

import logging

from pet.tools.executor import ToolCall, ToolExecutor

from .catalog import find, method_exists, usage
from .types import EXIT_TOOL, EXIT_USAGE, LocalCommand, LocalContext, LocalResult, PREFIX

logger = logging.getLogger(__name__)


def is_local(text: str) -> bool:
    """输入是否属于本地命令。"""
    return str(text or "").lstrip().startswith(PREFIX)


def run(text: str, cwd: str, runner) -> LocalResult:
    """执行一条本地命令；未知命令与非法用法都返回 EXIT_USAGE。"""
    body = str(text or "").strip()[len(PREFIX):].strip()
    name, _, arg = body.partition(" ")
    if not name:
        return usage("help")
    command = find(name.lower())
    if command is None:
        return LocalResult(stderr=f"未知命令「{name}」，用 :help 查看全部命令",
                           exit_code=EXIT_USAGE)
    try:
        if command.handler is None:
            return _run_tool(command, arg.strip())
        return command.handler(LocalContext(cwd=cwd, runner=runner), arg.strip())
    except Exception as exc:
        logger.exception("[TerminalCommand] %s 执行异常", name)
        return LocalResult(stderr=f"命令执行失败：{exc}", exit_code=EXIT_TOOL)


def _run_tool(command: LocalCommand, arg: str) -> LocalResult:
    """工具命令：参数翻译在工具里，调用统一走工具执行器。"""
    try:
        method, args = command.resolve(arg)
    except ValueError as exc:
        return usage(command.name, str(exc))
    return _call_tool(f"{command.tool}__{method}", args)


def _call_tool(full_name: str, args: dict) -> LocalResult:
    """经通用执行器调用 tool__method，把返回的 headline / body 折成正文。"""
    if not method_exists(full_name):
        return LocalResult(
            stderr=f"{full_name} 不可用：在设置里启用对应工具后重试",
            exit_code=EXIT_TOOL,
        )
    result = ToolExecutor().execute_one(ToolCall(name=full_name, args=args))
    if not result.ok:
        return LocalResult(stderr=result.error_message or result.error_code,
                           exit_code=EXIT_TOOL)
    output = result.output if isinstance(result.output, dict) else {}
    headline = str(output.get("headline") or "").strip()
    body = str(output.get("body") or "").strip()
    if not headline and not body:
        return LocalResult("（没有输出）")
    return LocalResult("\n".join(part for part in (headline, body) if part))
