# 桌宠终端环境设计

> 给恋恋一份可交互的终端：模型通过 `terminal__run` 执行命令，用户通过右键主菜单的「终端」打开窗口直接输入命令。两条入口共用同一个执行核心与同一份策略；模型发起的命令默认逐条等待用户确认，用户自己输入的命令免确认。执行结果同时进入 LLM 上下文与终端窗口，窗口按来源标记区分，执行记录落盘可回溯。模型能力与终端入口由两个独立开关控制。

## §0 背景与现状

| 项 | 现状 | 位置 |
|---|---|---|
| 工具注册 | `register()` + `add_method(output=...)`，输出契约必填 | `pet/tools/registry.py` |
| 元工具 | `meta=True` 的工具在 `default` 组常驻，不受 `TOOLS_ENABLED` 过滤，不进右键工具列表与提示词工具清单 | `pet/tools/registry.py`、`pet/brain/prompts.py` |
| 参数校验 | 按 `args` spec 校验类型、必填、枚举；不做数值范围夹紧 | `pet/tools/executor.py` |
| 超时语义 | handler 在独立线程执行，`worker.join(method.timeout)` 超时只返回错误，线程与子进程都不终止 | `pet/tools/executor.py` |
| 工具并行 | `LLM_TOOL_PARALLEL` 默认为真；同轮多调用交给线程池并行，含 `game__` 时退化为串行 | `pet/brain/tool_loop.py` |
| 菜单入口 | `add_menu_action` 定义在 `pet/tools/registry.py`，项由 `pet/ui/pet_window.py` 挂成「主菜单 → 工具 → 工具名 → 项」第三级；`meta` 工具整项跳过，其子菜单不可达 | `pet/tools/registry.py`、`pet/ui/pet_window.py` |
| 工具上下文 | `TOOL_CTX` 保持零依赖，不 import `pet.agent`，只操作已绑定的对象 | `pet/tools/context.py`、ARCH001 |
| 模块级约束 | ARCH009 禁止模块级出现定义与登记之外的调用，包括创建锁与实例 | `tests/test_architecture_contracts.py` |
| 退出路径 | brain 线程未退出时走 `logging.shutdown()` 后 `os._exit(code)`，跳过解释器清理 | `pet/app.py` |
| 文档生成 | `tools.md` 静态解析 `registry.py` 的 `register()` / `add_method()` 字面量，不判断注册条件 | `scripts/gen_docs.py` |
| 副作用标记 | `side_effect` 只写入工具调用事件的 payload，供事件日志与回放使用 | `pet/brain/tool_loop.py`、`pet/brain/base.py` |
| 窗口范式 | 无边框窗口 + 跨线程 `Signal` 桥接 + `deque` 缓冲 + 块数上限 | `pet/ui/log_window.py` |
| 文件能力 | `file_ops` 限桌面/文档，不做命令执行 | `pet/tools/file_ops/` |

三处约束直接影响设计：工具调用是并行的，确认不能假设串行；executor 的超时不终止子进程，终止必须由执行核心自己完成；`TOOL_CTX` 不能反向依赖 agent。

## §1 目标

- 模型可通过 `terminal__run` 执行单条命令，返回退出码、标准输出、标准错误。
- 用户可通过右键主菜单的「终端」打开窗口，直接输入并执行命令。
- 模型发起的命令默认等待用户确认；拒绝、超时、界面不可用一律不执行。
- 用户输入的命令以 `source="user"` 标记，跳过确认；沙箱只约束模型发起的命令。
- 同轮多个工具调用并行到达时，确认排队串行展示，执行串行，不出现并发弹卡与并发执行。
- 命令解析为参数列表后直接执行；Windows 内建命令显式经 `cmd /c` 路由。
- 超时与异常路径下终止整个进程树；终止失败如实报错，不静默放行。
- 输出按字节上限捕获、按头尾截断进入模型上下文，完整输出留在终端窗口。
- 模型可通过 `terminal__list` 查询当前策略快照：平台、沙箱根、免确认的程序与参数、解释器名单、程序的安装情况。
- 两类失败分开处理：命令非零退出属于正常结果；策略拒绝与执行异常走工具失败通道。

## §2 非目标

- 不做持久 shell 会话：`cd`、`activate`、`export` 不跨命令生效，工作目录靠 `cwd` 参数显式传递。
- 不做终端仿真：不解析 ANSI 转义序列，不支持需要 TTY 的交互式程序（`vim`、`top`、`ssh` 登录）。
- 模型路径不解析管道、重定向与变量展开；需要这些特性时显式选择 `shell="pwsh"` 或 `shell="cmd"`。用户输入默认交给系统 shell，因此终端里这些语法可用。
- 不为 POSIX 模拟 Windows 内建命令；内建路由只在本平台生效。
- 不做面向模型的流式回传：命令结束后一次性返回。终端窗口在用户命令执行期间按块追加显示，返回值不受影响。
- 不额外落盘执行记录：模型命令已作为工具调用进入事件日志，终端回显只存在内存。
- 不引入进程间通信，不做外部 CLI 客户端。
- 不改动现有分组激活机制与工具循环结构。

## §3 总体结构

| 组件 | 职责 | 依赖 |
|---|---|---|
| `pet/terminal/core.py` | 命令解析、内建命令路由、子进程执行、终止进程树、解码与截断 | 标准库 |
| `pet/terminal/policy.py` | 策略档位、免确认命令、危险模式、程序解析、cwd 与环境变量 | `pet.config`、标准库 |
| `pet/terminal/runner.py` | `TerminalRunner` 实例：执行闸门（带超时的锁）、调用 core 与 policy、生成策略快照 | 上述三者 |
| `pet/tools/registry.py` | `_register_terminal_tools()` 内置注册元工具，创建 `TerminalRunner` 实例 | registry、`pet/terminal` |
| `pet/ui/terminal_window.py` | 只读回显、输入行、来源标记、命令历史 | Qt |
| `pet/ui/confirm_card.py` | 确认卡片：队列展示、倒计时、允许与拒绝 | Qt |
| `pet/tools/context.py` | `set_confirm_sink()` 与 `confirm(request)`，零依赖 | 已绑定的 agent 对象 |
| `pet/agent/pet_agent.py` | 确认队列、`confirm_requested` 信号、中断与退出时统一拒绝 | Qt |

数据流（模型发起）：

1. 工具循环调用 `terminal__run`，handler 在工具线程执行。
2. `TerminalRunner` 检查 `shell` 开关，调用 `core.parse_command(command, shell)` 得到 `Parsed(argv, builtin, routed)`。
3. `policy.check()` 给出 `allow` / `ask` / `deny`；`ask` 时调用 `TOOL_CTX.confirm()` 进入确认队列，等待期间不持执行闸门。
4. 确认通过后获取执行闸门，等待超过 `queue_wait_s` 返回 `busy`；以 `policy.build_env()` 的结果调用 `core.run(routed, cwd, timeout_s, env)`。
5. `TerminalRunner` 调用 `core.truncate_streams()` 生成 `body`，完整输出写入终端窗口。
6. handler 返回规范值，投影层渲染为模型可见文本。

数据流（用户发起）：

1. 终端窗口输入行提交，`source="user"`；`policy.check()` 首步直接返回 `allow`，不做档位、免确认清单、危险模式、程序解析与沙箱校验，也不进入确认队列。`shell` 开关检查同样跳过。
2. 与模型命令共用解析、闸门、`build_env()` 与 `core.run()`；用户输入默认带 `shell` 取值，见 §7.4。
3. 结果写回终端窗口，不进入 LLM 上下文。

`TerminalRunner` 在 `_register_terminal_tools()` 内创建，不在模块级构造；锁是实例属性，不触发 ARCH009。

## §4 执行核心

### §4.1 命令解析与内建命令路由

`TerminalRunner` 调用 `core.parse_command(command, shell)` 拆分命令：

```python
parsed, err = parse_command(command, shell="")
```

