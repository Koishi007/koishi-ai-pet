"""重启命令构造：冻结/入口 exe/脚本/-m pet 四种入口都要还原成同一份代码。"""

import sys

from pet import relaunch


def test_frozen_uses_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Koishi.exe"))
    monkeypatch.setattr(sys, "argv", ["Koishi.exe"])

    cmd, cwd = relaunch.relaunch_command()

    assert cmd == [str(tmp_path / "Koishi.exe")]
    assert cwd == str(tmp_path)


def test_frozen_keeps_extra_args(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Koishi.exe"))
    monkeypatch.setattr(sys, "argv", ["Koishi.exe", "--tray"])

    cmd, _ = relaunch.relaunch_command()

    assert cmd == [str(tmp_path / "Koishi.exe"), "--tray"]


def test_entry_exe_reused(monkeypatch, tmp_path):
    entry = tmp_path / "koishi.exe"
    entry.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(entry)])

    cmd, cwd = relaunch.relaunch_command()

    assert cmd == [str(entry)]
    assert cwd == str(tmp_path)


def test_package_entry_uses_module_and_project_root(monkeypatch, tmp_path):
    pkg = tmp_path / "proj" / "pet"
    pkg.mkdir(parents=True)
    main = pkg / "__main__.py"
    main.write_text("", encoding="utf-8")
    pythonw = tmp_path / "pythonw.exe"
    pythonw.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(main)])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

    cmd, cwd = relaunch.relaunch_command()

    assert cmd == [str(pythonw), "-m", "pet"]
    assert cwd == str(tmp_path / "proj")


def test_package_entry_falls_back_to_python(monkeypatch, tmp_path):
    pkg = tmp_path / "proj" / "pet"
    pkg.mkdir(parents=True)
    main = pkg / "__main__.py"
    main.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(main)])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

    cmd, _ = relaunch.relaunch_command()

    assert cmd == [str(tmp_path / "python.exe"), "-m", "pet"]


def test_plain_script_reused(monkeypatch, tmp_path):
    script = tmp_path / "run.py"
    script.write_text("", encoding="utf-8")
    pythonw = tmp_path / "pythonw.exe"
    pythonw.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(script), "--flag"])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

    cmd, cwd = relaunch.relaunch_command()

    assert cmd == [str(pythonw), str(script), "--flag"]
    assert cwd == str(tmp_path)


def test_spawn_failure_is_reported(monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError("找不到解释器")

    monkeypatch.setattr(relaunch.subprocess, "Popen", _boom)
    assert relaunch.spawn_relaunch() is False


def test_spawn_passes_command_cwd_and_detach(monkeypatch):
    calls = []
    monkeypatch.setattr(relaunch.subprocess, "Popen",
                        lambda cmd, **kwargs: calls.append((cmd, kwargs)))

    assert relaunch.spawn_relaunch() is True

    cmd, kwargs = calls[0]
    assert cmd == relaunch.relaunch_command()[0]
    assert kwargs["cwd"] == relaunch.relaunch_command()[1]
    if sys.platform == "win32":
        assert kwargs["creationflags"] & relaunch.subprocess.DETACHED_PROCESS
    else:
        assert kwargs["start_new_session"] is True
