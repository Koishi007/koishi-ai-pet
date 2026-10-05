# 面向 Agent 的架构契约测试设计

> 把 `docs/architecture.md`、`docs/tool-development.md` 与 ADR 中靠人工审查维持的结构约束，翻译成可执行的 pytest 契约测试；失败输出面向 Agent：哪里错了、为什么错、应该怎么改、去哪看正确模式。

## §0 背景

当前仓库已经有文档一致性测试，集中在 `tests/test_docs.py`：

- `docs/reference/*.md` 由 `scripts/gen_docs.py --check` 防漂移；
- `docs/architecture.md` 必须覆盖 `pet/` 下的包与顶层模块；
- 文档相对链接、`docs/README.md` 索引、ADR 索引都会被检查。

这些测试能保证“文档结构不漂移”，但还不能保证“代码遵守文档写下的设计红线”。

`docs/architecture.md` §11 与 `CONTRIBUTING.md` §6 里有多条需要人守的规则：依赖方向只能自上而下、工具目录必须有注册入口、平台后端接口要一致、重依赖要延迟导入、不要新增跨对象私有访问。这些规则如果只靠人工 review，在 Agent 代工代码越来越多后会变成高成本瓶颈。

## §1 核心思想：测试 / 工具即反馈

本设计不把结构测试当成普通 linter。普通 linter 的错误消息主要面向人类，默认读者知道项目背景与修复方式；Agent 看到这类消息时，往往只能根据关键词猜测，容易走错方向。

这里的测试输出要成为 Agent 下一轮修复的输入。每条失败都应该同时回答五个问题：

1. **在哪里错**：文件、行号、触发证据；
2. **违反了什么规则**：稳定的 rule id + 清晰规则文本；
3. **为什么这是问题**：对应的分层或运行时约束；
4. **应该怎么修**：首选修复路径，而不是泛泛地说“不要这样”；
5. **看哪里学正确模式**：项目内示例与文档引用。

典型输出形态：

```text
ERROR [ARCH001]: Layer violation
- FILE: pet/tools/weather/core.py:12
- IMPORT: from pet.ui.speech_bubble import SpeechBubble
- VIOLATION: tools layer must not import ui/agent directly.
- WHY: tools are loaded independently and must drive the pet through TOOL_CTX, not UI objects.
- FIX: Use TOOL_CTX from pet.tools.context, or add a callback to TOOL_CTX and wire it in pet/app.py.
- EXAMPLE: See pet/tools/timer/core.py for TOOL_CTX.register_alarm usage.
- REFERENCE: docs/architecture.md §11; docs/decisions/0009-layering-by-singletons-and-deferred-imports.md
```

同一条失败也保留机器可读字段，方便未来独立脚本或 CI artifact 汇总：

```json
{
  "rule": "ARCH001",
  "title": "Layer violation",
  "file": "pet/tools/weather/core.py",
  "line": 12,
  "evidence": "from pet.ui.speech_bubble import SpeechBubble",
  "violation": "tools layer must not import ui/agent directly",
  "why": "tools are loaded independently and must drive the pet through TOOL_CTX",
  "fix": {
    "strategy": "replace direct UI dependency with TOOL_CTX callback",
    "preferred_module": "pet.tools.context",
    "wiring_module": "pet.app"
  },
  "example": "pet/tools/timer/core.py",
  "reference": [
    "docs/architecture.md §11",
    "docs/decisions/0009-layering-by-singletons-and-deferred-imports.md"
  ]
}
```

第一版不做自动修复器。测试只负责高质量反馈，修复仍由 Agent 或开发者完成。

## §2 目标

- 把最容易被 Agent 写坏、但最适合静态检查的设计红线变成 pytest。
- 错误消息面向 Agent 优化，包含明确修复路径、示例与文档引用。
- 检查过程只做 AST 与文件系统扫描，不 import 业务模块，不触发 Qt、数据库、配置、网络或工具依赖安装。
- 对现有设计债采用 allowlist 冻结：旧问题可暂时存在，新问题不再扩散。
- 失败规则稳定编号，便于 CI、Agent prompt、后续文档引用与分阶段收敛。

## §3 非目标

