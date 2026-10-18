"""纯逻辑函数测试：无 IO、无 Qt 依赖。"""

import re
import subprocess
import sys

import pytest

from pet.config import _convert
from pet.game.tic_tac_toe import _board_text, _check_winner, _is_full
from pet.tools.executor import ToolExecutor, _MAX_LOG_VALUE_LEN, _log_preview
from pet.tools.knowledge.chunker import chunk_text
from pet.tools.timer.core import TimerTool
from pet.version_utils import strip_v, ver_newer


class TestChunkText:
    def test_empty_or_blank_returns_empty(self):
        assert chunk_text("") == []
        assert chunk_text("   \n\n  ") == []

    def test_short_fragment_filtered(self):
        assert chunk_text("太短") == []

    def test_short_text_single_chunk(self):
        assert chunk_text("这是一段足够长的文本内容") == ["这是一段足够长的文本内容"]

    def test_overlap_clamped_below_max_chars(self):
        # overlap >= max_chars 时会被夹取，保证切分步长大于 0
        chunks = chunk_text("甲" * 250, max_chars=100, overlap=100)
        assert len(chunks) > 1
        assert all(len(c) <= 100 for c in chunks)

    def test_long_paragraph_hard_split(self):
        chunks = chunk_text("乙" * 1000, max_chars=300, overlap=0)
        assert len(chunks) > 1
        assert all(len(c) <= 300 for c in chunks)

    def test_paragraphs_merged_until_limit(self):
        text = "\n\n".join(["段落内容足够长以便测试合并行为"] * 6)
        chunks = chunk_text(text, max_chars=60, overlap=0)
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.strip()


class TestVersionCompare:
    def test_strip_single_v_prefix(self):
        assert strip_v("v1.5.4") == "1.5.4"
        assert strip_v("V1.5.4") == "1.5.4"
        assert strip_v("1.5.4") == "1.5.4"
        assert strip_v("vv1") == "v1"

    def test_numeric_order_not_lexical(self):
        assert ver_newer("1.5.10", "1.5.9")
        assert not ver_newer("1.5.9", "1.5.10")

    def test_equal_version_is_not_newer(self):
        assert not ver_newer("1.5.4", "1.5.4")

    def test_invalid_version_returns_false(self):
        assert not ver_newer("not-a-version", "1.5.4")
        assert not ver_newer("1.5.4", "not-a-version")


