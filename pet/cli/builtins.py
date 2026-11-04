"""终端内建命令：只处理终端自身的能力，不碰工具。"""

from __future__ import annotations

from pet.config import config
from pet.terminal import core, policy

from .types import EXIT_USAGE, LocalContext, LocalResult

# 策略拒绝原因码的解释：回答「这条命令为什么没过」，与工具失败文案不共用
_REASON_TEXT = {
    "terminal_disabled": "档位为 deny，模型命令一律拒绝",
    "bad_command": "程序在本机找不到",
    "untrusted_program": "程序文件在可写目录内，按不可信处理",
    "cwd_denied": "工作目录在沙箱根之外",
}

_DECISION_TEXT = {"allow": "直接执行", "ask": "弹卡片询问", "deny": "拒绝执行"}


def check(ctx: LocalContext, arg: str) -> LocalResult:
    """按模型身份试算一条命令的策略判定。"""
    command = (arg or "").strip()
    if not command:
        # 用法文本由 catalog 依命令表拼，导入期拉进来会与 catalog 成环
        from .catalog import usage

        return usage("check")
    parsed, err = core.parse_command(command, "")
    if err:
        return LocalResult(stderr=f"无法解析：{command}", exit_code=EXIT_USAGE)
    verdict = policy.check("model", parsed.argv, parsed.builtin, command, ctx.cwd)
    lines = [
        f"判定：{_DECISION_TEXT.get(verdict.decision, verdict.decision)}"
        f"（{verdict.decision}）",
        f"档位：{config.TERMINAL_MODE}",
    ]
    if verdict.reason:
        lines.append("原因：" + _REASON_TEXT.get(verdict.reason, verdict.reason))
    if verdict.matched:
        lines.append("命中危险类别：" + "、".join(verdict.matched))
    if verdict.resolved:
        lines.append(f"程序：{verdict.resolved}")
    if verdict.untrusted:
        lines.append("程序落在可写目录，视为不可信")
    lines.append("判定只描述模型调用，终端里你自己输入的命令不受策略约束")
    return LocalResult("\n".join(lines))


def status(ctx: LocalContext, arg: str) -> LocalResult:
    """档位、平台、沙箱根与免确认条数。"""
    snapshot = ctx.runner.list()
    lines = [
        f"档位：{snapshot.get('mode', '')}",
        f"平台：{snapshot.get('platform', '')}",
        f"沙箱根：{snapshot.get('sandbox_root', '')}",
        f"当前目录：{ctx.cwd}",
        f"免确认命令：{len(snapshot.get('allowed') or [])} 条",
        "整条命令交给 shell：" + ("开" if snapshot.get("shell_mode") else "关"),
    ]
    return LocalResult("\n".join(lines))


def policy_snapshot(ctx: LocalContext, arg: str) -> LocalResult:
    """免确认清单、解释器名单与危险类别。"""
    snapshot = ctx.runner.list()
    lines = [str(snapshot.get("headline") or "")]
    allowed = snapshot.get("allowed") or []
    if not allowed:
        lines.append("  清单为空，每条命令都需要确认")
    for entry in allowed:
        mark = "" if entry.get("installed") else "（未安装）"
        lines.append(f"  {entry.get('command', '')}{mark}")
    lines.append("解释器一律询问：" + "、".join(snapshot.get("interpreters") or []))
    lines.append("危险类别：" + "、".join(snapshot.get("deny_categories") or []))
    return LocalResult("\n".join(lines))


def clear(ctx: LocalContext, arg: str) -> LocalResult:
    """清屏。"""
    return LocalResult(clear=True)

