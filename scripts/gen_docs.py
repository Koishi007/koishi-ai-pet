#!/usr/bin/env python3
"""从代码生成 docs/reference/*.md —— 参考类文档的唯一真源是代码本身。

用法：
    python scripts/gen_docs.py            # 生成 / 刷新 docs/reference/
    python scripts/gen_docs.py --check    # 只校验与代码是否一致（CI 用），不一致退出码 1

取数方式分两类：
  * 运行时导入：配置项（pet.config._KEY_META）、动作表（pet.action.registry.REGISTRY）、
    提示词块（pet.brain.prompts）——这几个模块是纯 Python，导入无副作用；
  * 静态解析（ast）：粒子特效、内置工具、设置界面页签——这些要么依赖 Qt/重依赖，
    要么需要读调用字面量，静态解析比导入更安全、更快。

生成文件里不含时间戳/版本号：内容必须只由代码决定，否则 --check 会永远报脏。
"""

import argparse
import ast
import atexit
import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = ROOT / "docs" / "reference"

_GENERATED_BY = "<!-- 由 scripts/gen_docs.py 生成，请勿手工编辑 -->"


def _bootstrap() -> None:
    """在导入任何 pet 模块之前隔离环境（与 tests/conftest.py 同一套做法）。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    tmp = tempfile.mkdtemp(prefix="koishi-docs-")
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    # settings.json 落盘位置由 APPDATA / XDG_CONFIG_HOME 决定，强制指向空临时目录：
    # 文档必须只由代码决定，否则会把开发者本机 settings.json 里的自定义值（调度间隔、
    # 交互提示词等）写进文档，别人机器上 --check 必然报脏。这里不能用 setdefault。
    os.environ["APPDATA"] = tmp
    os.environ["XDG_CONFIG_HOME"] = tmp
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

    # pet/__init__.py 会安装崩溃钩子并改写 logs/startup.state，这里顶替掉
    stub = types.ModuleType("pet.crash_reporter")
    stub.install = lambda: None
    stub.set_enabled = lambda *a, **k: None
    sys.modules.setdefault("pet.crash_reporter", stub)


# --------------------------------------------------------------------------- #
# 取数：运行时导入
# --------------------------------------------------------------------------- #

def load_config_meta() -> dict:
    """配置项元数据（pet/config.py 的 _KEY_META，唯一真源）。"""
    from pet.config import _KEY_META

    return _KEY_META


def load_settings_tabs() -> list[tuple[str, list[str]]]:
    """设置界面页签 → 该页签按顺序列出的配置键（静态解析 settings_window.py）。

    页签归属是界面代码手写的，和 _KEY_META 的 category 不是同一维度（例如
    MEMORY_* 的 category 是 memory，却显示在「行为」页），所以只能从源码里读。
    """
    keys = set(load_config_meta())
    tree = ast.parse(_read(pet_path("ui", "settings_window.py")))
    node_types = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    tabs: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "addTab"):
            continue
        if len(node.args) < 2:
            continue
        builder, label = node.args[0], node.args[1]
        if not (isinstance(builder, ast.Call) and isinstance(label, ast.Constant)):
            continue
        builder_name = getattr(builder.func, "attr", "")
        if builder_name not in node_types:
            continue
        tabs.append((str(label.value), _collect_keys(node_types[builder_name], keys)))
    return tabs


def _collect_keys(node: ast.AST, known: set[str]) -> list[str]:
    """按源码顺序收集函数体内出现的配置键字面量。"""
    found: list[str] = []
    for child in _walk_source_order(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str) and child.value in known:
            if child.value not in found:
                found.append(child.value)
    return found


def _walk_source_order(node: ast.AST):
    """深度优先、按源码顺序遍历（ast.walk 是广度优先，会打乱源码顺序）。"""
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _walk_source_order(child)


def load_actions() -> dict:
    """动作注册表（pet/action/registry.py）。"""
    from pet.action import registry

    return {
        "defs": registry.REGISTRY,
        "names": registry.ACTION_NAMES,
        "durations": registry._DURATION_ACTION_DEFS,
        "target": registry.target_sequence_duration(),
        "min_count": registry.min_action_count(),
        "sample": registry.generate_action_section(),
    }


def load_prompts() -> dict:
    """提示词块与段落组合（pet/brain/prompts.py）。"""
    from pet.brain import prompts

    blocks: dict[str, str] = {}
    for name, value in vars(prompts).items():
        # 块常量多为私有命名（_MEMORY_GUIDE 等），只保留字符串值，顺带滤掉 dict/函数
        if not name.strip("_").isupper() or not isinstance(value, str):
            continue
        blocks[name] = _summarize_block(value)

    perception: list[tuple[str, list[str]]] = []
    for mode, sections in prompts._PERCEPTION_SECTIONS.items():
        perception.append((mode, [_reference_name(item, prompts) for item in sections]))

    tasks = {name: fn.__name__ for name, fn in prompts._TASK_SECTIONS.items()}
    return {"blocks": blocks, "perception": perception, "tasks": tasks, "combos": _prompt_combos()}


def _prompt_combos() -> list[tuple[str, str]]:
    """合法 mode/task 组合白名单。

    它是 build_system_prompt 的函数局部变量（不是模块常量），只能从源码里取。
    """
    tree = ast.parse(_read(pet_path("brain", "prompts.py")))
    for node in _walk_source_order(tree):
        if isinstance(node, ast.Assign) and _assign_name(node) == "_VALID_COMBOS":
            combos = []
            for elt in node.value.elts:
                if isinstance(elt, ast.Tuple) and len(elt.elts) == 2:
                    mode, task = elt.elts
                    if isinstance(mode, ast.Constant) and isinstance(task, ast.Constant):
                        combos.append((str(mode.value), str(task.value)))
            return sorted(combos)
    return []


def _summarize_block(value) -> str:
    """块常量 → 一句话摘要（取首个非标题行，压掉换行）。"""
    text = str(value).strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    head = lines[0]
    if head.startswith("[") and len(lines) > 1:
        head = f"{head} {lines[1]}"
    return head.replace("|", "｜")[:120]


def _reference_name(obj, module) -> str:
    """把 _PERCEPTION_SECTIONS 里的元素还原成可读名字。"""
    for name, value in vars(module).items():
        if value is obj and name.isupper():
            return name
    if obj.__class__.__name__ == "_Lazy":
        fn = getattr(obj, "fn", None)  # _Lazy 把被包装的工厂存在 .fn
        if fn is not None:
            return f"{getattr(fn, '__name__', 'lazy')}()"
        return "_Lazy(...)"
    return repr(obj)[:60]


# --------------------------------------------------------------------------- #
# 取数：静态解析
# --------------------------------------------------------------------------- #

def pet_path(*parts: str) -> Path:
    return ROOT / "pet" / Path(*parts)


def _read(path: Path) -> str:
    # utf-8-sig：仓库里有带 BOM 的源文件（如 pet/tools/registry.py），
    # 用 utf-8 读会把 BOM 留在首行导致 ast.parse 失败
    return path.read_text(encoding="utf-8-sig")


def load_effects() -> dict:
    """粒子特效注册表（pet/ui/particle.py，静态解析以避免引入 Qt）。"""
    tree = ast.parse(_read(pet_path("ui", "particle.py")))
    spawners: dict[str, dict] = {}
    default_y: dict[str, str] = {}

    for node in tree.body:
        assigned = _assigned_dict(node)  # _SPAWNERS 带类型注解，是 AnnAssign
        if assigned and assigned[0] == "_SPAWNERS":
            for key, value in zip(assigned[1].keys, assigned[1].values):
                if isinstance(key, ast.Constant) and isinstance(value, ast.Name):
                    spawners[str(key.value)] = {"func": value.id, "doc": _func_doc(tree, value.id)}
        if isinstance(node, ast.ClassDef) and node.name == "ParticleWidget":
            for item in node.body:
                assigned = _assigned_dict(item)
                if assigned and assigned[0] == "_DEFAULT_Y":
                    for key, value in zip(assigned[1].keys, assigned[1].values):
                        if isinstance(key, ast.Constant):
                            default_y[str(key.value)] = _unparse(value)
    return {"spawners": spawners, "default_y": default_y}


def _func_doc(tree: ast.Module, name: str) -> str:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            doc = ast.get_docstring(node) or ""
            return doc.splitlines()[0].strip() if doc else ""
    return ""


def _assign_name(node: ast.Assign) -> str:
    target = node.targets[0]
    return target.id if isinstance(target, ast.Name) else ""


def _assigned_dict(node: ast.AST) -> tuple[str, ast.Dict] | None:
    """取「变量名 = {...}」的字面量赋值，兼容带类型注解的写法。"""
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
        name = _assign_name(node)
        return (name, node.value) if name else None
    if isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Dict):
        if isinstance(node.target, ast.Name):
            return node.target.id, node.value
    return None


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - 仅在异常 AST 上触发
        return "?"


def load_tools() -> dict:
    """内置工具：分组常量 + register()/add_method() 调用（静态解析）。

    不走运行时加载，是因为 pet/tools/__init__.py 会在首次加载时 pip 安装工具私有依赖，
    且部分工具引用 playwright 等重依赖，文档生成不应触发这些副作用。
    """
    tools: list[dict] = []
    for init in sorted((ROOT / "pet" / "tools").glob("*/__init__.py")):
        tree = ast.parse(_read(init))
        info = {
            "dir": init.parent.name,
            "name": "",
            "description": "",
            "group": "default",
            "methods": [],
        }
        for node in tree.body:
            if isinstance(node, ast.Assign):
                target = _assign_name(node)
                if target == "TOOL_NAME" and isinstance(node.value, ast.Constant):
                    info["name"] = str(node.value.value)
                elif target == "TOOL_DESCRIPTION" and isinstance(node.value, ast.Constant):
                    info["description"] = str(node.value.value)
                elif target == "TOOL_GROUP" and isinstance(node.value, ast.Constant):
                    info["group"] = str(node.value.value)
        info["methods"] = _collect_methods(tree)
        tools.append(info)

    # 元工具（tool_search / food / game / recall）直接注册在 registry.py 里
    meta_tree = ast.parse(_read(pet_path("tools", "registry.py")))
    meta_tools = _collect_registrations(meta_tree)

    return {"tools": tools, "meta_tools": meta_tools}


def _collect_methods(tree: ast.AST) -> list[dict]:
    return [
        method
        for node in _walk_source_order(tree)
        if isinstance(node, ast.Call)
        for method in [_method_from_call(node)]
        if method
    ]


def _collect_registrations(tree: ast.AST) -> list[dict]:
    """从 registry.py 收集 register()/add_method() 的字面量声明。"""
    by_name: dict[str, dict] = {}
    for call in _walk_source_order(tree):
        if not isinstance(call, ast.Call):
            continue
        attr = getattr(call.func, "attr", "")
        args = [a.value for a in call.args if isinstance(a, ast.Constant)]
        if attr == "register" and len(args) >= 1 and isinstance(args[0], str):
            name = args[0]
            by_name.setdefault(name, {
                "name": name,
                "description": args[1] if len(args) > 1 and isinstance(args[1], str) else "",
                "group": _keyword(call, "group", "default"),
                "meta": bool(_keyword(call, "meta", False)),
                "methods": [],
            })
        elif attr == "add_method" and len(args) >= 2:
            tool_name = args[0]
            entry = by_name.setdefault(tool_name, {
                "name": tool_name, "description": "", "group": "default", "meta": False,
                "methods": [],
            })
            method = _method_from_call(call, tool_name=tool_name)
            if method:
                entry["methods"].append(method)
    return list(by_name.values())


def _method_from_call(call: ast.Call, tool_name: str | None = None) -> dict | None:
    if getattr(call.func, "attr", "") != "add_method" or len(call.args) < 3:
        return None
    name_node, desc_node = call.args[1], call.args[2]
    if not (isinstance(name_node, ast.Constant) and isinstance(name_node.value, str)):
        return None
    args_node = _keyword_node(call, "args")
    return {
        "tool": tool_name or _literal(call.args[0]),
        "name": name_node.value,
        "description": _literal(desc_node),
        "args": _parse_args_dict(args_node) if args_node is not None else [],
    }


def _parse_args_dict(node: ast.AST) -> list[dict]:
    if not isinstance(node, ast.Dict):
        return []
    params = []
    for key, value in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(value, ast.Dict):
            continue
        spec: dict = {"name": str(key.value), "type": "str", "required": False,
                      "default": None, "desc": "", "enum": None}
        for spec_key, spec_value in zip(value.keys, value.values):
            if not isinstance(spec_key, ast.Constant):
                continue
            field = str(spec_key.value)
            if field == "type":
                spec["type"] = _literal(spec_value, "str")
            elif field in ("desc", "description"):
                spec["desc"] = _literal(spec_value)
            elif field == "required":
                spec["required"] = bool(_literal(spec_value, False))
            elif field == "default":
                spec["default"] = _literal(spec_value, None)
            elif field == "enum" and isinstance(spec_value, (ast.List, ast.Tuple)):
                spec["enum"] = [_literal(v) for v in spec_value.elts]
        params.append(spec)
    return params


def _keyword(call: ast.Call, name: str, fallback):
    node = _keyword_node(call, name)
    return _literal(node, fallback) if node is not None else fallback


def _keyword_node(call: ast.Call, name: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _literal(node: ast.AST | None, fallback=None):
    if node is None:
        return fallback
    try:
        return ast.literal_eval(node)
    except Exception:
        return f"`{_unparse(node)}`"


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #

def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        cells = [str(c).replace("\n", " ").replace("|", "｜") for c in row]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def _fmt_default(value) -> str:
    if isinstance(value, str):
        return f'`"{value}"`' if value else "`\"\"`"
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_config(meta: dict, tabs: list[tuple[str, list[str]]]) -> str:
    lines = [
        _GENERATED_BY,
        "",
        "# 配置项参考",
        "",
        "配置项定义在 `pet/config.py` 的 `_KEY_META`（唯一真源），这里是它的机械展开。",
        "用户配置写入 `settings.json`（位置见「持久化」一节），`hidden` 的项只能手改文件。",
        "",
        "运行 `python scripts/gen_docs.py` 可重新生成本文件。",
        "",
        "## 设置界面页签",
        "",
        "页面归属由 `pet/ui/settings_window.py` 手写，和 `_KEY_META.category` **不是一回事**"
        "（例如 `MEMORY_*` 的 category 是 `memory`，却显示在「行为」页）。"
        "需要改归属时改界面代码，然后重新生成本文档。",
        "",
    ]

    listed: set[str] = set()
    for label, keys in tabs:
        lines += [f"### {label}", ""]
        if not keys:
            lines += ["_（未解析到配置键）_", ""]
            continue
        rows = []
        for key in keys:
            item = meta.get(key)
            if item is None:
                continue
            listed.add(key)
            rows.append([
                f"`{key}`",
                item["type"],
                _fmt_default(item["default"]),
                "是" if item.get("needs_restart") else "",
                _describe(item),
            ])
        lines += [_table(["键", "类型", "默认", "需重启", "说明"], rows), ""]

    rest = [k for k in meta if k not in listed]
    if rest:
        lines += [
            "## 未在设置界面列出的键",
            "",
            "经 `hidden`（高级设置，仅 settings.json）或未接入界面；按 `category` 分组：",
            "",
        ]
        groups: dict[str, list[str]] = {}
        for key in rest:
            groups.setdefault(meta[key]["category"], []).append(key)
        for category, keys in sorted(groups.items()):
            rows = []
            for key in sorted(keys):
                item = meta[key]
                rows.append([
                    f"`{key}`",
                    item["type"],
                    _fmt_default(item["default"]),
                    "是" if item.get("hidden") else "",
                    "是" if item.get("needs_restart") else "",
                    _describe(item),
                ])
            lines += [
                f"### category = `{category}`",
                "",
                _table(["键", "类型", "默认", "高级", "需重启", "说明"], rows),
                "",
            ]

    lines += [
        "## 字段含义",
        "",
        "- `type`：`str` / `int` / `float` / `bool` / `str_list`，决定 `settings.json` 的取值转换",
        "- `default`：缺省值，用户未覆盖时生效",
        "- `category`：内部归类，用于「改连接要重建客户端、改行为要刷新调度器」这类副作用判断",
        "- `needs_restart`：改动后需重启才生效（界面会提示）",
        "- `hidden`：`true` 表示不在界面显示，只能直接编辑 `settings.json`",
        "- `enum` / `minimum` / `maximum`：取值约束，会写进 `settings-schema.json`",
        "",
        f"共 {len(meta)} 项（其中 {len(rest)} 项未在界面列出）。",
        "",
    ]
    return "\n".join(lines)


def _describe(item: dict) -> str:
    text = item.get("description", "") or ""
    if item.get("enum"):
        options = " / ".join(str(v) for v in item["enum"])
        text = f"{text}（可选：{options}）" if text else f"可选：{options}"
    bounds = []
    if item.get("minimum") is not None:
        bounds.append(f"≥{item['minimum']}")
    if item.get("maximum") is not None:
        bounds.append(f"≤{item['maximum']}")
    return f"{text}（{', '.join(bounds)}）" if bounds else text


def render_actions(data: dict) -> str:
    defs = data["defs"]
    lines = [
        _GENERATED_BY,
        "",
        "# 动作参考",
        "",
        "动作定义在 `pet/action/registry.py`，是 LLM 可输出动作的唯一真源；",
        "`pet/brain/parsing.py` 用它校验 LLM 输出里的动作名。本文件由注册表机械展开。",
        "",
        "## 时长与数量（随调度间隔变化）",
        "",
        "下表按**默认配置**计算；用户改过 `settings.json` 里的调度间隔后，提示词中的实际范围会随之变化。",
        "",
        f"- 动作序列目标总时长：`{data['target']}s`（= `SCHEDULER_MID_MS` × `_SEQUENCE_RATIO`）",
        f"- 单轮最少动作数：`{data['min_count']}`（受 `LLM_ACTION_MIN_DIVISOR` 影响）",
        "",
        "带时长的动作只能在此范围内取值：",
        "",
    ]
    lines += [
        _table(
            ["动作", "最小秒数", "占目标时长比例"],
            [[f"`{name}`", str(floor), str(ratio)] for name, (floor, ratio) in data["durations"].items()],
        ),
        "",
    ]

    for category in _stable_categories(defs.values()):
        rows = []
        for name, item in defs.items():
            if item.category != category:
                continue
            rows.append([
                f"`{name}`",
                " ".join(item.params) if item.params else "无参数",
                item.description,
                f"`{item.usage_example}`" if item.usage_example else "",
            ])
        lines += [
            f"## {category}",
            "",
            _table(["动作", "参数", "说明", "示例"], rows),
            "",
        ]

    lines += [
        "## 注入给 LLM 的样子",
        "",
        "`generate_action_section()` 会把上表渲染成提示词里的 `[可用动作]` 段，"
        "由 `pet/brain/prompts.py` 的 `_PERCEPTION_SECTIONS` 按模式（视觉/非视觉）拼进 system prompt。",
        "这段文本被 `_Lazy` 缓存（首次求值后不再重算，保 system 前缀稳定）；改完 `settings.json` 里"
        "与调度相关的项后，设置界面会调用 `invalidate_action_section()` 让它按新配置重算。",
        "",
        "```text",
        data["sample"].rstrip(),
        "```",
        "",
    ]
    return "\n".join(lines)


def _stable_categories(items) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item.category not in seen:
            seen.append(item.category)
    return seen


def render_effects(data: dict) -> str:
    spawners, default_y = data["spawners"], data["default_y"]
    lines = [
        _GENERATED_BY,
        "",
        "# 粒子特效参考",
        "",
        "特效注册表是 `pet/ui/particle.py` 里的 `_SPAWNERS`（名字 → 生成函数）。",
        "新增特效只需在这里登记：调试面板的测试按钮由 `ParticleWidget.effect_names()` 自动列出，",
        "动作到特效的映射（`pet/pulse` 与 `pet/agent/scheduled_tasks.py`）按同一个名字引用。",
        "",
        _table(
            ["特效名", "生成函数", "默认纵向位置", "说明"],
            [
                [
                    f"`{name}`",
                    f"`{info['func']}()`",
                    _fmt_y(default_y.get(name, "1/3（兜底）")),
                    info["doc"],
                ]
                for name, info in spawners.items()
            ],
        ),
        "",
        "## 位置与生命周期",
        "",
        "- 默认纵向位置是相对宠物窗口高度的比例，`-1` 表示特殊值（脚底）；未登记的特效按 `1/3` 兜底",
        "- 粒子窗口比宠物窗口大 `_MARGIN`（100px），特效在这里面绘制，超出会被裁掉",
        "- 每个粒子按 `lifetime` 淡出（最后 30% 加速），`gravity` 为负值时持续上飘",
        "",
    ]
    return "\n".join(lines)


def _fmt_y(value: str) -> str:
    if value == "-1":
        return "脚底（特殊值）"
    return f"`{value}`"


def render_modules() -> str:
    rows = []
    for path in sorted((ROOT / "pet").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(_read(path))
        summary = _module_summary(tree)
        symbols = _top_symbols(tree)
        rows.append([f"`{rel}`", summary.replace("|", "｜"), symbols])

    lines = [
        _GENERATED_BY,
        "",
        "# 模块清单",
        "",
        "`pet/` 下每个 Python 文件的一行职责与主要定义（取自模块 docstring 与顶层定义）。",
        "包级别的心智模型见 [架构总览](../architecture.md)。",
        "",
        _table(["文件", "职责", "主要定义"], rows),
        "",
        f"共 {len(rows)} 个模块。",
        "",
    ]
    return "\n".join(lines)


def _module_summary(tree: ast.Module) -> str:
    """模块一句话职责：优先 module docstring，没有就退回第一个类的 docstring。"""
    doc = ast.get_docstring(tree) or ""
    if doc.strip():
        return doc.strip().splitlines()[0].strip()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_doc = ast.get_docstring(node) or ""
            if class_doc.strip():
                return class_doc.strip().splitlines()[0].strip()
    return "（模块未提供 docstring）"


def _top_symbols(tree: ast.Module) -> str:
    names = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names.append(f"`{node.name}`")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            names.append(f"`{node.name}()`")
        if len(names) >= 6:
            break
    return " ".join(names)


def render_tools(data: dict) -> str:
    tools, meta_tools = data["tools"], data["meta_tools"]
    groups: dict[str, list[str]] = {}
    for tool in tools:
        groups.setdefault(tool["group"], []).append(tool["name"] or tool["dir"])
    for tool in meta_tools:
        groups.setdefault(tool["group"], []).append(tool["name"])

    lines = [
        _GENERATED_BY,
        "",
        "# 内置工具参考",
        "",
        "工具分组与方法的机械展开；怎么写一个新工具见 [工具开发指南](../tool-development.md)，"
        "测试与提交规范见 [CONTRIBUTING.md](../../CONTRIBUTING.md)。",
        "工具调用名是 `工具名__方法名`（双下划线），参数里的 `aside` 由注册表统一追加。",
        "",
        "## 分组",
        "",
        _table(
            ["分组", "工具"],
            [[f"`{group}`", "、".join(f"`{name}`" for name in names)]
             for group, names in sorted(groups.items())],
        ),
        "",
        "## 独立工具（`pet/tools/<目录>/`）",
        "",
    ]

    for tool in tools:
        title = tool["name"] or tool["dir"]
        lines += [
            f"### `{title}`",
            "",
            f"- 目录：`pet/tools/{tool['dir']}/`",
            f"- 分组：`{tool['group']}`",
            f"- 说明：{tool['description'] or '（模块未声明 TOOL_DESCRIPTION）'}",
            "",
        ]
        lines += [_render_methods(tool["methods"]), ""] if tool["methods"] else ["_（未解析到 add_method 调用）_", ""]

    lines += [
        "## 元工具（注册在 `pet/tools/registry.py`）",
        "",
        "元工具由系统内置，不可禁用，也不出现在工具管理列表中。",
        "",
    ]
    for tool in meta_tools:
        lines += [
            f"### `{tool['name']}`",
            "",
            f"- 分组：`{tool['group']}`",
            f"- 说明：{tool['description'] or '（未解析到描述）'}",
            "",
        ]
        lines += [_render_methods(tool["methods"]), ""] if tool["methods"] else ["_（未解析到 add_method 调用）_", ""]

    return "\n".join(lines)


def _render_methods(methods: list[dict]) -> str:
    rows = []
    for method in methods:
        params = []
        for param in method["args"]:
            note = [param["type"]]
            if param["required"]:
                note.append("必需")
            elif param["default"] is not None:
                note.append(f"默认 {param['default']!r}" if not isinstance(param["default"], str)
                            else f'默认 "{param["default"]}"')
            if param["enum"]:
                note.append("可选：" + "/".join(str(v) for v in param["enum"]))
            desc = param["desc"]
            params.append(f"`{param['name']}`（{', '.join(note)}）{' ' + desc if desc else ''}")
        rows.append([
            f"`{method['name']}`",
            "<br>".join(params) if params else "无参数",
            method["description"],
        ])
    return _table(["方法", "参数", "说明"], rows)


def render_prompt_blocks(data: dict) -> str:
    lines = [
        _GENERATED_BY,
        "",
        "# 提示词块参考",
        "",
        "system prompt 是拼出来的：`pet/brain/prompts.py` 提供块与组合表，",
        "`pet/brain/context_builder.py` 的 `_build_system` 负责运行时注入口（状态/惦记/记忆）。",
        "拼接顺序与缓存相关的取舍见 [架构总览](../architecture.md)。",
        "",
        "## 模块级块常量",
        "",
        _table(
            ["常量", "首行摘要"],
            [[f"`{name}`", _summarize_block(value)] for name, value in data["blocks"].items()],
        ),
        "",
        "## 感知段（按模式）",
        "",
        "`_PERCEPTION_SECTIONS`：每种模式注入哪些块，按顺序。",
        "",
        _table(
            ["模式", "注入的块"],
            [[f"`{mode}`", " → ".join(f"`{name}`" for name in sections)]
             for mode, sections in data["perception"]],
        ),
        "",
        "## 任务段（按任务）",
        "",
        "`_TASK_SECTIONS`：值是构建函数（各任务自己决定段落组成），返回值是字符串列表。",
        "",
        _table(["任务", "构建函数"], [[f"`{task}`", f"`{fn}()`"] for task, fn in data["tasks"].items()]),
        "",
        "## 合法组合",
        "",
        "`build_system_prompt(mode, task)` 只接受下列组合（`_VALID_COMBOS`），",
        "新增模式或任务必须同时更新 `_PERCEPTION_SECTIONS`、`_TASK_SECTIONS` 与这张白名单。",
        "",
        _table(
            ["mode", "task"],
            [[f"`{mode}`", f"`{task}`"] for mode, task in data["combos"]],
        ),
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def render_all() -> dict[Path, str]:
    """生成所有参考文档：{相对路径: 内容}。"""
    return {
        Path("docs/reference/config.md"): render_config(load_config_meta(), load_settings_tabs()),
        Path("docs/reference/actions.md"): render_actions(load_actions()),
        Path("docs/reference/tools.md"): render_tools(load_tools()),
        Path("docs/reference/effects.md"): render_effects(load_effects()),
        Path("docs/reference/prompt-blocks.md"): render_prompt_blocks(load_prompts()),
        Path("docs/reference/modules.md"): render_modules(),
    }


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="生成 docs/reference/*.md")
    parser.add_argument("--check", action="store_true",
                        help="只校验文档是否与代码一致，不写文件（不一致退出码 1）")
    args = parser.parse_args(argv)

    _bootstrap()
    rendered = render_all()

    if args.check:
        stale = []
        for rel, content in rendered.items():
            path = ROOT / rel
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(rel.as_posix())
        if stale:
            print("以下参考文档与代码不一致，请运行 python scripts/gen_docs.py：")
            for name in stale:
                print(f"  - {name}")
            return 1
        print(f"docs/reference 与代码一致（{len(rendered)} 个文件）")
        return 0

    for rel, content in rendered.items():
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"写入 {rel.as_posix()}（{len(content.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