- `shell` 为空时按平台拆分 argv；`shell` 非空时 `command` 原样作为单个参数，`Parsed.argv` 为 `[command]`，`builtin` 为空串，`routed` 按下表组装。
- POSIX 用 `shlex.split(posix=True)`，未闭合引号由 `shlex` 抛 `ValueError`，转为 `bad_command`。
- Windows 用 `posix=False`，保留 `C:\path\to\file` 中的反斜杠，随后对每个 token 剥掉成对的包裹引号。
- `posix=False` 不报未闭合引号，拆前用状态机自检：`\` 转义下一个字符；进入双引号段后段内的 `'` 不计入配对，段未闭合返回 `bad_command`。双引号段外的 `'` 在 Windows 上按普通字符处理。`echo "it's"` 因此解析为 `["echo", "it's"]`。
- 拆分结果为空时返回 `bad_command`。

Windows 内建命令不在 `PATH` 里，`shutil.which` 找不到，必须显式路由。`parse_command()` 一次给出三份结果 `Parsed(argv, builtin, routed)`：

- `argv`：拆分后的原始 token，供策略判定。
- `builtin`：命中的内建命令名，未命中为空串。
- `routed`：执行用 argv；`builtin` 非空时改写为 `["cmd", "/d", "/s", "/c", subprocess.list2cmdline(argv)]`，否则等于 `argv`。
- 内建集合固定：`dir`、`type`、`echo`、`cls`、`ver`、`where`、`findstr`、`tree`、`vol`、`path`、`attrib`、`more`、`sort`。
- `cd`、`set`、`copy`、`move`、`del`、`md`、`rd`、`ren` 不进入集合：`cd` 不跨命令生效，其余是写操作。
- 参数由 `list2cmdline` 从已拆分的 token 重建，不把用户原文拼进命令行。
- 路由在策略判定之后生效：策略读 `argv` 与 `builtin`，执行层只拿 `routed`。`builtin` 非空时策略跳过程序解析，免确认清单按 `dir` 这类原始程序名匹配；`routed[0]` 是 `cmd`，不触发 `TERMINAL_ALLOW_SHELL`。

`shell` 取值的平台差异：

| 平台 | 取值 | `routed` |
|---|---|---|
| 全部 | `""` | `argv` 或内建路由结果 |
| Windows | `"pwsh"` | `["powershell", "-NoProfile", "-NonInteractive", "-Command", command]` |
| Windows | `"cmd"` | `["cmd", "/d", "/s", "/c", command]` |
| POSIX | `"sh"` | `["sh", "-c", command]` |
| POSIX | `"bash"` | `["bash", "-c", command]` |

`shell` 非空时危险模式按 `command` 原文匹配，免确认清单按 `argv[0]` 字面匹配；`auto` 档一律转 `ask`。方法的 `enum` 在注册时按 `os.name` 生成，POSIX 上不出现 `pwsh` / `cmd`。展示 argv 时 Windows 用 `subprocess.list2cmdline`、POSIX 用 `shlex.join`，两者不通用。

`shell` 模式由 `TERMINAL_ALLOW_SHELL` 控制，默认关闭。`TerminalRunner` 在调用 `parse_command()` 前检查：`source="model"` 且开关为假时返回 `shell_mode_disabled`；`source="user"` 不检查该开关。开启时确认卡片标注「整条命令交给 shell」。

### §4.2 进程与超时

用 `Popen` 加两个读线程，不用 `wait` 配 `PIPE`：`wait` 不消费管道，输出超过管道缓冲后子进程阻塞在写，`wait` 永不返回。

| 环节 | 做法 |
|---|---|
| 启动 | `Popen(argv, shell=False, stdin=DEVNULL, stdout=PIPE, stderr=PIPE)`，Windows 加 `CREATE_NEW_PROCESS_GROUP`，POSIX 加 `start_new_session=True` |
| 读线程 | stdout、stderr 各一个守护线程，`read(8192)` 循环追加到 `bytearray`，累计超过 `max_capture_bytes` 后继续读但丢弃并置 `truncated` |
| 等待 | 主线程 `wait(timeout)`；读线程持续消费，管道不会填满 |
| 超时 | 终止进程树，再 `wait(KILL_WAIT_S)`，然后 join 读线程最多 `OUTPUT_JOIN_S` |
| 终止 | Windows `taskkill /F /T /PID`，POSIX `os.killpg(os.getpgid(pid), SIGKILL)` |

- `KILL_WAIT_S = 5`、`OUTPUT_JOIN_S = 2` 是执行核心的模块级常量，方法超时预算按 §10.3 引用这两个值。
- 命令级 `timeout` 参数按 §8 的换算规则取默认值或夹紧；executor 不做范围校验。
- `core.run(routed, cwd, timeout_s, env)` 的 `env` 由 `TerminalRunner` 调用 `policy.build_env()` 生成，模型命令与用户命令使用同一份。
- 终止失败（`taskkill` 非零退出、`killpg` 抛 `PermissionError`）返回 `kill_failed`，并在错误消息里带 PID 与「进程可能仍在运行」，不再记录日志后放行。`killpg` 抛 `ProcessLookupError` 表示进程已自行退出，按成功处理。
- 终止成功但读线程 2 秒内未结束，返回输出并附「读取线程未退出」标注，不影响退出码。
- 执行闸门：`TerminalRunner.lock.acquire(timeout=queue_wait_s)`，失败返回 `busy`，不无限等待；模型命令与用户命令共用同一把锁。闸门在确认通过之后获取，等待确认期间不持锁，避免并行命令在确认阶段被 `busy` 拒绝。

### §4.3 输出与编码

以字节捕获，按顺序尝试解码：

1. UTF-8 严格解码；
2. 失败时按 `locale.getpreferredencoding(False)` 解码（中文 Windows 为 GBK）；
3. 再失败按 UTF-8 加 `errors="replace"`。

截断口径（`max_output_chars`，默认 4000，指两流合计）：

- 每流配额 `max_output_chars // 2`；配额内头占 3/4、尾占 1/4，默认值下即每流头 1500 + 尾 500。
- 标准输出与标准错误各自独立截断；任一流不足时，结余不转移给另一流，保证口径可预测。
- 配额小于 4 时只保留头部。
- 中间插 `<省略 N 字符>`；`truncated` 为真时 `headline` 附「输出已截断」。
- `body` 中标准输出在前，标准错误非空时以 `--- stderr ---` 分隔追加。
- `truncate_streams()` 由 `TerminalRunner` 在 `core.run()` 返回后调用，结果填入 `body`；完整输出交给终端窗口，窗口侧按块保留最近 5000 条。

### §4.4 返回结构

```python
{
    "headline": "退出码 0，输出 12 行",
    "body": "...",
    "exit_code": 0,
    "hint": "",
}
```

非零退出不是工具失败：`exit_code` 与标准错误照常返回。失败只用于下列情况，统一 `{"error": ..., "code": ...}` 且不带 `headline`，由 executor 转成失败通道。

| code | 触发 | hint |
|---|---|---|
| `terminal_disabled` | 策略档位为 `deny` | 不要重试，工具已被用户关闭 |
| `denied_by_user` | 用户拒绝或确认超时 | 不要重复提交同一条命令 |
| `bad_command` | 引号不配对、拆分为空、可执行文件未找到 | 重新给出一条完整命令；不确定本机有哪些程序时先调用 `terminal__list` |
| `untrusted_program` | `auto` 档下可执行文件来自沙箱根或当前目录 | 用 `terminal__list` 查看可信程序，不要换路径绕过 |
| `cwd_denied` | `cwd` 解析后落在沙箱根之外 | 换用允许的目录 |
| `command_timeout` | 命令超过 `timeout` 被终止 | 命令已终止，不要原样重试 |
| `busy` | 执行闸门等待超时 | 说明前面的命令还在跑，不要立即重试 |
| `kill_failed` | 终止进程树失败 | 提示进程可能仍在运行，不要重试 |
| `shell_mode_disabled` | 传了 `shell` 但 `TERMINAL_ALLOW_SHELL` 为假 | 去掉 `shell` 参数 |
| `internal_error` | handler 内未预期异常 | 不要重试，告知用户命令未执行 |

### §4.5 平台差异

| 能力 | Windows | macOS | Linux |
|---|---|---|---|
| 命令拆分 | `posix=False` 加剥引号 | `posix=True` | `posix=True` |
| 内建命令 | `cmd /d /s /c` 路由 | 不需要 | 不需要 |
| argv 展示 | `list2cmdline` | `shlex.join` | `shlex.join` |
| 进程树终止 | `taskkill /F /T /PID` | `killpg(SIGKILL)` | `killpg(SIGKILL)` |
| `shell` 取值 | `pwsh` / `cmd` | `sh` / `bash` | `sh` / `bash` |
| 可执行劫持 | `which` 搜当前目录，需拦截 | 不搜当前目录 | 不搜当前目录 |
| 路径大小写 | 不敏感 | 默认不敏感 | 敏感 |
| 默认编码 | GBK（中文系统） | UTF-8 | 取决于 locale |
| 窗口置顶 | 稳定 | `Tool` 窗口不进 Dock | Wayland 下由合成器决定 |
| 任务栏图标 | `ensure_taskbar_icon` | no-op | no-op |
| 数据目录 | `%APPDATA%` | `Application Support` | `XDG_CONFIG_HOME` |

- 前五项与进程终止由执行核心按 `os.name` 与 `sys.platform` 分支，上层逻辑同一套。
- Wayland 可能忽略客户端的绝对定位与置顶请求。确认卡片在 Wayland 下退化为相对恋恋窗口定位，定位失败时接受合成器给出的位置，不阻塞确认流程。
- Linux 的 locale 为 `C` 或 `POSIX` 时 `getpreferredencoding` 返回 `ASCII`，解码链会落到 UTF-8 replace，中文输出可能出现替换字符。这不是本设计引入的问题，与现有日志窗口一致。
- `app_dir()` 已按平台分流，窗口状态文件不需要额外处理。

## §5 安全模型

### §5.1 策略档位

配置项 `TERMINAL_MODE` 三档，只在设置页切换：

| 档位 | 模型发起的命令 | 用户输入的命令 |
|---|---|---|
| `deny` | 不允许执行任何命令，返回 `terminal_disabled` | 正常执行 |
| `ask` | 任何命令都询问，卡片标注命中项 | 正常执行 |
| `auto` | 清单里的命令直接执行，其余询问 | 正常执行 |

默认值为 `ask`。`auto` 档下仍转询问的情形：解释器、命中危险模式、整条命令交给系统 shell；程序来自可写目录时直接拒绝 `untrusted_program`。

用户输入跳过策略判定、确认与 `shell` 开关检查，只经过解析、闸门、执行与终端回显。免确认清单、危险模式、沙箱根与 `TERMINAL_MODE` 都不参与。执行记录不另存，模型命令作为工具调用进事件日志。

`TERMINAL_ENABLED` 控制 `terminal` 元工具的注册：默认开启，关闭后模型看不到 `terminal__run`，终端窗口照常可用。终端入口始终显示在右键主菜单，不受任何开关影响。

`mode="deny"` 保留工具在 schema 里但拒绝执行，便于临时收紧而不重启；代价是 schema 与提示词位置仍然被占用。

### §5.2 免确认清单

免确认清单完全由设置页「通用 → 终端」的「auto 档免确认命令」决定，没有隐藏的内置条目；留空表示 `auto` 档全部询问。

```
TERMINAL_ALLOW = ["git status", "git log", "dir *", "where *"]
```

- 默认值为一组只读命令：`dir *`、`ls *`、`where *`、`whoami *`、`hostname *`、`git status`、`git log`、`git rev-parse`、`git describe`；可在设置页增删，清空后不再免确认。
- 条目去重，非法条目记日志后跳过。
- 每个条目是空格分隔的简写：第一个词是程序名，其余是参数模式。
- `*` 只在末位表示任意参数；只写程序名（如 `"git"`）不匹配任何调用，要放行某程序的全部参数必须写 `git *`。
- 条目首词必须是程序名，没有「任意程序」通配。设置页把单值 `*` 保存为 `["*"]`，`parse_allow_entry()` 判为非法条目。
- 条目之间用逗号分隔，这是设置页 `str_list` 控件的解析规则，条目内的空格保留。
- 程序名匹配 `argv[0]` 的 basename（去扩展名、小写）；参数模式与 `argv[1:]` 做前缀匹配：`"git status"` 允许 `git status --short`，不允许 `git push`。内建命令按原始程序名匹配，`dir` 命中 `dir *`，路由产生的 `cmd` 不参与判定。
- 解释器名单是内置常量，`auto` 档一律转询问，写进免确认清单也不改变：`python`、`python3`、`py`、`node`、`npm`、`npx`、`pwsh`、`powershell`、`cmd`、`bash`、`sh`、`wsl`。内建命令不受该条约束，`cmd` 只在用户直接输入 `cmd` 时按解释器处理。
- 免确认清单只决定「哪些命令免确认」，不承担拦截恶意命令的职责；拦截由危险模式、解释器规则与沙箱负责。

### §5.3 危险模式

`TERMINAL_DENY_PATTERNS` 是一组正则，写入 `settings.json` 的高级项（设置页不展示），只作用于模型发起的命令，与完整命令字符串做不区分大小写的匹配；空列表表示使用内置清单。命中时：