- 不从自然语言 docs 自动推导规则。docs 负责解释“为什么”，测试里的规则表负责执行“检查什么”。
- 不要求一次性清零 `docs/architecture.md` §14 记录的历史设计债：旧债进 allowlist，新债失败。
- 不引入额外第三方 linter 或架构图工具；第一版只用 Python 标准库和 pytest。
- 不检查所有可想象的代码风格问题；只检查与架构边界、导入副作用、工具契约强相关的结构约束。
- 不检查静态 AST 判不了的运行时约束（线程亲和、Qt 信号连接类型、调用时序），它们仍靠评审。
- 不把需要 import 业务模块的检查（如导入顺序冒烟测试）并入本套，与 §2 的隔离原则冲突。
- 不在 CI 中输出大段模型提示词或全文档内容，避免失败日志噪声过大。

## §4 规则总览

第一版建议新增 `tests/test_architecture_contracts.py`，内部用轻量规则表表达约束。

| Rule | 检查对象 | 拦截什么 | 首选修复反馈 |
|---|---|---|---|
| `ARCH001` | `pet/**/*.py` 的 import AST | 下层直接 import 上层：`tools` / `brain` / `action` / `pulse` / `food` / `game` / `voice` 依赖 `ui` / `agent` | 改走模块级单例或回调，例如 `TOOL_CTX`；必要时在 `pet/app.py` 集中装配 |
| `ARCH002` | 函数内 import AST | 无理由的延迟 import，或未登记的函数内跨层 import | 只有打断循环依赖、推迟 Qt / 平台后端 / playwright 这两类允许；否则移到文件头或改结构 |
| `ARCH003` | `pet/tools/<name>/` 文件系统 + `__init__.py` AST | 工具目录缺 `TOOL_NAME` / `TOOL_DESCRIPTION` / `register()`，或 `TOOL_NAME` 与目录名不一致 | 按 `docs/tool-development.md` 的注册入口模板补齐 |
| `ARCH004` | 平台 detector 模块 AST | `win_detector.py` / `mac_detector.py` / `linux_detector.py` 对外函数集合不一致 | 三个平台保持同一接口；缺哪个后端函数就补哪个 |
| `ARCH005` | 顶层 import AST | 纯逻辑模块顶层 import Qt / playwright / 平台重依赖 | 把重依赖推迟到真正使用处，或把 Qt 逻辑移到 UI 层 |
| `ARCH006` | 属性访问 AST | 新增跨对象私有成员访问 | 给被访问对象新增公开方法，或把协作提升到调用方；旧债登记在 allowlist |
| `ARCH007` | `assets/actions/*` 文件系统 | 动作素材目录缺同名 json，或 json 名称与目录不匹配 | 按 `docs/subsystems/assets-pipeline.md` 的素材契约补齐 |
| `ARCH008` | 包内 import AST 构图 | 新增的包内 import 环（SCC），如 `pet.brain` 自引用、`pet.tools.todo` ↔ `panel` | 把共享状态抽到第三个模块，或改成子模块直导断开回绕；已知两处进 allowlist |
| `ARCH009` | 模块级语句 AST | 模块级实例化、开文件/数据库、连网、起线程等导入期副作用 | 推迟到首次使用；模块级只留常量、类型、函数/类定义与注册表登记 |
| `ARCH000` | 规则内置的 allowlist 自身 | allowlist 条目 stale（不再命中任何违规） | 删除该条目；确需保留则重写 `reason` 与 `reference` |

`ARCH000` 是元规则，不扫代码，只校验其他规则的 allowlist 是否过期；必须在所有规则跑完并聚合输出后
执行（见 §6）。

`ARCH007` / `ARCH008` / `ARCH009` 可以作为第二批规则。如果第一版要尽快落地，先做 `ARCH000`～`ARCH006`。

## §5 规则细节

### §5.1 `ARCH001`：模块依赖方向

分层规则来自 `docs/architecture.md` §1 与 §11：

- `ui` 只管画与点；
- `agent` 管编排；
- `brain` 管模型交互与记忆；
- `action` 管动作执行；
- `pulse` 管数值；
- `tools` 是被 LLM 调用的外部能力；
- 下层不反向 import 上层。

实现上维护一张显式映射：

