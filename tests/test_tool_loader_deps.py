"""工具依赖安装：源码环境走 pip，冻结打包跳过。"""

import subprocess
import sys

from pet.tools import _ensure_tool_deps


def _make_tool_dir(tmp_path):
    tool_dir = tmp_path / "demo"
    tool_dir.mkdir()
    (tool_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")
    return tool_dir


def test_frozen_build_skips_pip(tmp_path, monkeypatch):
    tool_dir = _make_tool_dir(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))

    _ensure_tool_deps(tool_dir)

    assert calls == []
    assert not (tool_dir / ".deps_installed").exists()


def test_source_build_installs_and_marks(tmp_path, monkeypatch):
    tool_dir = _make_tool_dir(tmp_path)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    _ensure_tool_deps(tool_dir)

    assert len(calls) == 1
    assert calls[0][0] == sys.executable
    assert str(tool_dir / "requirements.txt") in calls[0]
    assert (tool_dir / ".deps_installed").is_file()
