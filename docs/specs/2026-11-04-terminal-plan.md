# 桌宠终端环境实施计划

> 步骤用 `- [ ]` 勾选跟踪。

**Goal:** 让桌宠具备受控的命令执行能力与一个本地终端窗口：模型经 `terminal__run` 执行命令并逐条等待确认，用户经右键菜单直接输入命令；执行过程终止完整进程树，输出按字节上限捕获并截断。

**Architecture:** 三层落地。`pet/terminal/` 是纯逻辑包（配置、策略、执行核心、Runner，不依赖 Qt）；`pet/tools/registry.py` 在 `TERMINAL_ENABLED` 为真时注册元工具并创建 `TerminalRunner`；`pet/tools/context.py` 增加确认 sink 与 `confirm()`，保持零依赖；`pet/ui/` 承担确认卡片与终端窗口，`pet/app.py` 负责退出清理。

**Tech Stack:** Python 3.11+、PySide6、pytest（`QT_QPA_PLATFORM=offscreen` 由 `tests/conftest.py` 设置）。

**Spec:** `docs/specs/2026-11-04-shell-terminal-design.md`

## Global Constraints

- 命令一律以参数列表执行，不使用 `shell=True`；Windows 内建命令经固定集合路由到 `cmd /d /s /c`，参数由 `list2cmdline` 从已拆分 token 重建（spec §4.1）。
- 策略判定读原始 `argv` 与 `builtin`，执行层只拿 `routed`；内建命令不参与程序解析与解释器降级（spec §4.1、§5.4）。
- 子进程必须 `stdin=DEVNULL`，stdout、stderr 各一个读线程，捕获上限按流计算；禁止 `wait` 配 `PIPE` 的写法（spec §4.2）。
- 超时由同一组配置推导：`method_timeout = TERMINAL_CONFIRM_TIMEOUT_S + TERMINAL_MAX_COMMAND_TIMEOUT_S + TERMINAL_QUEUE_WAIT_S + KILL_WAIT_S + OUTPUT_JOIN_S + 3`，不得另写字面量（spec §10.3）。
- `TerminalRunner` 与执行锁在 `_register_terminal_tools()` 内创建，模块级不出现构造调用（ARCH009）。
- `pet/tools/context.py` 不 import `pet.agent`，只调用已绑定对象上的信号与 sink（ARCH001）。
- `run` 传 `output=ToolOutputDefinition(schema=TERMINAL_OUTPUT)`，`list` 传 `schema=TERMINAL_LIST_OUTPUT`；两个 schema 是 `pet/tools/registry.py` 的模块级 dict 字面量，`args` 也是 dict 字面量。命令级 `timeout` 在 handler 内按 spec §8 换算，executor 不做范围校验。
- 模型传 `shell` 时先检查 `TERMINAL_ALLOW_SHELL`，再调用 `core.parse_command(command, shell)`；`source="user"` 跳过该检查（spec §4.1、§8）。
- 确认在获取执行闸门之前完成，等待确认期间不持锁（spec §4.2、§6.2）。
- 终止进程树失败返回 `kill_failed`，不静默放行（spec §4.2）。
- `pet/terminal/` 不顶层导入 Qt。
- 每个任务结束跑该任务最后一步列出的命令：本任务新增测试、`python scripts/gen_docs.py`、`python scripts/gen_docs.py --check`，全部 PASS。

---

## Task 1: 全局开关与 `pet/terminal` 纯逻辑（配置、策略）

**Files:**
- Modify: `pet/config.py`（`_KEY_META` 新增 13 个 `TERMINAL_*` 键，紧随 `FOOD_*` 之后）
- Create: `pet/terminal/__init__.py`、`pet/terminal/policy.py`
- Create: `tests/test_terminal_policy.py`
- Modify: `docs/architecture.md`（§9 模块职责表补 `pet/terminal/` 一行）
- Regenerate: `docs/reference/config.md`

**Interfaces:**
- Consumes: `pet.config`。
- Produces（Task 2、3 依赖这些符号）：
  - `check(source, argv, builtin, command, cwd) -> Verdict`，`Verdict(decision, reason, matched, resolved)`，`decision ∈ {"allow", "ask", "deny"}`
  - `resolve_program(argv0, cwd, sandbox_root) -> tuple[str, str]`（绝对路径、错误码；只解析，不做可写判定）
  - `is_writable_path(path, roots, platform) -> bool`（`realpath` 后与每个根做带边界的 `commonpath` 比较；`platform` 取 `sys.platform`，决定大小写策略）
  - `sandbox_cwd(cwd, sandbox_root, source) -> tuple[str, str]`（`source="user"` 时直接返回 `cwd`）
  - `build_env() -> dict`（读 `config.TERMINAL_ENV_ALLOW`）
  - `clamp_timeout(value, default_s) -> float`（0 或负数取 `default_s`，正数夹到 `[1, config.TERMINAL_MAX_COMMAND_TIMEOUT_S]`）
  - `parse_allow_entry(text) -> tuple[str, list[str]]`（免确认命令条目归一化）
  - `extra_allow() -> list[tuple[str, list[str]]]`（配置里填写的命令）
  - `allowed_entries() -> list[tuple[str, list[str]]]`（配置条目，去重）

