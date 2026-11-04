# 桌宠命令行环境实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让桌宠具备受控的命令执行能力与一个本地终端窗口：模型经 `shell__run` 执行命令并逐条等待确认，用户经右键菜单直接输入命令；执行过程终止完整进程树，输出按字节上限捕获并截断，每次执行落一条审计记录。

**Architecture:** 四层落地。`pet/shell/` 是纯逻辑包（配置、策略、执行核心、审计、Runner，不依赖 Qt）；`pet/tools/registry.py` 在 `SHELL_ENABLED` 为真时注册元工具并创建 `ShellRunner`；`pet/tools/context.py` 增加确认 sink 与 `confirm()`，保持零依赖；`pet/ui/` 承担确认卡片与终端窗口，`pet/app.py` 负责退出清理。

**Tech Stack:** Python 3.11+、PySide6、pytest（`QT_QPA_PLATFORM=offscreen` 由 `tests/conftest.py` 设置）。

**Spec:** `docs/specs/2026-11-04-shell-terminal-design.md`

## Global Constraints

- 命令一律以参数列表执行，不使用 `shell=True`；Windows 内建命令经固定集合路由到 `cmd /d /s /c`，参数由 `list2cmdline` 从已拆分 token 重建（spec §4.1）。
- 子进程必须 `stdin=DEVNULL`，stdout、stderr 各一个读线程，捕获上限按流计算；禁止 `wait` 配 `PIPE` 的写法（spec §4.2）。
- 超时由同一组配置推导：`method_timeout = confirm_timeout_s + max_command_timeout_s + queue_wait_s + 5`，不得另写字面量（spec §10.2）。
- `ShellRunner` 与执行锁在 `_register_shell_tools()` 内创建，模块级不出现构造调用（ARCH009）。
- `pet/tools/context.py` 不 import `pet.agent`，只调用已绑定对象上的信号与 sink（ARCH001）。
- 注册方法必须传 `output=ToolOutputDefinition(schema=SHELL_OUTPUT)`；`timeout` 参数在 handler 内夹紧，executor 不做范围校验（spec §8）。
- 终止进程树失败返回 `kill_failed`，不静默放行；审计写入失败只记日志，不影响返回（spec §4.2、§7.4）。
- `pet/shell/` 不顶层导入 Qt。
- 每个任务结束跑 `python -m pytest -q` 与 `python scripts/gen_docs.py --check`。

---

## Task 1: 全局开关与 `pet/shell` 纯逻辑（配置、策略）

**Files:**
- Modify: `pet/config.py`（`_KEY_META` 新增 15 个 `SHELL_*` 键，紧随 `FOOD_*` 之后）
- Create: `pet/shell/__init__.py`、`pet/shell/policy.py`
- Create: `tests/test_shell_policy.py`
- Modify: `docs/architecture.md`（§9 模块职责表补 `pet/shell/` 一行）
- Regenerate: `docs/reference/config.md`

