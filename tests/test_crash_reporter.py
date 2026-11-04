"""崩溃启动标记的归属校验。

conftest.py 用空模块顶替 pet.crash_reporter 以避免安装钩子，这里按文件加载真实模块。
"""

import importlib.util
import json
import os
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "pet" / "crash_reporter.py"


def _load_real_module():
    spec = importlib.util.spec_from_file_location("_crash_reporter_real", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_REAL = _load_real_module()


def _reporter(tmp_path):
    reporter = _REAL.CrashReporter()
    reporter.marker_path = str(tmp_path / "startup.state")
    return reporter


def _write_marker(reporter, pid):
    with open(reporter.marker_path, "w", encoding="utf-8") as f:
        json.dump({"pid": pid, "status": "running", "started_at": "2026-11-04T12:00:00"}, f)


def test_clear_marker_removes_own_marker(tmp_path):
    reporter = _reporter(tmp_path)
    _write_marker(reporter, os.getpid())
    reporter.clear_marker()
    assert not os.path.exists(reporter.marker_path)


def test_clear_marker_keeps_other_instance_marker(tmp_path):
    """重启时新实例已写入标记，旧实例退出不能删除它。"""
    reporter = _reporter(tmp_path)
    _write_marker(reporter, os.getpid() + 1)
    reporter.clear_marker()
    assert os.path.exists(reporter.marker_path)


def test_clear_marker_removes_unreadable_marker(tmp_path):
    reporter = _reporter(tmp_path)
    with open(reporter.marker_path, "w", encoding="utf-8") as f:
        f.write("{ 不是 JSON")
    reporter.clear_marker()
    assert not os.path.exists(reporter.marker_path)


def test_clear_marker_without_file(tmp_path):
    reporter = _reporter(tmp_path)
    reporter.clear_marker()
    assert not os.path.exists(reporter.marker_path)