- `ask` 档：确认卡片标红，默认按钮为「拒绝」，需要显式点击「仍要执行」；
- `auto` 档：按未命中免确认清单处理，转 `ask`；
- 不提供静默放行路径。

默认清单覆盖删除、磁盘、关机、账户、注册表、下载即执行六类：

```
rm\s+-[rf]{1,2}\s+/ | del\s+/[fsq] | format\s+[a-z]: | diskpart
shutdown | restart-computer | stop-computer | reg\s+delete | bcdedit
vssadmin\s+delete | cipher\s+/w | net\s+user | mkfs
invoke-expression | \biex\b | -encodedcommand
(curl|wget|iwr|irm)[^|]*\|\s*(sh|bash|iex|powershell)
>\s*/dev/sd | chmod\s+-R\s+777\s+/
```

清单可增删；编译失败的模式记日志后跳过，不影响其它条。

### §5.4 程序解析与目录约束

只按 basename 判定会被劫持：Windows 的 `Popen` 按 `cwd` 参数搜索子进程当前目录，`shutil.which` 搜索父进程当前目录与 `PATH`，两者不是同一个目录；而 `file_ops` 能向默认沙箱根（桌面）写 `.bat`、`.ps1`、`.py`。程序解析分两步：

- 第一步查目标目录：在 `cwd` 下按 `PATHEXT` 顺序探测 `argv[0]` 的同名文件，POSIX 直接探测文件名。命中即视为程序来自可写目录。
- 第二步查系统路径：`shutil.which(argv[0])` 解析为绝对路径；`None` 且第一步未命中时返回 `bad_command`。
- 可写判定按目录树计算：候选路径 `realpath` 后与 `sandbox_root`、`cwd` 做带边界的 `os.path.commonpath` 比较，落在任一目录树内即视为来自可写目录。沙箱根的子目录同样命中，`sandbox_root/tools/git.bat` 与以绝对路径调用的 `sandbox_root/tools/git.bat` 都按命中处理。
- 判定命中时，`auto` 档返回 `untrusted_program`，`ask` 档在卡片上标注「来自可写目录」后询问。
- `builtin` 非空时跳过上面三步：内建命令由系统 `cmd` 执行，不参与程序解析与劫持判定。
- 所有档位执行时都使用判定出的绝对路径，避免判定与执行之间被替换。
- POSIX 的 `shutil.which` 不搜索当前目录，第一步命中只在 `cwd` 内存在同名可执行文件时发生；macOS 的 `/usr/local/bin`、`/opt/homebrew/bin` 与 Linux 的 `/usr/bin`、`/bin` 视为可信目录。

### §5.5 工作目录与环境变量

- 用户与模型共用工作目录：终端打开时的初始目录取 `TERMINAL_SANDBOX_ROOT`（留空为桌面），之后用户 `cd` 切换，模型命令默认跟着走。
- `cwd` 为空时取共享目录；相对路径按共享目录解析，绝对路径与相对路径都经 `os.path.realpath` 后与共享目录做 `commonpath` 比较，失败返回 `cwd_denied`。
- 比较前两侧都过 `os.path.normcase`：Windows 路径大小写不敏感，macOS 默认文件系统也不敏感但 `posixpath` 区分大小写，不统一会误判越界。
- 共享目录就是模型允许的根：用户把终端切到 `C:\` 后，模型命令的默认目录与允许范围也随之扩大到 `C:\`。模型不能自己切换目录。
- 子进程环境只保留 `PATH`、`SYSTEMROOT`、`WINDIR`、`TEMP`、`TMP`、`HOME`、`USERPROFILE`、`LANG`、`LC_ALL`、`PYTHONIOENCODING`，加上 `TERMINAL_ENV_ALLOW` 列出的键；`LLM_KEY`、`*_API_KEY`、`*_TOKEN` 即使出现在该列表里也拒绝透传。

### §5.6 失败即拒绝

以下情况一律按拒绝处理，不存在默认放行分支：

- `TOOL_CTX` 尚未绑定 agent；
- 确认接收者（sink）尚未注册，或已被注销；
- 确认卡片所在窗口已销毁、应用正在退出；
- 等待超时；
- 确认期间在途 LLM 调用被中断；
- `confirm` 调用本身抛异常；
- 执行闸门等待超时。

前两条不进入等待：`confirm()` 先查 sink，为空立即返回拒绝，避免白等 60 秒。

## §6 确认通道

### §6.1 跨线程流程

handler 在工具线程中运行，界面在主线程，确认必须跨线程。队列归 `PetAgent`：它持有信号、未决请求与退出清理；`TerminalRunner` 只填请求与判定，`TOOL_CTX` 只转发。

1. UI 启动后调用 `TOOL_CTX.set_confirm_sink(agent.request_confirm)` 注册接收函数；应用退出时 `set_confirm_sink(None)` 注销。
2. `TOOL_CTX.confirm(request)` 检查 sink；为空立即返回 `False`。
3. `request_confirm(request)` 把请求放入 `_confirm_queue` 并写入 `sequence` 与 `total`；当前没有展示中的卡片时直接 `confirm_requested.emit(request)`，否则只入队。
4. 主线程槽函数展示该请求，一次只显示一条卡片；标题用 `sequence` 与 `total`（如「命令 2/3」）。
5. 用户点击后写 `request.result` 并 `request.event.set()`；同一请求只唤醒一次。主线程不触碰工具线程的其他状态。
6. 卡片关闭后调用 `next_confirm()` 推进下一条；队列为空时清空展示中的标记。
7. 工具线程 `event.wait(confirm_timeout_s)` 返回，读 `result` 决定执行或拒绝。
8. 卡片提供「全部拒绝」，清空队列并把剩余请求统一置为拒绝。

请求对象字段：

| 字段 | 内容 | 写入方 |
|---|---|---|
| `argv`、`builtin`、`routed` | 解析结果；卡片显示 `routed` | `TerminalRunner` |
| `command`、`cwd`、`shell_mode`、`timeout_s`、`source` | 原始命令、工作目录、shell 模式、超时与来源 | `TerminalRunner` |
| `matched_patterns`、`untrusted` | 命中的危险类别与可写目录标注 | `TerminalRunner` |
| `sequence`、`total` | 队列序号与总数 | `PetAgent.request_confirm()` |
| `event`、`result` | `threading.Event` 与确认结果 | `TerminalRunner` 初始化；主线程写 `result` 并 `event.set()` |

卡片只读这些字段，不做二次解析。卡片的视觉与键盘交互见 §7.6。

### §6.2 并行调用与串行化

`LLM_TOOL_PARALLEL` 默认为真，同轮多个 `terminal__run` 会并行进入，因此：

- 确认队列保证同一时刻只有一张卡片，不会叠加弹窗；
- 执行闸门保证同一时刻只有一条命令在跑，其余在确认通过后于 `queue_wait_s` 内排队，超时返回 `busy`；
- 队列顺序按请求到达时间，不保证与模型声明顺序一致；卡片标题显示序号，便于对应；
- 用户命令与模型命令共用闸门，用户在终端跑长命令时，模型调用可能返回 `busy`。

### §6.3 超时与中断

- 确认超时 `confirm_timeout_s` 默认 60 秒，卡片显示剩余秒数，归零后自动关闭并按拒绝处理。
- 中断在途调用时，`PetAgent` 遍历未决请求置为拒绝并清空队列。
- 应用退出时同样清空未决请求；清理写在 `pet/app.py` 的 `_shutdown()` 回调中，调用 `reject_pending_confirms("app exit")` 与 `set_confirm_sink(None)`。该回调在 `app.exec()` 返回前触发，覆盖 `os._exit(code)` 与 `sys.exit(code)` 两条分支；`atexit` 与析构函数不参与。
- 终端窗口关闭不注销 sink：窗口是单例，关闭只是 `hide()`，下次菜单再次 `show()`。

### §6.4 拒绝语义

```python
{"error": "用户拒绝了这条命令", "code": "denied_by_user",
 "hint": "不要重复提交同一条命令；需要时先说明想做什么，等用户同意"}
```

模型看到 `[FAIL terminal__run] denied_by_user: 用户拒绝了这条命令`。工具描述、错误 `hint` 与 `_TERMINAL_GUIDE` 三处都写「不要重试」。同一轮内不做拒绝缓存，模型再次调用仍会再次询问；这是模型行为问题，不用代码拦截。

## §7 终端窗口

界面使用 `pet/ui/styles.py` 中的 `TERMINAL_*` 暗色色板，不复用同文件的米白配色，也不影响现有窗口配色。

### §7.1 设计语言

| 维度 | 取值 |
|---|---|
| 底色 | 炭灰 `#17191a`，面板 `#1f2224` |
| 代码块 | `#101213` |
| 强调色 | 青绿 `#5fbfa8` |
| 来源色 | 用户琥珀 `#d9a441`，恋恋青绿 `#5fbfa8` |
| 危险色 | `#d9534f` |
| 边框 | 1px `#2f3437`，无阴影 |
| 圆角 | 窗口 8px，控件 6px |
| 字体 | 等宽 `JetBrains Mono` → `Cascadia Mono` → `Consolas` → `Menlo` → `DejaVu Sans Mono` → `Noto Sans Mono`；界面字 `Segoe UI` → `PingFang SC` → `Microsoft YaHei UI` → `Noto Sans CJK SC` |
| 密度 | 行高 1.5，命令块间距 14px |
| 层次 | 底色差加 1px 线分区 |

对比度：主文字 `#e8e5de` 在 `#17191a` 上约 12:1，次要文字 `#a8adaf` 约 6:1，元信息 `#8a9195` 约 4.6:1，均满足正文可读。

### §7.2 入口与布局

入口是右键主菜单的一项，与「日志」「对话历史」并列，始终显示，不受开关影响。

无边框窗口，默认 680×480：

| 区域 | 内容 |
|---|---|
| 状态栏 | 34px：左侧 6px 状态圆点 + 「终端」；右侧 shell 选择、清空、最小化、关闭 |
| 回显区 | 只读 `QTextEdit`，无边框，占满剩余高度 |
| 输入行 | 底部 40px，无边框；输入框内左侧显示目录提示，行尾是状态文字 |

- 状态栏兼作拖动区。
- 档位只在设置页修改，终端不提供切换入口；免确认清单与沙箱根同样只在设置页编辑。
- shell 选择只影响用户输入，取值为平台可选集；模型调用的 `shell` 参数仍由 `TERMINAL_ALLOW_SHELL` 控制。选择结果记在窗口状态里，不写回配置。
- 当前目录以提示符形式画在输入框内部左侧，形如 `C:\Users\tqzth>`，用主文字色，光标与输入文字紧接提示之后；输入框底色与窗口底一致 `#17191a`。目录只通过 `cd` 切换。
- 窗口图标取 `ICON_PATH`，`showEvent` 调 `ensure_taskbar_icon`；终端进任务栏（`Qt.Window`），确认卡片不进（`Qt.Tool`）。