- [x] **Step 1: 写失败测试**

新建 `tests/test_terminal_policy.py`，覆盖 spec §12 的 10 条策略用例。样本在 `tmp_path` 现造，不依赖本机是否安装 `git`：用 `monkeypatch.setattr(shutil, "which", ...)` 控制解析结果。

| 测试类 | 断言要点 |
|---|---|
| `TestMode` | `deny` 返回 `terminal_disabled`；`ask` 一律 `ask`；`auto` 对免确认清单内返回 `allow`、对其余返回 `ask` |
| `TestArgsPattern` | `git status`、`git log --oneline`、`git status --short` 在额外命令含 `git status` 与 `git log` 时放行；`git push` 转询问；只写 `git` 不匹配任何调用；`dir *` 匹配任意参数，`dir /a` 在 `auto` 档按配置条目放行；裸 `*` 判为非法条目跳过；空配置不放行任何命令 |
| `TestBuiltin` | `dir /a` 在 `auto` 档命中额外命令 `dir *` 直接放行，不因 `routed[0]` 是 `cmd` 转询问；用户直接输入 `cmd /c dir` 时 `builtin` 为空并按解释器转询问 |
| `TestInterpreters` | `python -V`、`python -c`、`node -e`、`pwsh -Command`、`wsl -l` 一律 `ask`，即使程序名出现在免确认清单 |
| `TestDenyPatterns` | 默认清单逐条命中 `ask`；非法正则被跳过且不影响其它条；`auto` 档命中后降级为 `ask` |
| `TestCwd` | 相对路径基于共享目录；`..` 逃逸返回 `cwd_denied`；绝对路径越界返回 `cwd_denied`；`source="user"` 不校验 |
| `TestHijack` | 沙箱根存在 `git.bat` 时 `auto` 返回 `untrusted_program`；沙箱根子目录 `tools/git.bat` 以绝对路径调用同样命中；解析到系统目录返回 `allow`；`ask` 档只标注来源不拒绝 |
| `TestEnv` | `LLM_KEY`、`OPENAI_API_KEY`、`GH_TOKEN` 不出现在子进程环境；`PATH` 与 `TEMP` 保留；`TERMINAL_ENV_ALLOW` 中的普通键被保留 |
| `TestClamp` | `clamp_timeout(999)` 等于 `TERMINAL_MAX_COMMAND_TIMEOUT_S`；`clamp_timeout(0)` 与 `clamp_timeout(-1)` 取传入的 `default_s`；非整数退回 `default_s` |
| `TestPathCase` | 比较函数接受平台参数：Windows 与 macOS 策略下大小写不同不判越界，Linux 策略下区分；`..` 逃逸三个平台都被拒 |

- [x] **Step 2: 运行测试，确认按预期失败**

Run: `python -m pytest tests/test_terminal_policy.py -v`

Expected: 全部 FAIL，`ModuleNotFoundError: No module named 'pet.terminal'`。

- [x] **Step 3: 新增配置键**

`pet/config.py` 的 `_KEY_META` 插入 13 个键，全部 `category: "behavior"`：

```python
    "TERMINAL_ENABLED":           {"type": "bool",     "default": True,  "needs_restart": True,  "hidden": False, "description": "允许模型执行命令"},
    "TERMINAL_MODE":              {"type": "str",      "default": "ask", "needs_restart": False, "hidden": False, "description": "终端命令放行策略", "enum": ["deny", "ask", "auto"]},
    "TERMINAL_ALLOW":       {"type": "str_list", "default": ["dir *", "ls *", "where *", "whoami *", "hostname *",
                                                                "git status", "git log", "git rev-parse", "git describe"],
                                "needs_restart": False, "hidden": False, "description": "auto 档免确认的命令，逗号分隔，空为全部询问"},
    "TERMINAL_SANDBOX_ROOT":      {"type": "str",      "default": "",    "needs_restart": False, "hidden": False, "description": "用户与模型共用的工作目录根，留空为桌面"},
    "TERMINAL_DENY_PATTERNS":     {"type": "str_list", "default": [],    "needs_restart": False, "hidden": True,  "description": "危险命令正则，空为内置清单"},
    "TERMINAL_ALLOW_SHELL":  {"type": "bool",     "default": False, "needs_restart": False, "hidden": True,  "description": "允许整条命令交给系统 shell"},
    "TERMINAL_ENV_ALLOW":         {"type": "str_list", "default": [],    "needs_restart": False, "hidden": True,  "description": "额外透传的环境变量名"},
    "TERMINAL_COMMAND_TIMEOUT_S": {"type": "int",      "default": 30,    "needs_restart": True,  "hidden": True,  "description": "单条命令默认超时秒数"},
    "TERMINAL_MAX_COMMAND_TIMEOUT_S": {"type": "int",  "default": 120,   "needs_restart": True,  "hidden": True,  "description": "模型可传的命令超时上限"},
    "TERMINAL_CONFIRM_TIMEOUT_S": {"type": "int",      "default": 60,    "needs_restart": True,  "hidden": True,  "description": "确认卡片倒计时秒数"},
    "TERMINAL_QUEUE_WAIT_S":      {"type": "int",      "default": 10,    "needs_restart": True,  "hidden": True,  "description": "执行闸门等待上限"},
    "TERMINAL_MAX_CAPTURE_BYTES": {"type": "int",      "default": 131072,"needs_restart": False, "hidden": True,  "description": "每流捕获字节上限"},
    "TERMINAL_MAX_OUTPUT_CHARS":  {"type": "int",      "default": 4000,  "needs_restart": False, "hidden": True,  "description": "进入模型的输出上限，两流合计"},
```

