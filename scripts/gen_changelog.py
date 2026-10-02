#!/usr/bin/env python3
"""从 git 历史生成 CHANGELOG.md（按 Conventional Commits 前缀分组）。

用法：
    python scripts/gen_changelog.py                 # 默认从 v1.5.0 起生成
    python scripts/gen_changelog.py --since v1.4.0  # 换起点 tag
    python scripts/gen_changelog.py --check         # 只校验，不写入（不一致时退出码 1）

为什么**不接进 CI**：最新 tag 之后的提交随时在变，待发布小节每次提交都会不同，
放进 CI 会逼着每个 PR 重新生成一遍变更记录。正确用法是发布时刷新，见 docs/operations/release.md。

待发布那一节用 `pyproject.toml` 的 `version` 命名（版本号是唯一真源）：版本还没打 tag 时
渲染成 `## vX.Y.Z — 日期`，这样 GitHub 按 tag 生成的源码包里，CHANGELOG 也带着当前版本的小节；
版本号已存在同名 tag 时恢复成「未发布」。

更早的历史（默认起点之前）没有规范化的提交信息，不回溯——那些版本的说明见 GitHub Releases。
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
DEFAULT_SINCE = "v1.5.0"
DEFAULT_LINE_CAP = 40          # 单个类型下最多列出多少条，超出折成一行
SEP = "\x1f"

SECTIONS = (
    ("feat", "新功能"),
    ("fix", "修复"),
    ("perf", "性能"),
    ("tune", "调优"),
    ("refactor", "重构"),
    ("style", "样式"),
    ("docs", "文档"),
    ("test", "测试"),
    ("chore", "杂项"),
)
OTHER = ("other", "其他")

_SUBJECT = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?!?:\s*(?P<desc>.+)$")


def _git(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", "--no-pager", *args], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _tags() -> list[str]:
    return _git("tag", "--sort=creatordate")


def _pending_version() -> str:
    """`pyproject.toml` 里的版本号（`vX.Y.Z`），即下一个待发布的版本。"""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"^\s*version\s*=\s*[\"']([^\"']+)", text, re.IGNORECASE | re.MULTILINE)
    if not match:
        raise SystemExit("pyproject.toml 里找不到 version")
    return "v" + match.group(1).lstrip("vV")


def _commits(rev_range: str) -> list[tuple[str, str]]:
    """返回 [(短 hash, 提交主题)]，跳过 merge 提交。"""
    items = []
    for line in _git("log", f"--format=%h{SEP}%s", rev_range):
        if SEP not in line:
            continue
        short_hash, subject = line.split(SEP, 1)
        if subject.startswith("Merge "):
            continue
        items.append((short_hash, subject))
    return items


def _classify(subject: str) -> tuple[str, str, str]:
    match = _SUBJECT.match(subject)
    if not match:
        return "other", "", subject
    return match.group("type"), match.group("scope") or "", match.group("desc")


def _render_section(title: str, commits: list[tuple[str, str]], line_cap: int) -> list[str]:
    lines = [f"## {title}", ""]
    if not commits:
        return lines + ["_（暂无）_", ""]

    grouped: dict[str, list[tuple[str, str, str]]] = {}
    for short_hash, subject in commits:
        type_, scope, desc = _classify(subject)
        grouped.setdefault(type_, []).append((scope, desc, short_hash))

    for key, label in SECTIONS + (OTHER,):
        items = grouped.get(key)
        if not items:
            continue
        lines.append(f"**{label}**")
        for scope, desc, short_hash in items[:line_cap]:
            prefix = f"**{scope}**: " if scope else ""
            lines.append(f"- {prefix}{desc}（{short_hash}）")
        if len(items) > line_cap:
            lines.append(f"- …另有 {len(items) - line_cap} 条同类改动")
        lines.append("")
    return lines


def render(since: str, line_cap: int) -> str:
    tags = _tags()
    if since not in tags:
        raise SystemExit(f"找不到起点 tag：{since}（可用 tag 见 git tag）")

    index = tags.index(since)
    selected = tags[index:]           # 含起点，按时间升序
    newest = selected[-1]

    lines = [
        "# 变更记录",
        "",
        "本文件由 `scripts/gen_changelog.py` 从 git 历史生成（脚本只在源码仓库里），",
        "按 [Conventional Commits](https://github.com/Koishi007/koishi-ai-pet/blob/master/CONTRIBUTING.md) 前缀分组；",
        f"起点 tag 为 `{since}`，更早的历史没有规范化的提交信息、未回溯（见 GitHub Releases）。",
        "",
        "发布前刷新一次：`python scripts/gen_changelog.py`，流程见",
        "[docs/operations/release.md](https://github.com/Koishi007/koishi-ai-pet/blob/master/docs/operations/release.md)。",
        "",
    ]
    # 版本号已打过 tag 就还是「未发布」；否则提前挂上版本号，让发布包里的
    # CHANGELOG 也带着当前版本的小节
    pending = _pending_version()
    pending_title = "未发布" if pending in tags else \
        f"{pending} — {_git('log', '-1', '--format=%cs', 'HEAD')[0]}"
    lines += _render_section(pending_title, _commits(f"{newest}..HEAD"), line_cap)

    for position in range(len(selected) - 1, -1, -1):
        tag = selected[position]
        prev_index = index + position - 1
        previous = tags[prev_index] if prev_index >= 0 else None
        date = _git("log", "-1", "--format=%cs", tag)[0]
        lines += _render_section(
            f"{tag} — {date}", _commits(f"{previous}..{tag}" if previous else tag), line_cap
        )
    return "\n".join(lines).rstrip() + "\n"


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="从 git 历史生成 CHANGELOG.md")
    parser.add_argument("--since", default=DEFAULT_SINCE, help=f"起点 tag（默认 {DEFAULT_SINCE}）")
    parser.add_argument("--line-cap", type=int, default=DEFAULT_LINE_CAP,
                        help=f"每个类型最多列出的条数（默认 {DEFAULT_LINE_CAP}）")
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args(argv)

    content = render(args.since, args.line_cap)
    if args.check:
        current = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.is_file() else None
        if current != content:
            print("CHANGELOG.md 与 git 历史不一致，请运行 python scripts/gen_changelog.py")
            return 1
        print("CHANGELOG.md 与 git 历史一致")
        return 0

    CHANGELOG.write_text(content, encoding="utf-8")
    print(f"写入 CHANGELOG.md（{len(content.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