命令块的结构：

```
▍[用户] $ git status
  On branch master
  nothing to commit
  exit 0 · 0.13s
```

- 块首的 `▍` 是来源竖条，颜色跟随来源，代替大面积彩色前缀。
- `[用户] $` 与 `[恋恋] $` 用来源色，命令文本用主文字色 `#e8e5de`。
- 输出接在命令行下方，缩进两格。
- 尾行 `exit 0 · 0.13s` 用 `#8a9195`；退出码非零时只把数字染成 `#d9534f`。
- 模型命令的 cwd 与沙箱根不同时，在尾行前追加一行 `cwd: C:\work`；用户命令不显示，输入行已显示目录。
- 块之间空 14px，不画分隔线。

### §7.3 配色

| 元素 | 色值 |
|---|---|
| 窗口底 | `#17191a` |
| 状态栏底 | `#1f2224` |
| 输入行底 | `#17191a`，与窗口底一致 |
| 边框与分隔线 | `#2f3437` |
| 主文字 | `#e8e5de` |
| 标准输出 | `#c9c6bf` |
| 标准错误 | `#e08b86` |
| 元信息 | `#8a9195` |
| 非零退出码 | `#d9534f` |
| 用户来源 | `#d9a441` |
| 恋恋来源 | `#5fbfa8` |
| 被拒绝的命令 | 整块 `#8a9195`，行尾标「已拒绝」 |

- 颜色用 `QTextCharFormat` 按块写入，不改全局 QSS，避免影响输入行与状态栏。
- 标准错误不整块染色，只在前缀加 `stderr` 标记并染 `#e08b86`，避免大面积红字。
- 缓冲上限 5000 个块，超出后从文档头部删除。
- 窗口内容只存在内存：隐藏后重新打开仍在，应用退出即清空。
- 命令历史 `deque(maxlen=100)`，上下方向键切换，只在内存中。

### §7.4 输入执行

- 用户提交的命令以 `source="user"` 进入执行核心，跳过确认、档位、免确认清单、危险模式与沙箱。
- 用户命令默认走 shell 模式：Windows 为 `cmd /d /s /c`，POSIX 为 `sh -c`。管道、重定向与变量展开因此可用，这是终端能替代系统终端窗口的前提。状态栏提供 shell 选择，Windows 可选 `cmd` / `pwsh`，POSIX 可选 `sh` / `bash`。
- 用户命令的超时取 `TERMINAL_MAX_COMMAND_TIMEOUT_S`（默认 120 秒），超时后同样终止进程树；比模型默认的 30 秒宽，避免安装依赖这类命令被过早打断。
- 执行放在独立线程，通过 `Signal(dict)` 把阶段、块起点、分块输出与结束事件交回主线程。
- 用户命令允许任意 cwd。打开时的工作目录取 `TERMINAL_SANDBOX_ROOT`（留空为桌面），每次启动都从这里开始，不恢复上次目录。
- 输入框内左侧显示当前目录，超过 40 字符时中间省略为 `C:\Users\...\project`，悬停显示完整路径；切换目录只走 `cd`。
- 用户输入以 `cd ` 开头且只有一个目录参数时，窗口层直接切换目录，不启动子进程，也不往回显区写提示行；Windows 下裸盘符 `D:` 与 `cd D:` 都切到该盘根；其它 `cd` 写法按普通命令交给 shell，目录提示不变。
- 窗口把当前目录注册给 `TerminalRunner`：模型命令的默认 cwd 与允许范围都取这个目录，用户 `cd` 后模型跟着走。
- 状态圆点四态：空闲 `#4a4f52`；运行中 `#5fbfa8`，1200ms 周期呼吸；等待闸门 `#d9a441`；执行出错短暂闪 `#d9534f` 一次后回到空闲。
- 输入行右侧显示计时：空闲为空，运行中「运行中 3.2s」每 0.5 秒刷新，等待时「等待执行」。
- 输入行右侧只有状态文字，执行期间输入行置灰；提交只靠 `Enter`。
- 回显区为空时显示两行灰字：`输入命令后回车执行`、`↑ ↓ 翻历史 · Ctrl+L 清屏`。
- 自动滚动默认跟随底部；用户向上滚动后暂停跟随，滚回底部时恢复。判据是竖直滚动条的值等于最大值。
- 快捷键：`Enter` 提交，`Esc` 清空输入，`↑` / `↓` 取历史，`Ctrl+L` 清屏。
- 粘贴内容含换行时只取第一行并丢弃其余部分，状态文字提示「已忽略后续行」。多行脚本需要自行写成单行或用文件执行。
- 用户命令不写 LLM 上下文，只写窗口。

### §7.5 执行记录

不额外落盘：模型命令作为工具调用进入事件日志，终端回显与命令历史只存在内存。「清空」按钮只清空回显区，不弹二次确认；应用退出后记录不再保留。

### §7.6 确认卡片

形态：独立无边框小窗，宽 420，高度随内容自适应，最小 150；底色 `#1f2224`，圆角 10，1px `#2f3437`；`Qt.Tool | WindowStaysOnTopHint`，不进任务栏。

位置由纯函数 `card_position(pet_rect, screen_rect, card_size)` 计算，便于单测：

1. 优先放在恋恋窗口上方 8px，水平居中对齐；
2. 上方空间不足时放到下方；
3. 恋恋不可见或完全在屏幕外时，放在鼠标所在屏的右下角，距边 24px；
4. 结果夹进屏幕可用区，不压住任务栏。

结构（自上而下）：

| 区域 | 内容 |
|---|---|
| 分类条 | 2px：常规青绿 `#5fbfa8`，命中危险 `#d9534f`，程序来自可写目录 `#d9a441` |
| 标题行 | 「恋恋想执行一条命令」，右侧「2/3」与剩余秒数 |
| 命令区 | 底色 `#101213`，圆角 6，内边距 12；等宽 12px 显示实际执行的 `routed`（Windows 用 `list2cmdline`、POSIX 用 `shlex.join` 还原），超过 4 行时内部滚动 |
| 元信息 | 11px `#8a9195`：来源（恋恋 / 用户）、cwd、超时秒数；`builtin` 非空时追加「内建 dir」 |
| 提示条 | 深底加左侧 3px 色条；危险用红、可写目录用琥珀，正文 12px |
| 倒计时 | 按钮行上方 1px 线，从右向左收缩；剩余 10 秒内由青绿转 `#d9a441` |
| 按钮行 | 左侧「全部拒绝」为纯文字按钮；右侧「拒绝」为幽灵按钮，「允许」为实心按钮 |

- 「允许」按钮常态是青绿实心加 `#0f1413` 文字；命中危险模式时改为 `#d9534f` 实心，文案变为「仍要执行」。
- 「拒绝」按钮透明底加 1px `#2f3437`，悬停时底色变 `#262a2c`。
- 「全部拒绝」平时是 `#8a9195` 文字，悬停转 `#d9534f`。

交互约定：

- `Esc` 拒绝当前项，`Enter` 允许；命中危险模式时 `Enter` 无效，只能点击「仍要执行」。
- `Tab` 在三个按钮间移动；关闭按钮等同拒绝。
- 卡片弹出时 `show()` + `raise_()` + `activateWindow()`，不阻塞主窗口。
- 同一时刻只存在一个卡片实例，队列推进时更新内容与标题，不反复创建窗口。
- 恋恋开启鼠标穿透时卡片不受影响，它是独立窗口，不继承穿透状态。
- 文案写「恋恋想执行一条命令」，不写「权限请求」「授权」这类系统术语；危险提示只写类别，不写正则原文。

### §7.7 输入补全

`Tab` 触发补全，候选按 token 位置与首词分成三层：结构候选（命令、子命令、选项、枚举）、命名空间候选（环境变量）、文件系统候选。

首词（非 `:` 开头）：

| 优先级 | 候选 | 数据源 |
|---|---|---|
| 1 | Shell 保留字与内建命令 | `completion.py` 的 `SHELL_WORDS_NT` / `SHELL_WORDS_POSIX` |
| 2 | `PATH` 可执行名 | `os.environ["PATH"]`，窗口缓存 60 秒 |
| 3 | 当前目录条目 | 目录读取，目录带分隔符 |

- 三层按顺序合并去重，同名保留优先级高的；目录与内建命令同名时内建命令在前。
- 首词含路径分隔符时不查命令名，直接按路径补全。

参数 token：

| 首词与上下文 | 候选 |
|---|---|
| 目录类命令（`cd`、`chdir`、`pushd`） | 当前目录的子目录 |
| 文件类命令（`type`、`cat`、`more`、`less`、`notepad`） | 当前目录的文件 |
| 其余 Shell 命令 | 当前目录的条目 |
| 已选 shell 且 token 以变量前缀开头 | 环境变量名：cmd `%NAME%`、pwsh `$env:NAME`、bash/sh `$NAME` |
| `:` 命令的第一个参数 | 登记的 `words`；`help` 取全部命令名 |
| `:` 命令的后续参数 | `values` 登记的该子命令候选，其次是 `paths` 命令的路径与程序名 |
| `:` 命令的 `-` 开头 token | 登记的 `flags` |

路径补全的解析规则：

