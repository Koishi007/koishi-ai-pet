"""拖入文件纯逻辑包的单元测试。

覆盖嗅探、解码链、截断与额度、体积与数量上限、拒绝名单、图片闸门、目录摘要。
样本文件一律在 tmp_path 现造，不依赖仓库素材。
"""

from pathlib import Path

from PIL import Image

from pet.file_intake import (
    check_drop,
    dir_summary,
    load_image,
    load_text,
    load_text_full,
    match_deny,
    sniff,
    truncate,
)

DENY = [".env*", "*.key", "*.pem", ".npmrc", "id_rsa*"]


def _file(tmp_path: Path, name: str, data: bytes) -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def _dir(tmp_path: Path, name: str, count: int) -> str:
    path = tmp_path / name
    path.mkdir()
    for i in range(count):
        (path / f"item{i:03d}.txt").write_text("x", encoding="utf-8")
    return str(path)


class TestCheckDrop:
    def test_too_many(self, tmp_path):
        paths = [_file(tmp_path, f"f{i}.txt", b"x") for i in range(3)]
        verdict = check_drop(paths, max_files=2, max_bytes=10 ** 6, deny_patterns=DENY)
        assert verdict.status == "too_many"
        assert verdict.refs == ()

    def test_too_large(self, tmp_path):
        path = _file(tmp_path, "big.txt", b"x" * 2048)
        verdict = check_drop([path], max_files=5, max_bytes=1024, deny_patterns=DENY)
        assert verdict.status == "too_large"
        assert "big.txt" in verdict.detail
        assert verdict.refs == ()

    def test_forbidden(self, tmp_path):
        path = _file(tmp_path, ".env", b"A=1")
        verdict = check_drop([path], max_files=5, max_bytes=10 ** 6, deny_patterns=DENY)
        assert verdict.status == "forbidden"
        assert ".env" in verdict.detail
        assert verdict.refs == ()

    def test_forbidden_by_dir_name(self, tmp_path):
        path = _dir(tmp_path, "secret.key", 1)
        verdict = check_drop([path], max_files=5, max_bytes=10 ** 6, deny_patterns=DENY)
        assert verdict.status == "forbidden"

    def test_ok_fills_kind_and_meta(self, tmp_path):
        text = _file(tmp_path, "note.md", "你好".encode("utf-8"))
        image = tmp_path / "pic.png"
        Image.new("RGB", (4, 4), (10, 20, 30)).save(image)
        folder = _dir(tmp_path, "folder", 2)

        verdict = check_drop(
            [text, str(image), folder], max_files=5, max_bytes=10 ** 6, deny_patterns=DENY
        )

        assert verdict.status == "ok"
        assert [ref.kind for ref in verdict.refs] == ["text", "image", "dir"]
        assert verdict.refs[0].name == "note.md"
        assert verdict.refs[0].suffix == ".md"
        assert verdict.refs[0].path == text
        assert verdict.refs[2].size == 0

    def test_directory_skips_size_gate(self, tmp_path):
        folder = _dir(tmp_path, "many", 3)
        verdict = check_drop([folder], max_files=5, max_bytes=1, deny_patterns=DENY)
        assert verdict.status == "ok"


class TestDenyPatterns:
    def test_basename_glob(self):
        assert match_deny(".env", DENY)
        assert match_deny(".env.local", DENY)
        assert match_deny("server.pem", DENY)
        assert match_deny("id_rsa", DENY)
        assert match_deny("id_rsa.pub", DENY)

    def test_case_insensitive_on_every_platform(self):
        assert match_deny(".ENV", DENY)
        assert match_deny("SERVER.PEM", DENY)

    def test_not_denied(self):
        assert not match_deny("note.pem.txt", DENY)
        assert not match_deny("environment.txt", DENY)
        assert not match_deny("mykey.key.txt", DENY)


class TestSniff:
    def test_utf8_text(self, tmp_path):
        assert sniff(_file(tmp_path, "a.md", "# 标题\n内容".encode("utf-8"))) == "text"

    def test_gbk_text(self, tmp_path):
        assert sniff(_file(tmp_path, "gbk.txt", "中文内容".encode("gbk"))) == "text"

    def test_utf16_le_text(self, tmp_path):
        raw = "中文内容".encode("utf-16")
        assert raw[:2] == b"\xff\xfe"
        assert sniff(_file(tmp_path, "u16.txt", raw)) == "text"

    def test_nul_bytes_are_binary(self, tmp_path):
        assert sniff(_file(tmp_path, "blob.dat", b"head\x00\x01\x02tail")) == "binary"

    def test_control_ratio_makes_binary(self, tmp_path):
        raw = b"\x01\x02\x03\x04" * 64 + b"abcd"
        assert sniff(_file(tmp_path, "ctrl.bin", raw)) == "binary"

    def test_image_by_suffix(self, tmp_path):
        path = tmp_path / "pic.jpg"
        Image.new("RGB", (4, 4), (1, 2, 3)).save(path)
        assert sniff(str(path)) == "image"

    def test_directory(self, tmp_path):
        assert sniff(_dir(tmp_path, "d", 1)) == "dir"

    def test_undecodable_is_binary(self, tmp_path):
        assert sniff(_file(tmp_path, "bad.bin", b"\xff\xff\xff")) == "binary"