**Interfaces:**
- Consumes: `pet.config`。
- Produces（Task 2、3 依赖这些符号）：
  - `check(source, argv, command, cwd) -> Verdict`，`Verdict(decision, reason, matched, resolved)`，`decision ∈ {"allow", "ask", "deny"}`
  - `resolve_program(argv0, cwd, sandbox_root) -> tuple[str, str]`（绝对路径、错误码）
  - `sandbox_cwd(cwd, sandbox_root) -> tuple[str, str]`
  - `build_env() -> dict`（读 `config.SHELL_ENV_ALLOW`）
  - `parse_allow_entry(text) -> tuple[str, list[str]]`（白名单条目归一化）
  - `default_auto_allow() -> list[tuple[str, list[str]]]`（平台默认）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_shell_policy.py`，覆盖 spec §12 的 8 条策略用例。样本在 `tmp_path` 现造，不依赖本机是否安装 `git`：用 `monkeypatch.setattr(shutil, "which", ...)` 控制解析结果。

| 测试类 | 断言要点 |
|---|---|
| `TestMode` | `deny` 返回 `shell_disabled`；`allowlist` 命中白名单返回 `allow`、未命中返回 `deny` 且原因为 `not_allowlisted`；`ask` 对白名单内外都返回 `ask`；`auto` 对白名单内返回 `allow`、对其余返回 `ask` |
| `TestArgsPattern` | `git status`、`git log --oneline`、`git status --short` 在白名单含 `git status` 与 `git log` 时放行；`git push` 转询问；只写 `git` 不匹配任何调用；`dir *` 匹配任意参数；空列表回落平台默认，非空整体替换 |
| `TestInterpreters` | `python -V`、`python -c`、`node -e`、`pwsh -Command`、`wsl -l` 一律 `ask`，即使程序名出现在白名单 |
| `TestDenyPatterns` | 默认清单逐条命中 `ask`；非法正则被跳过且不影响其它条；`auto` 档命中后降级为 `ask`；`allowlist` 档命中返回 `denied_by_pattern` |
| `TestCwd` | 相对路径基于沙箱根；`..` 逃逸返回 `cwd_denied`；绝对路径越界返回 `cwd_denied`；`source="user"` 不校验 |
| `TestHijack` | 沙箱根存在 `git.bat` 时 `auto` 返回 `untrusted_program`；解析到系统目录返回 `allow`；`ask` 档只标注来源不拒绝 |
| `TestEnv` | `LLM_KEY`、`OPENAI_API_KEY`、`GH_TOKEN` 不出现在子进程环境；`PATH` 与 `TEMP` 保留；`SHELL_ENV_ALLOW` 中的普通键被保留 |
| `TestClamp` | `clamp_timeout(999)` 等于 `SHELL_MAX_COMMAND_TIMEOUT_S`；`clamp_timeout(0)` 等于 1；非整数退回默认 |
| `TestPathCase` | 比较函数接受平台参数：Windows 与 macOS 策略下大小写不同不判越界，Linux 策略下区分；`..` 逃逸三个平台都被拒 |

- [ ] **Step 2: 运行测试，确认按预期失败**

Run: `python -m pytest tests/test_shell_policy.py -v`

Expected: 全部 FAIL，`ModuleNotFoundError: No module named 'pet.shell'`。

- [ ] **Step 3: 新增配置键**

`pet/config.py` 的 `_KEY_META` 插入 15 个键，全部 `category: "behavior"`：

```python
    "SHELL_ENABLED":           {"type": "bool",     "default": False, "needs_restart": True,  "hidden": False, "description": "允许模型执行命令"},
    "SHELL_TERMINAL_ENABLED":  {"type": "bool",     "default": True,  "needs_restart": True,  "hidden": False, "description": "右键菜单显示终端"},
    "SHELL_MODE":              {"type": "str",      "default": "ask", "needs_restart": False, "hidden": False, "description": "终端命令放行策略", "enum": ["deny", "allowlist", "ask", "auto"]},
    "SHELL_AUTO_ALLOW":        {"type": "str_list", "default": [],    "needs_restart": False, "hidden": False, "description": "终端命令白名单，逗号分隔，空为平台默认"},
    "SHELL_SANDBOX_ROOT":      {"type": "str",      "default": "",    "needs_restart": False, "hidden": False, "description": "模型命令的工作目录根，留空为桌面"},
    "SHELL_DENY_PATTERNS":     {"type": "str_list", "default": [],    "needs_restart": False, "hidden": True,  "description": "危险命令正则，空为内置清单"},
    "SHELL_ALLOW_SHELL_MODE":  {"type": "bool",     "default": False, "needs_restart": False, "hidden": True,  "description": "允许整条命令交给系统 shell"},
    "SHELL_ENV_ALLOW":         {"type": "str_list", "default": [],    "needs_restart": False, "hidden": True,  "description": "额外透传的环境变量名"},
    "SHELL_COMMAND_TIMEOUT_S": {"type": "int",      "default": 30,    "needs_restart": True,  "hidden": True,  "description": "单条命令默认超时秒数"},
    "SHELL_MAX_COMMAND_TIMEOUT_S": {"type": "int",  "default": 120,   "needs_restart": True,  "hidden": True,  "description": "模型可传的命令超时上限"},
    "SHELL_CONFIRM_TIMEOUT_S": {"type": "int",      "default": 60,    "needs_restart": True,  "hidden": True,  "description": "确认卡片倒计时秒数"},
    "SHELL_QUEUE_WAIT_S":      {"type": "int",      "default": 10,    "needs_restart": True,  "hidden": True,  "description": "执行闸门等待上限"},
    "SHELL_MAX_CAPTURE_BYTES": {"type": "int",      "default": 131072,"needs_restart": False, "hidden": True,  "description": "每流捕获字节上限"},
    "SHELL_MAX_OUTPUT_CHARS":  {"type": "int",      "default": 4000,  "needs_restart": False, "hidden": True,  "description": "进入模型的输出上限，两流合计"},
    "SHELL_HISTORY_LIMIT":     {"type": "int",      "default": 5000,  "needs_restart": False, "hidden": True,  "description": "审计文件行数上限"},