| 已输入 | 基准 |
|---|---|
| `~` / `~/` | 用户主目录 |
| `..` / `../` | 终端当前目录的上一级 |
| `/`、`C:\` 等绝对路径 | 从磁盘根开始 |
| 其余 | 终端当前目录，不是子进程的 cwd |

- 参数类型按首词决定：`cd`、`chdir`、`pushd` 只列目录；`type`、`cat`、`more`、`less`、`notepad` 只列文件；其余命令列文件与目录。常量表放在 `completion.py` 模块级。
- 目录候选以当前平台的分隔符结尾（Windows `\`，POSIX `/`），接受后不补空格，继续按 `Tab` 进入下一级；文件候选接受后补一个空格。
- 点开头的条目默认不列出，叶前缀以 `.` 开头时才列出。
- 先按已输入前缀过滤，再截断到 500 条。

几个典型输入：

```
C:\Users\me> cd Doc␉           → cd Documents\
C:\Users\me> cd Documents\␉    → 列出 Documents 下的条目
C:\Users\me\Documents> cd "Pro␉ → cd "Program Files"\
C:\Users\me> type no␉          → type notes.txt␣
C:\Users\me> cd .g␉            → cd .git\
```

交互规则：

- 唯一候选直接补全；目录保留分隔符且不补空格，其余候选补一个空格。
- 多候选时首次按 `Tab` 直接取第一个候选并就地替换 token，不显示候选行；继续按 `Tab` 在候选间循环，`Shift+Tab` 反向，`Esc` 退出循环但保留已输入内容。
- 当前 token 已被引号包裹时保留引号，只替换内部内容；补全结果含空格时自动加双引号。
- 大小写按平台：Windows 不敏感，`cd doc` 能补出 `Documents\`；POSIX 敏感。
- 候选为空时不改变输入，状态栏提示「无匹配」一次。

不做的部分：管道与重定向之后的命令补全、`/` 内建命令与工具方法名候选、环境变量值补全、`Ctrl+R` 历史搜索、`~user` 展开、SSH 主机名、glob 展开（`*.py`）、Shell 命令的选项补全（`git --`）。变量只补名字不补值；`:` 开头的本地命令走单独候选来源，见 §7.8。

实现约束：

- 补全逻辑放在 `pet/ui/completion.py`，纯函数 `complete(buffer, cursor, ctx) -> Completion`，`Completion(start, candidates, common_prefix)`，不依赖 Qt，可单独测试。
- `ctx` 提供终端当前目录、大小写策略、所选 shell、`PATH` 缓存与 `:` 命令表（子命令、别名、选项、枚举值）；窗口只负责按键与写回输入框。
- `PATH` 候选首次使用时扫描并缓存 60 秒；目录候选每次读取，先按前缀过滤再截断到 500 条。
- 补全不使用 `QCompleter`，由窗口按 `Tab` 轮换候选。

## §7.8 本地命令

冒号前缀的输入由窗口层处理，不启动子进程，也不进 LLM 工具层。窗口在 `cd` 与盘符切换之后、`_busy` 闸门之前分流：前缀命中即走本地命令，其余输入照旧交给系统 shell。

- 命令来源分两层：`help`、`clear`、`status`、`policy`、`check` 是 `pet/cli` 的内建命令；`timer`、`todo`、`memory`、`knowledge` 由各工具在自己的 `register(registry)` 里登记，与文件气泡按钮、右键菜单项同一套做法。
- 登记接口 `registry.add_command(tool_name, name=, group=, rows=, resolve=, words=, paths=, examples=, aliases=, flags=, values=)`：`rows` 是帮助里的 (用法, 说明) 行，子命令各占一行；`examples` 是帮助末尾的示例行；`resolve` 只把参数文本翻成 (方法名, 参数)，参数不合法抛 `ValueError`；`words` 是第一个参数的候选，`values` 是「子命令 → 位置参数候选」，`flags` 是选项名，`paths` 表示按程序名与路径补全，`aliases` 是命令别名。`pet/cli` 只聚合 `TOOL_REGISTRY.commands()`，不认具体工具。
- 工具命令一律构造 `ToolCall` 交给 `ToolExecutor.execute_one()`，不直接调用工具内部实现。工具被关闭时它的命令随之消失（`commands()` 按 `is_enabled` 过滤），返回码 1 提示去设置里启用。
- 返回码：成功 0；工具未注册或执行失败 1（正文写 stderr）；未知命令与非法用法 2。
- 本地命令与 shell 命令共用 `_busy` 闸门，本地命令在独立线程执行。知识库与记忆检索要等 embedding，期间输入按「上一条命令还在执行」处理，避免流式分块与本地回显交错。
- 回显块尾与 shell 命令同形：`exit <code> · 0.02s`，退出码取 `LocalResult.exit_code`（0 成功、1 工具失败、2 用法错误），不额外标注命令来自本地。
- 命令输出统一为 `键: 值` 两列：键按终端显示宽度（全角字符按两列）补空格，值起点对齐；分块输出用空行分隔，块标题单独成行且不带冒号；空值写 `-`；说明行以 `# ` 开头，与数据行之间空一行。

| 分组 | 用法 | 来源 | 作用 | 工具方法 |
|---|---|---|---|---|
| 通用 | `:help [command]` | cli | 列出全部命令；带参数时只显示该命令的分组、别名、用法与示例 | 无 |
| 通用 | `:clear` | cli | 清屏 | 无 |
| 恋恋 | `:status [--json]` | cli | 恋恋的状态机、当前动作、贴边、生理、心理与长期记忆条数；`--json` 输出快照 JSON | `TOOL_CTX.pet_status()` |
| 状态与策略 | `:policy` | cli | 免确认清单逐条列出并标注未安装项、沙箱根、解释器名单、危险类别、shell 模式开关 | `TerminalRunner.list()` |
| 状态与策略 | `:check <command>` | cli | 以 `source="model"` 试算 `policy.check()`，输出判定码、档位、原因、命中的危险类别与解析到的程序 | 无 |
| 计时与待办 | `:timer <duration> [message]` | timer | 设定倒计时，时长接受 `90`、`90s`、`10m`、`1h30m` | `timer__set` |
| 计时与待办 | `:timer list` | timer | 列出未到期的倒计时，每行带 id | `timer__list` |
| 计时与待办 | `:timer cancel <id>` | timer | 取消指定倒计时 | `timer__cancel` |
| 计时与待办 | `:todo add <title>` | todo | 添加待办 | `todo__add` |
| 计时与待办 | `:todo list [status]` | todo | 列出待办，`status` 取 `pending`、`done`、`all`，默认 pending | `todo__list` |
| 检索 | `:memory search <keyword>` | recall | 检索长期记忆 | `recall__search` |
| 检索 | `:knowledge search <keyword>` | knowledge | 检索知识库 | `knowledge__search` |

`:check` 只读：不执行命令、不改任何状态，用于排查免确认清单、解释器与危险规则为什么给出某个判定。

`:status` 只读终端无关的恋恋状态：状态机（中文名与英文码）、已持续时长、当前动作、贴边、饱食度、精力、饥饿与疲倦、好感度、愉悦度、理智、长期记忆条数。快照经 `TOOL_CTX.pet_status()` 向 agent 取，未绑定或采集失败时退出码 1，不显示半份数据；`--json` 输出同一份快照的 JSON。

`:help` 排版：

- 段落依次为标题 `终端命令` 与一行说明、`用法`、`命令`、`示例`，段间空一行。
- 分组顺序由 `GROUP_ORDER` 决定（通用、恋恋、状态与策略、计时与待办、检索），工具声明的新分组排在后面；组内按登记顺序，组间空一行，组名单独占一行。
- `命令` 段落两列：用法列按最长用法补空格，说明列起点一致，两列间是两个空格，不用冒号连接。
- `示例` 段落按分组顺序列出各命令登记的 `examples`，命令未登记就没有对应示例。
- 占位符统一为尖括号英文形式，字面子命令保持原样，两类不混用。
- 用法错误只回一行，内容为该命令在 `rows` 里的全部用法，用 ` | ` 连接。
- `:help <command>` 只显示该命令：首行 `:命令`，缩进两列的 `分组` 与 `别名`（有别名时），随后 `用法` 逐行列出用法与说明，`示例` 逐行列出示例；未知命令回一行用法错误。`:help` 的别名是 `:?`。

补全规则：

- 首词以 `:` 开头时，候选只来自命令表：命令名在前、别名在后，带冒号返回，比较不区分大小写，不混入 PATH 程序名与目录条目。
- 第一个参数按 `words` 补子命令，`help` 补全部命令名；后续参数按 `values` 补该子命令的候选；`-` 开头的 token 按 `flags` 补选项。
- 带 `paths` 的命令补 PATH 程序名与当前目录条目；其余命令不补参数。
- 唯一候选被接受后，目录保留分隔符不补空格，其余候选补一个空格；多候选轮换期间不补。

不做的部分：任意工具通用直调入口、写配置、退出与重启。通用直调会把「用户亲手输入」变成越权通道，写配置绕过设置页的类型校验与重启提示。

## §8 工具契约

```python
TOOL_NAME = "terminal"
TOOL_DESCRIPTION = "终端命令执行：运行系统命令并返回退出码与输出"
```

`terminal` 是元工具，注册在 `ToolRegistry._register_default_tools()` 内，与 `food`、`game`、`recall` 同一层：

```python
def _register_terminal_tools(self):
    runner = get_runner()
    runner.set_confirm(TOOL_CTX.confirm)
    self.register("terminal", "终端命令执行：运行系统命令并返回退出码与输出",
                  group="default", meta=True, side_effect=True)
    self.add_method(
        tool_name="terminal", method_name="run",
        description="...", handler=runner.run,
        args={...}, timeout=runner.method_timeout,
        output=ToolOutputDefinition(schema=TERMINAL_OUTPUT),
    )
    self.add_method(
        tool_name="terminal", method_name="list",
        description="...", handler=runner.list,
        args={}, output=ToolOutputDefinition(schema=TERMINAL_LIST_OUTPUT),
    )
```

`_register_default_tools()` 只在 `config.TERMINAL_ENABLED` 为真时调用它。`output` 是 keyword-only 参数且无默认值，省略抛 `TypeError`，传 `None` 抛 `ValueError`。

`TERMINAL_OUTPUT` 与 `TERMINAL_LIST_OUTPUT` 是 `pet/tools/registry.py` 的模块级 dict 字面量常量。`gen_docs.py` 只展开同文件与 `pet/tools/output.py` 内的 dict 常量，`args` 也必须是 dict 字面量，否则 `tools.md` 里该方法的参数与输出字段为空。

方法 `run` 的参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `command` | str | 是 | - | 要执行的命令；`shell` 为空时按参数列表拆分 |
| `cwd` | str | 否 | `""` | 工作目录，空为沙箱根 |
| `timeout` | int | 否 | 30 | 命令超时秒数，换算规则见下 |
| `shell` | str | 否 | `""` | 交给系统 shell；Windows 枚举 `["", "pwsh", "cmd"]`，POSIX 枚举 `["", "sh", "bash"]` |

`TerminalRunner` 分两个入口，`source` 不是工具参数：

| 入口 | 调用 |
|---|---|
| 模型 | `runner.run(command, cwd, timeout, shell, **_ignored)`，固定 `source="model"`；executor 透传的未声明参数被丢弃 |
| 用户终端 | `runner.run_user(command, cwd=<输入行目录>, timeout=0, shell=<窗口选择>)` |

