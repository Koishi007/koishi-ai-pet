"""单实例锁契约：失败原因可区分，锁不可用时能上报原因。"""

from PySide6.QtCore import QLockFile

from pet import single_instance
from pet.single_instance import SingleInstanceGuard


class _PermissionDeniedLock:
    """模拟锁文件建不出来：tryLock 失败，error 为 PermissionError。"""

    LockError = QLockFile.LockError

    def __init__(self, path: str):
        self.path = path

    def setStaleLockTime(self, msecs: int) -> None:
        pass

    def tryLock(self, timeout: int) -> bool:
        return False

    def error(self):
        return QLockFile.LockError.PermissionError

    def isLocked(self) -> bool:
        return False

    def unlock(self) -> None:
        pass


def test_second_guard_reports_lock_failed(tmp_path):
    path = str(tmp_path / "KoishiAI.lock")
    holder = SingleInstanceGuard(path)
    contender = SingleInstanceGuard(path)
    assert holder.try_acquire() is True
    try:
        assert contender.try_acquire() is False
        assert contender.is_locked_by_other() is True
        assert contender.error() == QLockFile.LockError.LockFailedError
    finally:
        holder.release()


def test_successful_acquire_reports_no_error(tmp_path):
    guard = SingleInstanceGuard(str(tmp_path / "KoishiAI.lock"))
    try:
        assert guard.try_acquire() is True
        assert guard.is_locked_by_other() is False
        assert guard.error() == QLockFile.LockError.NoError
    finally:
        guard.release()


def test_permission_error_is_not_locked_by_other(tmp_path, monkeypatch):
    monkeypatch.setattr(single_instance, "QLockFile", _PermissionDeniedLock)
    guard = SingleInstanceGuard(str(tmp_path / "KoishiAI.lock"))
    assert guard.try_acquire() is False
    # 启动代码按此区分「已有实例」与「锁不可用」，后者需要 error() 给出原因
    assert guard.is_locked_by_other() is False
    assert guard.error() == QLockFile.LockError.PermissionError