在 `docs/architecture.md` §9 模块职责表补 `pet/terminal/` 一行；`tests/test_docs.py` 检查新增包是否已登记。

- [x] **Step 4: 实现免确认命令解析**

`policy.py` 直接读 `pet.config` 实例属性，不新增配置文件。`parse_allow_entry()` 去掉首尾空白后按空白拆分：第一个词是程序名，其余是参数模式；末位为 `*` 时记为通配；首词为 `*` 的条目非法，记日志后跳过。`allowed_entries()` 只解析 `config.TERMINAL_ALLOW` 里的条目，重复项跳过。条目数量少，每次调用直接解析，不做缓存，避免设置页保存后出现新旧两份。

- [x] **Step 5: 实现 `policy.py`**

判定顺序固定，任一步拒绝即返回：

1. `source="user"` → `allow`，不进入后续判定；
2. `mode` 为 `deny` → `terminal_disabled`；
3. `builtin` 非空：跳过程序解析，按原始 `argv` 继续后面的判定；
4. `resolve_program()`：先按 `PATHEXT` 在 `cwd` 内探测同名文件，再用 `shutil.which` 解析系统路径，两处都未命中 → `bad_command`；
5. `is_writable_path(..., platform)`：候选路径 `realpath` 后与 `sandbox_root`、`cwd` 做带边界的 `commonpath` 比较，落在任一目录树内时，`auto` 档返回 `untrusted_program`，`ask` 档把标注记入 `untrusted`；
6. `sandbox_cwd(cwd, sandbox_root, source)`：`source="model"` 时校验，比较前两侧过 `os.path.normcase`，失败 → `cwd_denied`；
7. 危险模式匹配：命中项记入 `matched`；
8. `mode="auto"`：命中危险模式或整条 shell 命令 → `ask`；`builtin` 为空且程序名在 `interpreters` → `ask`；命中免确认清单的「程序 + 参数」→ `allow`；否则 `ask`；
9. `mode="ask"`：一律 `ask`。

`build_env()` 以 `os.environ` 为源，只保留固定的允许键与 `config.TERMINAL_ENV_ALLOW`，剔除名称含 `KEY`、`TOKEN`、`SECRET`、`PASSWORD` 的键，无论是否出现在该列表中。

- [x] **Step 6: 运行测试并校验**

Run: `python -m pytest tests/test_terminal_policy.py -v`；`python scripts/gen_docs.py`；`python scripts/gen_docs.py --check`

Expected: 单文件测试 PASS，生成物一致。

---

## Task 2: 执行核心（解析、内建路由、读线程、终止、截断）

**Files:**
- Create: `pet/terminal/core.py`
- Create: `tests/test_terminal_core.py`
- Regenerate: `docs/reference/modules.md`

**Interfaces:**
- Consumes: Task 1 的 `pet.config` 实例属性；`env` 由 `TerminalRunner` 传入 `core.run()`。
- Produces（Task 3、5 依赖）：
  - `RunResult(exit_code, stdout, stderr, duration_ms, truncated, error, code)`，`error` 为空表示命令执行完毕
  - `Parsed(argv, builtin, routed)`
  - `parse_command(command, shell="") -> tuple[Parsed, str]`（解析结果、错误码；`shell` 非空时按 spec §4.1 组装 `routed`，`argv` 为 `[command]`、`builtin` 为空）
  - `run(routed, cwd, timeout_s, env) -> RunResult`
  - `truncate_streams(stdout, stderr, max_chars) -> tuple[str, bool]`（由 `TerminalRunner` 调用）

- [x] **Step 1: 写失败测试**

`tests/test_terminal_core.py` 覆盖 spec §12 的第 1 至 8 条：

