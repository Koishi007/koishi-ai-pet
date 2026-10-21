"""事件记忆汇总：整点把同主题的 event 记忆合并成一条。"""

import logging
import re
from datetime import datetime

from openai import BadRequestError, OpenAI

from pet.brain.llm_client import active_llm_profile, resolve_llm_profile
from pet.brain.prompts import MEMORY_MERGE_PROMPT
from pet.config import config

logger = logging.getLogger(__name__)

# 与 prompts.MEMORY_MERGE_PROMPT 里的字数约定保持一致
_MAX_SUMMARY_CHARS = 400
# 思考与正文共用：思考约占 1~4k token，正文约 0.5~1.5k
_MAX_TOKENS = 8192
_TEMPERATURE = 0.2

_GROUP_RE = re.compile(r"^Group\s*[:：]\s*(.+)$")


def _resolve_connection() -> tuple[str, str, str] | None:
    """合并请求的 (key, url, model)：三项齐全用独立配置，否则沿用当前启用方案。"""
    url = (config.MEMORY_MERGE_URL or "").strip()
    key = (config.MEMORY_MERGE_KEY or "").strip()
    model = (config.MEMORY_MERGE_MODEL or "").strip()
    if url and key and model:
        return key, url, model
    key, url, model = resolve_llm_profile(active_llm_profile())
    if not (url and key and model):
        return None
    return key, url, model


def _format_records(rows: list[dict]) -> str:
    """候选记录渲染成输入协议：#id MM-DD HH:MM 内容；跨年时补年份。"""
    current_year = datetime.now().year
    lines = []
    for r in rows:
        created = r.get("created_at") or ""
        if len(created) >= 16:
            stamp = created[5:16].replace("T", " ")
            if created[:4].isdigit() and int(created[:4]) != current_year:
                stamp = created[:16].replace("T", " ")
        else:
            stamp = created
        lines.append(f"#{r['id']} {stamp} {r['content']}")
    return "\n".join(lines)


def _call_llm(messages: list[dict]) -> str:
    """调用合并模型，返回原始文本输出；思考模式开启，不支持时降级重试一次。"""
    conn = _resolve_connection()
    if conn is None:
        raise RuntimeError("未配置可用的模型连接")
    key, url, model = conn
    client = OpenAI(base_url=url, api_key=key, timeout=config.LLM_TIMEOUT, max_retries=0)
    kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": _MAX_TOKENS,
        "temperature": _TEMPERATURE,
        "extra_body": {"thinking": {"type": "enabled"}},
    }
    try:
        resp = client.chat.completions.create(**kwargs)
    except BadRequestError:
        logger.info("[MemoryMerge] 模型不支持 thinking 参数，去掉后重试")
        kwargs.pop("extra_body")
        resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def parse_groups(text: str, valid_ids: set[int]) -> list[tuple[list[int], str]]:
    """解析 Group 行，返回 [(ids, summary)]。

    只接受输入集合内的 id；同一 id 只归首组；去重后不足两条的组忽略。
    """
    groups: list[tuple[list[int], str]] = []
    used: set[int] = set()
    for raw in text.splitlines():
        match = _GROUP_RE.match(raw.strip())
        if not match:
            continue
        body = match.group(1)
        if "|" in body:
            ids_part, summary = body.split("|", 1)
        elif "｜" in body:
            ids_part, summary = body.split("｜", 1)
        else:
            continue
        ids: list[int] = []
        seen = set(used)
        for token in re.split(r"[,\s，]+", ids_part.strip()):
            token = token.strip().lstrip("#＃")
            if not token.isdigit():
                continue
            mid = int(token)
            if mid in valid_ids and mid not in seen:
                ids.append(mid)
                seen.add(mid)
        summary = summary.strip()[:_MAX_SUMMARY_CHARS]
        # 无效组不占用 id，后面的组仍可拿到这些 id
        if len(ids) < 2 or not summary:
            continue
        used.update(ids)
        groups.append((ids, summary))
    return groups


def run_merge(memory_store) -> int:
    """执行一次汇总，返回应用的组数；无候选或输出无可应用组时返回 0，请求失败时抛出。"""
    rows = memory_store.list_event_candidates()
    if len(rows) < 2:
        logger.info(f"[MemoryMerge] event 记忆不足两条，跳过（当前 {len(rows)} 条）")
        return 0
    messages = [
        {"role": "system", "content": MEMORY_MERGE_PROMPT},
        {"role": "user", "content": _format_records(rows)},
    ]
    try:
        text = _call_llm(messages)
    except Exception as e:
        logger.warning(f"[MemoryMerge] 请求失败: {e}")
        raise
    groups = parse_groups(text, {r["id"] for r in rows})
    if not groups:
        logger.info("[MemoryMerge] 输出无可应用的组")
        return 0
    applied = memory_store.apply_event_merges(groups, rows)
    logger.info(f"[MemoryMerge] 完成：应用 {applied} 组，候选 {len(rows)} 条")
    return applied