class TestLightweightImports:
    """轻量环境（无 PySide6）也必须能导入这些纯逻辑模块。"""

    @pytest.mark.parametrize("module", [
        "pet.version_utils",
        "pet.action.registry",
        "pet.tools.knowledge.chunker",
    ])
    def test_no_qt_dependency(self, module):
        code = (
            # 子进程不加载 conftest，需自行顶替崩溃钩子，避免改写 logs/startup.state
            "import sys, types\n"
            "stub = types.ModuleType('pet.crash_reporter')\n"
            "stub.install = lambda: None\n"
            "sys.modules.setdefault('pet.crash_reporter', stub)\n"
            "import importlib\n"
            f"importlib.import_module({module!r})\n"
            "assert 'PySide6' not in sys.modules, sorted(\n"
            "    m for m in sys.modules if m.startswith('PySide6'))\n"
        )
        try:
            proc = subprocess.run([sys.executable, "-c", code],
                                  capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            pytest.fail(f"模块 {module} 导入超时（60s）")
        assert proc.returncode == 0, proc.stderr


class TestTicTacToe:
    def test_row_column_diagonal_win(self):
        assert _check_winner([["X", "X", "X"], ["O", "O", ""], ["", "", ""]]) == "X"
        assert _check_winner([["O", "X", ""], ["O", "X", ""], ["", "X", ""]]) == "X"
        assert _check_winner([["X", "O", ""], ["O", "X", ""], ["", "", "X"]]) == "X"
        assert _check_winner([["", "O", "X"], ["O", "X", ""], ["X", "", ""]]) == "X"

    def test_no_winner_on_empty_or_mixed_board(self):
        assert _check_winner([["", "", ""], ["", "", ""], ["", "", ""]]) is None
        assert _check_winner([["X", "O", "X"], ["X", "O", "O"], ["O", "X", ""]]) is None

    def test_board_full(self):
        assert _is_full([["X", "O", "X"], ["X", "O", "O"], ["O", "X", "X"]])
        assert not _is_full([["X", "O", "X"], ["X", "O", "O"], ["O", "X", ""]])

    def test_board_text_uses_dots_for_empty(self):
        text = _board_text([["X", "", "O"], ["", "X", ""], ["", "", "O"]])
        assert text.splitlines()[0].strip() == "1 2 3"
        assert "A X . O" in text


class TestValidateArgs:
    SCHEMA = {
        "count": {"type": "int", "required": True},
        "mode": {"type": "str", "enum": ["fast", "slow"], "default": "fast"},
        "flag": {"type": "bool", "default": False},
    }

    def test_type_mismatch_rejected(self):
        args, err = ToolExecutor._validate_args({"count": "5"}, self.SCHEMA)
        assert args == {}
        assert "count" in err

    def test_missing_required_rejected(self):
        _, err = ToolExecutor._validate_args({}, self.SCHEMA)
        assert "count" in err

    def test_enum_out_of_range_rejected(self):
        _, err = ToolExecutor._validate_args({"count": 1, "mode": "turbo"}, self.SCHEMA)
        assert "mode" in err

    def test_defaults_and_extra_keys(self):
        args, err = ToolExecutor._validate_args({"count": 3, "aside": "看看"}, self.SCHEMA)
        assert err == ""
        assert args == {"count": 3, "mode": "fast", "flag": False, "aside": "看看"}

    def test_explicit_value_beats_default(self):
        args, err = ToolExecutor._validate_args({"count": 1, "mode": "slow"}, self.SCHEMA)
        assert err == ""
        assert args["mode"] == "slow"


class TestLogPreview:
    def test_long_value_truncated_with_marker(self):
        value = "x" * 400
        preview = _log_preview({"data": value})
        assert preview.count("x") == _MAX_LOG_VALUE_LEN
        assert str(len(value) - _MAX_LOG_VALUE_LEN) in preview

    def test_short_value_untouched(self):
        assert _log_preview({"ok": 1}) == "{ ok=1 }"

    def test_non_dict_uses_str(self):
        assert _log_preview([1, 2]) == "[1, 2]"


class TestFormatDuration:
    def test_under_one_minute(self):
        assert re.findall(r"\d+", TimerTool._format_duration(30)) == ["30"]

    def test_minute_scales(self):
        assert re.findall(r"\d+", TimerTool._format_duration(60)) == ["1"]
        assert re.findall(r"\d+", TimerTool._format_duration(90)) == ["1", "30"]
        assert re.findall(r"\d+", TimerTool._format_duration(600)) == ["10"]

    def test_hour_scales(self):
        assert re.findall(r"\d+", TimerTool._format_duration(3600)) == ["1"]
        assert re.findall(r"\d+", TimerTool._format_duration(3661)) == ["1", "1", "1"]
        assert re.findall(r"\d+", TimerTool._format_duration(7320)) == ["2", "2"]


class TestConvertSettingValues:
    def test_bool_variants(self):
        assert _convert("1", "bool") is True
        assert _convert("TRUE", "bool") is True
        assert _convert("yes", "bool") is True
        assert _convert("0", "bool") is False
        assert _convert(False, "bool") is False

    def test_number_conversion(self):
        assert _convert("5", "int") == 5
        assert _convert("2.5", "float") == 2.5

    def test_str_list_splits_and_strips(self):
        assert _convert("a, b ,c", "str_list") == ["a", "b", "c"]
        assert _convert("", "str_list") == []
        assert _convert(["x"], "str_list") == ["x"]
