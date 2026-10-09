"""测试公共隔离设施。

以下三处必须在导入任何 pet 模块之前生效：
1. settings.json 与 settings-schema.json 的落盘位置由 APPDATA 决定，重定向到临时目录
2. 各存储类通过 pet.db.get_db_path 定位 pet.db，替换为临时库，避免污染真实数据
3. pet/__init__ 会安装崩溃钩子并改写 logs/startup.state、全局 excepthook，此处用空模块顶替
"""

import atexit
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

import pytest

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="koishi-ai-pet-tests-"))
atexit.register(shutil.rmtree, _TMP_ROOT, ignore_errors=True)
os.environ["APPDATA"] = str(_TMP_ROOT)
os.environ["XDG_CONFIG_HOME"] = str(_TMP_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

class _NoopGuard:
    """崩溃守卫的空实现：装配层只用到 set_enabled。"""

    def set_enabled(self, enabled: bool) -> None:
        pass


_crash_stub = types.ModuleType("pet.crash_reporter")
_crash_stub.install = lambda: None
_crash_stub.get_guard = lambda: _NoopGuard()
sys.modules.setdefault("pet.crash_reporter", _crash_stub)

import pet.db as _db  # noqa: E402

_TEST_DB = _TMP_ROOT / "pet.db"
_db.get_db_path = lambda: str(_TEST_DB)


@pytest.fixture(scope="session")
def session_db_path() -> Path:
    """会话级临时数据库路径。"""
    return _TEST_DB


@pytest.fixture
def case_db_path(tmp_path) -> Path:
    """用例级独立数据库路径，供支持 db_path 参数的存储类使用。"""
    return tmp_path / "case.db"
