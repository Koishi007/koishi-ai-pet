"""单实例限制 — 防止桌宠被重复启动导致状态/数据库冲突。"""

import logging
import os

from PySide6.QtCore import QLockFile

from pet.settings import settings_path

logger = logging.getLogger(__name__)

# 锁文件与 settings.json 同目录，保证任意工作目录启动都能识别同一实例
_LOCK_FILE_NAME = "KoishiAI.lock"


def _lock_file_path() -> str:
    """返回锁文件路径。"""
    return os.path.join(os.path.dirname(settings_path()), _LOCK_FILE_NAME)


class SingleInstanceGuard:
    """基于 QLockFile 的单实例锁。"""

    def __init__(self, path: str | None = None):
        self.path = path or _lock_file_path()
        self._lock = QLockFile(self.path)
        self._lock.setStaleLockTime(0)

    def try_acquire(self) -> bool:
        """尝试获取单实例锁，成功返回 True。"""
        return self._lock.tryLock(0)

    def is_locked_by_other(self) -> bool:
        """上次 try_acquire 失败是否因另一实例持有锁。"""
        return self._lock.error() == QLockFile.LockError.LockFailedError

    def release(self) -> None:
        """释放锁（正常退出时调用，幂等）。"""
        try:
            if self._lock.isLocked():
                self._lock.unlock()
        except Exception as e:
            logger.warning("[SingleInstance] 释放锁失败: %s", e)
