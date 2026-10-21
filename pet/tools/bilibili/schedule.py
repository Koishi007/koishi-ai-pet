"""定时提醒：到点给模型一个念头，由提示词决定用什么工具。

不记录是否跑过，只按频率排下一次；状态不可用时指数退避，窗口外顺延到窗口开始。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from pet.tools.bilibili.core import BiliClient, BiliError
from pet.tools.bilibili.duty import load_config, load_credential
from pet.tools.bilibili.storage import BiliStorage
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

_SCHEDULE_FILE = Path(__file__).parent / "schedule.md"
_ALARM_KEY = "bilibili_schedule"
_MIN_INTERVAL = 60
_BACKOFF_MAX = 60
_DEFAULT_WINDOW = (9 * 60, 23 * 60)

_FALLBACK_SCHEDULE = """# 到点了

现在是你自己安排的一段时间，没有人在等你回话。

B 站工具在 info 组里，默认不在你的工具列表里。要用之前先调用
tool_search__search，关键词填 bilibili，等工具出现在列表里再调用。

可用工具：
- bilibili__browse：刷一轮，挑出想看的视频，返回里带 bvid
- bilibili__like / bilibili__coin：给刚刷到的视频点赞或投币
- bilibili__comment：在刚刷到的视频下留一条评论
- bilibili__dm：给主人发私信
- bilibili__status：查看今日额度与开关状态

写操作都受开关与每日额度约束，未开启时工具会直接返回原因，如实说明即可，不要重试。

可以做的事：
- 用 bilibili__browse 看看有没有想看的视频
- 看到值得分享的，用 bilibili__dm 给主人发一条
- 什么都不想做，就待着

用不用工具由你决定，不要为了用工具而用工具。
"""

_backoff_level = 0
_storage: BiliStorage | None = None


def _store() -> BiliStorage:
    global _storage
    if _storage is None:
        _storage = BiliStorage()
    return _storage


def _fetch_mentions(cfg: dict) -> list[dict]:
    """取主人新 @ 的视频并落表去重；未配置主人或接口失败返回空。"""
    owner = int(cfg["dm_uid"])
    credential = load_credential()
    if owner <= 0 or credential is None:
        return []

    storage = _store()
    today = datetime.now().date().isoformat()
    if storage.count_mentions_today(today) >= int(cfg["mention_per_day"]):
        return []

    fresh: list[dict] = []
    try:
        with BiliClient(credential, timeout=float(cfg["timeout_s"]), interval=0.0) as client:
            for item in client.mentions(limit=10):
                if item["uid"] != owner:
                    continue
                try:
                    info = client.view_by_aid(item["aid"])
                except BiliError:
                    continue
                if not storage.claim_mention(item["id"], item["uid"], info["bvid"],
                                             item["rpid"], item["name"], item["content"]):
                    continue
                fresh.append({"bvid": info["bvid"], "title": info["title"],
                              "name": item["name"]})
                if len(fresh) >= int(cfg["mention_per_day"]):
                    break
    except BiliError as exc:
        logger.info(f"[bilibili] 取 @ 失败：{exc}")
    return fresh


def _parse_range(text: str) -> tuple[int, int] | None:
    try:
        start, end = str(text).split("-", 1)
        start_hour, start_minute = (int(part) for part in start.strip().split(":"))
        end_hour, end_minute = (int(part) for part in end.strip().split(":"))
    except (TypeError, ValueError):
        return None
    start_at = start_hour * 60 + start_minute
    end_at = end_hour * 60 + end_minute
    if not (0 <= start_at < end_at <= 24 * 60):
        return None
    return start_at, end_at


def parse_window(text: str) -> tuple[int, int]:
    """解析 ``HH:MM-HH:MM`` 为当日分钟区间；非法时回退 09:00-23:00。"""
    return _parse_range(text) or _DEFAULT_WINDOW


def parse_pause_windows(text: str) -> list[tuple[int, int]]:
    """解析逗号分隔的多个禁用区间；非法项跳过，空串返回空列表。"""
    parts = str(text).replace("，", ",").split(",")
    return [window for window in (_parse_range(part) for part in parts) if window]


def _minutes(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute


def in_window(moment: datetime, window: tuple[int, int]) -> bool:
    return window[0] <= _minutes(moment) < window[1]


def is_paused(moment: datetime, windows: list[tuple[int, int]],
              weekdays_only: bool = True) -> bool:
    """是否落在禁用时段；weekdays_only 为真时周末不判定。"""
    if weekdays_only and moment.weekday() >= 5:
        return False
    return any(start <= _minutes(moment) < end for start, end in windows)


def next_out_of_pause(moment: datetime, windows: list[tuple[int, int]],
                      weekdays_only: bool = True) -> datetime:
    """若在禁用时段内，返回该时段的结束时间；否则原样返回。"""
    if not is_paused(moment, windows, weekdays_only):
        return moment
    for start, end in windows:
        if start <= _minutes(moment) < end:
            return moment.replace(hour=end // 60, minute=end % 60,
                                  second=0, microsecond=0)
    return moment


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
