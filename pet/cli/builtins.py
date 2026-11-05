"""终端内建命令：帮助、清屏、策略快照、判定试算与状态。"""

from __future__ import annotations

import json

from pet.config import config
from pet.terminal import core, policy
from pet.tools.context import TOOL_CTX

from .format import key_values, note, sections
from .table import usage
from .types import EXIT_TOOL, EXIT_USAGE, LocalContext, LocalResult

# 策略拒绝原因码的解释：回答「这条命令为什么没过」，与工具失败文案不共用
_REASON_TEXT = {
    "terminal_disabled": "档位为 deny，模型命令一律拒绝",
    "bad_command": "程序在本机找不到",
    "untrusted_program": "程序文件在可写目录内，按不可信处理",
    "cwd_denied": "工作目录在沙箱根之外",
}

_DECISION_TEXT = {"allow": "直接执行", "ask": "弹卡片询问", "deny": "拒绝执行"}
_STATE_TEXT = {"idle": "空闲", "autonomous": "自主活动", "interacting": "交互中"}
_DOCK_TEXT = {"left": "左侧", "right": "右侧"}

_CHECK_NOTE = "只描述模型发起的调用，你在终端里自己输入的命令不受策略约束"
_POLICY_NOTE = ("auto 档下，解释器与命中危险类别的命令都转确认；"
                "「沙箱」只约束初始工作目录，不是系统级隔离，子进程仍拥有当前用户权限")


def _duration(seconds: int) -> str:
    """秒数转时长短写：12s、3m 20s、1h 05m。"""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


def _score(value) -> str:
    """百分比数值统一保留一位小数。"""
    return f"{float(value):.1f} / 100"


def check(ctx: LocalContext, arg: str) -> LocalResult:
    """按模型身份试算一条命令的策略判定。"""
    command = (arg or "").strip()
    if not command:
        return usage("check")
    parsed, err = core.parse_command(command, "")
    if err:
        return LocalResult(stderr=f"无法解析：{command}", exit_code=EXIT_USAGE)
    verdict = policy.check("model", parsed.argv, parsed.builtin, command, ctx.cwd)
    decision = verdict.decision.value
    rows = [
        ("判定", f"{_DECISION_TEXT.get(decision, decision)}（{decision}）"),
        ("档位", config.TERMINAL_MODE),
    ]
    if verdict.reason:
        rows.append(("原因", _REASON_TEXT.get(verdict.reason, verdict.reason)))
    if verdict.matched:
        rows.append(("类别", "、".join(verdict.matched)))
    if verdict.resolved:
        rows.append(("程序", verdict.resolved))
    if verdict.untrusted:
        rows.append(("可信", "否（程序位于可写目录）"))
    lines = key_values(rows)
    lines.extend(["", note(_CHECK_NOTE)])
    return LocalResult("\n".join(lines))


def status(ctx: LocalContext, arg: str) -> LocalResult:
    """恋恋的状态、生理、心理与记忆。"""
    flag = (arg or "").strip()
    if flag and flag != "--json":
        return usage("status", f"未知参数「{flag}」")
    snapshot = TOOL_CTX.pet_status()
    if not snapshot:
        return LocalResult(stderr="恋恋状态不可用：应用还没完成初始化",
                           exit_code=EXIT_TOOL)
    if flag == "--json":
        return LocalResult(json.dumps(snapshot, ensure_ascii=False, indent=2))
    vitals = snapshot["vitals"]
    mood = snapshot["mood"]
    state = str(snapshot["state"])
    lines = sections([
        ("恋恋", [
            ("状态", f"{_STATE_TEXT.get(state, state)}（{state}）"),
            ("已持续", _duration(int(snapshot["state_seconds"]))),
            ("动作", str(snapshot["action"] or "-")),
            ("贴边", _DOCK_TEXT.get(str(snapshot["dock"]), "未贴边")),
        ]),
        ("生理", [
            ("饱食度", _score(vitals["satiety"])),
            ("精力", _score(vitals["energy"])),
            ("饥饿", "是" if vitals["hungry"] else "否"),
            ("疲倦", "是" if vitals["tired"] else "否"),
        ]),
        ("心理", [
            ("好感度", _score(mood["affection"])),
            ("愉悦度", _score(mood["joy"])),
            ("理智", _score(mood["sanity"])),
        ]),
        ("记忆", [("长期记忆", f"{int(snapshot['memories'])} 条")]),
    ])
    return LocalResult("\n".join(lines))


def policy_snapshot(ctx: LocalContext, arg: str) -> LocalResult:
    """免确认清单、解释器名单与危险类别。"""
    snapshot = ctx.runner.snapshot()
    allowed = snapshot.get("allowed") or []
    lines = key_values([
        ("档位", str(snapshot.get("mode") or "")),
        ("沙箱根", str(snapshot.get("sandbox_root") or "")),
        ("免确认", f"{len(allowed)} 条"),
        ("解释器", "、".join(snapshot.get("interpreters") or [])),
        ("危险类别", "、".join(snapshot.get("deny_categories") or [])),
        ("shell 模式", "开" if snapshot.get("shell_mode") else "关"),
    ])
    lines.extend(["", "免确认清单"])
    if allowed:
        lines.extend(f"  {entry.get('command', '')}"
                     f"{'' if entry.get('installed') else '（未安装）'}"
                     for entry in allowed)
    else:
        lines.append("  （空，每条命令都需要确认）")
    lines.extend(["", note(_POLICY_NOTE)])
    return LocalResult("\n".join(lines))


def quit_app(ctx: LocalContext, arg: str) -> LocalResult:
    """退出应用。"""
    if (arg or "").strip():
        return usage("quit", f"未知参数「{arg.strip()}」")
    if ctx.control is None or not ctx.control.quit():
        return LocalResult(stderr="无法退出：终端没拿到应用控制",
                           exit_code=EXIT_TOOL)
    return LocalResult("正在退出…")


def restart_app(ctx: LocalContext, arg: str) -> LocalResult:
    """重启应用。"""
    if (arg or "").strip():
        return usage("restart", f"未知参数「{arg.strip()}」")
    if ctx.control is None or not ctx.control.restart():
        return LocalResult(stderr="无法重启：终端没拿到应用控制",
                           exit_code=EXIT_TOOL)
    return LocalResult("正在重启…")


def clear(ctx: LocalContext, arg: str) -> LocalResult:
    """清屏。"""
    return LocalResult(clear=True)