```python
LAYER_BY_PACKAGE = {
    "pet.ui": "ui",
    "pet.agent": "agent",
    "pet.brain": "brain",
    "pet.action": "action",
    "pet.pulse": "pulse",
    "pet.tools": "tools",
    "pet.food": "food",
    "pet.game": "game",
    "pet.voice": "voice",
}
```

第一版不建立完整有向无环图，只拦截 `docs/architecture.md` §11.13 那条硬红线：

- `pet.tools.*` 不允许 import `pet.ui.*` / `pet.agent.*`；
- `pet.brain.*` 不允许 import `pet.ui.*` / `pet.agent.*`；
- `pet.action.*` 不允许 import `pet.ui.*`，除已知历史形态或确认为动作播放入口的模块；
- `pet.pulse.*` 不允许 import `pet.ui.*` / `pet.agent.*` / `pet.brain.*`；
- `pet.food.*` / `pet.game.*` / `pet.voice.*` 不允许 import `pet.ui.*` / `pet.agent.*`；
- `pet.tools.context` 不允许 import 任何其他 `pet.*` 模块。

本规则只判 `docs/architecture.md` §11.13 那条硬红线（其余包不得 import `ui` / `agent`），**不判下层
彼此之间的先后**：`brain` ↔ `action`、`brain` ↔ `tools` 这类双向依赖是既有形态，不据此报错；
本版也不建完整 DAG。

失败反馈要给出具体替代路径：

- 工具驱动 UI / 动作 / 记忆：使用 `pet.tools.context.TOOL_CTX`；
- 需要装配真实实现：在 `pet/app.py` 的 `main()` 中接线；
- 需要动作名：依赖 `pet.action.registry`，不依赖 UI 动画实现；
- 需要配置：依赖 `pet.config.config`。

### §5.2 `ARCH002`：函数内延迟 import

文档允许函数内 import 的理由只有两类：

1. 打断循环依赖；
2. 推迟重依赖：Qt / 平台后端 / playwright。

测试可以识别所有非顶层 import，并要求命中 allowlist 或重依赖白名单。

allowlist 条目的形态（数据契约见 §5.9）：

```python
ALLOWED_DEFERRED_IMPORTS = {
    # (文件, 被导入模块) -> reason + reference
    ("pet/brain/memory.py", "pet.config"): {
        "reason": "无理由的历史延迟 import，待清理",
        "reference": "docs/architecture.md §11.15",
    },
}
```

函数内的 `pet.*` import 有几十处，多为合理的重依赖延迟，所以要分成两条通道，避免 allowlist 膨胀：

- **重依赖白名单**（命中即放行，不写原因）：目标模块落在 §5.5 的 `HEAVY_IMPORTS`，或属于平台后端 /
  Qt 子窗口 / playwright 这类明确允许推迟的依赖；
- **allowlist**（必须写原因与依据）：既不是重依赖、也不是环的例外，即**待清偿的债**。

只有 allowlist 参与 §5.9 的 stale 校验。

失败反馈不能只说“不要函数内 import”，而要区分：

- 如果不是重依赖也不是循环依赖：移到文件头；
- 如果是跨层调用：优先拆接口或改走单例回调；
- 如果确实是合理例外：登记到 allowlist，并在原因里写明约束。

### §5.3 `ARCH003`：工具目录契约

规则来自 `docs/architecture.md` §11 第 8 条与 `docs/tool-development.md`。

对 `pet/tools/` 下每个包含 `__init__.py` 的一级目录检查：

- 必须定义 `TOOL_NAME`；
- 必须定义 `TOOL_DESCRIPTION`；
- 必须定义函数 `register(registry)`；
- `TOOL_NAME` 默认要求等于目录名；
- 不允许提交 `config.json`；
- `config.example.json` 与 `requirements.txt` 只作为可选文件存在。

失败反馈示例：

```text
ERROR [ARCH003]: Invalid tool package contract
- FILE: pet/tools/calendar/__init__.py
- VIOLATION: register(registry) is missing.
- FIX: Add a register(registry) function and call registry.register(...) / registry.add_method(...).
- EXAMPLE: See docs/tool-development.md §register() 模板.
- REFERENCE: docs/architecture.md §11; docs/tool-development.md
```

