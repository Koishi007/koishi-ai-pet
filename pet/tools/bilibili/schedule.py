"""定时提醒：到点给模型一个念头，由提示词决定用什么工具。

不记录是否跑过，只按频率排下一次；状态不可用时指数退避，窗口外顺延到窗口开始。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from pet.tools.bilibili.core import BiliClient, BiliError
from pet.tools.bilibili.duty import load_config, load_credential
from pet.tools.bilibili.storage import BiliStorage
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

_SCHEDULE_FILE = Path(__file__).parent / "schedule.md"
_ALARM_KEY = "bilibili_schedule"
_MIN_INTERVAL = 10
_BACKOFF_MAX = 60
_DEFAULT_WINDOW = (9 * 60, 23 * 60)
_MENTION_SCAN = 20  # 每次拉取的 @ 通知条数

# 完整文案以 schedule.md 为准；文件读不出来时只保留工具发现与必做任务
_FALLBACK_SCHEDULE = """# 到点了

现在是你自己安排的一段时间，没有人在等你回话。

B 站工具在 info 组里，默认不在你的工具列表里。要用之前先调用
`tool_search__search`，关键词填 `bilibili`，等工具出现在列表里再调用。

主人 @ 了你某个视频时，先处理 @：用 `bilibili__browse` 传那个 BV 号去看，再用 `bilibili__comment` 在视频下回一条评论，不必再发私信。

