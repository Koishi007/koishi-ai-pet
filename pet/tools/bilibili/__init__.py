"""bili 工具 — 刷 B 站：搜索、读字幕、点赞投币评论、给主人发私信。

写操作受 config.json 的四个开关与每日额度约束，默认全部关闭。
"""

from __future__ import annotations

import functools
import logging

from pet.tools.bilibili import duty, schedule
from pet.tools.bilibili.storage import BiliStorage
from pet.tools.context import TOOL_CTX
from pet.tools.output import ToolOutputDefinition

logger = logging.getLogger(__name__)

TOOL_NAME = "bilibili"
TOOL_DESCRIPTION = "B 站：按关键词、BV号刷视频、读字幕，点赞、投币、评论、私信"
TOOL_GROUP = "info"

BRIEF_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "maxLength": 120, "x-role": "headline",
                     "description": "操作结果的一句话"},
        "data": {"type": "object", "x-role": "data", "description": "计数与开关"},
    },
}

BROWSE_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "maxLength": 120, "x-role": "headline",
                     "description": "关键词与刷到的部数"},
        "body": {"type": "string", "x-role": "body",
                 "description": "每部：标题、网址、字幕全文"},
        "data": {"type": "object", "x-role": "data", "description": "略过与统计"},
    },
}

_storage: BiliStorage | None = None

# 转成规范值时保留为 data 的字段：ok/summary/videos 与图片魔法键另有归属
_SKIP_KEYS = frozenset({"ok", "summary", "videos", "detail", "reason", "reason_code",
                        "__image__", "__image_mime__", "__context__"})


def _normalized(handler):
    """把 duty 的 ok / reason / summary 返回映射成输出契约。"""
    @functools.wraps(handler)
    def _wrapper(**kwargs):
        result = handler(**kwargs) or {}
        if result.get("ok"):
            out = {"headline": result.get("summary") or "操作完成"}
            videos = result.get("videos") or []
            if videos:
                out["body"] = "\n\n".join(
                    f"《{v.get('title', '')}》{v.get('url', '')}\n{v.get('subtitle', '')}"
                    for v in videos)
            rest = {k: v for k, v in result.items() if k not in _SKIP_KEYS}
            if rest:
                out["data"] = rest
            return out
        return {"error": result.get("summary") or "操作失败",
                "code": result.get("reason") or "bili_failed"}
    return _wrapper


def _show_panel():
    """右键菜单「设置」回调；面板依赖 Qt，延迟导入。"""
    from pet.tools.bilibili.panel import show_panel

    show_panel()


@_normalized
def _browse(keyword: str = "", **_):
    if _storage is None:
        return {"ok": False, "reason": "not_ready", "summary": "B 站工具未初始化"}
    return duty.run_round(_storage, keyword=(keyword or "").strip())


@_normalized
def _status(**_):
    if _storage is None:
        return {"ok": False, "reason": "not_ready", "summary": "B 站工具未初始化"}
    return duty.status(_storage)


@_normalized
def _like(bvid: str, **_):
    return duty.write_action(_storage, "like", bvid=bvid)


@_normalized
def _coin(bvid: str, **_):
    return duty.write_action(_storage, "coin", bvid=bvid)


@_normalized
def _comment(bvid: str, text: str, **_):
    return duty.write_action(_storage, "comment", bvid=bvid, text=text)


@_normalized
def _dm(text: str, **_):
    return duty.write_action(_storage, "dm", text=text)


@_normalized
def _verify(limit: int = 10, **_):
    return duty.verify(_storage, limit=limit)


def register(registry):
    global _storage
    try:
        _storage = BiliStorage()
    except Exception as exc:
        logger.error(f"[bili] 初始化失败：{exc}")
        return

    registry.register(TOOL_NAME, TOOL_DESCRIPTION, group=TOOL_GROUP, side_effect=True)

    registry.add_method(
        TOOL_NAME, "browse",
        "刷一轮 B 站：按关键词搜最近几天的视频，随机取几部连同字幕交给你。"
        "没有字幕的视频照常返回，靠标题、简介与封面判断。"
        "只读，不自动点赞、投币或评论；要不要互动由随后调用 like / coin / comment 决定。"
        "传关键词前可先用 status 查看关注列表。",
        handler=_browse,
        args={
            "keyword": {"type": "str", "required": False, "default": "",
                        "desc": "一个或多个关键词，逗号分隔，最多 3 个；留空时从关注列表随机选一个"},
        },
        timeout=300.0,
        output=ToolOutputDefinition(schema=BROWSE_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "status",
        "查看 B 站账号状态：昵称、UID、等级、硬币、关注关键词，以及今日各项操作次数与开关。",
        handler=_status,
        timeout=20.0,
        output=ToolOutputDefinition(schema=BRIEF_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "like",
        "给最近一轮刷到的某个视频点赞。目标必须在最近一轮的候选里。",
        handler=_like,
        args={"bvid": {"type": "str", "required": True, "desc": "视频 BV 号"}},
        timeout=30.0,
        output=ToolOutputDefinition(schema=BRIEF_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "coin",
        "给最近一轮刷到的某个视频投一枚硬币。受每日额度与硬币余额下限约束。",
        handler=_coin,
        args={"bvid": {"type": "str", "required": True, "desc": "视频 BV 号"}},
        timeout=30.0,
        output=ToolOutputDefinition(schema=BRIEF_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "comment",
        "在最近一轮刷到的某个视频下留一条评论。内容会经过长度、链接、隐私与重复检查，"
        "被拦下时不发送。",
        handler=_comment,
        args={
            "bvid": {"type": "str", "required": True, "desc": "视频 BV 号"},
            "text": {"type": "str", "required": True, "desc": "评论正文"},
        },
        timeout=30.0,
        output=ToolOutputDefinition(schema=BRIEF_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "dm",
        "给主人在 B 站发一条私信。收件人取配置里的 dm_uid，未配置时不发送。",
        handler=_dm,
        args={"text": {"type": "str", "required": True, "desc": "私信正文"}},
        timeout=30.0,
        output=ToolOutputDefinition(schema=BRIEF_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "verify",
        "复核待确认的评论是否已经公开可见。未核到的保持待确认，不会重发。",
        handler=_verify,
        args={"limit": {"type": "int", "required": False, "default": 10,
                        "desc": "最多检查几条"}},
        timeout=60.0,
        output=ToolOutputDefinition(schema=BRIEF_OUTPUT),
    )

    registry.add_menu_action(TOOL_NAME, "设置", _show_panel)
    TOOL_CTX.on_bind(schedule.start)
    logger.info("[bili] tool registered")