| 测试类 | 断言要点 |
|---|---|
| `TestQuotes` | POSIX 下未闭合的单双引号返回 `bad_command`；Windows 下未闭合的双引号返回 `bad_command`，双引号段内的单引号不参与配对（`echo "it's"` 正常解析）；`\"` 与 `\\` 不计入配对；空串返回 `bad_command` |
| `TestParse` | 带空格路径保留为一个 token；Windows 反斜杠不被吞；`posix=False` 时包裹引号被剥掉 |
| `TestBuiltin` | Windows 下 `dir /a` 的 `routed` 为 `["cmd","/d","/s","/c","dir /a"]`、`builtin` 为 `dir`、`argv` 保持原样；`cd`、`del`、`set` 的 `builtin` 为空；POSIX 下 `ls` 的 `builtin` 为空；`shell="cmd"` 时 `routed` 为 `["cmd","/d","/s","/c",command]`、`builtin` 为空、`argv` 为 `[command]` |
| `TestLargeOutput` | 输出 1MB 的命令 5 秒内返回，`truncated` 为真，`len(stdout) <= max_capture_bytes` |
| `TestTimeout` | `timeout=1` 的长命令返回 `command_timeout`，按 PID 探测进程不存在 |
| `TestKillFailure` | 桩替换终止函数使其失败，返回 `kill_failed` 且错误消息含 PID |
| `TestTruncate` | 默认配置下两流各头 1500 + 尾 500；省略量等于总长减 2000；`max_chars=1000` 时每流配额 500、头 375 尾 125，两流合计不超上限 |
| `TestDecode` | UTF-8 中文、GBK 字节、含 `\xff` 的非法序列均能返回文本且不抛异常 |
| `TestPlatform` | 注入 `os.name="posix"` 时 `ls` 不路由、argv 展示走 `shlex.join`；`killpg` 抛 `ProcessLookupError` 视作终止成功 |

- [x] **Step 2: 运行测试，确认按预期失败**

Run: `python -m pytest tests/test_terminal_core.py -v`

Expected: 全部 FAIL，`ModuleNotFoundError: No module named 'pet.terminal.core'`。

- [x] **Step 3: 实现解析与路由**

`parse_command(command, shell="")` 在 `shell` 为空时按平台拆分：Windows 分支用状态机自检引号（双引号段内的单引号不计配对），POSIX 分支交给 `shlex` 抛错；随后按平台选择 `shlex.split` 的 `posix` 参数，最后剥成对引号，填充 `argv`。内建路由在同一函数内完成：Windows 命中固定集合时填 `builtin` 并生成 `routed`，其余平台 `builtin` 为空、`routed` 等于 `argv`；集合内不含写操作与 `cd`。`shell` 非空时按 spec §4.1 的平台表组装 `routed`，`argv` 为 `[command]`，`builtin` 为空。

- [x] **Step 4: 实现执行与终止**

`run()` 的步骤：`Popen` → 两个读线程 → `wait(timeout)` → 超时则终止进程树并再 `wait(5)` → join 读线程 → 组装 `RunResult`。读线程累计超过 `max_capture_bytes` 后继续读并丢弃，保证子进程不会阻塞在写。`env` 由 `TerminalRunner` 传入，`core` 不读配置。

- [x] **Step 5: 运行测试并校验**

Run: `python -m pytest tests/test_terminal_core.py -v`；`python scripts/gen_docs.py`；`python scripts/gen_docs.py --check`

Expected: 单文件测试 PASS，生成物一致。

---

## Task 3: `TerminalRunner` 与元工具注册

**Files:**
- Create: `pet/terminal/runner.py`
- Modify: `pet/tools/registry.py`（`_register_default_tools()` 增加 `TERMINAL_ENABLED` 分支与 `_register_terminal_tools()`）
- Modify: `pet/brain/prompts.py`（`_TERMINAL_GUIDE` 与注入条件）
- Create: `tests/test_terminal_runner.py`
- Regenerate: `docs/reference/tools.md`、`docs/reference/prompt-blocks.md`

**Interfaces:**
- Consumes: Task 1 的 `config`、`policy`；Task 2 的 `core`。
- Produces（Task 4、5 依赖）：
  - `TerminalRunner.run(command, cwd="", timeout=0, shell="", source="model") -> dict`
  - `TerminalRunner.list() -> dict`（符合 `TERMINAL_LIST_OUTPUT` 的策略快照，含 `installed` 探测）
  - `TerminalRunner.method_timeout: float`
  - `TERMINAL_OUTPUT`、`TERMINAL_LIST_OUTPUT`（`pet/tools/registry.py` 的模块级 dict 字面量，供 `gen_docs.py` 静态解析）

- [x] **Step 1: 写失败测试**

`tests/test_terminal_runner.py` 覆盖：

1. `TERMINAL_ENABLED` 为假时 `TOOL_REGISTRY.get_tool("terminal")` 为 `None`；为真时存在且 `meta`、`side_effect` 为真、`group` 为 `default`；
2. `method_timeout` 等于三项配置之和加 `KILL_WAIT_S`、`OUTPUT_JOIN_S` 与 3 秒余量，默认 200；
3. 闸门被占用时第二次调用在 `TERMINAL_QUEUE_WAIT_S` 内返回 `busy`；
4. 确认函数返回 `False` 时返回 `denied_by_user`，且执行核心未被调用（桩计数为 0）；
5. 确认函数未设置时立即返回 `denied_by_user`，不计入确认超时；
6. 非零退出返回正常结果（无 `error` 键），`headline` 含退出码；
7. `list` 返回 `TERMINAL_LIST_OUTPUT` 声明的字段，`headline` 与 `body` 非空；`installed` 与 `shutil.which` 的桩一致；不含正则原文；
8. `TERMINAL_ALLOW_SHELL` 为假时模型传 `shell` 返回 `shell_mode_disabled`，开关为真时按平台组装 `routed`，`run_user` 不受限制。

