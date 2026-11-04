"""终端输入补全：候选来源、公共前缀、引号、大小写与条目上限。"""

import os

import pytest

from pet.ui import completion
from pet.ui.completion import CompletionContext, complete, quote_candidate


@pytest.fixture
def tree(tmp_path):
    root = str(tmp_path)
    parent = os.path.dirname(root)
    home = os.path.expanduser("~")
    return {
        root: [("Documents", True), ("data", True), ("notes.txt", False),
               ("Program Files", True), ("alpha.txt", False), ("alt.txt", False)],
        os.path.join(root, "Documents"): [("report.txt", False)],
        parent: [("sibling", True)],
        home: [("Documents", True)],
    }


@pytest.fixture
def ctx(tmp_path, tree):
    return CompletionContext(
        cwd=str(tmp_path),
        case_sensitive=False,
        path_commands=("git", "python"),
        list_dir=lambda path: tree.get(os.path.normpath(path), []),
    )


class TestFirstWord:

    def test_directory_entry_completes_with_separator(self, ctx):
        result = complete("Doc", 3, ctx)
        assert result.start == 0
        assert result.candidates == ("Documents" + os.sep,)
        assert result.common_prefix == "Documents" + os.sep

    def test_path_commands_join_directory_entries(self, ctx):
        names = complete("", 0, ctx).candidates
        assert "git" in names
        assert "notes.txt" in names

    def test_prefix_filters(self, ctx):
        assert complete("gi", 2, ctx).candidates == ("git",)

    def test_empty_candidates(self, ctx):
        assert complete("zzz", 3, ctx).candidates == ()


class TestArgument:

    def test_cd_lists_directories(self, ctx):
        result = complete("cd Doc", 6, ctx)
        assert result.start == 3
        assert result.candidates == ("Documents" + os.sep,)

    def test_type_lists_files(self, ctx):
        result = complete("type no", 7, ctx)
        assert result.candidates == ("notes.txt",)

    def test_other_commands_list_both(self, ctx):
        names = complete("dir no", 6, ctx).candidates
        assert names == ("notes.txt",)


class TestCommonPrefix:

    def test_shared_prefix(self, ctx):
        result = complete("a", 1, ctx)
        assert set(result.candidates) == {"alpha.txt", "alt.txt"}
        assert result.common_prefix == "al"


class TestQuotes:

    def test_quoted_token_replaces_inside_quotes(self, ctx):
        result = complete('cd "Pro', 7, ctx)
        assert result.start == 4
        assert result.candidates == ("Program Files" + os.sep,)

    def test_space_adds_quotes_and_keeps_separator_outside(self):
        assert quote_candidate("Program Files" + os.sep) == '"Program Files"' + os.sep
        assert quote_candidate("notes.txt") == "notes.txt"
        assert quote_candidate("Program Files", quoted=True) == "Program Files"


class TestCase:

    def test_windows_is_case_insensitive(self, ctx):
        assert complete("doc", 3, ctx).candidates == ("Documents" + os.sep,)

    def test_posix_is_case_sensitive(self, ctx):
        ctx.case_sensitive = True
        assert complete("doc", 3, ctx).candidates == ()


class TestPathBases:

    def test_home_directory(self, ctx):
        result = complete("cd ~/Doc", 9, ctx)
        assert result.candidates == ("~/Documents" + os.sep,)

    def test_parent_directory(self, ctx):
        token = "cd .." + os.sep + "sib"
        result = complete(token, len(token), ctx)
        assert result.candidates == (".." + os.sep + "sibling" + os.sep,)

    def test_bare_dotdot_completes_to_separator(self, ctx):
        result = complete("cd ..", 5, ctx)
        assert result.candidates == (".." + os.sep,)

    def test_absolute_path(self, ctx, tmp_path):
        token = "cd " + str(tmp_path) + os.sep + "Doc"
        result = complete(token, len(token), ctx)
        assert result.candidates == (str(tmp_path) + os.sep + "Documents" + os.sep,)


class TestLimit:

    def test_candidates_are_truncated(self, tmp_path):
        entries = [(f"f{index}.txt", False) for index in range(600)]
        limited = CompletionContext(
            cwd=str(tmp_path),
            list_dir=lambda path: entries,
        )
        assert len(complete("f", 1, limited).candidates) == completion.MAX_CANDIDATES


@pytest.fixture
def local_ctx(tmp_path, tree):
    """冒号命令的补全上下文：命令名与参数候选由命令表提供。"""
    return CompletionContext(
        cwd=str(tmp_path),
        case_sensitive=True,
        path_commands=("git", "python"),
        list_dir=lambda path: tree.get(os.path.normpath(path), []),
        local_commands=("help", "check", "timer", "todo", "memory"),
        local_subcommands={"timer": ("list", "cancel"), "memory": ("search",)},
        local_path_commands=("check",),
    )


class TestLocalCommand:

    def test_prefix_lists_command_names_only(self, local_ctx):
        result = complete(":", 1, local_ctx)
        assert result.start == 0
        assert result.candidates == tuple(":" + name for name in local_ctx.local_commands)

    def test_local_candidates_are_words(self, local_ctx):
        """命令名与子命令是词，接受后补空格；路径类候选不补。"""
        assert complete(":", 1, local_ctx).append_space is True
        assert complete(":timer l", 8, local_ctx).append_space is True
        assert complete("Doc", 3, local_ctx).append_space is False

    def test_single_match_is_complete_word(self, local_ctx):
        result = complete(":he", 3, local_ctx)
        assert result.candidates == (":help",)
        assert result.append_space is True

    def test_name_match_ignores_case(self, local_ctx):
        assert complete(":HE", 3, local_ctx).candidates == (":help",)

    def test_system_commands_do_not_mix_in(self, local_ctx):
        names = complete(":", 1, local_ctx).candidates
        assert "git" not in names
        assert "notes.txt" not in names

    def test_subcommand_candidates(self, local_ctx):
        result = complete(":timer l", 8, local_ctx)
        assert result.start == 7
        assert result.candidates == ("list",)
        assert result.append_space is True

    def test_subcommand_prefix_filters(self, local_ctx):
        assert complete(":timer c", 8, local_ctx).candidates == ("cancel",)

    def test_command_without_arguments_has_no_candidates(self, local_ctx):
        assert complete(":help x", 6, local_ctx).candidates == ()

    def test_check_completes_programs_and_entries(self, local_ctx):
        assert "git" in complete(":check gi", 9, local_ctx).candidates
        assert (complete(":check Doc", 10, local_ctx).candidates
                == ("Documents" + os.sep,))