```

- [ ] **Step 4: 实现白名单解析**

`policy.py` 直接读 `pet.config` 实例属性，不新增配置文件。`parse_allow_entry()` 去掉首尾空白后按空格拆分：第一个词是程序名，其余是参数模式；末尾为 `*` 时记为通配。`config.SHELL_AUTO_ALLOW` 为空时回落到 `default_auto_allow()`，非空时整体替换。条目数量少，每次调用直接解析，不做缓存，避免设置页保存后出现新旧两份。

- [ ] **Step 5: 实现 `policy.py`**

判定顺序固定，任一步拒绝即返回：

1. `mode` 为 `deny` 且 `source="model"` → `shell_disabled`；
2. `resolve_program()`：`shutil.which` 失败 → `bad_command`；父目录等于 `cwd` 或 `sandbox_root` 且 `mode="auto"` → `untrusted_program`；
3. `sandbox_cwd()`：`source="model"` 时校验，比较前两侧过 `os.path.normcase`，失败 → `cwd_denied`；
4. 危险模式匹配：`allowlist` 档直接返回 `denied_by_pattern`，其余档把命中项记入 `matched`；
5. `mode="allowlist"`：命中白名单 → `allow`，否则 `not_allowlisted`；
6. `mode="auto"`：程序名在 `interpreters` → `ask`；命中白名单的「程序 + 参数」→ `allow`；否则 `ask`；
7. `mode="ask"`：一律 `ask`。

`build_env()` 以 `os.environ` 为源，只保留白名单键与 `config.SHELL_ENV_ALLOW`，剔除名称含 `KEY`、`TOKEN`、`SECRET`、`PASSWORD` 的键，无论是否出现在该列表中。

- [ ] **Step 6: 运行测试并提交**

Run: `python -m pytest tests/test_shell_policy.py -v`

Expected: 全部 PASS。

---

## Task 2: 执行核心（解析、内建路由、读线程、终止、截断、审计）

**Files:**
- Create: `pet/shell/core.py`、`pet/shell/audit.py`
- Create: `tests/test_shell_core.py`
- Regenerate: `docs/reference/modules.md`

**Interfaces:**
- Consumes: Task 1 的 `config.load()`、`policy.build_env()`。
- Produces（Task 3、5 依赖）：
  - `RunResult(exit_code, stdout, stderr, duration_ms, truncated, error, code)`，`error` 为空表示命令执行完毕
  - `parse_command(command) -> tuple[list[str], str]`（argv、错误码）
  - `route_builtin(argv) -> list[str]`
  - `run(argv, cwd, timeout_s, env) -> RunResult`
  - `truncate_streams(stdout, stderr, max_chars) -> tuple[str, bool]`

- [ ] **Step 1: 写失败测试**

`tests/test_shell_core.py` 覆盖 spec §12 的第 1 至 7 条：

| 测试类 | 断言要点 |
|---|---|
| `TestQuotes` | 未闭合的单引号、双引号返回 `bad_command`；`\"` 与 `\\` 不计入配对；空串返回 `bad_command` |
| `TestParse` | 带空格路径保留为一个 token；Windows 反斜杠不被吞；`posix=False` 时包裹引号被剥掉 |
| `TestBuiltin` | Windows 下 `dir /a` 改写为 `["cmd","/d","/s","/c","dir /a"]`；`cd`、`del`、`set` 不改写；POSIX 下 `ls` 不改写 |
| `TestLargeOutput` | 输出 1MB 的命令 5 秒内返回，`truncated` 为真，`len(stdout) <= max_capture_bytes` |
| `TestTimeout` | `timeout=1` 的长命令返回 `command_timeout`，按 PID 探测进程不存在 |
| `TestKillFailure` | 桩替换终止函数使其失败，返回 `kill_failed` 且错误消息含 PID |
| `TestTruncate` | 两流各头 1500 + 尾 500；省略量等于总长减 2000；`max_chars` 变小时按比例仍不超上限 |
| `TestDecode` | UTF-8 中文、GBK 字节、含 `\xff` 的非法序列均能返回文本且不抛异常 |
| `TestAudit` | 追加后行数正确；超过 `history_limit` 后保留最近 3000 条；写入目录不可写时只记日志 |
| `TestPlatform` | 注入 `os.name="posix"` 时 `ls` 不路由、argv 展示走 `shlex.join`；`killpg` 抛 `ProcessLookupError` 视作终止成功 |