- [x] **Step 2: 运行测试，确认按预期失败**

Run: `python -m pytest tests/test_terminal_runner.py -v`

Expected: 全部 FAIL。

- [x] **Step 3: 实现 `TerminalRunner`**

构造时读取配置、创建 `threading.Lock()`、计算 `method_timeout`。`run()` 顺序：`source="model"` 且 `shell` 非空时检查 `TERMINAL_ALLOW_SHELL` → `core.parse_command(command, shell)` → `policy.clamp_timeout(timeout, default_s)`，`default_s` 按 `source` 取 `TERMINAL_COMMAND_TIMEOUT_S` 或 `TERMINAL_MAX_COMMAND_TIMEOUT_S` → `policy.check(source, argv, builtin, command, cwd)`，`source="user"` 在首步放行 → 需要确认时调用 `TOOL_CTX.confirm()`（不持闸门）→ 获取闸门（带超时）→ `policy.build_env()` → `core.run(routed, cwd, timeout_s, env)` → `core.truncate_streams(stdout, stderr, config.TERMINAL_MAX_OUTPUT_CHARS)` → 组装返回值。截断结果填 `body`，完整输出交终端窗口。`run()` 捕获所有异常并转为 `internal_error`，不让异常穿到 executor。`list()` 返回 `TERMINAL_LIST_OUTPUT` 声明的字段，`installed` 用 `shutil.which` 探测并缓存 60 秒，危险模式只输出类别名。

- [x] **Step 4: 注册元工具**

`_register_default_tools()` 中：

```python
if config.TERMINAL_ENABLED:
    self._register_terminal_tools()
```

`_register_terminal_tools()` 内延迟导入 `TerminalRunner`，实例化后 `self.register(...)`，再注册两个方法：`run`（`args` 为 dict 字面量、`timeout=runner.method_timeout`、`output=ToolOutputDefinition(schema=TERMINAL_OUTPUT)`）与 `list`（`args={}`、`output=ToolOutputDefinition(schema=TERMINAL_LIST_OUTPUT)`）。两个 schema 常量定义在本文件模块级；`gen_docs.py` 只展开同文件与 `pet/tools/output.py` 内的 dict 字面量。

- [x] **Step 5: 提示词块**

`prompts.py` 增加 `_TERMINAL_GUIDE`，注入条件是 `TERMINAL_ENABLED and shell 工具已注册`。正文写明：命令只执行一次、每次都需用户确认、被拒绝或返回 `busy` 时不要重试、有专用工具时优先用专用工具、不确定本机有哪些命令时先调用 `terminal__list`。不列具体命令，避免配置变化冲掉 system 前缀缓存。按 `_FOOD_GUIDE` 的方式在 `guides` 追加处注入，条件为 `TERMINAL_ENABLED` 且工具已注册；`_VALID_COMBOS` 只校验 `(mode, task)` 组合，与提示词块无关，不改动。

- [x] **Step 6: 运行测试并校验**

Run: `python -m pytest tests/test_terminal_runner.py -v`；`python scripts/gen_docs.py`；`python scripts/gen_docs.py --check`

Expected: 单文件测试 PASS，生成物一致；`tools.md` 出现 `terminal__run`、`terminal__list` 及其参数与输出字段，`prompt-blocks.md` 出现 `_TERMINAL_GUIDE`。

---

## Task 4: 确认通道与退出清理

**Files:**
- Modify: `pet/tools/context.py`（`set_confirm_sink()`、`confirm()`，保持零依赖）
- Modify: `pet/agent/pet_agent.py`（`confirm_requested` 信号、未决请求登记、中断时统一拒绝）
- Modify: `pet/app.py`（`_shutdown()` 回调清空未决确认并注销 sink）
- Create: `tests/test_terminal_confirm.py`

**Interfaces:**
- Consumes: Task 3 的 `TerminalRunner`（确认经 `TOOL_CTX.confirm()` 进入）。
- Produces（Task 5 依赖）：
  - `TOOL_CTX.set_confirm_sink(fn | None)`
  - `TOOL_CTX.confirm(request) -> bool`
  - `PetAgent.confirm_requested = Signal(object)`
  - `PetAgent.request_confirm(request)`（入队、写 `sequence` 与 `total`、无展示中卡片时 emit）
  - `PetAgent.next_confirm()`（当前卡片结束后推进下一条）
  - `PetAgent.reject_pending_confirms(reason)`

- [x] **Step 1: 写失败测试**

`tests/test_terminal_confirm.py` 覆盖：

