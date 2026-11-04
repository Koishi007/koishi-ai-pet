"""执行核心：解析、内建路由、读线程、终止、截断、解码与审计。"""

import os
import sys

import pytest

from pet.terminal import core

_WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Windows 分支")
_POSIX = pytest.mark.skipif(os.name == "nt", reason="POSIX 分支")


class TestQuotes:

    @_POSIX
    @pytest.mark.parametrize("text", ["echo 'a", 'echo "a'])
    def test_posix_unclosed_quote(self, text):
        parsed, err = core.parse_command(text)
        assert err == "bad_command"
        assert parsed.argv == []

    @_WINDOWS
    def test_windows_unclosed_double_quote(self):
        _, err = core.parse_command('echo "a')
        assert err == "bad_command"

    @_WINDOWS
    def test_windows_escaped_quote_is_not_paired(self):
        _, err = core.parse_command('echo "a\\"')
        assert err == "bad_command"

    @_WINDOWS
    def test_windows_single_quote_inside_double_quotes(self):
        parsed, err = core.parse_command('echo "it\'s"')
        assert err == ""
        assert parsed.argv == ["echo", "it's"]

    def test_blank_command_is_rejected(self):
        for text in ("", "   "):
            _, err = core.parse_command(text)
            assert err == "bad_command"


class TestParse:

    def test_plain_command(self):
        parsed, err = core.parse_command("echo hi")
        assert err == ""
        assert parsed.argv == ["echo", "hi"]

    @_WINDOWS
    def test_windows_backslashes_are_kept(self):
        parsed, err = core.parse_command(r"type C:\Users\me\notes.txt")
        assert err == ""
        assert parsed.argv == ["type", r"C:\Users\me\notes.txt"]

    @_WINDOWS
    def test_wrapping_quotes_are_stripped(self):
        parsed, err = core.parse_command('type "C:\\a b\\notes.txt"')
        assert err == ""
        assert parsed.argv[1] == r"C:\a b\notes.txt"


class TestBuiltin:

    @_WINDOWS
    def test_dir_is_routed_through_cmd(self):
        parsed, err = core.parse_command("dir /a")
        assert err == ""
        assert parsed.argv == ["dir", "/a"]
        assert parsed.builtin == "dir"
        assert parsed.routed == ["cmd", "/d", "/s", "/c", "dir /a"]

    @_WINDOWS
    @pytest.mark.parametrize("name", ["cd", "del", "set", "copy", "move", "md"])
    def test_write_and_cd_commands_are_not_routed(self, name):
        parsed, err = core.parse_command(f"{name} x")
        assert err == ""
        assert parsed.builtin == ""
        assert parsed.routed == parsed.argv

    @_POSIX
    def test_ls_is_not_routed_on_posix(self):
        parsed, err = core.parse_command("ls -l")
        assert err == ""
        assert parsed.builtin == ""
        assert parsed.routed == ["ls", "-l"]


class TestShellMode:

    @_WINDOWS
    def test_cmd_shell_mode(self):
        parsed, err = core.parse_command("dir | findstr py", "cmd")
        assert err == ""
        assert parsed.argv == ["dir | findstr py"]
        assert parsed.builtin == ""
        assert parsed.routed == ["cmd", "/d", "/s", "/c", "dir | findstr py"]

    @_WINDOWS
    def test_pwsh_shell_mode(self):
        parsed, _ = core.parse_command("dir", "pwsh")
        assert parsed.routed[:4] == ["powershell", "-NoProfile", "-NonInteractive", "-Command"]

    @_POSIX
    def test_sh_shell_mode(self):
        parsed, err = core.parse_command("echo $HOME", "sh")
        assert err == ""
        assert parsed.routed == ["sh", "-c", "echo $HOME"]

    def test_unknown_shell_is_rejected(self):
        _, err = core.parse_command("dir", "nosuchshell")
        assert err == "bad_command"

    def test_choices_are_platform_specific(self):
        choices = core.shell_choices()
        assert choices[0] == ""
        assert ("pwsh" in choices) == (os.name == "nt")
        assert ("sh" in choices) == (os.name != "nt")

    @_WINDOWS
    def test_windows_default_shell_is_cmd(self):
        assert core.shell_choices()[:2] == ["", "cmd"]