- [ ] **Step 2: 运行测试，确认按预期失败**

Run: `python -m pytest tests/test_shell_core.py -v`

Expected: 全部 FAIL，`ModuleNotFoundError: No module named 'pet.shell.core'`。

- [ ] **Step 3: 实现解析与路由**

`parse_command()` 先做引号配对自检，再按平台选择 `shlex.split` 的 `posix` 参数，最后剥成对引号。`route_builtin()` 只处理 Windows 内建集合，集合内不含写操作与 `cd`。

- [ ] **Step 4: 实现执行与终止**

`run()` 的步骤：`Popen` → 两个读线程 → `wait(timeout)` → 超时则终止进程树并再 `wait(5)` → join 读线程 → 组装 `RunResult`。读线程累计超过 `max_capture_bytes` 后继续读并丢弃，保证子进程不会阻塞在写。

- [ ] **Step 5: 实现审计**

`audit.append(record)` 以 `jsonl` 追加，`audit.load(limit)` 读取末尾若干行，`audit.trim(limit)` 在行数超限时重写。全部异常在函数内捕获并记日志。

- [ ] **Step 6: 运行测试并提交**

Run: `python -m pytest tests/test_shell_core.py -v`

Expected: 全部 PASS。

---

## Task 3: `ShellRunner` 与元工具注册

**Files:**
- Create: `pet/shell/runner.py`
- Modify: `pet/tools/registry.py`（`_register_default_tools()` 增加 `SHELL_ENABLED` 分支与 `_register_shell_tools()`）
- Modify: `pet/brain/prompts.py`（`_SHELL_GUIDE` 与注入条件）
- Create: `tests/test_shell_runner.py`
- Regenerate: `docs/reference/tools.md`、`docs/reference/prompt-blocks.md`

**Interfaces:**
- Consumes: Task 1 的 `config`、`policy`；Task 2 的 `core`、`audit`。
- Produces（Task 4、5 依赖）：
  - `ShellRunner.run(command, cwd="", timeout=0, shell="") -> dict`
  - `ShellRunner.list() -> dict`（策略快照，含 `installed` 探测）
  - `ShellRunner.method_timeout: float`
  - `ShellRunner.set_confirm(fn)`（Task 4 注入）
  - `SHELL_OUTPUT`（模块级 schema 字面量，供 gen_docs 静态解析）

- [ ] **Step 1: 写失败测试**

`tests/test_shell_runner.py` 覆盖：