这一轮要做完两件事：用 `bilibili__browse` 刷一轮挑出想推荐的视频，再用
`bilibili__dm` 把 BV 号和推荐理由发给主人。点赞、投币、评论可选。
"""

_backoff_level = 0
_storage: BiliStorage | None = None


def _store() -> BiliStorage:
    global _storage
    if _storage is None:
        _storage = BiliStorage()
    return _storage


def _fetch_mentions(cfg: dict) -> list[dict]:
    """拉 @ 落库，返回未回复的 @：未回复的一直带进提示词，回复后不再出现。"""
    owner = int(cfg["dm_uid"])
    storage = _store()
    if owner <= 0:
        return []

    credential = load_credential()
    if credential is not None:
        try:
            with BiliClient(credential, timeout=float(cfg["timeout_s"]),
                            interval=float(cfg["min_interval_s"])) as client:
                for item in client.mentions(limit=_MENTION_SCAN):
                    if item["uid"] != owner or storage.has_mention(item["id"]):
                        continue
                    try:
                        info = client.view(item["bvid"])
                    except BiliError:
                        continue
                    storage.add_mention(item["id"], owner, info["bvid"], info["title"],
                                        item["rpid"], item["name"], item["content"])
        except BiliError as exc:
            logger.info(f"[bilibili] 取 @ 失败：{exc}")
    mentions = storage.unreplied_mentions(owner, max(1, int(cfg["mention_per_day"])))
    if mentions:
        listed = "；".join(f"{m['name']} {m['bvid']}《{m['title']}》" for m in mentions)
        logger.info(f"[bilibili] 待回复 @ {len(mentions)} 条：{listed}")
    return mentions


def _parse_range(text: str, allow_wrap: bool = False) -> tuple[int, int] | None:
    """解析 ``HH:MM-HH:MM`` 为当日分钟区间；allow_wrap 时 end < start 表示跨零点。"""
    try:
        start, end = str(text).split("-", 1)
        start_hour, start_minute = (int(part) for part in start.strip().split(":"))
        end_hour, end_minute = (int(part) for part in end.strip().split(":"))
    except (TypeError, ValueError):
        return None
    start_at = start_hour * 60 + start_minute
    end_at = end_hour * 60 + end_minute
    if not (0 <= start_at < 24 * 60 and 0 <= end_at <= 24 * 60):
        return None
    if start_at == end_at:
        return None
    if start_at > end_at and not allow_wrap:
        return None
    return start_at, end_at


def parse_window(text: str) -> tuple[int, int]:
    """解析 ``HH:MM-HH:MM`` 为当日分钟区间；非法时回退 09:00-23:00。"""
    return _parse_range(text) or _DEFAULT_WINDOW


def parse_pause_windows(text: str) -> list[tuple[int, int]]:
    """解析逗号分隔的多个禁用区间，允许跨零点；非法项跳过，空串返回空列表。"""
    parts = str(text).replace("，", ",").split(",")
    return [window for window in (_parse_range(part, allow_wrap=True) for part in parts) if window]


def invalid_pause_parts(text: str) -> list[str]:
    """解析不出的时段片段；面板据此拒绝保存。"""
    parts = (part.strip() for part in str(text).replace("，", ",").split(","))
    return [part for part in parts if part and _parse_range(part, allow_wrap=True) is None]


def _minutes(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute


def _day_start(moment: datetime) -> datetime:
    """当日零点；区间终点允许 24:00，按零点加分钟数换算成次日 00:00。"""
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def in_window(moment: datetime, window: tuple[int, int]) -> bool:
    return window[0] <= _minutes(moment) < window[1]


def _in_range(minutes: int, start: int, end: int) -> bool:
    """分钟是否落在区间内；end 小于 start 表示跨零点。"""
    if end > start:
        return start <= minutes < end
    return minutes >= start or minutes < end


def is_paused(moment: datetime, windows: list[tuple[int, int]],
              weekdays_only: bool = True) -> bool:
    """是否落在任一个禁用时段；weekdays_only 为真时周末不判定。"""
    if weekdays_only and moment.weekday() >= 5:
        return False
    minutes = _minutes(moment)
    return any(_in_range(minutes, start, end) for start, end in windows)


def next_out_of_pause(moment: datetime, windows: list[tuple[int, int]],
                      weekdays_only: bool = True) -> datetime:
    """若在禁用时段内，返回命中时段中最晚的结束时间；否则原样返回。"""
    if not is_paused(moment, windows, weekdays_only):
        return moment
    minutes = _minutes(moment)
    ends = [_range_end(moment, start, end) for start, end in windows
            if _in_range(minutes, start, end)]
    return max(ends) if ends else moment


def _range_end(moment: datetime, start: int, end: int) -> datetime:
    """命中区间的结束时刻；跨零点且已过 end 时落在次日。"""
    base = _day_start(moment)
    if end > start:
        return base + timedelta(minutes=end)
    if _minutes(moment) >= start:
        return base + timedelta(minutes=end) + timedelta(days=1)
    return base + timedelta(minutes=end)


def next_window_start(moment: datetime, window: tuple[int, int]) -> datetime:
    """返回窗口开始时间：当天未开始则当天，否则次日。"""
    start_at = moment.replace(hour=window[0] // 60, minute=window[0] % 60,
                              second=0, microsecond=0)
    if _minutes(moment) < window[0]:
        return start_at
    return start_at + timedelta(days=1)


def next_trigger(moment: datetime, interval_minutes: int,
                 window: tuple[int, int]) -> datetime:
    """下一次触发时间：moment 加间隔，落在窗口外则顺延到下一个窗口开始。"""
    candidate = moment + timedelta(minutes=max(0, int(interval_minutes)))
    if in_window(candidate, window):
        return candidate
    return next_window_start(candidate, window)


def next_allowed(moment: datetime, interval: int, window: tuple[int, int],
                 cfg: dict) -> datetime:
    """下一次允许触发的时间：落在禁用时段则顺延到该段结束，再确保仍在作息窗口内。"""
    candidate = next_trigger(moment, interval, window)
    windows = parse_pause_windows(cfg["schedule_pause_hours"])
    if not windows:
        return candidate
    weekdays = bool(cfg["schedule_pause_weekdays_only"])
    for _ in range(8):
        if not is_paused(candidate, windows, weekdays):
            return candidate
        candidate = next_trigger(next_out_of_pause(candidate, windows, weekdays), 0, window)
    return candidate


def backoff_at(moment: datetime, level: int, base_minutes: float) -> datetime:
    """指数退避：base × 2^level，上限 60 分钟。"""
    delay = min(float(base_minutes) * (2 ** max(0, level)), _BACKOFF_MAX)
    return moment + timedelta(minutes=delay)


def schedule_text() -> str:
    """读取可自定义的提醒文案；缺失时用内置兜底。"""
    try:
        text = _SCHEDULE_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(f"[bilibili] schedule.md 读不出来：{exc}")
        return _FALLBACK_SCHEDULE
    return text.strip() or _FALLBACK_SCHEDULE


def _arm(moment: datetime):
    TOOL_CTX.register_alarm(int(moment.timestamp() * 1000), _tick, key=_ALARM_KEY)
    logger.info(f"[bilibili] 下次提醒：{moment:%Y-%m-%d %H:%M}")


def _tick():
    """闹钟回调：窗口外顺延，状态不允许则退避，否则发起一次交互。"""
    global _backoff_level
    try:
        _advance()
    except Exception:
        # 闹钟是一次性的，回调抛异常后不会再排下一次
        logger.exception("[bilibili] 定时提醒出错，退避后重排")
        _backoff_level = min(_backoff_level + 1, 6)
        try:
            _arm(backoff_at(datetime.now(), _backoff_level,
                            load_config()["schedule_backoff_base_minutes"]))
        except Exception:
            logger.exception("[bilibili] 重排闹钟失败")


def _advance():
    global _backoff_level
    cfg = load_config()
    if not cfg["schedule_enabled"]:
        return

    now = datetime.now()
    window = parse_window(cfg["schedule_active_hours"])
    windows = parse_pause_windows(cfg["schedule_pause_hours"])
    if windows:
        weekdays = bool(cfg["schedule_pause_weekdays_only"])
        if is_paused(now, windows, weekdays):
            _arm(next_out_of_pause(now, windows, weekdays))
            return
    if not in_window(now, window):
        _arm(next_window_start(now, window))
        return

    if not TOOL_CTX.is_idle():
        moment = backoff_at(now, _backoff_level, cfg["schedule_backoff_base_minutes"])
        _backoff_level = min(_backoff_level + 1, 6)
        logger.info(f"[bilibili] 桌宠忙碌，退避到 {moment:%H:%M}")
        _arm(moment)
        return

    _backoff_level = 0
    interval = max(_MIN_INTERVAL, int(cfg["schedule_interval_minutes"]))
    _arm(next_allowed(now, interval, window, cfg))
    threading.Thread(target=_interact, args=(cfg,), daemon=True).start()


def _interact(cfg: dict):
    """取主人 @ 后请求一次交互；网络请求不占主线程。"""
    hint = schedule_text()
    fresh = _fetch_mentions(cfg) if cfg["mention_enabled"] else []
    if fresh:
        lines = [f"- {m['name']} @ 了你：{m['bvid']}《{m['title']}》" for m in fresh]
        hint = hint + "\n\n主人 @ 了你：\n" + "\n".join(lines)
    TOOL_CTX.request_interact(hint, cooldown_ms=0, thinking=True, enable_tools=True)


def start(cfg: dict | None = None):
    """注册第一次闹钟；已启用时重复调用只覆盖，不叠加。"""
    global _backoff_level
    cfg = cfg or load_config()
    if not cfg["schedule_enabled"]:
        return
    _backoff_level = 0
    now = datetime.now()
    window = parse_window(cfg["schedule_active_hours"])
    interval = max(_MIN_INTERVAL, int(cfg["schedule_interval_minutes"]))
    _arm(next_allowed(now, interval, window, cfg))


def stop():
    TOOL_CTX.unregister_alarm(_ALARM_KEY)


def trigger_now():
    """立即发起一次提醒：不看开关、作息窗口与禁用时段，也不改闹钟链。"""
    cfg = load_config()
    threading.Thread(target=_interact, args=(cfg,), daemon=True).start()