class TestDecode:
    def test_reads_gbk(self, tmp_path):
        text, reason = load_text(_file(tmp_path, "gbk.txt", "中文内容".encode("gbk")), 1500)
        assert text == "中文内容"
        assert reason == ""

    def test_reads_utf16(self, tmp_path):
        text, _ = load_text(_file(tmp_path, "u16.txt", "中文内容".encode("utf-16")), 1500)
        assert text == "中文内容"

    def test_reads_utf8_chinese(self, tmp_path):
        text, _ = load_text(_file(tmp_path, "u8.txt", "中文内容".encode("utf-8")), 1500)
        assert text == "中文内容"

    def test_gbk_bytes_that_look_like_utf8(self, tmp_path):
        # 「一」的 GBK 字节 d2bb 是合法的 UTF-8 两字节序列（解成西里尔字母 һ），
        # 先到先得会静默返回乱码，按合理性打分才取回中文
        raw = "一一一".encode("gbk")
        assert raw.decode("utf-8") == "һһһ"
        text, reason = load_text(_file(tmp_path, "tricky.txt", raw), 1500)
        assert text == "一一一"
        assert reason == ""

    def test_full_read_ignores_limit(self, tmp_path):
        body = "中文内容" * 1000
        path = _file(tmp_path, "long.txt", body.encode("utf-8"))
        assert len(load_text_full(path)) == len(body)

    def test_binary_reports_reason(self, tmp_path):
        text, reason = load_text(_file(tmp_path, "bad.bin", b"\xff\xff\xff"), 1500)
        assert text == ""
        assert reason

    def test_directory_reports_reason(self, tmp_path):
        text, reason = load_text(_dir(tmp_path, "d", 1), 1500)
        assert text == ""
        assert reason

    def test_full_read_of_binary_is_empty(self, tmp_path):
        assert load_text_full(_file(tmp_path, "bad.bin", b"\xff\xff\xff")) == ""


class TestTruncate:
    def test_short_text_untouched(self):
        text = "a" * 1500
        assert truncate(text, 1500) == text

    def test_long_text_head_tail(self):
        text = "".join(str(i % 10) for i in range(3000))
        result = truncate(text, 1500)
        assert result.startswith(text[:1000])
        assert result.endswith(text[-500:])
        assert "（已省略 1500 字符）" in result
        assert text[:1000] + result[len(text[:1000]):-len(text[-500:])] + text[-500:] == result

    def test_marker_not_counted_in_quota(self):
        text = "b" * 2000
        result = truncate(text, 1500)
        assert result.count("b") == 1500

    def test_disabled_limit(self):
        text = "c" * 100
        assert truncate(text, 0) == text


class TestQuota:
    def test_each_file_gets_full_quota(self, tmp_path):
        body = "".join(str(i % 10) for i in range(2000))
        first = _file(tmp_path, "one.txt", body.encode("utf-8"))
        second = _file(tmp_path, "two.txt", ("z" + body).encode("utf-8"))

        verdict = check_drop([first, second], max_files=5, max_bytes=10 ** 6, deny_patterns=DENY)
        assert verdict.status == "ok"

        texts = [load_text(ref.path, 1500)[0] for ref in verdict.refs]
        assert all(len(text) > 1500 for text in texts)
        assert texts[0] != texts[1]


class TestImageGate:
    def test_gate_rejects_over_limit(self, tmp_path):
        path = tmp_path / "small.png"
        Image.new("RGB", (20, 20), (5, 5, 5)).save(path)
        assert load_image(str(path), 100) is None

    def test_gate_uses_size_after_draft(self, tmp_path):
        # 4000×4000 = 16 MP，draft 到 (1024, 1024) 之后是 2000×2000 = 4 MP。
        # 闸门取 8 MP 时通过，说明判的不是原始像素数；取 3 MP 时被拒。
        path = tmp_path / "large.jpg"
        Image.new("RGB", (4000, 4000), (200, 180, 160)).save(path, format="JPEG")
        assert load_image(str(path), 8_000_000) is not None
        assert load_image(str(path), 3_000_000) is None

    def test_scales_down_to_1024(self, tmp_path):
        path = tmp_path / "wide.png"
        Image.new("RGB", (2400, 1200), (30, 60, 90)).save(path)
        image = load_image(str(path), 10 ** 9)
        assert image is not None
        assert max(image.size) <= 1024

    def test_broken_file_returns_none(self, tmp_path):
        assert load_image(_file(tmp_path, "broken.png", b"\x89PNG\r\n\x1a\n"), 10 ** 9) is None

    def test_directory_returns_none(self, tmp_path):
        assert load_image(_dir(tmp_path, "d", 1), 10 ** 9) is None


class TestDirMeta:
    def test_counts_and_limits_names(self, tmp_path):
        count, names = dir_summary(_dir(tmp_path, "d", 25))
        assert count == 25
        assert len(names) == 20

    def test_count_cap(self, tmp_path):
        count, names = dir_summary(_dir(tmp_path, "big", 250))
        assert count == 200
        assert len(names) == 20

    def test_empty_directory(self, tmp_path):
        count, names = dir_summary(_dir(tmp_path, "empty", 0))
        assert count == 0
        assert names == []