1. `SHELL_ENABLED` 为假时 `TOOL_REGISTRY.get_tool("shell")` 为 `None`；为真时存在且 `meta`、`side_effect` 为真、`group` 为 `default`；
2. `method_timeout` 等于四项配置之和加 5；
3. 闸门被占用时第二次调用在 `SHELL_QUEUE_WAIT_S` 内返回 `busy`；
4. 确认函数返回 `False` 时返回 `denied_by_user`，且执行核心未被调用（桩计数为 0）；
5. 确认函数未设置时立即返回 `denied_by_user`，不计入确认超时；
6. 非零退出返回正常结果（无 `error` 键），`headline` 含退出码；
7. 审计写入被调用一次，字段含 `source`、`argv`、`cwd`、`exit_code`；
8. `list` 返回平台、沙箱根、白名单、解释器、危险类别与档位；`installed` 与 `shutil.which` 的桩一致；不含正则原文。

- [ ] **Step 2: 运行测试，确认按预期失败**

Run: `python -m pytest tests/test_shell_runner.py -v`

Expected: 全部 FAIL。

- [ ] **Step 3: 实现 `ShellRunner`**

构造时读取配置、创建 `threading.Lock()`、计算 `method_timeout`。`run()` 顺序：夹紧 `timeout` → 获取闸门（带超时）→ `policy.check()` → 需要确认时调用注入的确认函数 → `core.run()` → 写审计 → 组装返回值。`run()` 捕获所有异常并转为 `internal_error`，不让异常穿到 executor。`list()` 读取当前配置生成快照，`installed` 用 `shutil.which` 探测并缓存 60 秒，危险模式只输出类别名。

- [ ] **Step 4: 注册元工具**

`_register_default_tools()` 中：

```python
if config.SHELL_ENABLED:
    self._register_shell_tools()
```

`_register_shell_tools()` 内延迟导入 `ShellRunner`，实例化后 `self.register(...)`，再注册两个方法：`run`（`output=ToolOutputDefinition(schema=SHELL_OUTPUT)`、`timeout=runner.method_timeout`）与 `list`（零参数、复用 `SHELL_OUTPUT`）。

- [ ] **Step 5: 提示词块**

`prompts.py` 增加 `_SHELL_GUIDE`，注入条件是 `SHELL_ENABLED and shell 工具已注册`。正文写明：命令只执行一次、每次都需用户确认、被拒绝或返回 `busy` 时不要重试、有专用工具时优先用专用工具、不确定本机有哪些命令时先调用 `shell__list`。不列具体命令，避免配置变化冲掉 system 前缀缓存。按 `_FOOD_GUIDE` 的方式加入合法组合，不改 `_VALID_COMBOS`。

- [ ] **Step 6: 运行测试并提交**

Run: `python -m pytest tests/test_shell_runner.py -v`；`python scripts/gen_docs.py`。

Expected: 测试 PASS，`tools.md` 出现 `shell` 与 `run`，`prompt-blocks.md` 出现 `_SHELL_GUIDE`。

---

## Task 4: 确认通道与退出清理

**Files:**
- Modify: `pet/tools/context.py`（`set_confirm_sink()`、`confirm()`，保持零依赖）
- Modify: `pet/agent/pet_agent.py`（`confirm_requested` 信号、未决请求登记、中断时统一拒绝）
- Modify: `pet/app.py`（退出函数在 `os._exit` 前清空未决确认）
- Create: `tests/test_shell_confirm.py`

**Interfaces:**
- Consumes: Task 3 的 `ShellRunner.set_confirm`。
- Produces（Task 5 依赖）：
  - `TOOL_CTX.set_confirm_sink(fn | None)`
  - `TOOL_CTX.confirm(request) -> bool`
  - `PetAgent.confirm_requested = Signal(object)`
  - `PetAgent.reject_pending_confirms(reason)`

- [ ] **Step 1: 写失败测试**

`tests/test_shell_confirm.py` 覆盖：

1. sink 未设置时 `confirm()` 立即返回 `False`，耗时小于 50ms；
2. sink 返回 `True` 时 `confirm()` 返回 `True`，请求字段完整；
3. sink 不调用回调时在 `confirm_timeout_s` 后返回 `False`（用 0.2 秒的配置代替）；
4. `reject_pending_confirms()` 后所有未决请求立即返回 `False`；
5. sink 抛异常时按拒绝处理；
6. `context.py` 的模块级没有 `pet.agent` 导入（用 `ast` 检查）。

