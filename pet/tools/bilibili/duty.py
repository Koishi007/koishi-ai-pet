"""一轮刷 B 站：搜索、判重、读字幕，随机取几条返回给模型。

写操作先占台账再发请求，超时后重复调用按有效记录数与 limit 拦住。
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from pet.tools.bilibili.core import (
    BiliClient,
    BiliError,
    Credential,
    credential_path,
    is_bvid,
    within_days,
)
from pet.tools.bilibili.filters import check_text, describe
from pet.tools.bilibili.storage import BiliStorage
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

_TOOL_DIR = Path(__file__).parent
_CONFIG_FILE = _TOOL_DIR / "config.json"

# 采集预算：留时间给工具返回与主循环
_COLLECT_BUDGET_S = 90.0
# 单页候选不够时继续翻页的上限
_MAX_SEARCH_PAGES = 3
# 一轮最多搜几个关键词
_MAX_SEARCH_KEYWORDS = 3
# 同一视频最多留几条有效评论
_COMMENT_PER_TARGET = 3
# 评论列表刷新有延迟，发出后隔这段时间再核可见性
_CONFIRM_DELAY_S = 60.0
# 两次评论之间的最短间隔：评论是唯一会被风控的写操作
_COMMENT_COOLDOWN_S = 120

# 本工具的错误码
_ERROR_HINTS = {
    "disabled": "这个功能在设置里关着，本轮不要再调用，如实告诉用户即可。",
    "not_ready": "工具还没启动完成，这次先不要调用。",
    "not_logged_in": "登录失效或未登录，让用户在账号面板重新扫码后再试。",
    "dm_uid_missing": "让用户在面板填好私信收件人 UID 后再试。",
    "target_unknown": "若已有具体 BV，用 browse 的 bvid 参数（不要附带关键词）拿候选后再操作；否则先刷一轮。",
    "quota": "今天的额度用完了，今天不要再调用这个操作。",
    "duplicate": "这个目标已有记录，不要重复调用。",
    "no_receipt": "回执缺失，可能已经发出去了，不要再调用。",
    "result_unknown": "请求超时，可能已经发出去了，不要再调用。",
    "insufficient_coins": "硬币不够，不要再投币。",
    "filtered": "内容被过滤，调整措辞后再试。",
    "invalid_keyword": "换一个关键词或 BV 号。",
    "invalid_bvid": "检查 BV 号格式（BV + 10 位字母数字），或改用 keyword 搜索。",
    "request_failed": "接口请求失败，稍后再试。",
    "mobile_unbound": "账号未绑定手机，评论会仅自己可见；先在 B 站 App 完成手机绑定。",
    "cooldown": "两条评论间隔不足，等冷却结束再试，不要连续重试。",
}

_DEFAULTS: dict = {
    "keywords": ["东方Project", "古明地觉", "古明地恋", "猫", "狗", "动物"],
    "per_run": 2,
    "scan_days": 30,
    "order": "totalrank",
    "subtitle_chars": 2500,
    "min_interval_s": 3.0,
    "timeout_s": 15.0,
    "allow_browse": True,
    "allow_like": False,
    "allow_coin": False,
    "allow_comment": False,
    "allow_dm": False,
    "coin_per_day": 2,
    "coin_floor": 1,
    "comment_per_day": 7,
    "comment_max_chars": 100,
    "comment_blocklist": [],
    "dm_uid": 0,
    "dm_max_chars": 400,
    "schedule_enabled": False,
    "schedule_interval_minutes": 180,
    "schedule_active_hours": "09:00-23:00",
    "schedule_backoff_base_minutes": 5,
    "schedule_pause_hours": "",
    "schedule_pause_weekdays_only": True,
    "mention_enabled": False,
    "mention_per_day": 3,
}

# 定时任务间隔的上下限（分钟）
_INTERVAL_MIN = 10
_INTERVAL_MAX = 1440

def _coerce(value, default):
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, list):
        return [str(v) for v in value] if isinstance(value, list) else default
    if isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        return type(default)(value)
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    return value


def _read_raw() -> dict:
    if not _CONFIG_FILE.is_file():
        return {}
    try:
        loaded = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(f"[bili] config.json 读不出来，改用默认值：{exc}")
        return {}
    return loaded if isinstance(loaded, dict) else {}


def load_config() -> dict:
    raw = _read_raw()
    cfg = {key: _coerce(raw.get(key), default) for key, default in _DEFAULTS.items()}
    cfg["keywords"] = normalize_keywords(raw.get("keywords")) or list(_DEFAULTS["keywords"])
    return cfg


def update_config(patch: dict) -> dict:
    """把指定开关写回 config.json，其余字段以当前配置为基准补齐。"""
    cfg = load_config()
    cfg.update({k: _coerce(v, _DEFAULTS[k]) for k, v in patch.items() if k in _DEFAULTS})
    _CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8")
    return cfg


def normalize_keywords(values: object) -> list[str]:
    """清洗关键词列表：去不可打印字符、去空、去重，单条上限 50 字，最多 20 个。

    BV 号固定 12 位，与普通关键词走同一套清洗，不做额外限制。
    """
    if not isinstance(values, list):
        return []
    words: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            continue
        word = "".join(ch for ch in raw if ch.isprintable()).strip()
        if not word or len(word) > 50 or word in words:
            continue
        words.append(word)
        if len(words) >= 20:
            break
    return words


def parse_keywords(text: str) -> list[str]:
    """按中英文逗号或换行切分，交给 normalize_keywords 清洗。"""
    return normalize_keywords((text or "").replace("，", ",").replace("\n", ",").split(","))


def set_keywords(text: str) -> dict:
    """保存关键词；空输入回退到默认列表，避免下一轮无词可搜。"""
    words = parse_keywords(text)
    return update_config({"keywords": words or list(_DEFAULTS["keywords"])})


def set_dm_uid(text: str) -> dict:
    """保存私信收件人；空输入表示不发私信，非数字或越界抛 ValueError。"""
    raw = (text or "").strip()
    if not raw:
        return update_config({"dm_uid": 0})
    if not raw.isdigit():
        raise ValueError("只能是数字")
    uid = int(raw)
    if uid <= 0 or uid > 9_999_999_999:
        raise ValueError("超出 UID 范围")
    return update_config({"dm_uid": uid})


def set_interval_minutes(text: str) -> dict:
    """保存定时任务间隔，非法值抛 ValueError。"""
    raw = (text or "").strip()
    if not raw:
        return update_config(
            {"schedule_interval_minutes": _DEFAULTS["schedule_interval_minutes"]})
    if not raw.isdigit():
        raise ValueError("只能填数字")
    minutes = int(raw)
    if not _INTERVAL_MIN <= minutes <= _INTERVAL_MAX:
        raise ValueError(f"超出 {_INTERVAL_MIN}-{_INTERVAL_MAX} 分钟")
    return update_config({"schedule_interval_minutes": minutes})


def load_credential() -> Credential | None:
    return Credential.from_file(credential_path())


def split_keywords(keywords: list[str]) -> tuple[list[str], list[str]]:
    """返回 (BV 号, 普通关键词)。BV 号直接读取视频，不走搜索。"""
    bv = [k for k in keywords if is_bvid(k)]
    text = [k for k in keywords if not is_bvid(k)]
    return bv, text


def _add_candidate(client: BiliClient, candidates: list[dict], skipped: dict,
                   bvid: str, has_cover: bool, keyword: str = "") -> None:
    """取单条视频的字幕并加入候选；失败只记统计。"""
    try:
        info = client.view(bvid)
        subtitle = client.subtitle(bvid, info["cid"])
    except BiliError as exc:
        logger.info(f"[bilibili] {bvid} 读取失败：{exc}")
        skipped["failed"] += 1
        return
    text = (subtitle or {}).get("text") or ""
    if not text:
        skipped["no_subtitle"] += 1
    candidates.append({
        "bvid": bvid,
        "aid": info["aid"],
        "title": info["title"],
        "up": info["up"],
        "duration": info["duration"],
        "play": info["play"],
        "desc": info["desc"],
        "pubdate": info["pubdate"],
        "pic": info["pic"],
        "keyword": keyword,
        "cover": client.fetch_cover(info["pic"]) if has_cover else "",
        "subtitle": text,
    })
    logger.info(f"[bilibili] 已取 {len(candidates)} 条候选")


def _search_keyword(client: BiliClient, cfg: dict, storage: BiliStorage,
                    keyword: str, order: str, pages: int,
                    needed: int) -> tuple[list[str], int]:
    """搜一个关键词，返回 (bvid 顺序, 其中已看过的数量)。

    未看过的排在前面；已看过的不丢弃，作为候选不足时的补位。
    """
    videos: dict[str, dict] = {}
    for page in range(1, pages + 1):
        items = client.search(keyword, order=order, page=page)
        if not items:
            break
        for item in items:
            videos.setdefault(item["bvid"], item)
        if len(storage.unseen([v["bvid"] for v in videos.values()])) >= needed:
            break

    now = time.time()
    fresh = [v for v in videos.values()
             if within_days(int(v.get("pubdate") or 0), int(cfg["scan_days"]), now)]
    if order == "pubdate":
        fresh.sort(key=lambda v: -int(v.get("pubdate") or 0))
    unseen = storage.unseen([v["bvid"] for v in fresh])
    known = set(unseen)
    reused = [v["bvid"] for v in fresh if v["bvid"] not in known]
    return unseen + reused, len(reused)


def _collect(client: BiliClient, cfg: dict, storage: BiliStorage,
             keywords: list[str]) -> tuple[list[dict], dict, int]:
    """返回 (有字幕的候选, 跳过统计, 请求数)。候选不足时换下一个关键词。"""
    order = str(cfg["order"])
    if order not in ("pubdate", "totalrank", "click"):
        order = "totalrank"
    bv_targets, search_keywords = split_keywords(keywords)
    if bv_targets:
        # 指定了 BV 就只取这些视频，不再搜索关键词
        search_keywords = []
    if not search_keywords and not bv_targets:
        pool = list(cfg["keywords"])
        random.shuffle(pool)
        search_keywords = pool[:_MAX_SEARCH_KEYWORDS]

    # 多关键词时每个只搜一页，靠词的数量扩大候选，不叠加翻页
    pages = _MAX_SEARCH_PAGES if len(search_keywords) == 1 else 1
    # 集齐本轮要返回的条数就停，剩下候选不再前台等待
    need = max(1, int(cfg["per_run"]))
    if bv_targets:
        need = max(need, len(bv_targets))
    skipped = {"seen": 0, "no_subtitle": 0, "failed": 0, "over_limit": 0}
    errors: list[str] = []
    tried: list[str] = []
    candidates: list[dict] = []
    deadline = time.time() + _COLLECT_BUDGET_S
    has_cover = TOOL_CTX.has_vision()

    def reached() -> bool:
        return len(candidates) >= need or time.time() >= deadline

    for bvid in bv_targets:
        if reached():
            break
        _add_candidate(client, candidates, skipped, bvid, has_cover)

    for kw in search_keywords:
        if reached():
            break
        tried.append(kw)
        try:
            listed, seen = _search_keyword(client, cfg, storage, kw, order, pages, need)
        except BiliError as exc:
            errors.append(f"{kw}: {exc}")
            if len(errors) >= 3:
                break
            continue
        skipped["seen"] += seen
        for bvid in listed:
            if reached():
                skipped["over_limit"] += 1
                break
            _add_candidate(client, candidates, skipped, bvid, has_cover, kw)

    if time.time() >= deadline:
        logger.info(f"[bilibili] 采集超 {_COLLECT_BUDGET_S:.0f} 秒，截断候选")
    return candidates, {"skipped": skipped, "errors": errors, "keywords": tried}, client.requests


def _reply_hidden(reply: dict) -> bool:
    """评论是否被隐藏：invisible 字段或阿瓦隆状态 17。"""
    return bool(reply.get("invisible")) or int(reply.get("state") or 0) == 17


def _reply_visibility(reply: dict) -> str:
    """可见性：hidden 为平台隐藏；只有 state 明确为 0 才是 visible，其余按未确认处理。"""
    if _reply_hidden(reply):
        return "hidden"
    return "visible" if reply.get("state") == 0 else "done"


def _match_reply(replies: list[dict], uid: int, rpid: int,
                 content: str = "") -> dict | None:
    """找本人的那条评论：有 rpid 只按 rpid 匹配，无 rpid 时退回正文前 20 字。"""
    def mine(reply: dict) -> bool:
        return int((reply.get("member") or {}).get("mid") or 0) == uid

    if rpid:
        return next((r for r in replies if int(r.get("rpid") or 0) == rpid and mine(r)), None)
    head = (content or "")[:20]
    if not head:
        return None
    return next((r for r in replies if mine(r)
                 and head in str((r.get("content") or {}).get("message") or "")), None)


def _confirm_comment(client: BiliClient, uid: int, aid: int, root: int,
                     rpid: int, content: str, bvid: str = "") -> str:
    """按公开视角判可见性：匿名列表里看得到才算公开。"""
    try:
        public = (client.comment_replies(aid, root, bvid=bvid, anonymous=True) if root
                  else client.my_replies(aid, bvid=bvid, anonymous=True))
    except BiliError as exc:
        logger.info(f"[bilibili] 匿名复核失败：{exc}")
        public = None
    if public is not None:
        hit = _match_reply(public, uid, rpid, content)
        if hit is not None:
            return _reply_visibility(hit)
    own = (client.comment_replies(aid, root, bvid=bvid) if root
           else client.my_replies(aid, bvid=bvid))
    mine = _match_reply(own, uid, rpid, content)
    if mine is not None and public is not None:
        return "hidden"
    return "done"


def _apply_confirm(storage: BiliStorage, action_id: int, root: int, rpid: int,
                   client: BiliClient, uid: int, aid: int, content: str,
                   bvid: str = ""):
    """核验单条评论并写回台账；查不到时保留原状态。"""
    state = _confirm_comment(client, uid, aid, root, rpid, content, bvid)
    if state != "done":
        storage.finish_action(action_id, state, remote_id=str(rpid), root=root)


def _confirm_later(storage: BiliStorage, credential: Credential, cfg: dict,
                   action_id: int, bvid: str, aid: int, root: int,
                   rpid: int, content: str):
    """评论发出后延时复核；进程提前退出时停在 done，由后续复核兜底。"""

    def run():
        try:
            with _open(cfg, credential) as client:
                _apply_confirm(storage, action_id, root, rpid, client,
                               int(credential.dedeuserid), aid, content, bvid)
        except Exception as exc:
            logger.info(f"[bilibili] 延时复核 {bvid} 失败：{exc}")

    timer = threading.Timer(_CONFIRM_DELAY_S, run)
    timer.daemon = True
    timer.start()


def _guarded_write(storage: BiliStorage, uid: int, kind: str, target: str,
                   content: str, send, limit: int = 1, root: int = 0,
                   defer=None) -> dict:
    """占台账后发送；defer 在台账记好后拿到 (action_id, 远端 id) 安排复核。"""
    action_id = storage.claim_action(uid, kind, target, content, limit=limit)
    if action_id is None:
        return {"kind": kind, "target": target, "ok": False,
                "reason": "duplicate", "detail": "该目标的有效记录已达上限"}
    try:
        remote = send()
    except BiliError as exc:
        # 回执缺失与请求超时都不知到底发没发出去，标记后不再重试
        if exc.kind == "receipt":
            status, reason = "unknown", "no_receipt"
        elif exc.kind == "timeout":
            status, reason = "unknown", "result_unknown"
        else:
            status, reason = "failed", "send_failed"
        storage.finish_action(action_id, status, reason=exc.kind)
        return {"kind": kind, "target": target, "ok": False,
                "reason": reason, "detail": str(exc)}
    storage.finish_action(action_id, "done", remote_id=str(remote or ""), root=root)
    if defer is not None:
        try:
            defer(action_id, int(remote or 0))
        except Exception as exc:
            # 评论已经发出，安排复核出错不能把这次写判为失败
            logger.info(f"[bilibili] 安排复核失败：{exc}")
    return {"kind": kind, "target": target, "ok": True}


def _post_comment(client: BiliClient, storage: BiliStorage, cfg: dict,
                  credential: Credential, uid: int, bvid: str, aid: int,
                  text: str) -> dict:
    """发评论；该视频有未回复的 @ 时挂在 @ 评论下。"""
    ok, code = check_text(text, max_chars=int(cfg["comment_max_chars"]),
                          blocklist=tuple(cfg["comment_blocklist"]),
                          recent=tuple(storage.recent_contents(uid, "comment")))
    if not ok:
        return {"kind": "comment", "target": bvid, "ok": False,
                "reason": "filtered", "reason_code": code, "detail": describe(code)}

    me = client.whoami()
    if not me.get("login"):
        return {"kind": "comment", "target": bvid, "ok": False,
                "reason": "not_logged_in", "detail": "B 站登录已失效"}
    if not me.get("mobile_verified"):
        return {"kind": "comment", "target": bvid, "ok": False,
                "reason": "mobile_unbound",
                "detail": "账号未绑定手机，评论会仅自己可见"}

    mention = storage.mention_for_bvid(bvid)
    root = int((mention or {}).get("rpid") or 0)
    body = text

    def defer(action_id: int, rpid: int):
        _confirm_later(storage, credential, cfg, action_id, bvid, aid, root,
                       rpid, body)

    if root:
        result = _guarded_write(storage, uid, "comment", bvid, body,
                                lambda: client.comment(aid, body, bvid=bvid, root=root),
                                limit=_COMMENT_PER_TARGET, root=root, defer=defer)
    else:
        result = _guarded_write(storage, uid, "comment", bvid, body,
                                lambda: client.comment(aid, body, bvid=bvid),
                                limit=_COMMENT_PER_TARGET, defer=defer)
    if result.get("ok") and root:
        storage.mark_mention_replied(mention["msg_id"])
    return result


def _parse_request(keyword: str) -> tuple[list[str], list[str]]:
    """拆分 BV 与关键词；关键词最多保留 _MAX_SEARCH_KEYWORDS 个。"""
    words = normalize_keywords(keyword.replace("，", ",").split(",")) if keyword else []
    bv_targets, text_words = split_keywords(words)
    return bv_targets, text_words[:_MAX_SEARCH_KEYWORDS]


def _pick_chosen(candidates: list[dict], bv_targets: list[str],
                 count: int) -> list[dict]:
    """BV 直通时按 BV 返回，随机抽样可能漏掉指定的目标。"""
    if bv_targets:
        wanted = set(bv_targets)
        return [c for c in candidates if c["bvid"] in wanted]
    return random.sample(candidates, count)


def run_round(storage: BiliStorage, keyword: str = "") -> dict:
    """跑一轮：搜索、读字幕，随机取几条连同字幕交给演绎模型。

    keyword 可以是指定关键词或 BV 号，不受配置里的关键词列表限制。
    """
    cfg = load_config()
    if not cfg["allow_browse"]:
        return {"error": "刷视频已在面板里关闭", "code": "disabled"}
    bv_targets, text_words = _parse_request(keyword)
    if keyword and not (bv_targets or text_words):
        return {"error": "关键词不合法：为空或超过 50 字", "code": "invalid_keyword"}
    words = bv_targets + text_words
    credential = load_credential()
    if credential is None:
        return {"error": "还没登录 B 站账号，右键菜单打开「设置」扫码",
                "code": "not_logged_in"}

    try:
        with BiliClient(credential, timeout=float(cfg["timeout_s"]),
                        interval=float(cfg["min_interval_s"])) as client:
            me = client.whoami()
            if not me.get("login"):
                return {"error": "B 站登录已失效，右键菜单打开「设置」重新扫码",
                        "code": "not_logged_in"}

            candidates, meta, requests = _collect(client, cfg, storage, words)
            skipped = meta["skipped"]
            if not candidates:
                return {
                    "headline": (f"这一轮没找到能看的：已看过 {skipped['seen']} 条，"
                                 f"无字幕 {skipped['no_subtitle']} 条，"
                                 f"读取失败 {skipped['failed']} 条"),
                    "data": {"skipped": skipped, "errors": meta["errors"],
                             "keyword": (meta["keywords"] or [""])[0],
                             "requests": requests},
                }

            count = max(1, min(int(cfg["per_run"]), len(candidates)))
            chosen = _pick_chosen(candidates, bv_targets, count)
            # 只标记实际返回的：没抽中的候选留到下一轮，同一关键词可以分多次取
            storage.mark_seen([
                {"bvid": c["bvid"], "title": c["title"], "up": c["up"],
                 "pubdate": c["pubdate"], "verdict": "picked", "note": ""}
                for c in chosen
            ])
            storage.save_round(int(me.get("uid") or 0), date.today().isoformat(),
                               len(chosen), requests)
            remember_targets(chosen)

            videos = [{
                "bvid": c["bvid"],
                "title": c["title"],
                "up": c["up"],
                "duration": c["duration"],
                "play": c["play"],
                "desc": c["desc"],
                "keyword": c.get("keyword", ""),
                "url": f"https://www.bilibili.com/video/{c['bvid']}",
                "subtitle": c["subtitle"],
            } for c in chosen]

            kw = "、".join(bv_targets or meta["keywords"])
            result = {
                "headline": (f"从「{kw}」刷到 {len(videos)} 部" if kw
                             else f"刷到 {len(videos)} 部"),
                "body": "\n\n".join(
                    f"《{v['title']}》{v['url']}\n{v['subtitle']}" for v in videos),
                "data": {
                    "keyword": kw, "skipped": skipped, "errors": meta["errors"],
                    "requests": requests,
                    "account": {"name": me.get("name", ""), "uid": me.get("uid", 0),
                                "level": me.get("level", 0),
                                "coins": me.get("coins", 0)},
                },
            }
            first_cover = next((c for c in chosen if c.get("cover")), None)
            if first_cover:
                result["images"] = [{"base64": first_cover["cover"], "mime": "image/jpeg",
                                     "caption": f"封面《{first_cover['title']}》"}]
            return result
    except BiliError as exc:
        logger.warning(f"[bili] 一轮失败：{exc}")
        return {"error": f"这一轮失败：{exc}", "code": "request_failed"}


_RECENT: dict[str, dict] = {}
_RECENT_AT: float = 0.0
_RECENT_TTL_S = 1800.0
_KIND_TEXT = {"like": "点赞", "coin": "投币", "comment": "评论", "dm": "私信"}


def remember_targets(candidates: list[dict]):
    global _RECENT, _RECENT_AT
    _RECENT = {c["bvid"]: c for c in candidates}
    _RECENT_AT = time.time()


def recent_target(bvid: str) -> dict | None:
    """只认最近一轮读过的视频，30 分钟后失效。"""
    if not _RECENT or time.time() - _RECENT_AT > _RECENT_TTL_S:
        return None
    return _RECENT.get(bvid)


@contextmanager
def _open(cfg: dict, credential: Credential):
    with BiliClient(credential, timeout=float(cfg["timeout_s"]),
                    interval=float(cfg["min_interval_s"])) as client:
        yield client


def _elapsed_seconds(since: str) -> float:
    """该时刻已经过去多少秒；格式不对按很久以前处理。"""
    try:
        return (datetime.now() - datetime.fromisoformat(since)).total_seconds()
    except ValueError:
        return float("inf")


def _comment_gate(storage: BiliStorage, uid: int) -> dict | None:
    """两条评论之间的最短间隔；点赞、投币、私信不受限。"""
    last = storage.last_comment_at(uid)
    if not last:
        return None
    remain = _COMMENT_COOLDOWN_S - _elapsed_seconds(last)
    if remain > 0:
        return {"error": f"评论间隔不足，{max(1, round(remain))} 秒后再试",
                "code": "cooldown"}
    return None


def with_hint(payload: dict) -> dict:
    """给错误返回附上本工具自己的下一步提示。"""
    hint = _ERROR_HINTS.get(str(payload.get("code") or ""))
    if payload.get("error") and hint:
        payload["hint"] = hint
    return payload


def _as_tool_result(result: dict) -> dict:
    """把一次写操作的内部结果映射成输出契约。"""
    if not result.get("ok"):
        return {"error": result.get("detail") or "操作失败",
                "code": result.get("reason") or "write_failed"}
    kind = result.get("kind", "")
    data = {"kind": kind, "target": result.get("target", "")}
    if kind != "comment":
        return {"headline": f"{_KIND_TEXT.get(kind, '操作')}完成", "data": data}
    return {"headline": "评论已发出，可见性还没确认", "data": data,
            "hint": "约一分钟后自动核对是否公开；需要立即确认时调用 verify。"}


def write_action(storage: BiliStorage, kind: str, *, bvid: str = "", text: str = "") -> dict:
    """执行一次写操作；失败返回 error 与 code。"""
    cfg = load_config()
    credential = load_credential()
    if credential is None:
        return {"error": "还没登录 B 站账号", "code": "not_logged_in"}
    if kind not in _KIND_TEXT:
        return {"error": "不支持的操作", "code": "unsupported"}
    if not cfg.get(f"allow_{kind}", False):
        return {"error": f"配置里没打开「{_KIND_TEXT[kind]}」", "code": "disabled"}

    uid = int(credential.dedeuserid)
    today = date.today().isoformat()

    if kind == "dm":
        target_uid = int(cfg["dm_uid"])
        if target_uid <= 0:
            return {"error": "私信收件人 UID 未配置，先在账号面板填写",
                    "code": "dm_uid_missing"}
        ok, code = check_text(text, max_chars=int(cfg["dm_max_chars"]), allow_urls=True,
                              blocklist=tuple(cfg["comment_blocklist"]))
        if not ok:
            return {"error": f"私信没发出去：{describe(code)}", "code": "filtered"}
        with _open(cfg, credential) as client:
            result = _guarded_write(storage, uid, "dm", str(target_uid), text,
                                    lambda: client.dm(target_uid, text), limit=0)
        return _as_tool_result(result)

    target = recent_target(bvid)
    if target is None:
        return {"error": "这个视频不在最近一轮刷到的候选里，先跑一轮",
                "code": "target_unknown"}

    if kind == "like":
        with _open(cfg, credential) as client:
            result = _guarded_write(storage, uid, "like", bvid, "",
                                    lambda: client.like(bvid))
        return _as_tool_result(result)

    if kind == "coin":
        if storage.count_today(uid, "coin", today) >= int(cfg["coin_per_day"]):
            return {"error": "今天的投币额度用完了", "code": "quota"}
        with _open(cfg, credential) as client:
            me = client.whoami()
            if int(me.get("coins") or 0) <= int(cfg["coin_floor"]):
                return {"error": "硬币不够，先留着", "code": "insufficient_coins"}
            result = _guarded_write(storage, uid, "coin", bvid, "",
                                    lambda: client.coin(bvid))
        return _as_tool_result(result)

    if storage.count_today(uid, "comment", today) >= int(cfg["comment_per_day"]):
        return {"error": "今天的评论额度用完了", "code": "quota"}
    blocked = _comment_gate(storage, uid)
    if blocked:
        return blocked
    with _open(cfg, credential) as client:
        result = _post_comment(client, storage, cfg, credential, uid, bvid,
                               int(target["aid"]), text)
    return _as_tool_result(result)


def verify(storage: BiliStorage, limit: int = 10) -> dict:
    """复核已发出的评论是否公开可见；有 rpid 只按 rpid 匹配，无 rpid 时用正文。"""
    cfg = load_config()
    credential = load_credential()
    if credential is None:
        return {"error": "还没登录 B 站账号", "code": "not_logged_in"}
    uid = int(credential.dedeuserid)
    pending = storage.reviewable_actions(uid, "comment", int(limit))
    if not pending:
        return {"headline": "没有待确认的评论"}

    checked = 0
    found = 0
    folded = 0
    missing = 0
    with _open(cfg, credential) as client:
        for action in pending:
            checked += 1
            try:
                aid = int(client.view(action["target"])["aid"])
            except BiliError as exc:
                logger.info(f"[bilibili] 复核 {action['target']} 失败：{exc}")
                continue
            rpid = int(action.get("remote_id") or 0)
            root = action.get("root")
            root = int(root) if root is not None else storage.mention_root(action["target"])
            try:
                state = _confirm_comment(client, uid, aid, root, rpid,
                                         action.get("content") or "", action["target"])
            except BiliError as exc:
                logger.info(f"[bilibili] 复核 {action['target']} 失败：{exc}")
                continue

            if state == "done":
                # 两边都看不到不等于没发出：留在 unverified，下轮再核
                storage.finish_action(action["id"], "unverified",
                                      remote_id=str(rpid), root=root)
                missing += 1
                continue
            storage.finish_action(action["id"], state, remote_id=str(rpid))
            if state == "visible":
                found += 1
            else:
                folded += 1
    return {"headline": (f"检查 {checked} 条评论，确认 {found} 条已公开"
                         + (f"，{folded} 条被平台隐藏" if folded else "")
                         + (f"，{missing} 条没查到" if missing else "")),
            "data": {"checked": checked, "found": found, "folded": folded,
                     "missing": missing}}


def status(storage: BiliStorage) -> dict:
    """账号信息、今日计数与开关状态。"""
    cfg = load_config()
    credential = load_credential()
    if credential is None:
        return {"error": "未登录 B 站账号", "code": "not_logged_in"}
    try:
        with BiliClient(credential, timeout=float(cfg["timeout_s"]),
                        interval=0.0) as client:
            me = client.whoami()
    except BiliError as exc:
        return {"error": f"查询失败：{exc}", "code": "request_failed"}
    today = date.today().isoformat()
    uid = int(me.get("uid") or 0)
    return {
        "headline": (f"{me.get('name', '')}（UID {uid}）Lv{me.get('level', 0)}，"
                     f"硬币 {me.get('coins', 0)}"),
        "data": {
            "account": me,
            "today": {
                "picked": storage.get_round(uid, today)["picked"],
                "like": storage.count_today(uid, "like", today),
                "coin": storage.count_today(uid, "coin", today),
                "comment": storage.count_today(uid, "comment", today),
                "dm": storage.count_today(uid, "dm", today),
            },
            "switches": {k: cfg[k] for k in ("allow_browse", "allow_like",
                                             "allow_coin", "allow_comment", "allow_dm")},
            "keywords": cfg["keywords"],
        },
    }