1. sink 未设置时 `confirm()` 立即返回 `False`，耗时小于 50ms；
2. sink 返回 `True` 时 `confirm()` 返回 `True`，请求字段含 `argv`、`builtin`、`routed`、`command`、`cwd`、`shell_mode`、`timeout_s`、`source`、`matched_patterns`、`untrusted`；
3. sink 不调用回调时在 `confirm_timeout_s` 后返回 `False`（用 0.2 秒的配置代替）；
4. `reject_pending_confirms()` 后所有未决请求立即返回 `False`；
5. sink 抛异常时按拒绝处理；
6. `context.py` 的模块级没有 `pet.agent` 导入（用 `ast` 检查）。

- [x] **Step 2: 运行测试，确认按预期失败**

Run: `python -m pytest tests/test_terminal_confirm.py -v`

Expected: 全部 FAIL，`AttributeError: set_confirm_sink`。

- [x] **Step 3: 实现 sink 与 `confirm()`**

`ToolContext` 增加 `_confirm_sink` 与 `_pending_confirms`。`confirm()` 接收 spec §6.1 字段完整的请求对象（含 `threading.Event` 与 `result`），登记到未决集合，调用 sink，最后 `event.wait(config.TERMINAL_CONFIRM_TIMEOUT_S)`；`finally` 中从未决集合移除。

- [x] **Step 4: 实现队列与信号**

`PetAgent` 持有确认队列：`request_confirm()` 入队并写 `sequence` 与 `total`，无展示中卡片时 emit `confirm_requested`；`next_confirm()` 在当前卡片结束后推进下一条。`interrupt_current_call` 与 `reject_pending_confirms` 遍历未决请求置拒绝并清空队列。信号只用于通知 UI，不携带结果。

- [x] **Step 5: 退出清理**

`pet/app.py` 的 `_shutdown()` 回调（`aboutToQuit`）调用 `reject_pending_confirms("app exit")` 与 `set_confirm_sink(None)`。该回调在 `app.exec()` 返回前触发，覆盖 `os._exit(code)` 与 `sys.exit(code)` 两条分支；`atexit` 不追加。

- [x] **Step 6: 运行测试并校验**

Run: `python -m pytest tests/test_terminal_confirm.py tests/test_architecture_contracts.py -v`；`python scripts/gen_docs.py`；`python scripts/gen_docs.py --check`

Expected: 单文件测试 PASS，生成物一致，ARCH001 与 ARCH009 无新增债务。

---

## Task 5: 确认卡片与终端窗口

**Files:**
- Create: `pet/ui/confirm_card.py`、`pet/ui/terminal_window.py`、`pet/ui/completion.py`
- Modify: `pet/ui/styles.py`（`TERMINAL_*` 暗色色板、字体回退链、圆角与间距）
- Modify: `pet/ui/pet_window.py`（右键主菜单「终端」项、`_show_terminal_window`、连接 `confirm_requested`）
- Modify: `pet/ui/settings_window.py`（行为页「终端」控件组）
- Modify: `pet/ui/__init__.py`（模块说明）
- Create: `tests/test_terminal_window.py`、`tests/test_confirm_card.py`、`tests/test_completion.py`

**Interfaces:**
- Consumes: Task 4 的 `confirm_requested` 与 `set_confirm_sink`；Task 3 的 `TerminalRunner`。
- Produces: `show_terminal()`、`show_confirm(request)`、`card_position(pet_rect, screen_rect, card_size)`、`clear()`。

- [x] **Step 1: 写失败测试**

`tests/test_terminal_window.py` 覆盖来源配色分组、5000 块上限、历史去重与上下键取值、多行粘贴只取首行、自动滚动跟随与恢复、用户输入不经确认且以 shell 模式提交、隐藏后重新显示保留内容。执行核心用桩替换。

`tests/test_confirm_card.py` 覆盖 `card_position()` 的四种分支、标题序号推进、`Esc` 拒绝、危险模式下 `Enter` 不触发允许、关闭等同拒绝。位置函数不依赖真实窗口，用 `QRect` 直接断言。

`tests/test_completion.py` 覆盖 spec §12 的补全用例：首词候选、`PATH` 与目录合并、目录类与文件类命令的候选过滤、公共前缀、唯一候选、引号与空格处理、平台大小写、`~` 与 `..` 基准、绝对路径、大目录截断到 500 条。目录与 `PATH` 数据由桩提供，不访问真实文件系统。

- [x] **Step 2: 运行测试，确认按预期失败**

Run: `python -m pytest tests/test_terminal_window.py -v`

Expected: 全部 FAIL，`ModuleNotFoundError: No module named 'pet.ui.terminal_window'`。

- [x] **Step 3: 实现终端窗口**