模块级导入期副作用由 `ARCH009`（§5.8）负责，不并入本规则。

### §5.4 `ARCH004`：平台 detector 接口一致性

规则来自 `docs/architecture.md` §12：“三个平台保持同一接口”。

第一版固定检查：

```python
WINDOW_DETECTOR_API = {
    "is_window_alive",
    "get_window_rect",
    "is_window_occluded",
    "get_visible_windows",
}
```

目标文件：

- `pet/brain/win_detector.py`
- `pet/brain/mac_detector.py`
- `pet/brain/linux_detector.py`

失败反馈要指出缺失函数与其他平台的参考实现。

### §5.5 `ARCH005`：重依赖顶层 import

规则来自 `docs/architecture.md` §11 第 12、15 条与 `CONTRIBUTING.md` §6：纯逻辑模块不要在导入期拖入 Qt 或平台后端。

第一版黑名单：

```python
HEAVY_IMPORTS = {
    "PySide6",
    "playwright",
    "win32gui",
    "win32con",
    "win32process",
    "Quartz",
    "AppKit",
    "Xlib",
}
```

允许范围：

- `pet/ui/**` 可以顶层 import PySide6；
- `pet/app.py` 可以顶层 import Qt 启动相关对象；
- 工具面板 `pet/tools/*/panel.py` 可以顶层 import PySide6，但只能由所在工具的 `__init__.py`
  **函数内**延迟 import，以保持工具包无 Qt 可加载；
- 平台 detector 后端可以 import 对应平台库；
- 工具实现如浏览器工具应延迟 import playwright，而不是顶层 import。

失败反馈要明确建议：

- 如果是 UI 代码：移动到 `pet/ui/`；
- 如果是工具重依赖：在 handler 内部或首次调用路径中延迟 import；
- 如果是平台后端：放到对应 `*_detector.py`。

### §5.6 `ARCH006`：跨对象私有成员访问冻结

规则来自 `docs/architecture.md` §11 第 14 条与 §14 “跨对象的私有访问”。

检测面要覆盖三种写法，不能只看 `obj._name`：

1. `ast.Attribute`：`behavior._flush_pending_summaries`、`_MemoryRetriever._format_memory_time`；
2. `ast.ImportFrom` 的私有别名：`from pet.brain.memory import _MemoryRetriever`、
   `from pet.action.registry import _DURATION_ACTION_DEFS`；
3. `sys.modules[...]` 后再取私有属性：`sys.modules["pet.tools.todo"]._panel`。

避免误报：

- `self._name` / `cls._name` 允许；
- 模块级常量定义与 `_KEY_META` / `_SPAWNERS` 这类真源常量不在本规则处理；
- 已知历史债登记到 allowlist（依据 `docs/architecture.md` §14 的表，逐条落到「文件 + 属性名」，
  数据契约与 stale 校验见 §5.9）；
- 新增跨对象访问失败。

allowlist 要尽量对应文档 §14 的清单，并把目标写细到文件 + 属性名。失败反馈给两个修法：

1. 给被访问对象加公开方法或属性；
2. 把协作逻辑提升到共同调用方，避免一个对象直接窥探另一个对象内部。

这条规则最容易误报，第一版可以先做成保守规则：不追复杂表达式，只认字面量下标与直接别名。

### §5.7 `ARCH008`：包内 import 环

`docs/architecture.md` §14 已登记两处环（`pet.brain` 自引用、`pet.tools.todo` ↔ `panel`），
把它们放进 allowlist 只是冻结，新环仍须失败。

实现：对每个包（如 `pet/brain/`、`pet/tools/todo/`）内部模块做 AST 构图，求强连通分量（SCC）；
命中 SCC 且不在 allowlist 中即失败。失败反馈要给出环上的节点路径与建议的断点。

### §5.8 `ARCH009`：模块级导入期副作用

规则来自 `docs/architecture.md` §11.12。

允许留在模块级的语句：`import`、常量 / 类型 / `__all__` 赋值、`def` / `class`、注册表登记
（如 `register(...)` 调用）。此外的模块级调用一律可疑，典型是模块级实例化、开文件或数据库、
连网、起线程。