- `run_user` 跳过档位、免确认清单、危险模式、程序解析与沙箱校验，不受 `TERMINAL_ALLOW_SHELL` 限制。
- `timeout` 传 0 或负数表示取默认值：模型取 `TERMINAL_COMMAND_TIMEOUT_S`，用户取 `TERMINAL_MAX_COMMAND_TIMEOUT_S`；正数夹紧到 `[1, TERMINAL_MAX_COMMAND_TIMEOUT_S]`。
- 用户命令不写 LLM 上下文，返回值只用于终端窗口回显。

方法描述写明三点：命令只执行一次、需要用户确认、被拒绝时不要重试。`side_effect=True` 让该调用在事件日志里带 `side_effect` 标记，供回放与事件投影区分写操作；它与崩溃报告窗口无直接关系。

方法 `run` 的输出 schema：

```python
TERMINAL_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "一句话结论，含退出码与输出行数"},
        "body": {"type": "string", "x-role": "body",
                 "description": "标准输出与标准错误，已截断"},
        "exit_code": {"type": "integer", "x-role": "data",
                      "description": "退出码，非零表示命令自己报错"},
        "hint": {"type": "string", "x-role": "hint",
                 "description": "下一步该传什么参数"},
    },
}
```

方法 `list` 零参数，使用单独的 `TERMINAL_LIST_OUTPUT`，`required` 为 `["headline", "body"]`：

| 字段 | 类型 | x-role | 内容 |
|---|---|---|---|
| `headline` | string | headline | 当前档位与免确认命令条数 |
| `body` | string | body | 每行一项，例如 `免确认命令：git status | git log | dir` |
| `platform` | string | data | `Windows` / `Linux` / `macOS` |
| `sandbox_root` | string | data | 模型命令允许的工作目录上限 |
| `allowed` | array | data | 配置里填写的「程序 + 参数」，附 `installed` 标记 |
| `interpreters` | array | data | 一律转询问的解释器名单 |
| `deny_categories` | array | data | 危险模式覆盖的类别名 |
| `shell_mode` | boolean | data | `TERMINAL_ALLOW_SHELL` 是否开启 |
| `mode` | string | data | 当前档位 |

- 程序是否安装用 `shutil.which` 探测，结果缓存 60 秒。
- 危险模式只给类别（删除、磁盘、关机、账户、注册表、下载即执行），不返回正则原文。
- 免确认清单与档位变化后立即反映；配置未重启时以当前文件内容为准。

模型不需要预先记住命令大全，只需要知道本机的免确认命令、沙箱根与解释器限制。`_TERMINAL_GUIDE` 只写机制与优先级（专用工具优先、需要确认、不要重试），不列具体命令，避免配置变化导致 system 前缀缓存失效。

终端入口不挂在工具子菜单上：`pet/ui/pet_window.py` 的菜单循环对 `meta` 工具直接 `continue`，`add_menu_action` 注册的子菜单不可达。改为在右键主菜单加一项「终端」，处理器 `_show_terminal_window()` 延迟导入 `pet.ui.terminal_window`，与 `_show_log_window` 的写法一致；不改动 `meta` 跳过分支。

## §9 与现有子系统的交互

| 项 | 行为 |
|---|---|
| 工具可见性 | `meta=True` 且 `default` 分组，模型第一轮就能在 schema 里看到 `terminal__run`，不经 `tool_search`；`TERMINAL_ENABLED` 为假时工具不注册，模型完全看不到 |
| 提示词 | `prompts.py` 生成工具清单时跳过 `meta`，需要单独的 `_TERMINAL_GUIDE` 段，写明用途、确认机制与「有专用工具时优先用专用工具」；不列具体命令，避免配置变化冲掉 system 前缀缓存 |
| 策略可见性 | `terminal__list` 返回平台、沙箱根、免确认命令与安装情况；`bad_command`、`untrusted_program`、`busy` 的 hint 都指向该方法，模型不确定时先查。免确认命令按原始程序名生效，内建命令的 `dir *` 条目在列表中原样展示 |
| 并行调用 | 同轮多个 `terminal__run` 并行进入；确认排队单卡展示，执行闸门串行，等待超时返回 `busy` |
| 工具循环 | 一轮内的多条命令会产生多次确认；工具描述要求一次只发一条，拿到结果后再发下一条 |
| 自主行动 | 允许调用，每次仍需确认；用户不在时超时拒绝，模型收到 `denied_by_user` 后不应重试 |
| 贴边与隐藏 | 确认卡片是独立窗口，不依赖恋恋可见；恋恋隐藏时仍能弹出确认与终端 |
| 中断调用 | 在途调用被中断时未决确认全部置为拒绝，队列清空 |
| 退出路径 | `pet/app.py` 的 `_shutdown()` 回调清空未决请求并注销 sink，覆盖 `os._exit(code)` 与 `sys.exit(code)`；不依赖 `atexit` |
| 事件日志 | `side_effect=True` 只写入 `tool_call` 事件 payload |
| 上下文 | 工具输出按 `x-role` 进入当轮视图；`headline` 进历史，`body` 只在当轮可见 |
| 文档生成 | `tools.md` 静态解析注册字面量，不判断 `TERMINAL_ENABLED`，`terminal` 恒在清单中；生成物是代码结构的事实表，与运行时开关无关 |
| 模块级约束 | `TerminalRunner` 与锁都在 `_register_terminal_tools()` 内创建，不踩 ARCH009；若确有模块级单例需求，需在契约测试登记 |
| 工具上下文 | `pet/tools/context.py` 不 import `pet.agent`，只调用已绑定对象上的信号与 sink，维持零依赖 |

## §10 配置项

全部配置并入 `pet/config.py` 的 `_KEY_META`，与 `FOOD_ENABLED` 等元工具开关同一套机制：`settings-schema.json` 与 `reference/config.md` 由元数据生成、`config.save()` 统一落盘，设置页控件按元数据手写。`terminal` 是元工具，不使用工具私有配置。

### §10.1 设置页可见项

「通用」页新增一组「终端」控件：

| 键 | 类型 | 默认 | 控件 | 说明 |
|---|---|---|---|---|
| `TERMINAL_ENABLED` | bool | true | 复选框 | 注册 `terminal` 元工具；关闭后模型看不到 `terminal__run` |
| `TERMINAL_MODE` | str | `"ask"` | 下拉 | `deny` / `ask` / `auto` |
| `TERMINAL_ALLOW` | str_list | `[]` | 单行文本 | `auto` 档免确认的命令，空为全部询问；条目格式见 §5.2 |
| `TERMINAL_SANDBOX_ROOT` | str | `""` | 文本框 | 用户与模型共用的工作目录根，也是终端初始目录，留空为桌面 |

- `TERMINAL_ENABLED` 为 `needs_restart: True`，其「（需重启）」由标签文字手写；该字段只进 `settings-schema.json` 与 `config.md`，设置页不读。档位、额外命令、沙箱根即时生效。
- 额外命令与 `TOOLS_ENABLED` 同为 `str_list`，用单行 `QLineEdit`，按逗号切分；占位提示写「逗号分隔，如 git status, git log, dir *」。
- tooltip 注明解释器不受免确认清单影响：写下 `python`、`node` 也不改变「始终询问」。
- 每个标签右侧有「?」标记，悬停显示说明与「风险：」清单：可用命令范围、三档逻辑、条目写法、工作目录根含义。

### §10.2 高级项

`hidden: True`，只在 `settings.json` 中可改：

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `TERMINAL_DENY_PATTERNS` | str_list | `[]` | 危险模式正则，空为内置六类 |
| `TERMINAL_ALLOW_SHELL` | bool | false | 是否允许 `shell="pwsh"` / `"cmd"` / `"sh"` |
| `TERMINAL_ENV_ALLOW` | str_list | `[]` | 额外透传的环境变量名 |
| `TERMINAL_COMMAND_TIMEOUT_S` | int | 30 | 单条命令的默认超时 |
| `TERMINAL_MAX_COMMAND_TIMEOUT_S` | int | 120 | 模型可传的超时上限 |
| `TERMINAL_CONFIRM_TIMEOUT_S` | int | 60 | 确认卡片倒计时 |
| `TERMINAL_QUEUE_WAIT_S` | int | 10 | 执行闸门等待上限 |
| `TERMINAL_MAX_CAPTURE_BYTES` | int | 131072 | 每流捕获的字节上限 |
| `TERMINAL_MAX_OUTPUT_CHARS` | int | 4000 | 进入模型的输出上限（两流合计） |

- `TERMINAL_DENY_PATTERNS` 留空表示用内置清单，非空整体替换；正则原文不进入 `terminal__list` 快照。
- `TERMINAL_MAX_COMMAND_TIMEOUT_S`、`TERMINAL_CONFIRM_TIMEOUT_S`、`TERMINAL_QUEUE_WAIT_S` 参与方法注册，四个超时键均 `needs_restart: True`；捕获上限与输出上限即时生效。

### §10.3 超时推导

```
method_timeout = TERMINAL_CONFIRM_TIMEOUT_S + TERMINAL_MAX_COMMAND_TIMEOUT_S + TERMINAL_QUEUE_WAIT_S
                + KILL_WAIT_S + OUTPUT_JOIN_S + 3
```

默认 200 秒，在 `_register_terminal_tools()` 内由 `TerminalRunner` 计算并传给 `add_method(timeout=...)`。`KILL_WAIT_S` 与 `OUTPUT_JOIN_S` 取自 §4.2，3 秒是调度余量。预算覆盖最长路径「确认 → 排队 → 执行到超时 → 终止进程树 → 收尾读线程」，handler 清理完成前不触发 executor 超时。

`policy` 每次调用都从 `config` 实例读取，`config.save()` 更新属性后立即生效，不存在配置缓存过期的问题。

## §11 文件改动