按 spec §7.1 至 §7.3 的暗色设计实现，不沿用 `log_window.py` 的米白风格；跨线程沿用 `LogRelay` 的模式。输入提交后在独立线程调用 `TerminalRunner.run_user(...)`，经 `Signal(dict)` 回主线程追加阶段、块起点、分块输出与结束事件。用户输入默认以 shell 模式提交（Windows `cmd`、POSIX `sh`），状态栏可切换，不经过免确认清单、危险模式与沙箱判定；超时取 `TERMINAL_MAX_COMMAND_TIMEOUT_S`。工作目录取 `TERMINAL_SANDBOX_ROOT`（留空为桌面）并与 `TerminalRunner` 共享，用户 `cd` 后模型命令跟着走；以 `cd ` 开头且只有一个目录参数的输入由窗口层拦截，只改输入框内的目录提示，不启动子进程、不写回显。档位只在设置页修改，终端不提供切换入口；额外命令与工作目录根同样只在设置页编辑。色值全部取自 `styles.py` 的 `TERMINAL_*`，用 `QTextCharFormat` 按块写入；自动滚动的判据是滚动条值等于最大值；多行粘贴只取首行，其余丢弃并在输入行提示。

- [x] **Step 4: 实现确认卡片**

队列 + 单卡展示，标题用 `sequence` 与 `total`；卡片关闭后调用 `PetAgent.next_confirm()` 推进下一条。`card_position()` 按 spec §7.6 的四条分支计算位置，四种分支都可脱离窗口断言。顶部 2px 分类条区分常规、危险与可写目录；倒计时用按钮行上方的 1px 收缩线，剩余 10 秒内转琥珀。命中危险模式时「允许」改为红色「仍要执行」并禁用 `Enter`。按钮为全部拒绝、拒绝、允许三个，关闭窗口等同拒绝。

- [x] **Step 5: 接入右键菜单**

`pet_window.py` 在「日志」之后插入「终端」项，始终显示。该项挂在主菜单一级，不进入「工具 → 工具名」子菜单；不修改工具子菜单对 `meta` 的跳过分支。

- [x] **Step 6: 设置页控件组**

`settings_window.py` 的通用页新增「终端」组：`TERMINAL_ENABLED` 复选框、`TERMINAL_MODE` 下拉、`TERMINAL_ALLOW` 单行文本、`TERMINAL_SANDBOX_ROOT` 文本框。额外命令占位提示为「逗号分隔，如 git status, git log, dir *」；每个标签右侧有「?」标记，tooltip 注明解释器不受免确认清单影响。保存走现有 `config.save()`，`TERMINAL_ENABLED` 的「（需重启）」写在标签文字里。

- [x] **Step 7: 输入补全**

`pet/ui/completion.py` 提供纯函数 `complete(buffer, cursor, ctx) -> Completion`，按 spec §7.7 的四类候选来源计算：首词为目录条目与 `PATH` 可执行名的并集，其余 token 按首词常量表过滤目录或文件；`ctx` 提供终端当前目录、平台大小写策略与 `PATH` 缓存。公共前缀、引号与大小写、路径基准（`~`、`..`、绝对路径、状态栏目录）按 spec §7.7 实现；大目录先过滤后截断到 500 条。候选不含 `/` 内建命令与工具方法名。窗口在 `Tab` / `Shift+Tab` / `Esc` 上接入，按 `Tab` 直接轮换候选，不显示候选行；`PATH` 缓存 60 秒。

- [x] **Step 8: 运行测试并校验**

Run: `python -m pytest tests/test_terminal_window.py tests/test_confirm_card.py tests/test_completion.py -v`；`python scripts/gen_docs.py`；`python scripts/gen_docs.py --check`；`python -m pytest -q`

Expected: 单文件测试 PASS，生成物一致。

---

## Task 6: 文档与生成物收尾

**Files:**
- Modify: `docs/architecture.md`（§9 模块职责、§11 若有新红线）
- Regenerate: `docs/reference/tools.md`、`config.md`、`prompt-blocks.md`、`modules.md`
- Modify: `README.md`（工具表增加 `terminal` 一行与终端入口说明）

**Steps:**

- [x] **Step 1: 手写文档**

`docs/architecture.md` §9 模块职责表补 `pet/ui/terminal_window.py` 与 `pet/ui/confirm_card.py`；`pet/terminal/` 的登记在 Task 1。`README.md` 工具表增加 `terminal` 一行，菜单说明增加「终端」入口。若引入新的不变量，写入 §11 并加契约测试。

- [x] **Step 2: 生成参考文档**

Run: `python scripts/gen_docs.py`

Expected: 四个生成物更新，`terminal__run`、`terminal__list` 与 `_TERMINAL_GUIDE` 出现在对应清单中。

- [x] **Step 3: 校验**

Run: `python -m pytest tests/test_terminal_core.py tests/test_terminal_policy.py tests/test_terminal_runner.py tests/test_terminal_confirm.py tests/test_terminal_window.py tests/test_confirm_card.py tests/test_completion.py tests/test_architecture_contracts.py -q`；`python scripts/gen_docs.py --check`

Expected: 全部 PASS。

- [ ] **Step 4: 手动验收**

按 spec §12 的 23 条清单逐项执行，重点确认：