- [ ] **Step 2: 运行测试，确认按预期失败**

Run: `python -m pytest tests/test_shell_confirm.py -v`

Expected: 全部 FAIL，`AttributeError: set_confirm_sink`。

- [ ] **Step 3: 实现 sink 与 `confirm()`**

`ToolContext` 增加 `_confirm_sink` 与 `_pending_confirms`。`confirm()` 构造请求对象（`threading.Event` + `result`），登记到未决集合，调用 sink，最后 `event.wait(timeout)`；`finally` 中从未决集合移除。

- [ ] **Step 4: 实现信号与中断处理**

`PetAgent` 增加信号，在 `interrupt_current_call` 与 `reject_pending_confirms` 中遍历未决请求置拒绝。信号只用于通知 UI，不携带结果。

- [ ] **Step 5: 退出清理**

`pet/app.py` 的退出函数在 `logging.shutdown()` 与 `os._exit(code)` 之前调用 `reject_pending_confirms("app exit")` 与 `set_confirm_sink(None)`。

- [ ] **Step 6: 运行测试并提交**

Run: `python -m pytest tests/test_shell_confirm.py tests/test_architecture_contracts.py -v`

Expected: 全部 PASS，ARCH001 与 ARCH009 无新增债务。

---

## Task 5: 确认卡片与终端窗口

**Files:**
- Create: `pet/ui/shell_theme.py`、`pet/ui/confirm_card.py`、`pet/ui/terminal_window.py`、`pet/ui/completion.py`
- Modify: `pet/ui/pet_window.py`（右键主菜单「终端」项、`_show_terminal_window`、连接 `confirm_requested`）
- Modify: `pet/ui/settings_window.py`（行为页「终端」控件组）
- Modify: `pet/ui/__init__.py`（模块说明）
- Create: `tests/test_terminal_window.py`、`tests/test_confirm_card.py`、`tests/test_completion.py`

**Interfaces:**
- Consumes: Task 4 的 `confirm_requested` 与 `set_confirm_sink`；Task 3 的 `ShellRunner`。
- Produces: `show_terminal()`、`show_confirm(request)`、`card_position(pet_rect, screen_rect, card_size)`、`clear()`。

- [ ] **Step 1: 写失败测试**

`tests/test_terminal_window.py` 覆盖来源配色分组、5000 块上限、历史去重与上下键取值、多行粘贴只取首行、自动滚动跟随与恢复、用户输入不经确认且以 shell 模式提交、启动时从审计文件回填。执行核心用桩替换。

`tests/test_confirm_card.py` 覆盖 `card_position()` 的四种分支、标题序号推进、`Esc` 拒绝、危险模式下 `Enter` 不触发允许、关闭等同拒绝。位置函数不依赖真实窗口，用 `QRect` 直接断言。

- [ ] **Step 2: 运行测试，确认按预期失败**

Run: `python -m pytest tests/test_terminal_window.py -v`

Expected: 全部 FAIL，`ModuleNotFoundError: pet.ui.terminal_window`。

- [ ] **Step 3: 实现终端窗口**

按 spec §7.1 至 §7.3 的暗色设计实现，不沿用 `log_window.py` 的米白风格；跨线程沿用 `LogRelay` 的模式。输入提交后在独立线程调用 `ShellRunner`（`source="user"`），经 `Signal(str)` 回主线程追加。用户输入默认以 shell 模式提交（Windows `cmd`、POSIX `sh`），状态栏可切换，不经过白名单、危险模式与沙箱判定；超时取 `SHELL_MAX_COMMAND_TIMEOUT_S`。工作目录首次取 `Path.home()`，之后读写 `shell_state.json` 的 `last_cwd`，目录失效时回退；以 `cd ` 开头且只有一个目录参数的输入由窗口层拦截，只改状态栏目录并回显提示，不启动子进程。状态栏的策略切换调用 `config.save("SHELL_MODE", ...)`，与设置页同一份数据；白名单与沙箱根只在设置页编辑，终端不提供编辑入口。色值全部取自 `shell_theme.py`，用 `QTextCharFormat` 按块写入；自动滚动的判据是滚动条值等于最大值；多行粘贴只取首行，其余丢弃并在状态栏提示。