| 文件 | 职责 |
|---|---|
| `pet/terminal/__init__.py` | 包说明与 `TOOL_NAME` / `TOOL_DESCRIPTION` 常量 |
| `pet/terminal/core.py` | 引号配对检查、`parse_command(command, shell="")`（输出 `argv` / `builtin` / `routed` 与 shell 模式路由）、读线程与进程树终止、解码与截断、`KILL_WAIT_S` 与 `OUTPUT_JOIN_S` |
| `pet/terminal/policy.py` | `check(source, argv, builtin, command, cwd)`、`build_env()`、`clamp_timeout()`、`is_writable_path(path, roots, platform)`、`sandbox_cwd(cwd, sandbox_root, source)`；档位、免确认命令、危险模式、程序解析与目录约束 |
| `pet/terminal/runner.py` | `TerminalRunner`：shell 开关检查、调用解析与策略、闸门、确认调用、`build_env()` 与截断调用、`method_timeout`、`list` 快照 |
| `pet/tools/registry.py` | `_register_terminal_tools()` 注册 `run` 与 `list`；`TERMINAL_OUTPUT` / `TERMINAL_LIST_OUTPUT` 字面量；`_register_default_tools()` 按 `TERMINAL_ENABLED` 调用；`ToolCommand` 与 `add_command()` / `commands()` 供工具登记终端命令，`recall` 的 `:memory` 在此登记 |
| `pet/config.py` | 十三个 `TERMINAL_*` 键，见 §10 |
| `pet/ui/settings_window.py` | 通用页「终端」组：开关、档位下拉、额外命令单行文本、沙箱根文本框，各控件带 tooltip |
| `pet/brain/prompts.py` | `_TERMINAL_GUIDE` 段与注入条件 |
| `pet/ui/styles.py` | 终端的 `TERMINAL_*` 色板、字体回退链、圆角与间距常量；与同文件米白 QSS 各自取值 |
| `pet/ui/completion.py` | 补全纯逻辑：Shell 内建与可执行名、目录条目与隐藏规则、环境变量名、冒号命令名/别名/子命令/选项/枚举、公共前缀、引号与大小写策略 |
| `pet/ui/terminal_window.py` | 终端窗口、来源配色、缓冲、输入执行、命令历史、自动滚动与状态栏、工作目录状态与 `cd` 拦截、补全交互、本地命令分流 |
| `pet/cli/types.py` | `LocalResult` / `LocalContext` / `LocalCommand` 与返回码、前缀常量；不依赖任何 pet 模块 |
| `pet/cli/format.py` | 输出排版：`key_values()` 按显示宽度对齐两列、`sections()` 分组、`note()` 说明行；纯函数，不依赖 Qt |
| `pet/cli/catalog.py` | 内建命令表、`commands()` 聚合注册表、帮助排版、补全数据与用法拼装；`TOOL_REGISTRY` 只在这一处取 |
| `pet/cli/builtins.py` | 内建命令实现：`help` 之外的 `check` / `status` / `policy` / `clear`；`:status` 经 `TOOL_CTX.pet_status()` 取恋恋快照 |
| `pet/cli/dispatch.py` | `is_local()`、`run()`：命中 handler 走内建，否则翻译参数后经 `ToolExecutor` 调用 `tool__method` |
| `pet/cli/__init__.py` | 只 re-export 公共 API 并声明 `__all__` |
| `pet/ui/confirm_card.py` | 确认卡片：队列展示、倒计时进度条、危险与可写目录提示、按钮与快捷键；`card_position()` 纯函数 |
| `pet/tools/timer/__init__.py` | `register_commands()` 登记 `:timer`（用法、子命令候选、示例），`_resolve_command()` 翻译参数，`parse_duration()` 解析 90 / 10m / 1h30m |
| `pet/tools/todo/__init__.py` | `register_commands()` 登记 `:todo`（用法、子命令候选、示例），`_resolve_command()` 翻译 add / list |
| `pet/tools/knowledge/__init__.py` | `register_commands()` 登记 `:knowledge search`（用法、子命令候选、示例），`_resolve_command()` 翻译关键词 |
| `pet/tools/context.py` | `set_confirm_sink()`、`confirm(request)`，保持零依赖 |
| `pet/agent/pet_agent.py` | 确认队列、`confirm_requested` 信号、未决请求登记、中断与退出时统一拒绝 |
| `pet/app.py` | `_shutdown()` 回调清空未决确认并注销 sink |
| `pet/ui/pet_window.py` | 右键主菜单「终端」项、`_show_terminal_window`、连接确认信号并显示卡片 |
| `pet/ui/__init__.py` | 模块说明补充 `terminal_window` 与 `confirm_card` |
| `tests/test_terminal_core.py` | 引号配对、内建路由、shell 模式路由、大输出不死锁、终止进程树、截断口径、解码 |
| `tests/test_terminal_policy.py` | 档位、免确认命令条目、解释器、危险模式、cwd 沙箱、程序劫持、环境变量 |
| `tests/test_terminal_runner.py` | 注册条件、超时推导、闸门 `busy`、确认拒绝、shell 开关、`list` 快照结构 |
| `tests/test_terminal_confirm.py` | sink 未注册立即拒绝、超时拒绝、中断拒绝、异常按拒绝处理、端到端确认链路 |
| `tests/test_terminal_window.py` | 来源配色、缓冲上限、命令历史、多行粘贴、自动滚动、输入跳过确认 |
| `tests/test_cli.py` | 前缀判定、内建命令分发、帮助排版与分组、`:check` 判定跟随档位、工具命令经 `ToolExecutor` 调用、工具未注册与执行失败、任意新登记命令无需改 cli |
| `tests/test_tool_commands.py` | 三个工具登记的用法、补全候选与示例、参数翻译的成功与拒绝用例、时长解析、注册表的重名忽略与按启用过滤 |
| `tests/test_confirm_card.py` | 卡片位置分支、标题序号、快捷键与危险态 |
| `tests/test_completion.py` | 候选来源、公共前缀、引号与空格处理、大小写策略、条目上限 |
| `tests/test_architecture_contracts.py` | 确认无新增 ARCH009 与 ARCH001 债务；`TerminalRunner` 在注册函数内创建 |
| `docs/architecture.md` | §9 模块职责表补 `pet/terminal/` 与新增窗口 |
| `docs/reference/tools.md` | `python scripts/gen_docs.py` 重新生成 |
| `docs/reference/config.md` | 同上，两个全局开关进表 |
| `docs/reference/prompt-blocks.md` | 同上，`_TERMINAL_GUIDE` 进块清单 |
| `docs/reference/modules.md` | 同上，新增模块的一句话职责 |

`tests/test_docs.py` 要求 `pet/terminal/` 出现在 `architecture.md` 中，新增包时同步补表。生成器静态解析 `add_method` 字面量，`tools.md` 会无条件列出 `terminal__run` 与 `terminal__list`，与 `TERMINAL_ENABLED` 的取值无关。

## §12 验证方式

自动化：

```
python -m pytest -q
python scripts/gen_docs.py --check
```

`tests/test_terminal_core.py` 覆盖：

1. 引号配对：POSIX 下未闭合的单引号与双引号返回 `bad_command`；Windows 下未闭合的双引号返回 `bad_command`，双引号段内的单引号不参与配对；转义引号不算配对；
2. 解析：普通命令、带空格路径、Windows 反斜杠、空串；
3. 内建路由：Windows 下 `dir /a` 的 `routed` 为 `["cmd", "/d", "/s", "/c", "dir /a"]`，`builtin` 为 `"dir"`，原始 `argv` 不变；`cd` 与 `del` 不路由；`shell="cmd"` 时 `routed` 为 `["cmd", "/d", "/s", "/c", command]`、`builtin` 为空、`argv` 为 `[command]`；
4. 大输出：命令输出 1MB 时 5 秒内返回，`truncated` 为真，进程无残留；
5. 超时：长命令超时后进程树不存在（按 PID 探测）；`taskkill` 失败路径返回 `kill_failed`；
6. 截断：两流各头 1500 + 尾 500，合计 4000，省略量标注正确；
7. 解码：UTF-8、GBK 字节、非法字节序列三条路径；
8. 平台分支：POSIX 下 `ls` 不被内建路由、argv 展示用 `shlex.join`；`killpg` 抛 `ProcessLookupError` 时按终止成功处理。

`tests/test_terminal_policy.py` 覆盖：

1. 档位：`deny` 返回 `terminal_disabled`；`ask` 一律询问；`auto` 对免确认清单内放行、对外询问；
2. 参数模式：`git status` 免确认、`git push` 询问、`git status --short` 免确认；`git *` 匹配任意参数，裸 `*` 被判为非法条目跳过；条目两端空白被裁剪，词间多空格不产生空 token；清单只来自配置，重复条目合并，空配置不放行任何命令；
3. 解释器：`python -V`、`python -c "print(1)"`、`node -e`、`pwsh -Command` 在 `auto` 与 `ask` 档都转询问；
4. 危险模式：默认清单逐条命中，非法正则被跳过，`auto` 档命中转询问；
5. cwd：相对路径落在沙箱内、`..` 逃逸被拒、绝对路径越界被拒、用户来源不受限；
6. 劫持：沙箱根放置 `git.bat` 且 `cwd` 取沙箱根时，`auto` 档返回 `untrusted_program`，`ask` 档标注后询问；沙箱根下 `tools/git.bat` 以绝对路径调用同样命中；沙箱内无同名文件、解析到系统 `git.exe` 时放行；
7. 环境变量：`LLM_KEY` 与 `*_TOKEN` 不出现，`TERMINAL_ENV_ALLOW` 中的普通键保留；
8. 超时换算：`clamp_timeout(999)` 等于 `TERMINAL_MAX_COMMAND_TIMEOUT_S`；`clamp_timeout(0)` 与负数取 `TERMINAL_COMMAND_TIMEOUT_S`；runner 的用户入口取 `TERMINAL_MAX_COMMAND_TIMEOUT_S`；
9. 路径大小写：`is_writable_path()` 接受 `platform` 参数，Windows 与 macOS 策略下同一路径的不同大小写不判越界，Linux 策略下区分；`..` 逃逸在各平台都被拒。
10. 内建命令：`dir /a` 在 `auto` 档命中额外命令 `dir *` 直接执行，不因 `routed[0]` 是 `cmd` 转询问；用户直接输入 `cmd /c dir` 时 `builtin` 为空，按解释器处理转询问。

`tests/test_terminal_runner.py` 覆盖：

1. 注册条件：`TERMINAL_ENABLED` 为假时工具不存在；为真时 `meta`、`side_effect`、`group` 三项正确；
2. 超时推导：`method_timeout` 等于三项配置之和加 `KILL_WAIT_S`、`OUTPUT_JOIN_S` 与 3 秒余量，默认 200；
3. 闸门：`auto` 档放行的命令占住闸门后，第二次调用在 `queue_wait_s` 内返回 `busy`；
4. 确认：确认函数返回 `False` 时返回 `denied_by_user` 且执行核心未被调用；未注入确认函数时立即拒绝；
5. 结果：非零退出不带 `error` 键；
6. `list` 快照：符合 `TERMINAL_LIST_OUTPUT` 的字段结构，`headline` 与 `body` 非空；`installed` 标记与 `shutil.which` 的桩一致；返回内容不含正则原文；
7. shell 开关：开关为假时模型传 `shell` 返回 `shell_mode_disabled`；开关为真时按平台组装 `routed`；`run_user` 不受开关限制。