1. `dir` 在确认后能正常执行（内建路由）；
2. 输出 1MB 的命令不卡住；
3. 同轮两条命令的确认依次显示；
4. 关闭 `TERMINAL_ENABLED` 后模型看不到工具、终端仍可用；
5. 退出时不因未决确认卡住。

---

## Task 7: 终端本地命令

**Files:**
- Create: `pet/cli/`（`__init__.py` 只导出公共 API，实现分在 `types.py` / `catalog.py` / `builtins.py` / `dispatch.py`）、`tests/test_cli.py`、`tests/test_tool_commands.py`
- Modify: `pet/tools/registry.py`（`ToolCommand`、`add_command()`、`commands()`，`recall` 的 `:memory` 登记）
- Modify: `pet/tools/timer/__init__.py`、`pet/tools/todo/__init__.py`、`pet/tools/knowledge/__init__.py`（`register_commands()` 与参数翻译，`parse_duration()` 移到 timer）
- Modify: `pet/ui/terminal_window.py`（`local_finished` 信号、`_submit` 分流、本地回显块、补全上下文）
- Modify: `pet/ui/completion.py`（冒号命令与子命令候选、`Completion.append_space`）
- Modify: `tests/test_completion.py`、`tests/test_terminal_window.py`（补全用例）
- Modify: `pet/ui/__init__.py`（模块说明）
- Modify: `docs/architecture.md`（§9 补 `pet/cli/`、§11 加「终端命令由工具自己登记」）

**Interfaces:**
- Consumes: `pet.terminal.core.parse_command()`、`pet.terminal.policy.check()`、`pet.terminal.runner.TerminalRunner.list()`、`pet.tools.executor.ToolExecutor.execute_one()`、`pet.tools.registry.TOOL_REGISTRY.commands()`。
- Produces: `is_local(text)`、`run(text, cwd, runner) -> LocalResult`、`commands()`、`command_names()`、`subcommands()`、`path_commands()`；注册表侧 `ToolCommand`、`ToolRegistry.add_command()`、`ToolRegistry.commands()`。

- [x] **Step 1: 写失败测试**

`tests/test_cli.py` 覆盖前缀判定、命令分发与三种返回码、`:check` 判定随档位与免确认清单变化、`:status` 与 `:policy` 的快照字段、时长解析、工具调用经 `ToolExecutor` 发出的方法名与参数、工具未注册与 handler 报错。注册表查询与执行器用桩替换。

- [x] **Step 2: 实现命令模块与登记接口**

冒号前缀只由窗口层处理。`pet/cli` 只定义内建命令（`help` / `clear` / `status` / `policy` / `check`），其余命令从 `TOOL_REGISTRY.commands()` 聚合：工具在 `register()` 里用 `add_command()` 声明用法、分组、参数翻译、补全候选与示例，与文件气泡按钮、右键菜单项同一模式。工具命令一律构造 `ToolCall` 交给 `ToolExecutor.execute_one()`，不直接调工具内部实现；`:check` 以 `source="model"` 调用 `policy.check()`，只读。内建命令表是模块级字面量，处理函数只登记不调用（ARCH009）。

- [x] **Step 3: 接入终端窗口**

`_submit()` 在 `cd` 与盘符切换之后、`_busy` 闸门之前分流本地命令；本地命令在独立线程执行并经 `local_finished` 回主线程渲染，与流式块共用同一把闸门。回显块带 `local` 标记，块尾写「本地 · 0.0xs」，不写 exit。

- [x] **Step 4: 接补全**

`completion.py` 增加冒号分支：首词以 `:` 开头时只从 `ctx.local_commands` 取名，比较不区分大小写；首词是已登记命令时按 `ctx.local_subcommands` 补子命令，`ctx.local_path_commands` 里的命令补 PATH 程序名与目录条目。`Completion` 增加 `append_space` 表示候选是词，窗口只在唯一候选被接受时补空格。候选数据由 `pet.cli` 的 `command_names()` / `subcommands()` / `path_commands()` 从注册表凑出，补全层不另立清单。

- [x] **Step 5: 校验**

Run: `python -m pytest tests/test_cli.py tests/test_tool_commands.py tests/test_terminal_window.py tests/test_completion.py tests/test_architecture_contracts.py -q`；`python scripts/gen_docs.py`；`python scripts/gen_docs.py --check`

Expected: 全部 PASS，`modules.md` 出现 `pet/cli/__init__.py`。

---

## 依赖顺序

```
Task 1 ──> Task 2 ──> Task 3 ──> Task 4 ──> Task 5 ──> Task 6
                                     └──────> Task 7
```

Task 7 依赖 Task 5 的终端窗口与 Task 3 的 `TerminalRunner`，与 Task 6 无先后关系。

Task 2 依赖 Task 1 的 `_KEY_META` 新增键，两者不能并行。Task 3 依赖 Task 1 的 `build_env()` 与 Task 2 的 `parse_command()`。Task 4 与 Task 5 的分界是「确认逻辑」与「确认界面」，Task 4 先落地可让 Task 5 用桩测试。