- [ ] **Step 4: 实现确认卡片**

队列 + 单卡展示，标题显示「命令 N/M」。`card_position()` 按 spec §7.6 的四条分支计算位置，四种分支都可脱离窗口断言。顶部 2px 分类条区分常规、危险与可写目录；倒计时用按钮行上方的 1px 收缩线，剩余 10 秒内转琥珀。命中危险模式时「允许」改为红色「仍要执行」并禁用 `Enter`。按钮为全部拒绝、拒绝、允许三个，关闭窗口等同拒绝。

- [ ] **Step 5: 接入右键菜单**

`pet_window.py` 在「日志」之后插入「终端」项，显示条件 `config.SHELL_TERMINAL_ENABLED`。注意不修改工具子菜单对 `meta` 的跳过分支。

- [ ] **Step 6: 设置页控件组**

`settings_window.py` 的行为页新增「终端」组：`SHELL_ENABLED` 与 `SHELL_TERMINAL_ENABLED` 复选框、`SHELL_MODE` 下拉、`SHELL_AUTO_ALLOW` 多行文本、`SHELL_SANDBOX_ROOT` 文本框。白名单占位提示为「逗号分隔，如 git status, git log, dir *」，说明文字提示解释器风险。保存走现有 `config.save()`，两个开关按 `needs_restart` 提示重启。

- [ ] **Step 7: 输入补全**

`pet/ui/completion.py` 提供纯函数 `complete(buffer, cursor, ctx) -> Completion`，按 spec §7.7 处理五类候选来源、公共前缀、引号与大小写；路径基准支持 `~`、`..`、绝对路径与状态栏目录，`cd` 只出目录、`type` 只出文件，大目录先过滤后截断到 500 条。窗口在 `Tab` / `Shift+Tab` / `Esc` 上接入，候选行显示在输入行上方，不写回显区。`PATH` 缓存 60 秒。

- [ ] **Step 8: 运行测试并提交**

Run: `python -m pytest tests/test_terminal_window.py -v`

Expected: 全部 PASS。

---

## Task 6: 文档与生成物收尾

**Files:**
- Modify: `docs/architecture.md`（§9 模块职责、§11 若有新红线）
- Modify: `docs/README.md`（设计文档与实施计划两条索引）
- Regenerate: `docs/reference/tools.md`、`config.md`、`prompt-blocks.md`、`modules.md`
- Modify: `README.md`（工具表增加 `shell` 一行与终端入口说明）

**Steps:**

- [ ] **Step 1: 架构文档**

§9 模块职责表补 `pet/shell/`、`pet/ui/terminal_window.py`、`pet/ui/confirm_card.py`。若引入新的不变量（例如「命令执行必须经 ShellRunner」），写入 §11 并考虑加契约测试。

- [ ] **Step 2: 生成参考文档**

Run: `python scripts/gen_docs.py`

Expected: 四个生成物更新，`shell` 与 `_SHELL_GUIDE` 出现在对应清单中。

- [ ] **Step 3: 校验**

Run: `python -m pytest -q`；`python scripts/gen_docs.py --check`

Expected: 全部 PASS。

- [ ] **Step 4: 手动验收**

按 spec §12 的 14 条清单逐项执行，重点确认：

1. `dir` 在确认后能正常执行（内建路由）；
2. 输出 1MB 的命令不卡住；
3. 同轮两条命令的确认依次显示；
4. 关闭 `SHELL_ENABLED` 后模型看不到工具、终端仍可用；
5. 退出时不因未决确认卡住。

---

## 依赖顺序

```
Task 1 ──> Task 2 ──> Task 3 ──> Task 4 ──> Task 5 ──> Task 6
```

Task 1 与 Task 2 可并行推进（策略与执行核心无相互依赖），但两者都完成后才能进入 Task 3。Task 4 与 Task 5 的分界是「确认逻辑」与「确认界面」，Task 4 先落地可让 Task 5 用桩测试。
