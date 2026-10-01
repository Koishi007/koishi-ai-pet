"""架构契约测试：把 docs/architecture.md §11/§14 的结构红线变成可执行的 pytest。

只做 AST 与文件系统扫描：不 import 任何 pet 模块，不需要 Qt、数据库、配置与网络。
规则定义与失败格式见 docs/specs/2026-09-30-agent-oriented-architecture-feedback-design.md。
allowlist 是待清偿的债，不是白名单：还清一笔就删条目，忘了删由 ARCH000 报出。
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PET = ROOT / "pet"

ARCH_DOC = "docs/architecture.md"
DESIGN_DOC = "docs/specs/2026-09-30-agent-oriented-architecture-feedback-design.md"
ADR_LAYERING = "docs/decisions/0009-layering-by-singletons-and-deferred-imports.md"
ADR_TIMEOUT = "docs/decisions/0007-action-timeout-settles.md"
TOOL_DOC = "docs/tool-development.md"
ASSET_DOC = "docs/subsystems/assets-pipeline.md"


# --------------------------------------------------------------------------
# 输出结构
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ContractViolation:
    """一条结构违规，字段顺序即报告顺序。key 为 allowlist 匹配键，None 表示不可豁免。"""

    rule: str
    title: str
    file: str
    line: int
    evidence: str
    violation: str
    why: str
    fix: str
    reference: str
    example: str = ""
    key: object = None

    def render(self) -> str:
        lines = [
            f"ERROR [{self.rule}]: {self.title}",
            f"- FILE: {self.file}:{self.line}",
            f"- EVIDENCE: {self.evidence}",
            f"- VIOLATION: {self.violation}",
            f"- WHY: {self.why}",
            f"- FIX: {self.fix}",
        ]
        if self.example:
            lines.append(f"- EXAMPLE: {self.example}")
        lines.append(f"- REFERENCE: {self.reference}")
        return "\n".join(lines)


# allowlist 匹配键：多数规则是 (文件, 符号) 元组，ARCH006 是 (文件, 接收者, 私有成员)，
# ARCH008 的环是模块名 frozenset
DebtKey = tuple | frozenset


@dataclass(frozen=True)
class Debt:
    """allowlist 条目：为什么现在还不能修 + 依据在哪 + 跟踪 issue。"""

    key: DebtKey
    reason: str
    reference: str
    issue: int | None = None


def _sym(rel: str, receiver: str, symbols: list[str], reason: str, reference: str,
         issue: int | None = None) -> list[Debt]:
    """同一接收者上一批同类私有成员共用一条原因；ARCH006 的键是 (文件, 接收者, 私有成员)。"""
    return [Debt((rel, receiver, symbol), reason, reference, issue) for symbol in symbols]


class Ledger:
    """按规则分组的 allowlist：命中即放行并记账，未命中的条目由 ARCH000 报出。"""

    def __init__(self, allowlist: dict[str, list[Debt]]):
        self.by_rule = {rule: {d.key: d for d in entries} for rule, entries in allowlist.items()}
        self.hit: set[tuple[str, DebtKey]] = set()

    def exempt(self, rule: str, key) -> bool:
        if key in self.by_rule.get(rule, {}):
            self.hit.add((rule, key))
            return True
        return False

    def stale(self):
        for rule, entries in self.by_rule.items():
            for key, debt in entries.items():
                if (rule, key) not in self.hit:
                    yield rule, debt


# --------------------------------------------------------------------------
# 扫描基础设施
# --------------------------------------------------------------------------

def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


# 模块级会执行的容器语句：其中的语句在导入期运行，需要展开检查
_MODULE_CONTAINERS = (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)


def _module_statements(tree: ast.Module):
    """导入期执行的语句：穿过 if/try/with/for 容器，不进函数与类体。

    反向入栈使产出顺序与源码一致，报告里的「首次命中」才符合阅读直觉。
    """
    stack = list(reversed(tree.body))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.If):
            stack.extend(reversed(node.body))
            stack.extend(reversed(node.orelse))
        elif isinstance(node, ast.Try):
            stack.extend(reversed(node.body))
            for handler in node.handlers:
                stack.extend(reversed(handler.body))
            stack.extend(reversed(node.orelse))
            stack.extend(reversed(node.finalbody))
        elif isinstance(node, _MODULE_CONTAINERS):
            stack.extend(reversed(node.body))


class Scanner:
    """一次性读出 pet/ 全部源码与语法树，供各规则共用。"""

    def __init__(self, root: Path):
        self.paths = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
        self.tree: dict[Path, ast.Module] = {}
        self.module: dict[Path, str] = {}
        for path in self.paths:
            raw = path.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):  # BOM 会让 ast.parse 报错
                raw = raw[3:]
            self.tree[path] = ast.parse(raw.decode("utf-8"), filename=str(path))
            self.module[path] = _module_name(path)
        self.path_of = {module: path for path, module in self.module.items()}
        self.packages = {self.module[p] for p in self.paths if p.name == "__init__.py"}
        self._import_cache: dict[Path, list[ast.stmt]] = {}
        self._edges: dict[str, set[str]] | None = None
        self._heavy: dict[str, bool] = {}

    def rel(self, path: Path) -> str:
        return path.relative_to(ROOT).as_posix()

    def is_pet_module(self, dotted: str) -> bool:
        """dotted 是否对应磁盘上的真实模块，用于区分子模块与同名符号。"""
        if not dotted.startswith("pet"):
            return False
        base = PET.joinpath(*dotted.split(".")[1:])
        return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()

    def module_imports(self, path: Path) -> list[ast.stmt]:
        """导入期执行的 import 语句（含 if/try 容器内的）。"""
        if path not in self._import_cache:
            self._import_cache[path] = [n for n in _module_statements(self.tree[path])
                                        if isinstance(n, (ast.Import, ast.ImportFrom))]
        return self._import_cache[path]

    def deferred_imports(self, path: Path):
        """函数体内的 import 语句：(所在函数名, 节点)。"""
        seen: set[int] = set()
        for func in ast.walk(self.tree[path]):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in seen:
                    seen.add(id(node))
                    yield func.name, node

    def absolute_target(self, node: ast.ImportFrom, current: str) -> str:
        """ImportFrom 的目标模块绝对名；相对 import 按所在包层级换算。"""
        if not node.level:
            return node.module or ""
        parts = current.split(".")
        base = parts if current in self.packages else parts[:-1]
        for _ in range(node.level - 1):
            base = base[:-1]
        if node.module:
            base = base + node.module.split(".")
        return ".".join(base)

    def import_targets(self, path: Path, node: ast.Import | ast.ImportFrom) -> list[str]:
        """一条 import 牵涉的 pet 模块：ImportFrom 含 from 模块与别名子模块。"""
        current = self.module[path]
        out = []
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                for i in range(1, len(parts) + 1):  # import a.b.c 会执行沿途每个包
                    prefix = ".".join(parts[:i])
                    if self.is_pet_module(prefix):
                        out.append(prefix)
        else:
            base = self.absolute_target(node, current)
            if base.startswith("pet") and self.is_pet_module(base):
                out.append(base)
            for alias in node.names:
                if alias.name == "*":
                    continue
                sub = f"{base}.{alias.name}"
                if self.is_pet_module(sub):
                    out.append(sub)
        return out

    def edges(self) -> dict[str, set[str]]:
        """模块级 import 依赖图：模块 -> 它导入的 pet 模块集合。"""
        if self._edges is None:
            graph: dict[str, set[str]] = {}
            for path in self.paths:
                own = self.module[path]
                targets = set()
                for node in self.module_imports(path):
                    targets.update(self.import_targets(path, node))
                targets.discard(own)
                graph[own] = targets
            self._edges = graph
        return self._edges

    def reaches(self, start: str, goal: str) -> bool:
        """start 能否沿模块级 import 图到达 goal。"""
        if start == goal:
            return True
        graph = self.edges()
        seen = {start}
        stack = [start]
        while stack:
            for nxt in graph.get(stack.pop(), ()):
                if nxt == goal:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    def drags_heavy(self, module: str, _visiting: frozenset = frozenset()) -> bool:
        """导入该模块是否会连带加载重依赖（Qt / 平台后端 / playwright）。"""
        if module in self._heavy:
            return self._heavy[module]
        if module in _visiting:
            return False
        path = self.path_of.get(module)
        if path is None:
            return False
        result = False
        for node in self.module_imports(path):
            if any(root in HEAVY_IMPORTS for root in _roots_of(node)):
                result = True
                break
        if not result:
            visiting = _visiting | {module}
            for node in self.module_imports(path):
                if any(self.drags_heavy(t, visiting) for t in self.import_targets(path, node)):
                    result = True
                    break
        self._heavy[module] = result
        return result

    def cycles(self) -> list[tuple[str, frozenset[str]]]:
        """各包内部模块级 import 环：返回 (包名, 强连通分量)。"""
        graph = self.edges()
        found = []
        for package in sorted(self.packages):
            members = {m for m in graph
                       if m == package or m.startswith(package + ".")
                       and m.count(".") == package.count(".") + 1}
            members.add(package)
            induced = {m: {t for t in graph.get(m, ()) if t in members} for m in members}
            for scc in _tarjan(induced):
                if len(scc) > 1:
                    found.append((package, scc))
        return found


def _tarjan(graph: dict[str, set[str]]) -> list[frozenset[str]]:
    """强连通分量（迭代式 Tarjan，避免深递归）。"""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[frozenset[str]] = []
    counter = 0

    for root in sorted(graph):
        if root in index:
            continue
        work = [(root, iter(sorted(graph[root])))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in graph:
                    continue
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(graph[nxt]))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                result.append(frozenset(component))
    return result


def _roots_of(node: ast.Import | ast.ImportFrom) -> set[str]:
    """一条 import 牵涉的顶层包名。"""
    if isinstance(node, ast.Import):
        return {alias.name.split(".")[0] for alias in node.names}
    if node.module:
        return {node.module.split(".")[0]}
    return set()


SCANNER = Scanner(PET)


# --------------------------------------------------------------------------
# 规则
# --------------------------------------------------------------------------

ASSEMBLER = "pet/app.py"

HEAVY_IMPORTS = {
    "PySide6", "playwright",
    "win32gui", "win32con", "win32process",
    "Quartz", "AppKit", "Xlib",
}

WINDOW_DETECTOR_API = {"is_window_alive", "get_window_rect", "is_window_occluded", "get_visible_windows"}
DETECTORS = ("win_detector.py", "mac_detector.py", "linux_detector.py")


@dataclass(frozen=True)
class _Sig:
    """调用兼容的最小签名形态：位置参数个数、默认值个数、*args、仅关键字参数、**kwargs、异步。"""

    pos: int
    defaults: int
    vararg: bool
    kwonly: int
    kwarg: bool
    async_: bool

    @classmethod
    def of(cls, func: ast.FunctionDef | ast.AsyncFunctionDef) -> _Sig:
        args = func.args
        return cls(
            pos=len(args.posonlyargs) + len(args.args),
            defaults=len(args.defaults),
            vararg=args.vararg is not None,
            kwonly=len(args.kwonlyargs),
            kwarg=args.kwarg is not None,
            async_=isinstance(func, ast.AsyncFunctionDef),
        )

    def render(self) -> str:
        parts = [f"{self.pos} 个位置参数"]
        if self.defaults:
            parts.append(f"{self.defaults} 个默认值")
        if self.vararg:
            parts.append("*args")
        if self.kwonly:
            parts.append(f"{self.kwonly} 个仅关键字参数")
        if self.kwarg:
            parts.append("**kwargs")
        parts.append("异步" if self.async_ else "同步")
        return "、".join(parts)


# 探测契约的期望签名：名字对了、调用形态漂移同样会让对应平台 TypeError
WINDOW_DETECTOR_SIGNATURES = {
    "is_window_alive": _Sig(1, 0, False, 0, False, False),
    "get_window_rect": _Sig(1, 0, False, 0, False, False),
    "is_window_occluded": _Sig(3, 2, False, 0, False, False),
    "get_visible_windows": _Sig(0, 0, False, 0, False, False),
}


def rule_arch001(scanner: Scanner):
    """§11.13：除 ui / agent 自身与装配入口 pet/app.py 外，谁都不许 import 它们。"""
    for path in scanner.paths:
        rel = scanner.rel(path)
        own = scanner.module[path]
        if rel == ASSEMBLER or own.startswith("pet.ui") or own.startswith("pet.agent"):
            continue
        nodes = list(scanner.module_imports(path))
        if own == "pet.tools.context":
            nodes += [n for _, n in scanner.deferred_imports(path)]  # TOOL_CTX 连延迟依赖都不许有
        for node in nodes:
            for target in scanner.import_targets(path, node):
                if target == own:
                    continue
                if own == "pet.tools.context":
                    yield ContractViolation(
                        rule="ARCH001", title="TOOL_CTX must stay dependency-free",
                        file=rel, line=node.lineno, evidence=f"import {target}",
                        violation="pet.tools.context 不得 import 任何其他 pet 模块。",
                        why="各层都要 import TOOL_CTX，引入依赖会闭合出跨层环。",
                        fix="只保留标准库与本地定义的 Callable。",
                        reference=f"{ARCH_DOC} §11.13; {ADR_LAYERING}",
                        key=(rel, target),
                    )
                elif target.startswith("pet.ui") or target.startswith("pet.agent"):
                    yield ContractViolation(
                        rule="ARCH001", title="Layer violation",
                        file=rel, line=node.lineno, evidence=f"import {target}",
                        violation="ui / agent 是上层，其余包不得顶层 import 它们。",
                        why="下层模块要能脱离运行中的桌宠单独加载与测试。",
                        fix="驱动桌宠改走 TOOL_CTX 或注入回调，在 pet/app.py 装配真实实现。",
                        example="pet/tools/timer 用 TOOL_CTX.register_alarm 驱动提醒",
                        reference=f"{ARCH_DOC} §11.13; {ADR_LAYERING}",
                        key=(rel, target),
                    )


def rule_arch002(scanner: Scanner):
    """§11.15：函数内延迟 import 只允许打断环或推迟重依赖。"""
    for path in scanner.paths:
        rel = scanner.rel(path)
        own = scanner.module[path]
        top_modules = set()
        for node in scanner.module_imports(path):
            top_modules.update(scanner.import_targets(path, node))
        for func, node in scanner.deferred_imports(path):
            if isinstance(node, ast.Import):
                base = node.names[0].name
            elif node.level:
                base = scanner.absolute_target(node, own)
            else:
                base = node.module or ""
            targets = [t for t in scanner.import_targets(path, node) if t != own]
            if not any(t.startswith("pet") for t in targets):
                continue  # 标准库与第三方依赖不归本规则管
            if base != own and base in top_modules:
                kind = "文件头已经导入过同一模块"
            elif any(scanner.reaches(t, own) for t in targets):
                continue  # 打断循环依赖
            elif any(scanner.drags_heavy(t) for t in targets):
                continue  # 推迟重依赖
            else:
                kind = "既不打断环也不推迟重依赖"
            yield ContractViolation(
                rule="ARCH002", title="Unjustified deferred import",
                file=rel, line=node.lineno,
                evidence=f"inside {func}(): {ast.unparse(node)}",
                violation=f"函数内延迟 import 没有理由：{kind}。",
                why="无理由的延迟让依赖方向看不出来，也拖慢首次调用。",
                fix="移到文件头；跨层调用改走模块级单例或回调。",
                example="pet/brain/context_builder.py 的环靠延迟 import 规避是登记过的例外",
                reference=f"{ARCH_DOC} §11.15; {ARCH_DOC} §14",
                key=(rel, base),
            )


def rule_arch003(scanner: Scanner):
    """§11.8：工具目录契约 TOOL_NAME / TOOL_DESCRIPTION / register(registry)。"""
    tools_dir = PET / "tools"
    tracked = _git_tracked("pet/tools")
    for pkg in sorted(d for d in tools_dir.iterdir() if (d / "__init__.py").is_file()):
        init = pkg / "__init__.py"
        rel = scanner.rel(init)
        assigned, funcs = {}, {}
        for node in _module_statements(scanner.tree[init]):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned[target.id] = node
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                    and node.value is not None:
                assigned[node.target.id] = node
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs[node.name] = node
        missing = [n for n in ("TOOL_NAME", "TOOL_DESCRIPTION") if n not in assigned]
        if "register" not in funcs:
            missing.append("register(registry)")
        if missing:
            yield ContractViolation(
                rule="ARCH003", title="Invalid tool package contract",
                file=rel, line=1, evidence=f"missing: {', '.join(missing)}",
                violation="每个工具包必须在 __init__.py 定义 TOOL_NAME、TOOL_DESCRIPTION 与 register(registry)。",
                why="加载器与文档生成器都按这三个入口识别工具。",
                fix="按 docs/tool-development.md 的注册入口模板补齐。",
                example="pet/tools/timer/__init__.py",
                reference=f"{ARCH_DOC} §11.8; {TOOL_DOC}",
                key=(rel, "contract"),
            )
            continue
        value = assigned["TOOL_NAME"].value
        declared = value.value if isinstance(value, ast.Constant) else None
        if declared != pkg.name:
            yield ContractViolation(
                rule="ARCH003", title="Invalid tool package contract",
                file=rel, line=assigned["TOOL_NAME"].lineno,
                evidence=f"TOOL_NAME = {declared!r}",
                violation=f"TOOL_NAME 必须等于目录名 {pkg.name!r}。",
                why="工具名与目录名不一致会让人找错地方。",
                fix=f"把 TOOL_NAME 改成 {pkg.name!r}；不要改目录名——更新脚本只覆盖不删除，"
                    "老用户机器上残留的旧目录会争抢工具注册。",
                example="pet/tools/timer/__init__.py",
                reference=f"{ARCH_DOC} §11.8; {TOOL_DOC}",
                key=(rel, "contract"),
            )
        register = funcs["register"]
        if not (register.args.args or register.args.posonlyargs or register.args.vararg):
            yield ContractViolation(
                rule="ARCH003", title="Invalid tool package contract",
                file=rel, line=register.lineno,
                evidence=f"def register({', '.join(a.arg for a in register.args.args)})",
                violation="register() 必须接收 registry 参数。",
                why="加载器用 registry 注入方法表，签名不对会被静默跳过。",
                fix="改成 register(registry)。",
                example="pet/tools/timer/__init__.py",
                reference=f"{ARCH_DOC} §11.8; {TOOL_DOC}",
                key=(rel, "contract"),
            )
        if f"pet/tools/{pkg.name}/config.json" in tracked:
            yield ContractViolation(
                rule="ARCH003", title="Invalid tool package contract",
                file=rel, line=1, evidence="pet/tools/{}/config.json 已提交".format(pkg.name),
                violation="工具私有配置 config.json 不得提交，运行时由 config.example.json 复制生成。",
                why="提交 config.json 会覆盖用户本地配置并泄漏私有数据。",
                fix="git rm --cached 后写进 .gitignore，只保留 config.example.json。",
                reference=f"{ARCH_DOC} §10; {TOOL_DOC}",
                key=(rel, "config.json"),
            )


def _git_tracked(prefix: str) -> set[str]:
    """git 索引里的文件；git 不可用时退化为空集（跳过该检查）。"""
    try:
        out = subprocess.run(["git", "ls-files", "--", prefix], cwd=ROOT,
                             capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return set()
    return {line for line in out.stdout.splitlines() if line}


def rule_arch004(scanner: Scanner):
    """§12：三个平台的窗口探测后端必须暴露同名、同调用形态的函数。"""
    for name in DETECTORS:
        path = PET / "brain" / name
        if not path.is_file():
            continue
        funcs = {n.name: n for n in scanner.tree[path].body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        missing = sorted(WINDOW_DETECTOR_API - funcs.keys())
        if missing:
            others = [o for o in DETECTORS if o != name and (PET / "brain" / o).is_file()]
            yield ContractViolation(
                rule="ARCH004", title="Platform detector API mismatch",
                file=f"pet/brain/{name}", line=1, evidence=f"missing: {', '.join(missing)}",
                violation="三个平台后端必须暴露同一组窗口函数。",
                why="window_detector.py 按平台分发同一组名字，缺一个就在对应平台 AttributeError。",
                fix=f"参照 pet/brain/{others[0] if others else 'win_detector.py'} 补上缺失函数。",
                example="pet/brain/win_detector.py",
                reference=f"{ARCH_DOC} §12",
                key=(name, "api"),
            )
        for func_name in sorted(WINDOW_DETECTOR_API & funcs.keys()):
            expected = WINDOW_DETECTOR_SIGNATURES[func_name]
            actual = _Sig.of(funcs[func_name])
            if actual == expected:
                continue
            yield ContractViolation(
                rule="ARCH004", title="Platform detector API mismatch",
                file=f"pet/brain/{name}", line=funcs[func_name].lineno,
                evidence=f"def {func_name}(...)",
                violation=f"{func_name} 的调用形态与契约不符：期望 {expected.render()}，实际 {actual.render()}。",
                why="window_detector.py 按平台用同一组调用方式分发，签名漂移会在对应平台 TypeError。",
                fix=f"按 WINDOW_DETECTOR_SIGNATURES 对齐参数与默认值（基准：pet/brain/win_detector.py）。",
                example="pet/brain/win_detector.py",
                reference=f"{ARCH_DOC} §12",
                key=(name, func_name),
            )


def _is_tool_panel(rel: str) -> bool:
    parts = rel.split("/")
    return len(parts) == 4 and parts[3] == "panel.py"


# 平台后端库只属于对应的平台探测模块（精确放行表，杜绝任意 *_detector.py 误放行）
_PLATFORM_BACKENDS = {
    "win32gui": "pet/brain/win_detector.py",
    "win32con": "pet/brain/win_detector.py",
    "win32process": "pet/brain/win_detector.py",
    "Quartz": "pet/brain/mac_detector.py",
    "AppKit": "pet/brain/mac_detector.py",
    "Xlib": "pet/brain/linux_detector.py",
}


def _heavy_allowed(rel: str, root: str) -> bool:
    """允许顶层 import 重依赖的位置。"""
    if root == "PySide6":
        return rel.startswith("pet/ui/") or rel == ASSEMBLER or _is_tool_panel(rel)
    if root == "playwright":
        return False  # 任何位置都应延迟
    allowed = _PLATFORM_BACKENDS.get(root)
    return allowed is not None and rel == allowed  # 平台库只属于对应的平台后端


def rule_arch005(scanner: Scanner):
    """§11.12/§11.15：纯逻辑模块不许在导入期拖入 Qt / 平台后端 / playwright。"""
    for path in scanner.paths:
        rel = scanner.rel(path)
        for node in scanner.module_imports(path):
            for root in sorted(_roots_of(node)):
                if root not in HEAVY_IMPORTS or _heavy_allowed(rel, root):
                    continue
                yield ContractViolation(
                    rule="ARCH005", title="Heavy import at module level",
                    file=rel, line=node.lineno, evidence=f"import {root}",
                    violation=f"{root} 不允许出现在模块级 import（此处不在豁免位置）。",
                    why="导入期拖入重依赖会让模块无法在无 Qt / 无平台库的环境里加载。",
                    fix="推迟到真正使用处；界面代码移到 pet/ui/，平台调用放到 *_detector.py。",
                    example="pet/tools/browser 在 handler 内延迟 import playwright",
                    reference=f"{ARCH_DOC} §11.12、§11.15",
                    key=(rel, root),
                )


def rule_arch006(scanner: Scanner):
    """§11.14：跨对象私有访问冻结，覆盖 obj._attr / from x import _Private / sys.modules[...]._x。"""
    for path in scanner.paths:
        rel = scanner.rel(path)
        tree = scanner.tree[path]
        local_classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        bindings = _import_bindings(tree, scanner.module[path], scanner)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                base = scanner.absolute_target(node, scanner.module[path])
                if not base.startswith("pet"):
                    continue
                for alias in node.names:
                    if _private_name(alias.name) and not alias.name.isupper():
                        yield _private_violation(
                            rel, node.lineno, f"from {base} import {alias.name}",
                            alias.name, "import 私有符号",
                            (rel, alias.name))
            elif isinstance(node, ast.Attribute):
                if not _private_name(node.attr) or node.attr.isupper():
                    continue  # 全大写真源常量（_KEY_META / _SPAWNERS）不按私有访问处理
                receiver, cross = _cross_object(node, local_classes, bindings)
                if not cross:
                    continue
                yield _private_violation(
                    rel, node.lineno, ast.unparse(node), node.attr,
                    f"跨对象访问私有成员（接收者：{receiver}）",
                    (rel, receiver, node.attr))


def _import_bindings(tree: ast.Module, current: str, scanner: Scanner) -> dict[str, bool]:
    """文件内的名字绑定：名字 -> 是否指向 pet 模块或 pet 导出的符号。"""
    bindings: dict[str, bool] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                bindings[name] = alias.name.split(".")[0] == "pet"
        elif isinstance(node, ast.ImportFrom):
            base = scanner.absolute_target(node, current)
            for alias in node.names:
                if alias.name != "*":
                    bindings[alias.asname or alias.name] = base.startswith("pet")
    return bindings


def _private_violation(rel, line, evidence, symbol, kind, key):
    return ContractViolation(
        rule="ARCH006", title="Cross-object private access",
        file=rel, line=line, evidence=evidence,
        violation=f"{kind}：{symbol}",
        why="跨对象读私有成员把两个对象的内部实现焊死，重构其中一个会弄坏另一个。",
        fix="给被访问对象加公开方法或属性；旧债登记在 allowlist 里。",
        reference=f"{ARCH_DOC} §11.14; {ARCH_DOC} §14「跨对象的私有访问」",
        key=key,
    )


def _private_name(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def _cross_object(node: ast.Attribute, local_classes: set[str],
                  bindings: dict[str, bool]) -> tuple[str, bool]:
    """返回 (接收者文本, 是否跨对象)：self/cls、本文件定义的类与标准库模块除外。

    未知局部变量/参数静态扫不出类型（可能是 pet 对象，也可能是 match 这类
    第三方对象），保守按跨对象报出；豁免键带上接收者文本，误报可精确豁免单条，
    不会因为 (文件, 属性) 粗粒度匹配放走真实违规。
    """
    value = node.value
    if isinstance(value, ast.Name):
        if value.id in ("self", "cls") or value.id in local_classes:
            return "", False
        if value.id in bindings:
            # pet 模块/符号算越界，os._exit 这类标准库不算
            return value.id, bindings[value.id]
        return value.id, True  # 局部变量与参数：类型未知，保守报出
    if isinstance(value, ast.Call):
        known_super = isinstance(value.func, ast.Name) and value.func.id == "super"
        return ast.unparse(value), not known_super
    return ast.unparse(value), isinstance(value, (ast.Attribute, ast.Subscript))  # 链式与 sys.modules[...] 形态


def rule_arch007(scanner: Scanner):
    """§11.9：动作素材目录必须有同名 json。"""
    actions = ROOT / "assets" / "actions"
    if not actions.is_dir():
        return
    for directory in sorted(d for d in actions.iterdir() if d.is_dir()):
        rel = f"assets/actions/{directory.name}"
        jsons = sorted(f.name for f in directory.glob("*.json"))
        expected = f"{directory.name}.json"
        if expected not in jsons:
            yield ContractViolation(
                rule="ARCH007", title="Broken action asset contract",
                file=rel, line=1, evidence=f"json files: {jsons or '（无）'}",
                violation=f"动作素材目录缺少同名配置 {expected}。",
                why="缺少同名 json 时该动作不会被加载，而 LLM 仍可能输出这个动作名。",
                fix=f"补上 {expected}（帧文件名排序即播放顺序）。",
                example="assets/actions/walk_left/walk_left.json",
                reference=ASSET_DOC,
                key=(rel, "json"),
            )
        elif len(jsons) > 1:
            extra = [n for n in jsons if n != expected]
            yield ContractViolation(
                rule="ARCH007", title="Broken action asset contract",
                file=rel, line=1, evidence=f"json files: {extra}",
                violation=f"目录里的 json 名称与目录名不匹配，应为 {expected}。",
                why="加载器按目录名找同名 json。",
                fix=f"把多余 json 重命名或合并进 {expected}。",
                reference=ASSET_DOC,
                key=(rel, "json"),
            )


def rule_arch008(scanner: Scanner):
    """§14：包内模块级 import 环（强连通分量）。"""
    for package, scc in scanner.cycles():
        names = sorted(scc)
        yield ContractViolation(
            rule="ARCH008", title="Import cycle inside package",
            file=package.replace(".", "/") + "/__init__.py", line=1,
            evidence=" <-> ".join(names),
            violation=f"{package} 包内存在模块级 import 环。",
            why="环让导入顺序变成隐式约束，模块无法单独加载。",
            fix="把共享状态抽到第三个模块，或把回绕的那处改成子模块直导。",
            example="把 from pet.a import b 的回绕写法改成 from pet.a.b import ... 即可断开",
            reference=f"{ARCH_DOC} §14「依赖环」",
            key=frozenset(scc),
        )


# 导入期允许执行的调用：纯函数、常量绑定与注册表登记
_BENIGN_CALLS = {
    "logging.getLogger", "re.compile", "Path", "pathlib.Path", "atexit.register",
    "os.path.join", "os.path.dirname", "os.path.abspath", "os.path.basename",
    "os.path.realpath", "os.path.normpath", "os.getcwd", "os.getpid",
    "min", "max", "len", "sorted", "abs", "round", "sum", "any", "all",
    "tuple", "list", "dict", "set", "frozenset", "str", "int", "float", "bool",
    "type", "isinstance", "getattr", "hasattr", "enumerate", "zip", "range", "repr",
    "super", "staticmethod", "classmethod", "property",
}

# 纯方法：结果只由接收者决定（str / Path 的常见用法）
_PURE_METHODS = {
    "replace", "split", "rsplit", "format", "upper", "lower", "strip",
    "lstrip", "rstrip", "title", "resolve", "join",
}


def _side_effect_call(node: ast.expr | None) -> ast.Call | None:
    """表达式里第一个非良性调用；良性调用自身放行但参数仍递归检查，Lambda 体不往下钻。"""
    if node is None or isinstance(node, ast.Lambda):
        return None
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _PURE_METHODS:
            benign = True  # 纯方法：结果只由接收者与参数决定
        else:
            name = ast.unparse(func)
            tail = name.rsplit(".", 1)[-1]
            benign = (name in _BENIGN_CALLS  # 纯函数、常量绑定
                      or tail == "register" or tail.startswith("register_"))  # 注册表登记
        if not benign:
            return node
        # 良性调用放行自身，但容器参数里的副作用仍要抓：dict(x=open(...))、list(load(...))
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.expr, ast.comprehension, ast.keyword)):
                found = _side_effect_call(child)
                if found is not None:
                    return found
        return None
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.expr, ast.comprehension, ast.keyword)):
            found = _side_effect_call(child)
            if found is not None:
                return found
    return None


def rule_arch009(scanner: Scanner):
    """§11.12：模块级只留常量、类型、函数/类定义与注册表登记。"""
    for path in scanner.paths:
        rel = scanner.rel(path)
        for node in _module_statements(scanner.tree[path]):
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef, ast.If, ast.Try, ast.With, ast.AsyncWith,
                                 ast.For, ast.AsyncFor, ast.While, ast.Pass)):
                continue  # 容器已被展开，成员单独检查
            if rel == "pet/__main__.py" and isinstance(node, ast.Expr) \
                    and isinstance(node.value, ast.Call):
                continue  # python -m pet 的入口调用
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                bad = _side_effect_call(node.value)
                if bad is not None:
                    yield _side_effect_violation(rel, node)
            elif isinstance(node, ast.Expr):
                if isinstance(node.value, ast.Constant):
                    continue
                if _side_effect_call(node.value) is not None:
                    yield _side_effect_violation(rel, node)


def _side_effect_violation(rel, node):
    return ContractViolation(
        rule="ARCH009", title="Import-time side effect",
        file=rel, line=node.lineno, evidence=ast.unparse(node)[:120],
        violation="模块级出现了定义与登记之外的调用。",
        why="导入期副作用会被任何一次 import 连带执行，碰到真实资源。",
        fix="推迟到首次使用；实例创建移进 register() 或工厂函数。",
        example="pet/tools/todo 的实例创建在导入期执行是登记过的例外",
        reference=f"{ARCH_DOC} §11.12",
        key=(rel, ast.unparse(node).split("(")[0].split("=")[0].strip()),
    )


RULE_FUNCS = {
    "ARCH001": rule_arch001,
    "ARCH002": rule_arch002,
    "ARCH003": rule_arch003,
    "ARCH004": rule_arch004,
    "ARCH005": rule_arch005,
    "ARCH006": rule_arch006,
    "ARCH007": rule_arch007,
    "ARCH008": rule_arch008,
    "ARCH009": rule_arch009,
}


# --------------------------------------------------------------------------
# allowlist：待清偿的债（键 -> 原因 / 依据 / 跟踪 issue）
# 每条 reason 说明为什么现在还不能修；reference 必须指向 docs 的条款（§5.9）。
# --------------------------------------------------------------------------

ALLOWLIST: dict[str, list[Debt]] = {
    "ARCH001": [
        Debt(("pet/tools/context.py", "pet.db"),
             "db_path() 钩子内延迟 import pet.db；出路是装配期注入路径提供者，保持 TOOL_CTX 零依赖",
             f"{ARCH_DOC} §11.13"),
    ],
    "ARCH002": [
        Debt(("pet/agent/pet_agent.py", "pet.game.gamebase"),
             "stop() 内延迟 import GAME；既不打断环也不推迟重依赖", f"{ARCH_DOC} §11.15"),
        Debt(("pet/app.py", "pet.self_update"),
             "main() 内延迟 import 更新脚本应用；非环非重依赖", f"{ARCH_DOC} §11.15"),
        Debt(("pet/brain/behavior.py", "pet.tools.context"),
             "_BehaviorToolSession 内延迟 import TOOL_CTX；非环非重依赖", f"{ARCH_DOC} §11.15"),
        Debt(("pet/brain/memory.py", "pet.config"),
             "文件头已导入 config，函数内 5 处重复 import", f"{ARCH_DOC} §11.15"),
        Debt(("pet/brain/memory.py", "pet.brain.embedding_client"),
             "VectorRetriever 构造时才加载向量化客户端（可选能力）", f"{ARCH_DOC} §11.15"),
        Debt(("pet/food/food.py", "pet.brain.prompts"),
             "_trigger_self_fed 内延迟 import 台词模板；非环非重依赖", f"{ARCH_DOC} §11.15"),
        Debt(("pet/tools/context.py", "pet.db"),
             "db_path() 钩子内延迟 import pet.db", f"{ARCH_DOC} §11.15"),
        Debt(("pet/tools/knowledge/storage.py", "pet.brain.embedding_client"),
             "向量初始化时才加载 EmbeddingClient（可选能力）", f"{ARCH_DOC} §11.15"),
        Debt(("pet/tools/knowledge/storage.py", "pet.tools.knowledge.chunker"),
             "add_document 内才 import 同包 chunker；非环非重依赖", f"{ARCH_DOC} §11.15"),
        Debt(("pet/tools/registry.py", "pet.brain.memory"),
             "recall 元工具惰性 import 记忆检索器：模块级导入会经 pet.brain.__init__ → behavior 回指本模块成环，父包隐式边不在扫描图上",
             f"{ARCH_DOC} §11.15"),
        Debt(("pet/tools/registry.py", "pet.tools.context"),
             "_recall_search 内延迟 import TOOL_CTX；非环非重依赖", f"{ARCH_DOC} §11.15"),
        Debt(("pet/tools/registry.py", "pet.game.gamebase"),
             "_game_list 内延迟 import GAME；非环非重依赖", f"{ARCH_DOC} §11.15"),
        Debt(("pet/ui/settings_window.py", "pet.brain.embedding_client"),
             "embedding 连通性测试线程内延迟 import（可选能力）", f"{ARCH_DOC} §11.15"),
        Debt(("pet/ui/settings_window.py", "pet.brain.prompts"),
             "保存配置后延迟 import invalidate_action_section；非环非重依赖", f"{ARCH_DOC} §11.15"),
    ],
    "ARCH003": [
        Debt(("pet/tools/file_ops/__init__.py", "contract"),
             "TOOL_NAME 为 file，与目录名 file_ops 不一致；修法是改 TOOL_NAME 对齐目录名，不要改目录名——"
             "更新脚本只覆盖不删除，老用户机器上残留的旧目录会与新目录争抢工具注册",
             f"{ARCH_DOC} §11.8"),
    ],
    "ARCH004": [],
    "ARCH005": [
        Debt(("pet/action/action.py", "PySide6"),
             "行走与驱动 tick 需要 QTimer / QApplication 取屏幕几何", f"{ARCH_DOC} §11.12、§11.15"),
        Debt(("pet/action/action_queue.py", "PySide6"),
             "动作队列挂在 QObject 信号上推进", f"{ARCH_DOC} §11.12、§11.15"),
        Debt(("pet/action/gravity.py", "PySide6"),
             "重力 tick 需要 QTimer 与窗口几何", f"{ARCH_DOC} §11.12、§11.15"),
        Debt(("pet/agent/pet_agent.py", "PySide6"),
             "脑线程是 QThread，编排层本身是 Qt 对象", f"{ARCH_DOC} §11.12、§11.15"),
        Debt(("pet/agent/scheduler.py", "PySide6"),
             "三档定时器基于 QTimer", f"{ARCH_DOC} §11.12、§11.15"),
        Debt(("pet/agent/state.py", "PySide6"),
             "状态机发 Qt 信号", f"{ARCH_DOC} §11.12、§11.15"),
        Debt(("pet/food/food.py", "PySide6"),
             "FoodManager 用 QTimer 轮询到期与到达判定", f"{ARCH_DOC} §11.12、§11.15"),
        Debt(("pet/pulse/mood.py", "PySide6"),
             "数值变化靠 Qt 信号广播", f"{ARCH_DOC} §11.12、§11.15"),
        Debt(("pet/pulse/vitals.py", "PySide6"),
             "数值变化靠 Qt 信号广播", f"{ARCH_DOC} §11.12、§11.15"),
        Debt(("pet/single_instance.py", "PySide6"),
             "单实例锁用 QLockFile", f"{ARCH_DOC} §11.12、§11.15"),
        Debt(("pet/version_check.py", "PySide6"),
             "版本检查在后台 QThread 里跑", f"{ARCH_DOC} §11.12、§11.15"),
        Debt(("pet/voice/hotkey_manager.py", "PySide6"),
             "热键回调要回主线程", f"{ARCH_DOC} §11.12、§11.15"),
        Debt(("pet/voice/mic_capture.py", "PySide6"),
             "采集线程通过 Qt 信号回传音频", f"{ARCH_DOC} §11.12、§11.15"),
        Debt(("pet/voice/voice_session.py", "PySide6"),
             "语音会话是 QObject", f"{ARCH_DOC} §11.12、§11.15"),
        Debt(("pet/voice/xunfei_stt.py", "PySide6"),
             "识别结果经 Qt 信号回传", f"{ARCH_DOC} §11.12、§11.15"),
    ],
    "ARCH006":
        _sym("pet/action/action.py", "g",
             ["_vy", "_clamp_pos", "_standing_hwnd", "_cached_effective_bottom",
              "_standing_title", "_standing_rect", "_falling", "_play_once"],
             "行走 / drive 直接读重力内部状态（§14 最大一处耦合），需给 Gravity 公开接口",
             f"{ARCH_DOC} §14")
        + _sym("pet/action/action.py", "self.gravity", ["_cached_effective_bottom"],
               "站立判定读重力缓存底部，需给 Gravity 公开接口", f"{ARCH_DOC} §14")
        + _sym("pet/action/action_queue.py", "self._actions", ["_anim", "_stop_drive"],
               "队列推进要读 Actions 内部动画器与驱动收尾", f"{ARCH_DOC} §14")
        + [Debt(("pet/action/action_queue.py", "self._actions.gravity", "_tick"),
                "动作结束前手动跑一次重力", f"{ARCH_DOC} §14; {ADR_TIMEOUT}")]
        + _sym("pet/agent/scheduled_tasks.py", "self._agent",
               ["_pet_window", "_thread", "_async_brain", "_autonomous_pipeline"],
               "定时任务探活与强制收尾读 agent 内部", f"{ARCH_DOC} §11.14")
        + [Debt(("pet/agent/scheduled_tasks.py", "self._agent.conversation_store", "_cleanup_old"),
                "慢档清理对话历史直呼私有方法", f"{ARCH_DOC} §14")]
        + [Debt(("pet/app.py", "_LogRelay"),
                "装配点导入 log_window 私有中继类", f"{ARCH_DOC} §11.14")]
        + _sym("pet/app.py", "window", ["_quit_fn"],
               "装配点直连窗口 / 托盘 / agent 的私有字段与方法", f"{ARCH_DOC} §14")
        + _sym("pet/app.py", "tray", ["_quit_fn"],
               "装配点直连窗口 / 托盘 / agent 的私有字段与方法", f"{ARCH_DOC} §14")
        + _sym("pet/app.py", "agent", ["_voice_session"],
               "装配点直连窗口 / 托盘 / agent 的私有字段与方法", f"{ARCH_DOC} §14")
        + _sym("pet/app.py", "agent.behavior", ["_save_context"],
               "装配点直连窗口 / 托盘 / agent 的私有字段与方法", f"{ARCH_DOC} §14")
        + _sym("pet/app.py", "chat_bubble", ["_on_submit"],
               "退出与提交路径直呼窗口私有方法与槽", f"{ARCH_DOC} §11.14")
        + _sym("pet/app.py", "_w", ["_force_close"],
               "退出与提交路径直呼窗口私有方法与槽", f"{ARCH_DOC} §11.14")
        + _sym("pet/brain/behavior.py", "memory_store", ["_db_path"],
               "Behavior 直读 memory_store 的库路径", f"{ARCH_DOC} §14")
        + _sym("pet/brain/behavior.py", "self._behavior",
               ["_build_tools_param", "_activate_tool_groups_from_search",
                "_activate_groups_from_keyword"],
               "_BehaviorToolSession 是 Behavior 的同文件适配器，把私有能力按 ToolSession 协议转出",
               f"{ARCH_DOC} §14")
        + _sym("pet/brain/context_builder.py", "BrainMixin",
               ["_format_context_time", "_format_duration"],
               "父类 BrainMixin（pet/brain/base.py）的私有静态工具被子类模块直呼",
               f"{ARCH_DOC} §11.14")
        + _sym("pet/brain/memory.py", "self._retriever",
               ["_conn", "_lock", "_effective_importance", "_format_memory_time",
                "_demote_l2_to_l3", "_promote_by_tool_hits", "_generate_embedding",
                "_upsert_vector"],
               "MemoryStore 直捅 _MemoryRetriever 内部（存储与检索分层不彻底）",
               f"{ARCH_DOC} §11.14")
        + _sym("pet/tools/__init__.py", "TOOL_REGISTRY", ["_tools"],
               "工具加载器读 TOOL_REGISTRY._tools", f"{ARCH_DOC} §14")
        + _sym("pet/ui/pet_window.py", "TOOL_REGISTRY", ["_tools"],
               "宠物窗口读 TOOL_REGISTRY._tools", f"{ARCH_DOC} §14")
        + _sym("pet/ui/debug_window.py", "self.agent.behavior", ["_context", "_score_entry"],
               "调试面板读 Behavior 内部上下文与打分", f"{ARCH_DOC} §14")
        + _sym("pet/ui/log_window.py", "widget", ["_append_log"],
               "日志中继直呼窗口私有方法", f"{ARCH_DOC} §11.14")
        + _sym("pet/ui/log_window.py", "self._widget", ["_append_log"],
               "日志中继直呼窗口私有方法", f"{ARCH_DOC} §11.14")
        + _sym("pet/ui/music_bubble.py", "speech_bubble", ["_speech_queue", "_is_active"],
               "音乐气泡抢占语音气泡的队列", f"{ARCH_DOC} §14")
        + _sym("pet/ui/settings_window.py", "cls._instance", ["_load_values"],
               "外部要求单例窗口重载配置，直呼私有方法", f"{ARCH_DOC} §11.14")
        + _sym("pet/ui/system_tray.py", "self.pet", ["_agent", "_mouse_penetration"],
               "托盘直读宠物窗口内部状态", f"{ARCH_DOC} §11.14"),
    "ARCH007": [],
    "ARCH008": [],
    "ARCH009": [
        Debt(("pet/__init__.py", "_crash_reporter"),
             "崩溃钩子要在任何 pet 模块导入前安装，测试与文档脚本用空模块顶替隔离",
             f"{ARCH_DOC} §9、§11.12"),
        Debt(("pet/action/fishing.py", "_fishing"),
             "模块级单例（钓捞产出回调），构造无副作用", f"{ARCH_DOC} §11.12; {ADR_LAYERING}"),
        Debt(("pet/action/registry.py", "REGISTRY: dict[str, ActionDef]"),
             "动作表在导入期构建，generate_action_section 每次调用会重建，可改惰性",
             f"{ARCH_DOC} §11.12"),
        Debt(("pet/action/registry.py", "ACTION_NAMES: list[str]"),
             "动作名列表在导入期从 REGISTRY 派生，随 REGISTRY 惰性化一并消除",
             f"{ARCH_DOC} §11.12"),
        Debt(("pet/brain/linux_detector.py", "_dpy_lock"),
             "X11 显示连接的模块级 RLock", f"{ARCH_DOC} §11.12"),
        Debt(("pet/brain/memory.py", "_MEMORY_STORE_LOCK"),
             "记忆库单例的模块级锁", f"{ARCH_DOC} §11.12; {ADR_LAYERING}"),
        Debt(("pet/brain/memory.py", "logger.info"),
             "jieba 缺失提示在导入期输出", f"{ARCH_DOC} §11.12"),
        Debt(("pet/brain/prompts.py", "_action_section"),
             "惰性求值包装器（缓存动作表文本以命中 prompt 缓存），构造无副作用",
             f"{ARCH_DOC} §11.12"),
        Debt(("pet/config.py", "config"),
             "Config 单例（§9 登记的装配模式），构造无副作用", f"{ARCH_DOC} §11.12; {ADR_LAYERING}"),
        Debt(("pet/food/food.py", "FOOD"),
             "FoodManager 单例（QObject，装配在 pet/app.py）", f"{ARCH_DOC} §11.12; {ADR_LAYERING}"),
        Debt(("pet/game/__init__.py", "GAME.register"),
             "包 __init__ 在导入期实例化四个游戏对象并登记，应移进装配入口（pet/app.py）",
             f"{ARCH_DOC} §11.12"),
        Debt(("pet/game/gamebase.py", "GAME"),
             "GameBase 单例（§9 登记的装配模式），构造无副作用", f"{ARCH_DOC} §11.12; {ADR_LAYERING}"),
        Debt(("pet/pulse/mood.py", "_DB_PATH"),
             "导入期定位 pet.db 路径（只读目录探测）", f"{ARCH_DOC} §11.12"),
        Debt(("pet/pulse/vitals.py", "_DB_PATH"),
             "导入期定位 pet.db 路径（只读目录探测）", f"{ARCH_DOC} §11.12"),
        Debt(("pet/tools/browser/__init__.py", "_instance"),
             "导入期实例化 BrowserTool，实例应移进 register()", f"{ARCH_DOC} §11.12"),
        Debt(("pet/tools/browser/core.py", "_cfg"),
             "导入期读取 config.json（文件 I/O）", f"{ARCH_DOC} §11.12"),
        Debt(("pet/tools/context.py", "TOOL_CTX"),
             "TOOL_CTX 单例（ADR 0009 装配模式），构造无副作用", f"{ARCH_DOC} §11.12; {ADR_LAYERING}"),
        Debt(("pet/tools/file_ops/__init__.py", "_instance"),
             "导入期实例化 FileOpsTool，实例应移进 register()", f"{ARCH_DOC} §11.12"),
        Debt(("pet/tools/file_ops/core.py", "_ALLOWED_ROOTS"),
             "导入期查询系统特殊目录（Shell API）", f"{ARCH_DOC} §11.12"),
        Debt(("pet/tools/registry.py", "TOOL_REGISTRY"),
             "TOOL_REGISTRY 单例（ADR 0009 装配模式），构造无副作用", f"{ARCH_DOC} §11.12; {ADR_LAYERING}"),
        Debt(("pet/tools/web_search/core.py", "logging.getLogger"),
             "导入期调低 trafilatura 日志级别（改全局状态）", f"{ARCH_DOC} §11.12"),
        Debt(("pet/ui/emotion.py", "VALID_EMOTIONS"),
             "从常量表 EMOTION_MAP 派生的合法表情集合（无外部副作用），可改字面量 set 消除",
             f"{ARCH_DOC} §11.12"),
        Debt(("pet/ui/system_tray.py", "_PROCESS"),
             "导入期创建 psutil.Process 句柄", f"{ARCH_DOC} §11.12"),
        Debt(("pet/version_check.py", "_ssl_ctx"),
             "导入期创建 SSL 上下文", f"{ARCH_DOC} §11.12"),
    ],
}


# --------------------------------------------------------------------------
# 用例
# --------------------------------------------------------------------------

def _run_rule(rule: str, ledger: Ledger) -> list[ContractViolation]:
    """跑一条规则并套用 allowlist；同一键只报一次（行号取首次命中）。"""
    found, seen = [], set()
    for violation in RULE_FUNCS[rule](SCANNER):
        key = violation.key
        if key is not None:
            if key in seen or ledger.exempt(rule, key):
                continue
            seen.add(key)
        found.append(violation)
    return found


def _fail(violations: list[ContractViolation]):
    if not violations:
        return
    body = "\n\n".join(v.render() for v in sorted(violations, key=lambda v: (v.rule, v.file, v.line)))
    pytest.fail(f"Architecture contract violations found: {len(violations)}\n\n{body}", pytrace=False)


def test_arch001_layer_direction():
    _fail(_run_rule("ARCH001", Ledger(ALLOWLIST)))


def test_arch002_deferred_imports():
    _fail(_run_rule("ARCH002", Ledger(ALLOWLIST)))


def test_arch003_tool_contract():
    _fail(_run_rule("ARCH003", Ledger(ALLOWLIST)))


def test_arch004_detector_api():
    _fail(_run_rule("ARCH004", Ledger(ALLOWLIST)))


def test_arch005_heavy_imports():
    _fail(_run_rule("ARCH005", Ledger(ALLOWLIST)))


def test_arch006_private_access():
    _fail(_run_rule("ARCH006", Ledger(ALLOWLIST)))


def test_arch007_action_assets():
    _fail(_run_rule("ARCH007", Ledger(ALLOWLIST)))


def test_arch008_import_cycles():
    _fail(_run_rule("ARCH008", Ledger(ALLOWLIST)))


def test_arch009_import_side_effects():
    _fail(_run_rule("ARCH009", Ledger(ALLOWLIST)))


def test_arch000_allowlist_not_stale():
    """元规则：先跑完全部规则，再比对未命中的条目。"""
    ledger = Ledger(ALLOWLIST)
    for rule in RULE_FUNCS:
        _run_rule(rule, ledger)
    violations = []
    for rule, debt in ledger.stale():
        violations.append(ContractViolation(
            rule="ARCH000", title="Stale allowlist entry",
            file="tests/test_architecture_contracts.py", line=1,
            evidence=f"{rule} 条目 {debt.key!r}",
            violation="条目没有命中任何违规，债已还清。",
            why="陈旧条目让 allowlist 只增不减，失去账本意义。",
            fix="删掉该条目；仍需豁免则重写 reason 与 reference。",
            reference=f"{DESIGN_DOC} §5.9",
        ))
    _fail(violations)


if __name__ == "__main__":
    # 标定模式：打印全部违规的匹配键，用于增删 allowlist 条目
    empty = Ledger({rule: [] for rule in RULE_FUNCS})
    for rule in RULE_FUNCS:
        for v in _run_rule(rule, empty):
            print(f"{rule}\t{v.file}:{v.line}\tkey={v.key!r}\t{v.evidence[:100]}")