class TestLaunchFlags:

    def test_no_console_window_on_windows(self, monkeypatch):
        import io

        captured = {}

        class _FakeProc:
            pid = 1
            returncode = 0
            stdout = io.BytesIO(b"")
            stderr = io.BytesIO(b"")

            def wait(self, timeout=None):
                return 0

        def _fake_popen(argv, **kwargs):
            captured.update(kwargs)
            return _FakeProc()

        monkeypatch.setattr(core.subprocess, "Popen", _fake_popen)
        core.run(["echo", "hi"], "", 1, None)
        if os.name == "nt":
            assert captured["creationflags"] & core._NO_WINDOW
        else:
            assert captured["start_new_session"] is True


class TestLargeOutput:

    def test_large_output_returns_quickly(self):
        code = "import sys; sys.stdout.write('x' * 1048576)"
        result = core.run([sys.executable, "-c", code], "", 20, None,
                          max_capture_bytes=4096)
        assert result.code == ""
        assert result.truncated is True
        assert len(result.stdout.encode("utf-8")) <= 4096


class TestStreaming:

    def test_lines_are_relayed_while_running(self):
        code = "import sys; print('first'); print('second')"
        chunks = []
        result = core.run([sys.executable, "-c", code], "", 20, None,
                          on_output=lambda stream, text: chunks.append((stream, text)))
        assert result.code == ""
        assert any(stream == "stdout" and "first" in text for stream, text in chunks)

    def test_no_callback_keeps_output(self):
        code = "import sys; print('only')"
        result = core.run([sys.executable, "-c", code], "", 20, None)
        assert result.stdout.strip() == "only"


class TestTimeout:

    def test_timeout_terminates_process(self):
        result = core.run([sys.executable, "-c", "import time; time.sleep(20)"],
                          "", 1, None)
        assert result.code == "command_timeout"
        assert result.duration_ms < 10000

    def test_kill_failure_is_reported(self, monkeypatch):
        reason = "terminate-sentinel"
        monkeypatch.setattr(core, "terminate_tree", lambda pid: (False, reason))
        result = core.run([sys.executable, "-c", "import time; time.sleep(1.5)"],
                          "", 0.1, None)
        assert result.code == "kill_failed"
        assert result.error == reason


class TestTruncate:

    def test_single_stream_keeps_head_and_tail(self):
        body, truncated = core.truncate_streams("a" * 4000, "", 4000)
        assert truncated is True
        assert body.count("a") == 2000
        assert core.TRUNCATE_NOTICE.format(count=2000) in body

    def test_two_streams_are_quota_split(self):
        body, truncated = core.truncate_streams("a" * 4000, "b" * 4000, 4000)
        assert truncated is True
        assert body.count("a") == 2000
        assert body.count("b") == 2000
        assert core.STDERR_HEADER in body

    def test_small_budget(self):
        body, _ = core.truncate_streams("c" * 1000, "", 1000)
        assert body.count("c") == 500
        assert core.TRUNCATE_NOTICE.format(count=500) in body

    def test_short_output_is_untouched(self):
        body, truncated = core.truncate_streams("ok\n", "", 4000)
        assert body == "ok\n"
        assert truncated is False

    def test_stderr_only(self):
        body, _ = core.truncate_streams("", "boom", 4000)
        assert body == "boom"


class TestDecode:

    def test_utf8(self):
        assert core.decode_bytes("中文".encode("utf-8")) == "中文"

    def test_system_encoding(self, monkeypatch):
        monkeypatch.setattr(core.locale, "getpreferredencoding", lambda do_setlocale: "gbk")
        assert core.decode_bytes("中文".encode("gbk")) == "中文"

    def test_invalid_bytes_do_not_raise(self):
        assert isinstance(core.decode_bytes(b"\xff\xfe\x00a"), str)




class _PosixOs:
    """只承载 terminate_tree / display_argv 用到的 os 接口，避免改动真实 os.name。"""

    name = "posix"

    @staticmethod
    def killpg(pid, sig):
        raise ProcessLookupError

    @staticmethod
    def getpgid(pid):
        return pid


class TestPlatform:

    def test_killpg_process_lookup_counts_as_success(self, monkeypatch):
        monkeypatch.setattr(core, "os", _PosixOs)
        assert core.terminate_tree(12345) == (True, "")

    def test_display_argv_uses_shlex_join_on_posix(self, monkeypatch):
        monkeypatch.setattr(core, "os", _PosixOs)
        assert core.display_argv(["ls", "-l"]) == "ls -l"