失败反馈要说明副作用发生在导入期：测试与文档脚本靠顶替模块隔离，导入期副作用会被连带执行。

### §5.9 `ARCH000`：allowlist 有效性与 stale 校验

**为什么需要它。** 测试只会因为**出现**违规而红，不会因为违规**消失**而红。没有 stale 校验，
allowlist 就只增不减。分工因此是：**新增条目靠人/Agent 判断，删除条目靠测试报。**

**数据契约。** 每条 allowlist 条目 = 匹配键 + 元信息：

```python
{
    # ARCH006：文件 + 符号
    ("pet/app.py", "behavior._save_context"): {
        "reason": "历史耦合，待把 behavior 的入口公开化",
        "reference": "docs/architecture.md §14",
        "issue": 123,         # 可选：跟踪 issue 编号，供 CI 之外的人工流程消费
    },
}
```

- 匹配键粒度由规则决定：`ARCH001` / `ARCH002` 是 `(文件, 被导入模块)`；`ARCH006` 是 `(文件, 符号)`；
  `ARCH008` 是包内环的节点集合；
- `reason` 必填，一句话说明为什么现在还不能修；
- `reference` 必填，指向 `docs/architecture.md` 的条款 - **没有依据的条目不允许新增**；
- `issue` 可选。

**校验流程。**

1. 正常跑完所有规则、聚合收集违规（§6 要求不因第一条失败而中断，这里正好依赖这一点）；
2. 记录本次运行中**被命中过**的 allowlist 键；
3. 全部规则结束后逐条比对：未被任何违规命中的条目即 stale。

**按规则分组，只比对本次真正执行的规则。** 未实现或被跳过的规则（例如第一批不含 `ARCH008`）
不参与 stale 判定，否则会误报。

**失败反馈。**

```text
ERROR [ARCH000]: Stale allowlist entry
- ENTRY: ("pet/brain/memory.py", "pet.config")
- VIOLATION: allowlist entry matches no current violation; the debt is presumably paid.
- WHY: a stale entry makes the allowlist grow monotonically and destroys its value as a ledger.
- FIX: Delete this entry. If the exemption is still needed, rewrite `reason` and `reference`.
- REFERENCE: docs/specs/2026-09-30-agent-oriented-architecture-feedback-design.md §5.9
```

**与「重依赖白名单」的分工。** 命中重依赖白名单（§5.2）的延迟 import 不进 allowlist，也不参与
stale 判定 - 它们是长期合理形态，不存在「清偿」。

## §6 输出格式设计

测试内部定义一个 `ContractViolation` 数据结构：

```python
@dataclass(frozen=True)
class ContractViolation:
    rule: str
    title: str
    file: Path
    line: int | None
    evidence: str
    violation: str
    why: str
    fix: str
    example: str | None
    reference: tuple[str, ...]
```

失败时聚合输出，避免 pytest 第一条失败就中断后续扫描。输出顺序固定：

1. rule id；
2. file path；
3. line；
4. evidence；
5. violation；
6. why；
7. fix；
8. example；
9. reference。

建议格式：

```text
Architecture contract violations found: 2

ERROR [ARCH001]: Layer violation
- FILE: pet/tools/foo/core.py:8
- EVIDENCE: from pet.ui.pet_window import PetWindow
- VIOLATION: tools layer must not import ui/agent directly.
- WHY: tools must be loadable without Qt and must drive UI through TOOL_CTX.
- FIX: Replace the direct UI dependency with TOOL_CTX, then wire the callback in pet/app.py if needed.
- EXAMPLE: pet/tools/timer/core.py
- REFERENCE: docs/architecture.md §11; docs/decisions/0009-layering-by-singletons-and-deferred-imports.md

ERROR [ARCH003]: Invalid tool package contract
...
```

第一版 JSON 不一定要写入文件。可以先让 `ContractViolation` 保持字段化，pytest 输出人类文本；如果后面要给 Agent 自动消费，再增加 `--contract-json` 或独立脚本。

## §7 文件改动清单

| 文件 | 改动 |
|---|---|
| `tests/test_architecture_contracts.py` | 新增结构契约测试、规则表、AST 扫描、面向 Agent 的失败格式 |
| `docs/architecture.md` | 补齐规则依据：外围模块层级、导入期副作用、依赖方向边界、私有访问的三种写法 |
| `docs/README.md` | 登记本文档 |

