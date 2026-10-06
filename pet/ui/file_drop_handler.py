"""拖放判定层：悬停与放下两层的判定、回调分发。

不持有 PetWindow，状态与出口都由装配层注入；不带 Qt 依赖，mime 只按鸭子类型取路径。
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Sequence

from pet.config import config
from pet.file_intake import FileRef, check_drop

logger = logging.getLogger(__name__)

_BUSY_NAMES_MAX = 3


def paths_from_mime(mime) -> list[str]:
    """取 mime 里的本地路径；非本地 URL 与纯文本返回空列表。

    QUrl 在 Windows 上给的是正斜杠路径，统一 normpath 成平台原生形态，
    与仓库其他位置的路径写法一致。
    """
    if mime is None or not mime.hasUrls():
        return []
    paths = []
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        path = url.toLocalFile()
        if path:
            paths.append(os.path.normpath(path))
    return paths


def busy_event_text(paths: Sequence[str]) -> str:
    """忙态一次性事件的文案，必须带文件名（缺 text 的条目会被上下文丢弃）。"""
    names = "、".join(os.path.basename(path) for path in paths[:_BUSY_NAMES_MAX])
    return f"用户趁你忙的时候丢来「{names}」，你没有接住"


class FileDropHandler:
    """悬停层只看三项（功能开关、穿透、有无本地路径）；放下层做硬条件判定。"""

    def __init__(self, hover_state: Callable[[], tuple[bool, bool]],
                 is_busy: Callable[[], bool],
                 on_show_bubble: Callable[[tuple[FileRef, ...]], None],
                 on_reject: Callable[[str, tuple[str, ...]], None],
                 on_busy_event: Callable[[str], None]):
        self._hover_state = hover_state
        self._is_busy = is_busy
        self._on_show_bubble = on_show_bubble
        self._on_reject = on_reject
        self._on_busy_event = on_busy_event

    def accept_hover(self, has_local_paths: bool) -> bool:
        """悬停是否接受：命中三项之一就 ignore，让系统显示禁止光标。"""
        enabled, penetration = self._hover_state()
        return bool(has_local_paths and enabled and not penetration)

    def handle_drop(self, paths: Sequence[str]) -> None:
        """放下阶段：忙态写一次性事件，三类硬边界发拒收请求，通过则弹气泡。"""
        candidates = [path for path in paths if path]
        if not candidates:
            return
        if self._is_busy():
            logger.info(f"[FileDrop] busy, drop ignored: {len(candidates)} item(s)")
            self._on_busy_event(busy_event_text(candidates))
            return

        verdict = check_drop(
            candidates,
            max_files=config.FILE_DROP_MAX_FILES,
            max_bytes=config.FILE_DROP_MAX_FILE_MB * 1024 * 1024,
            deny_patterns=config.FILE_DROP_DENY_PATTERNS,
        )
        if verdict.status == "ok":
            logger.info(f"[FileDrop] accepted: {len(verdict.refs)} item(s)")
            self._on_show_bubble(verdict.refs)
            return

        logger.info(f"[FileDrop] rejected ({verdict.status}): {verdict.detail}")
        self._on_reject(verdict.status, verdict.names)