`tests/test_terminal_confirm.py` 覆盖 sink 未注册立即拒绝（耗时小于 50ms）、sink 正常回传、超时拒绝、`reject_pending_confirms` 生效、sink 抛异常按拒绝处理，以及端到端链路：注册 sink 后模型命令进入确认队列，允许时执行核心被调用一次，拒绝时不被调用。另断言 `pet/tools/context.py` 的模块级没有 `pet.agent` 导入。

`tests/test_terminal_window.py` 覆盖来源前缀与配色分组、5000 块上限、历史去重与上下键取值、多行粘贴截断、自动滚动跟随、用户输入不经确认且以 shell 模式提交、首次打开用主目录、恢复 `last_cwd`、目录失效回退、`cd` 拦截、隐藏后重新显示保留内容、冒号命令的 Tab 补全与轮换。用例用桩替换执行核心，不真的调用命令。

`tests/test_cli.py` 覆盖前缀判定、内建命令分发与三种返回码、帮助的逐行用法与分组顺序、说明列对齐、示例随命令出现与消失、单命令帮助与别名、`:check` 判定随档位与免确认清单变化、`:status`（含 `--json`）与 `:policy` 的快照字段，以及工具命令经 `ToolExecutor` 发出的方法名与参数；工具方法缺失与注册表查询异常都落到返回码 1，另有一条用例证明新工具登记的命令无需改 cli 即可分发与进帮助。用例用桩注册表替换全局注册表，不碰真实工具与数据库。

`tests/test_tool_commands.py` 覆盖 `timer` / `todo` / `knowledge` 各自 `register_commands()` 产出的分组、用法行、补全候选与参数翻译，含非法输入抛 `ValueError` 的用例；另覆盖注册表的聚合：`recall` 的 `:memory` 已登记、工具关闭后命令消失、重名命令只留第一条、未注册工具直接拒绝。

`tests/test_confirm_card.py` 覆盖 `card_position()` 的四种分支（上方足够、上方不足转下方、恋恋在屏幕外落右下角、结果夹进可用区），以及标题序号、`Esc` 拒绝、危险模式下 `Enter` 不触发允许、关闭等同拒绝。`tests/test_completion.py` 覆盖：首词候选、Shell 内建与可执行名、目录的优先级、`PATH` 与目录合并、目录类与文件类命令的候选过滤、隐藏条目的显示条件、公共前缀计算、引号保留与含空格时自动加引号、Windows 与 POSIX 的大小写差异、`~` 与 `..` 的基准解析、绝对路径、大目录先过滤后截断到 500 条、冒号命令名与子命令候选、命令名不区分大小写、别名解析子命令、`flags` 与 `values` 候选、三种 shell 的环境变量名、`help` 的命令名候选、`check` 的参数补程序名与目录条目。

手动检查清单：

1. 右键恋恋 → 终端，窗口打开；再次点击只前置窗口；
2. 模型要求执行 `dir` 时弹出确认卡片，显示实际执行的 `cmd /d /s /c`，元信息含「内建 dir」与 cwd，倒计时归零后自动拒绝；
3. 允许后命令执行，输出进入终端窗口与模型下一轮上下文；
4. 拒绝后模型收到 `denied_by_user`，本轮不再重复提交同一命令；
5. 在终端输入 `dir` 直接执行，无确认卡片，来源前缀为 `[用户] $`；
6. 同轮两条命令并行到达时，确认卡片依次显示「命令 1/2」「命令 2/2」，第二条在第一条结束后才执行；
7. `TERMINAL_MODE="auto"` 时 `git status` 与 `dir` 免确认，`git push` 与 `python -c "print(1)"` 仍询问；
8. 命令超时后任务管理器中无残留子进程；
9. 恋恋隐藏或贴边时确认卡片仍能弹出；
10. 应用退出时有未决确认，进程立即结束，不卡住；
11. 模型命令的 `cwd` 传 `..\\..` 时返回 `cwd_denied`；
12. 关闭 `TERMINAL_ENABLED` 重启后，模型工具列表里没有 `terminal__run`，终端窗口仍可用；
13. 重启后终端窗口显示上次的最近命令记录；
14. 在桌面放一个 `git.bat`，`auto` 档执行 `git status` 返回 `untrusted_program`；在桌面建子目录放同名文件并按其绝对路径调用，同样返回 `untrusted_program`；
15. 询问模型能用什么命令时，它先调用 `terminal__list`，回复里给出本机免确认命令与沙箱根；未安装的程序标注为未安装；
16. 确认卡片在恋恋上方弹出；恋恋靠在屏幕顶部时改到下方；恋恋隐藏时落在鼠标所在屏的右下角；
17. 命令命中危险模式时分类条转红、`Enter` 不生效，点击「仍要执行」后才执行；
18. 终端里贴上多行文本，只执行首行，输入行提示已忽略后续行；
19. 在 macOS 或 Linux 上执行 `ls` 与 `echo`，不经过 `cmd` 路由、输出正常；Wayland 下确认卡片仍能弹出，位置可能由合成器决定；
20. 设置页「通用」把档位改为 `auto`，模型执行 `git status` 直接执行、执行 `git push` 弹卡片，保存后立即生效；
21. 设置页修改额外命令与沙箱根后不重启即生效；切换 `TERMINAL_ENABLED` 后需重启；
22. 在终端输入 `dir | findstr py` 与 `echo %PATH%`，管道与变量展开生效；把 `git status` 写进额外命令后，`auto` 档下它直接执行；
23. 状态栏没有档位下拉；输入框内显示 `C:\Users\...>`，文字紧接提示；用 `cd` 切换目录后提示更新且不回显提示行。
24. 输入 `:help` 列出全部命令，块尾与 shell 命令同形显示 `exit 0 · 0.0xs`；输入 `:nope` 只写一行错误（`exit 2`），不启动子进程。
25. 把 `git status` 写进额外命令后，`:check git status` 显示直接执行，`:check python script.py` 显示弹卡片询问，`:check rm -rf /` 显示命中危险类别。
26. `:timer 10m 泡面` 到点提醒，`:timer list` 能看到该条；`:todo add 买牛奶` 后 `:todo list` 可见，面板里同样可见。
27. 输入 `:he` 按 `Tab` 补成 `:help `；输入 `:` 按 `Tab` 依次轮换命令名与别名；`:timer ` 后按 `Tab` 出 `list` 与 `cancel`；`:todo list ` 出 `pending`、`done`、`all`；`:status --` 出 `--json`；`:help ` 列出命令名；`cd Doc` 补成目录并保留分隔符，文件补全后带一个空格；`$PA`（bash/sh）或 `%PA`（cmd）补环境变量名；`:check gi` 补出 `git`。
28. `:status` 显示恋恋的状态、已持续、动作、贴边、四项数值与长期记忆条数；应用未完成初始化时只回一行错误（`exit 1`），不显示半份数据。

## §13 风险与约束

- **确认疲劳**：同轮多条命令会连续弹卡片，用户容易形成机械点击。缓解手段是工具描述要求一次只发一条、卡片标红高危与可写目录来源、`auto` 档承担高频只读命令；代码层不做「本次会话内全部允许」。
- **shell 逃逸**：`python`、`node`、`pwsh` 一经确认就能执行任意代码，免确认清单与危险模式只对字面命令生效。安全边界实际落在用户确认上，`ask` 档的卡片必须显示完整 argv 而不是摘要。
- **终端始终可用**：右键菜单的「终端」不受开关控制，首次启动即可打开等价于系统终端的入口。
- **内建命令路由**：改写为 `cmd /d /s /c` 后由 cmd 重新解析参数，`list2cmdline` 的引用规则与 cmd 的解析规则存在少量差异，个别含特殊字符的参数可能被改写。内建集合固定为只读命令，影响面可控。
- **终端内容不落盘**：回显与命令历史只存在内存，退出即清空；模型命令的调用记录在事件日志里。
- **并行确认**：确认队列按到达顺序展示，与模型声明顺序可能不同。卡片标注序号与完整 argv，减少错配。
- **被拒后重试**：模型可能反复提交同一条命令，`denied_by_user` 的 `hint` 与工具描述都写明不要重试，但无法从代码层阻止。
- **executor 超时**：方法超时由配置推导，正常路径不会触发；触发后 `pet/tools/executor.py` 返回 `error_code="timeout"`，`ToolResult.hint` 为空，`pet/brain/tool_loop.py` 使用缺省 hint「稍后重试」。业务失败返回结构里的 `hint` 由 executor 写入 `ToolResult.hint`，优先于缺省 hint。
- **闸门占用**：用户在终端运行长命令时，模型调用在 `queue_wait_s` 内等待后返回 `busy`，模型可能反复尝试；工具描述要求遇到 `busy` 时告知用户而不是重试。
- **编码差异**：中文 Windows 下部分程序输出 GBK，部分输出 UTF-8，三级解码仍有乱码可能，`errors="replace"` 保证不崩溃。
- **挂起程序**：拦截交互式程序只靠 `stdin=DEVNULL` 与超时，某些程序在前台等待其它事件时仍会占满超时时间。
- **线程语义**：executor 超时后不终止 handler 线程；本设计的确认与执行都自带超时并主动终止子进程，仍存在「用户在最后一刻点击允许」与工具超时重叠的窗口。
- **生成物与开关**：`tools.md`、`prompt-blocks.md` 无条件列出 `terminal` 与 `_TERMINAL_GUIDE`，默认关闭时生成物仍有条目。生成物描述代码结构，不描述运行时开关。
- **平台**：Windows 是主路径，内建路由、`taskkill` 与可执行劫持拦截都在该平台落地；macOS 与 Linux 共用 POSIX 分支，`killpg`、`shlex.join`、Homebrew 目录可信判定与字体回退需要实机验证。Wayland 下卡片定位由合成器决定，可能偏离恋恋。
- **上下文成本**：每次命令输出最多 4000 字符，多轮工具调用会累积；`headline` 进历史、`body` 只进当轮，避免长期占用。

## §14 结论

终端命令能力由三块组成：执行核心负责解析、内建路由、读取输出与终止进程树；策略层负责档位、免确认命令、危险模式、程序解析与沙箱；界面层负责确认卡片与终端窗口。模型入口是 `terminal__run`，用户入口是右键主菜单的终端窗口，两者共用 `TerminalRunner`，区别只在 `run` 与 `run_user` 两个入口的策略分支与闸门排队。安全边界放在用户确认上：`ask` 档全部询问、`auto` 档只放行免确认清单内的「程序 + 参数模式」、`deny` 档全部拒绝，任何异常路径都归为拒绝。`TERMINAL_ENABLED` 控制模型能力，终端入口始终可用。模型不预置命令大全：本机能做什么由 `terminal__list` 提供快照，提示词只约束行为，不承载易变的清单。