如果实现时发现 `tests/test_architecture_contracts.py` 过大，可以拆出 `tests/architecture_contracts.py` 作为测试辅助模块；第一版优先单文件，降低结构成本。

## §8 验证方式

实现完成后至少运行：

```bash
python -m pytest tests/test_architecture_contracts.py
python -m pytest tests/test_docs.py
```

合入前运行：

```bash
python -m pytest
python scripts/gen_docs.py --check
```

这套测试必须满足：

- 不 import `pet` 业务模块；
- 不创建或修改用户配置；
- 不要求 Qt 可用；
- 不联网；
- 不安装工具私有依赖；
- 失败消息可直接指导 Agent 修改；
- stale 校验可独立验证：人为删掉一条仍被命中的条目应失败，人为保留一条已失效的条目也应失败。

## §9 风险与缓解

### 误报

AST 静态检查无法完整理解运行时依赖，尤其是私有访问和函数内 import。

缓解：第一版只做保守规则；确认为合理例外时进入 allowlist，allowlist 必须写原因和文档引用。

### 规则与 docs 双写

规则表会和 docs 中的文字形成双写。

缓解：docs 只解释原则和理由，测试规则只表达可执行约束；每条规则都引用 docs。修改原则时，同时改 docs 与规则。

### Agent 过度照着 FIX 硬改

如果 `FIX` 写得太绝对，Agent 可能把建议性修复当成唯一方案。

缓解：把修复分成两类：

- **确定性修复**：例如工具层调用 UI，明确改走 `TOOL_CTX`；
- **建议性修复**：例如跨对象私有访问，提示“优先新增公开方法或上提协作”，不指定唯一代码形态。

### 现有设计债导致测试无法落地

仓库已经承认少量依赖环和私有访问。

缓解：旧债 allowlist 化，新债失败；清偿后由 `ARCH000`（§5.9）提示删除条目。

### allowlist 的收敛边界

条目是**待清偿的债**，不是永久白名单：任意一次改动清偿了某笔债，就该在同一次改动里删掉对应条目，
忘记删由 `ARCH000`（§5.9）报错。

不同条目的清偿难度不同：无理由的函数内 import 之类可以随改动顺手清掉，而
`pet/action/action.py` 与 gravity 的私有耦合需要专门重构。所以验收标准是**每次改动后 allowlist
与代码一致**，不是「allowlist 某天为空」。

## §10 被否决的方案

### 方案 A：只靠文档和 review

这是现状。问题是 Agent 代工时代 review 成本会越来越高，且很多结构破坏在功能测试中不一定暴露。

### 方案 B：直接引入第三方架构 linter

可以减少自写扫描逻辑，但第一版会带来额外依赖、配置格式、跨平台稳定性和错误消息定制成本。当前规则数量少，标准库 AST 足够。

### 方案 C：做自动修复器

自动修复看起来适合 Agent，但第一版风险过高：架构违规常常需要判断调用关系和对象边界，机械改动容易引入更大的设计问题。先把反馈做好，等规则稳定后再考虑局部自动修复。

### 方案 D：从 docs 自然语言自动生成规则

这会让 docs 写法被测试实现绑架，也很难做到确定性。更好的边界是：docs 解释原则，测试规则表执行原则，二者通过 rule id 和 reference 连接。

## §11 后续演进

第一版稳定后，可以继续扩展：

- 把规则输出汇总成 JSON artifact，供 Agent 自动读取；
- 在 PR 模板中增加“结构契约测试失败时先读 FIX 字段”的说明；
- 把 `ARCH007` 素材契约、`ARCH008` 包内环、`ARCH009` 导入期副作用、配置项 UI 可见性、
  prompt 静态前缀稳定性纳入同一套反馈格式；
- allowlist 的 `issue` 字段接入自动检查（对应 issue 已关闭则提示删除）；
- 对 allowlist 做「必须对应 `docs/architecture.md` §14 条目」的反向校验。

本设计的成功标准是先把最容易反复犯、最适合机器判断的结构红线变成稳定反馈，让 Agent 能在失败结果里直接看到正确修复路径。
