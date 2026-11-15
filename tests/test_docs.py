"""文档一致性测试。

守住六件事：
1. `docs/reference/*.md` 与代码一致（漂移就红，并提示跑 scripts/gen_docs.py）
2. `docs/architecture.md` 覆盖全部包与顶层模块（新增包不能没有归属）
3. 文档里的相对链接都指向真实存在的文件
4. 每一页文档都在 `docs/README.md` 里被索引、每条 ADR 都在 `docs/decisions/README.md` 里登记
5. 仓库文档没有被 .gitignore 静默忽略
6. 手写文档的文风符合 `docs/README.md` 的「文风约定」（无人称、无破折号）

CHANGELOG 不做校验：最新 tag 之后的提交随时在变，放进测试会逼着每个提交都重新生成一次。
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
REFERENCE = DOCS / "reference"

# 需要校验相对链接的 Markdown：docs/ 下全部（含生成物）+ 根目录与 .github 的文档
LINK_CHECKED = (ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "CHANGELOG.md",
                ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md")

# 文风检查：只查手写文档（生成物由代码决定，改代码重新生成即可）
STYLE_CHECKED = (ROOT / "README.md", ROOT / "CONTRIBUTING.md",
                 ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md")

# 提示词章节名等被引用的术语，其中的第二人称按原文保留
SECOND_PERSON_TERMS = ("你惦记着的事", "你现在的状态", "你对用户的记忆", "你的人格")

# 「你」这种被引用为示例的字（含引号包裹）不算第二人称
SECOND_PERSON = re.compile(r"(?<![「『《])(你|您)(?![」』》])")
DASHES = ("—", "–")
_FENCE = re.compile(r"^```.*?^```", re.DOTALL | re.MULTILINE)


def test_reference_docs_match_code():
    """参考文档必须与代码一致。

    放在独立子进程里跑（和 CI 的同一条命令）：同进程内其他用例可能改过 config，
    而生成结果里含有随配置变化的数值（动作时长范围等）。
    """
    result = subprocess.run(
        [sys.executable, "scripts/gen_docs.py", "--check"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, (
        "参考文档与代码不一致：\n" + (result.stdout or "") + (result.stderr or "")
    )


def test_docs_index_lists_every_page():
    """每一页文档都要在 docs/README.md 里被索引到（ADR 由 decisions/README.md 索引）。"""
    index = (DOCS / "README.md").read_text(encoding="utf-8")
    missing = []
    for path in sorted(DOCS.rglob("*.md")):
        rel = path.relative_to(DOCS).as_posix()
        if rel == "README.md" or rel.startswith("decisions/"):
            continue
        if path.name not in index:
            missing.append(rel)
    assert not missing, "docs/README.md 索引缺失：" + "、".join(missing)


def test_decision_index_lists_every_adr():
    """每条 ADR 都要在 docs/decisions/README.md 的索引表里登记。"""
    index = (DOCS / "decisions" / "README.md").read_text(encoding="utf-8")
    missing = [
        path.name for path in sorted((DOCS / "decisions").glob("[0-9]*.md"))
        if path.name not in index
    ]
    assert not missing, "docs/decisions/README.md 索引缺失：" + "、".join(missing)


def test_architecture_describes_every_package_and_module():
    """新增包或顶层模块必须出现在架构文档里，否则文档会慢慢与代码脱节。"""
    text = (DOCS / "architecture.md").read_text(encoding="utf-8")

    packages = sorted(
        child.name
        for child in (ROOT / "pet").iterdir()
        if child.is_dir() and (child / "__init__.py").is_file()
    )
    modules = sorted(path.name for path in (ROOT / "pet").glob("*.py"))

    missing = [f"pet/{name}/" for name in packages if f"pet/{name}/" not in text]
    missing += [f"pet/{name}" for name in modules if name not in text]
    assert not missing, "docs/architecture.md 缺少这些包/模块的说明：" + "、".join(missing)


def test_doc_relative_links_resolve():
    """文档里的相对链接不能指向不存在的文件。"""
    pattern = re.compile(r"\]\(([^)]+)\)")
    broken = []
    for doc in sorted(DOCS.rglob("*.md")) + list(LINK_CHECKED):
        if not doc.is_file():
            continue
        for target in pattern.findall(doc.read_text(encoding="utf-8")):
            target = target.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "/")):
                continue
            if not (doc.parent / target).exists():
                broken.append(f"{doc.relative_to(ROOT).as_posix()} → {target}")
    assert not broken, "文档中存在失效链接：" + "、".join(broken)


def test_handwritten_docs_style():
    """手写文档的文风：无人称、破折号统一为「空格-连字符-空格」。

    只覆盖能机械判定的两条（约定见 docs/README.md 的「文风约定」），
    陈述句与标题名词化仍靠评审。
    """
    problems = []
    for path in sorted(DOCS.rglob("*.md")) + list(STYLE_CHECKED):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("docs/reference/"):
            continue
        # 行内代码与代码块里的破折号是内容本身（含本文档举的反例），不参与检查
        text = re.sub(r"`[^`]*`", "", _FENCE.sub("", path.read_text(encoding="utf-8")))
        for term in SECOND_PERSON_TERMS:
            text = text.replace(term, "")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if any(dash in line for dash in DASHES):
                problems.append(f"{rel}:{lineno} 破折号改用「空格-连字符-空格」：{line.strip()[:40]}")
            if SECOND_PERSON.search(line):
                problems.append(f"{rel}:{lineno} 第二人称改写为无人称陈述：{line.strip()[:40]}")
    assert not problems, "手写文档文风不符合约定：\n" + "\n".join(problems)


def test_docs_are_not_gitignored():
    """仓库文档必须纳入版本管理，不能被 .gitignore 静默忽略。"""
    if not (ROOT / ".git").exists():
        pytest.skip("不在 git 工作区中")

    candidates = sorted(DOCS.rglob("*.md")) + [ROOT / "CHANGELOG.md"]
    relatives = [path.relative_to(ROOT).as_posix() for path in candidates if path.is_file()]
    result = subprocess.run(
        ["git", "check-ignore", *relatives],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    ignored = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    assert not ignored, "以下文档被 .gitignore 忽略：" + "、".join(sorted(ignored))
