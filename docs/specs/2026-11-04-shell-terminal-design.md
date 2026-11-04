# 桌宠命令行环境设计

> 给恋恋一份可交互的终端：模型通过 `shell__run` 执行命令，用户通过右键主菜单的「终端」打开窗口直接输入命令。两条入口共用同一个执行核心与同一份策略；模型发起的命令默认逐条等待用户确认，用户自己输入的命令免确认。执行结果同时进入 LLM 上下文与终端窗口，窗口按来源标记区分，执行记录落盘可回溯。模型能力与终端入口由两个独立开关控制。

## §0 背景与现状

| 项 | 现状 | 位置 |
|---|---|---|
| 工具注册 | `register()` + `add_method(output=...)`，输出契约必填 | `pet/tools/registry.py` |
| 元工具 | `meta=True` 的工具在 `default` 组常驻，不受 `TOOLS_ENABLED` 过滤，不进右键工具列表与提示词工具清单 | `pet/tools/registry.py`、`pet/brain/prompts.py` |
| 参数校验 | 按 `args` spec 校验类型、必填、枚举；不做数值范围夹紧 | `pet/tools/executor.py` |
| 超时语义 | handler 在独立线程执行，`worker.join(method.timeout)` 超时只返回错误，线程与子进程都不终止 | `pet/tools/executor.py` |
| 工具并行 | `LLM_TOOL_PARALLEL` 默认为真；同轮多调用交给线程池并行，含 `game__` 时退化为串行 | `pet/brain/tool_loop.py` |
| 菜单入口 | `add_menu_action` 的项挂成工具子菜单的第二级；`meta` 工具整项跳过，其子菜单不可达 | `pet/ui/pet_window.py` |
| 工具上下文 | `TOOL_CTX` 保持零依赖，不 import `pet.agent`，只操作已绑定的对象 | `pet/tools/context.py`、ARCH001 |
| 模块级约束 | ARCH009 禁止模块级出现定义与登记之外的调用，包括创建锁与实例 | `tests/test_architecture_contracts.py` |
| 退出路径 | brain 线程未退出时走 `logging.shutdown()` 后 `os._exit(code)`，跳过解释器清理 | `pet/app.py` |
| 文档生成 | `tools.md` 静态解析 `registry.py` 的 `register()` / `add_method()` 字面量，不判断注册条件 | `scripts/gen_docs.py` |
| 副作用标记 | `side_effect` 只写入工具调用事件的 payload，供事件日志与回放使用 | `pet/brain/tool_loop.py`、`pet/brain/base.py` |
| 窗口范式 | 无边框窗口 + 跨线程 `Signal` 桥接 + `deque` 缓冲 + 块数上限 | `pet/ui/log_window.py` |
| 文件能力 | `file_ops` 限桌面/文档，不做命令执行 | `pet/tools/file_ops/` |

三处约束直接影响设计：工具调用是并行的，确认不能假设串行；executor 的超时不终止子进程，终止必须由执行核心自己完成；`TOOL_CTX` 不能反向依赖 agent。

## §1 目标

- 模型可通过 `shell__run` 执行单条命令，返回退出码、标准输出、标准错误。
- 用户可通过右键主菜单的「终端」打开窗口，直接输入并执行命令。
- 模型发起的命令默认等待用户确认；拒绝、超时、界面不可用一律不执行。
- 用户输入的命令以 `source="user"` 标记，跳过确认；沙箱只约束模型发起的命令。
- 同轮多个工具调用并行到达时，确认排队串行展示，执行串行，不出现并发弹卡与并发执行。
- 命令解析为参数列表后直接执行；Windows 内建命令显式经 `cmd /c` 路由。
- 超时与异常路径下终止整个进程树；终止失败如实报错，不静默放行。
- 输出按字节上限捕获、按头尾截断进入模型上下文，完整输出留在终端窗口。
- 每次执行写一条审计记录，字段含时间、来源、命令、工作目录、退出码、耗时。
- 模型可通过 `shell__list` 查询当前策略快照：平台、沙箱根、免确认的程序与参数、解释器名单、程序的安装情况。
- 两类失败分开处理：命令非零退出属于正常结果；策略拒绝与执行异常走工具失败通道。

## §2 非目标

- 不做持久 shell 会话：`cd`、`activate`、`export` 不跨命令生效，工作目录靠 `cwd` 参数显式传递。
- 不做终端仿真：不解析 ANSI 转义序列，不支持需要 TTY 的交互式程序（`vim`、`top`、`ssh` 登录）。
- 模型路径不解析管道、重定向与变量展开；需要这些特性时显式选择 `shell="pwsh"` 或 `shell="cmd"`。用户输入默认交给系统 shell，因此终端里这些语法可用。
- 不为 POSIX 模拟 Windows 内建命令；内建路由只在本平台生效。
- 不做输出流式回传：命令结束后一次性返回。
- 不做审计记录的加密与脱敏。
- 不引入进程间通信，不做外部 CLI 客户端。
- 不改动现有分组激活机制与工具循环结构。

## §3 总体结构

| 组件 | 职责 | 依赖 |
|---|---|---|
| `pet/shell/core.py` | 命令解析、内建命令路由、子进程执行、终止进程树、解码与截断 | 标准库 |
| `pet/shell/policy.py` | 策略档位、白名单参数模式、危险模式、程序解析、cwd 与环境变量 | `pet.config`、标准库 |
| `pet/shell/audit.py` | 审计记录追加、读取、裁剪 | 标准库 |
| `pet/shell/runner.py` | `ShellRunner` 实例：执行闸门（带超时的锁）、调用 core 与 policy、生成策略快照 | 上述三者 |
| `pet/tools/registry.py` | `_register_shell_tools()` 内置注册元工具，创建 `ShellRunner` 实例 | registry、`pet/shell` |
| `pet/ui/terminal_window.py` | 只读回显、输入行、来源标记、命令历史 | Qt |
| `pet/ui/confirm_card.py` | 确认卡片：队列展示、倒计时、允许与拒绝 | Qt |
| `pet/tools/context.py` | `set_confirm_sink()` 与 `confirm(request)`，零依赖 | 已绑定的 agent 对象 |

数据流（模型发起）：

1. 工具循环调用 `shell__run`，handler 在工具线程执行。
2. `ShellRunner` 尝试获取执行闸门，等待超过 `queue_wait_s` 返回 `busy`。
3. `policy.check()` 给出 `allow` / `ask` / `deny`；`ask` 时调用 `TOOL_CTX.confirm()` 进入确认队列。
4. 允许后 `core.run()` 执行，输出同时写入终端窗口与审计文件。
5. handler 返回规范值，投影层渲染为模型可见文本。

数据流（用户发起）：

1. 终端窗口输入行提交，`policy` 以 `source="user"` 判定，跳过确认与档位。
2. 通过同一个 `ShellRunner` 获取闸门并调用 `core.run()`。
3. 结果写回终端窗口与审计文件，不进入 LLM 上下文。

`ShellRunner` 在 `_register_shell_tools()` 内创建，不在模块级构造；锁是实例属性，不触发 ARCH009。

## §4 执行核心

### §4.1 命令解析与内建命令路由

`run(command: str)` 是一个字符串参数，解析规则：

```python
if not _quotes_balanced(command):
    return _error("bad_command", "命令里的引号没有闭合")
argv = shlex.split(command, posix=(os.name != "nt"))
```

- 引号配对由执行核心自检：统计未被转义的 `"` 与 `'`，任一为奇数即失败。`shlex.split(posix=False)` 不报未闭合引号，必须自检。
- Windows 用 `posix=False`，保留 `C:\path\to\file` 中的反斜杠；随后对每个 token 剥掉成对的包裹引号。
- POSIX 用 `posix=True`，按 shell 词法拆分。
- 拆分结果为空时返回 `bad_command`。

Windows 内建命令不在 `PATH` 里，`shutil.which` 找不到，必须显式路由。命中内建集合时改写为：

```python
argv = ["cmd", "/d", "/s", "/c", subprocess.list2cmdline(argv)]
```

- 内建集合固定：`dir`、`type`、`echo`、`cls`、`ver`、`where`、`findstr`、`tree`、`vol`、`path`、`attrib`、`more`、`sort`。
- `cd`、`set`、`copy`、`move`、`del`、`md`、`rd`、`ren` 不进入集合：`cd` 不跨命令生效，其余是写操作。
- 参数由 `list2cmdline` 从已拆分的 token 重建，不把用户原文拼进命令行。
- 路由发生在策略判定之前，策略看到的是改写后的 argv，`argv[0] == "cmd"` 的调用按 `shell` 模式对待。

`shell` 参数非空时，`command` 原样作为单个参数交给子进程，不再拆分。可选值是平台相关的：

| 平台 | 取值 | 实际 argv |
|---|---|---|
| 全部 | `""` | `argv` |
| Windows | `"pwsh"` | `["powershell", "-NoProfile", "-NonInteractive", "-Command", command]` |
| Windows | `"cmd"` | `["cmd", "/d", "/s", "/c", command]` |
| POSIX | `"sh"` | `["sh", "-c", command]` |
| POSIX | `"bash"` | `["bash", "-c", command]` |

方法的 `enum` 在注册时按 `os.name` 生成，POSIX 上不出现 `pwsh` / `cmd`。展示 argv 时 Windows 用 `subprocess.list2cmdline`、POSIX 用 `shlex.join`，两者不通用。

`SHELL_ALLOW_SHELL_MODE` 只约束模型发起的调用；用户从终端提交的命令由窗口层直接指定 shell 模式，不受该开关限制。

`shell` 模式由 `SHELL_ALLOW_SHELL_MODE` 控制，默认关闭；开启时确认卡片标注「整条命令交给 shell」，因为解析由 shell 负责，白名单与危险模式只能做字面匹配。

### §4.2 进程与超时

用 `Popen` 加两个读线程，不用 `wait` 配 `PIPE`：`wait` 不消费管道，输出超过管道缓冲后子进程阻塞在写，`wait` 永不返回。

| 环节 | 做法 |
|---|---|
| 启动 | `Popen(argv, shell=False, stdin=DEVNULL, stdout=PIPE, stderr=PIPE)`，Windows 加 `CREATE_NEW_PROCESS_GROUP`，POSIX 加 `start_new_session=True` |
| 读线程 | stdout、stderr 各一个守护线程，`read(8192)` 循环追加到 `bytearray`，累计超过 `max_capture_bytes` 后继续读但丢弃并置 `truncated` |
| 等待 | 主线程 `wait(timeout)`；读线程持续消费，管道不会填满 |
| 超时 | 终止进程树，再 `wait(5)`，然后 join 读线程最多 2 秒 |
| 终止 | Windows `taskkill /F /T /PID`，POSIX `os.killpg(os.getpgid(pid), SIGKILL)` |

- 命令级 `timeout` 参数默认取 `command_timeout_s`，由 handler 夹紧到 `[1, max_command_timeout_s]`；executor 不做范围校验。
- 终止失败（`taskkill` 非零退出、`killpg` 抛 `PermissionError`）返回 `kill_failed`，并在错误消息里带 PID 与「进程可能仍在运行」，不再记录日志后放行。`killpg` 抛 `ProcessLookupError` 表示进程已自行退出，按成功处理。
- 终止成功但读线程 2 秒内未结束，返回输出并附「读取线程未退出」标注，不影响退出码。
- 执行闸门：`ShellRunner.lock.acquire(timeout=queue_wait_s)`，失败返回 `busy`，不无限等待；模型命令与用户命令共用同一把锁。

### §4.3 输出与编码

以字节捕获，按顺序尝试解码：

1. UTF-8 严格解码；
2. 失败时按 `locale.getpreferredencoding(False)` 解码（中文 Windows 为 GBK）；
3. 再失败按 UTF-8 加 `errors="replace"`。

截断口径（`max_output_chars` 默认 4000，指两流合计）：

- 每流头 1500 字符 + 尾 500 字符，标准输出与标准错误各自独立截断。
- 合计上限 4000 字符与上面的分配合计一致；任一流不足时，结余不转移给另一流，保证口径可预测。
- 中间插 `<省略 N 字符>`；`truncated` 为真时 `headline` 附「输出已截断」。
- `body` 中标准输出在前，标准错误非空时以 `--- stderr ---` 分隔追加。
- 完整输出交给终端窗口，窗口侧按块保留最近 5000 条。

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
| `shell_disabled` | 策略档位为 `deny` | 不要重试，工具已被用户关闭 |
| `not_allowlisted` | `allowlist` 档下程序或参数不在白名单 | 用 `shell__list` 查看允许的命令，不要换写法绕过 |
| `denied_by_pattern` | `allowlist` 档下命中危险模式 | 该命令被规则禁止，不要重试 |
| `denied_by_user` | 用户拒绝或确认超时 | 不要重复提交同一条命令 |
| `bad_command` | 引号不配对、拆分为空、可执行文件未找到 | 重新给出一条完整命令；不确定本机有哪些程序时先调用 `shell__list` |
| `untrusted_program` | `auto` 档下可执行文件来自沙箱根或当前目录 | 用 `shell__list` 查看可信程序，不要换路径绕过 |
| `cwd_denied` | `cwd` 解析后落在沙箱根之外 | 换用允许的目录 |
| `command_timeout` | 命令超过 `timeout` 被终止 | 命令已终止，不要原样重试 |
| `busy` | 执行闸门等待超时 | 说明前面的命令还在跑，不要立即重试 |
| `kill_failed` | 终止进程树失败 | 提示进程可能仍在运行，不要重试 |
| `shell_mode_disabled` | 传了 `shell` 但 `SHELL_ALLOW_SHELL_MODE` 为假 | 去掉 `shell` 参数 |

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
- `app_dir()` 已按平台分流，审计文件不需要额外处理。

## §5 安全模型

### §5.1 策略档位

配置项 `SHELL_MODE` 四档，设置页与终端窗口状态栏都可切换：

| 档位 | 模型发起的命令 | 用户输入的命令 |
|---|---|---|
| `deny` | 全部拒绝，返回 `shell_disabled` | 正常执行 |
| `allowlist` | 只执行白名单命中的命令，其余返回 `not_allowlisted` | 正常执行 |
| `ask` | 全部询问，卡片标注命中项 | 正常执行 |
| `auto` | 白名单命中直接执行，其余转 `ask` | 正常执行 |

`allowlist` 是「只允许用户列出的命令」，适合长期放着不管或交给恋恋自主轮使用；`ask` 是「每次都看一眼」，适合有人在场时使用。默认值为 `ask`。

用户输入只经过命令组装与审计两步：白名单、危险模式、沙箱根与 `SHELL_MODE` 都不参与。终端窗口因此等价于系统终端，区别只在于它挂在恋恋的右键菜单里，执行记录与恋恋的命令混在同一份审计中。

两个全局开关与档位的关系：

| 开关 | 作用 | 关闭后果 |
|---|---|---|
| `SHELL_ENABLED` | 注册 `shell` 元工具 | 模型看不到 `shell__run`，终端窗口照常可用 |
| `SHELL_TERMINAL_ENABLED` | 右键主菜单显示「终端」 | 只影响窗口入口，模型能力不受影响 |

`mode="deny"` 与 `mode="allowlist"` 都保留工具在 schema 里但可能拒绝执行，便于临时收紧而不重启；代价是 schema 与提示词位置仍然被占用。

### §5.2 命令白名单

白名单由用户完全自定义，写在设置页「行为」的「终端命令白名单」，用于两种场合：`auto` 档决定哪些命令免确认，`allowlist` 档决定哪些命令唯一可执行。

```
SHELL_AUTO_ALLOW = ["git status", "git log", "dir *", "where *"]
```

- 每个条目是空格分隔的简写：第一个词是程序名，其余是参数模式。
- `*` 作为末尾词表示任意参数；只写程序名（如 `"git"`）不匹配任何调用，要放行全部必须写 `git *`。
- 条目之间用逗号分隔，这是设置页 `str_list` 控件的解析规则，条目内的空格保留。
- 空列表表示使用内置的平台默认白名单；非空时整体替换默认，不做合并，避免出现删不掉的默认项。
- 程序名匹配 `argv[0]` 的 basename（去扩展名、小写），参数匹配 `argv[1]`，采用前缀式：`"git status"` 允许 `git status --short`，不允许 `git push`。
- 解释器名单是内置常量，只在 `auto` 档把免确认降级为询问，`allowlist` 档以用户显式列出的条目为准：`python`、`python3`、`py`、`node`、`npm`、`npx`、`pwsh`、`powershell`、`cmd`、`bash`、`sh`、`wsl`。把 `python` 写进白名单等于允许任意代码执行，设置页的说明文字需要提示这一点。
- 平台默认白名单按平台区分：Windows 含 `dir`、`type`、`where`、`findstr`、`tree`、`ver`、`vol`、`path` 与 `git` 的只读子命令；POSIX 含 `ls`、`cat`、`pwd`、`whoami`、`hostname` 与 `git` 的只读子命令。
- 白名单只表达「哪些命令可以不经确认或允许执行」，不承担拦截恶意命令的职责；拦截由危险模式与沙箱负责。

### §5.3 危险模式

`SHELL_DENY_PATTERNS` 是一组正则，写入 `settings.json` 的高级项（设置页不展示），只作用于模型发起的命令，与完整命令字符串做不区分大小写的匹配；空列表表示使用内置清单。命中时：

- `allowlist` 档：直接拒绝，返回 `denied_by_pattern`，不提供「仍要执行」通道；
- `ask` 档：确认卡片标红，默认按钮为「拒绝」，需要显式点击「仍要执行」；
- `auto` 档：按未命中白名单处理，转 `ask`；
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

只按 basename 判定会被劫持：Windows 的 `Popen` 与 `shutil.which` 都会搜索当前目录，而 `file_ops` 能向默认沙箱根（桌面）写 `.bat`、`.ps1`、`.py`。因此：

- 先 `shutil.which(argv[0])` 解析为绝对路径；`None` 时返回 `bad_command`。
- 解析结果的父目录等于 `cwd` 或 `sandbox_root` 时，`auto` 档返回 `untrusted_program`，`ask` 档在卡片上标注「来自可写目录」。
- 内建命令路由后的 `cmd` 从系统目录解析，不受上面两条影响。
- `ask` 档执行时也使用解析出的绝对路径，避免判定与执行之间被替换。
- POSIX 的 `shutil.which` 不搜索当前目录，`untrusted_program` 实际只在 Windows 触发；macOS 的 `/usr/local/bin`、`/opt/homebrew/bin` 与 Linux 的 `/usr/bin`、`/bin` 视为可信目录。

### §5.5 工作目录与环境变量

- `cwd` 为空时取沙箱根（`SHELL_SANDBOX_ROOT`，留空为桌面）。
- 相对路径按沙箱根解析；绝对路径与相对路径都经 `os.path.realpath` 后与根做 `commonpath` 比较，失败返回 `cwd_denied`。
- 比较前两侧都过 `os.path.normcase`：Windows 路径大小写不敏感，macOS 默认文件系统也不敏感但 `posixpath` 区分大小写，不统一会误判越界。
- 沙箱只约束 `source="model"`；用户输入的 `cwd` 不受限，输入行左侧显示当前目录。
- 子进程环境只保留 `PATH`、`SYSTEMROOT`、`WINDIR`、`TEMP`、`TMP`、`HOME`、`USERPROFILE`、`LANG`、`LC_ALL`、`PYTHONIOENCODING`，加上 `SHELL_ENV_ALLOW` 列出的键；`LLM_KEY`、`*_API_KEY`、`*_TOKEN` 即使出现在该列表里也拒绝透传。

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

handler 在工具线程中运行，界面在主线程，确认必须跨线程：

1. UI 启动后调用 `TOOL_CTX.set_confirm_sink(fn)` 注册接收函数；应用退出时 `set_confirm_sink(None)` 注销。
2. `TOOL_CTX.confirm(request)` 检查 sink；为空立即返回 `False`。
3. 请求进入 `ShellRunner` 持有的队列，`PetAgent.confirm_requested = Signal(object)` 发出通知。
4. 主线程的槽函数从队列取请求，一次只显示一条卡片；队列长度写进标题（如「命令 2/3」）。
5. 用户点击后写 `request.result` 并 `request.event.set()`；主线程不触碰工具线程的其他状态。
6. 工具线程 `event.wait(confirm_timeout_s)` 返回，读 `result` 决定执行或拒绝。
7. 卡片提供「全部拒绝」，清空队列并把剩余请求统一置为拒绝。

请求对象字段：`argv`、`command`、`cwd`、`shell_mode`、`matched_patterns`、`timeout_s`、`source`、`sequence`。卡片只读这些字段，不做二次解析。卡片的视觉与键盘交互见 §7.6。

### §6.2 并行调用与串行化

`LLM_TOOL_PARALLEL` 默认为真，同轮多个 `shell__run` 会并行进入，因此：

- 确认队列保证同一时刻只有一张卡片，不会叠加弹窗；
- 执行闸门保证同一时刻只有一条命令在跑，其余在 `queue_wait_s` 内排队，超时返回 `busy`；
- 队列顺序按请求到达时间，不保证与模型声明顺序一致；卡片标题显示序号，便于对应；
- 用户命令与模型命令共用闸门，用户在终端跑长命令时，模型调用可能返回 `busy`。

### §6.3 超时与中断

- 确认超时 `confirm_timeout_s` 默认 60 秒，卡片显示剩余秒数，归零后自动关闭并按拒绝处理。
- 中断在途调用时，`PetAgent` 遍历未决请求置为拒绝并清空队列。
- 应用退出时同样清空未决请求；清理必须写在 `pet/app.py` 的退出函数里，且位于 `os._exit(code)` 之前，`atexit` 与析构函数不参与。
- 终端窗口关闭不注销 sink：窗口是单例，关闭只是 `hide()`，下次菜单再次 `show()`。

### §6.4 拒绝语义

```python
{"error": "用户拒绝了这条命令", "code": "denied_by_user",
 "hint": "不要重复提交同一条命令；需要时先说明想做什么，等用户同意"}
```

模型看到 `[FAIL shell__run] denied_by_user: 用户拒绝了这条命令`。工具描述、错误 `hint` 与 `_SHELL_GUIDE` 三处都写「不要重试」。同一轮内不做拒绝缓存，模型再次调用仍会再次询问；这是模型行为问题，不用代码拦截。

## §7 终端窗口

界面自成一套暗色主题，不复用 `pet/ui/styles.py` 的米白扁平风格。终端天然属于暗底等宽的语境，暗色也让桌面焦点留在恋恋身上而不是窗口上。全部色值集中在 `pet/ui/shell_theme.py`，与现有窗口的配色互不影响。

### §7.1 设计语言

| 维度 | 选择 | 理由 |
|---|---|---|
| 底色 | 炭灰 `#17191a`，面板 `#1f2224` | 纯黑对比过硬，炭灰久看不累 |
| 代码块 | `#101213`，比窗口底色更深 | 用下沉的底色表示「这是机器的输出」 |
| 强调色 | 青绿 `#5fbfa8` | 终端语义，低饱和，不与恋恋抢视觉 |
| 来源色 | 用户琥珀 `#d9a441`，恋恋青绿 `#5fbfa8` | 暖色代表人、冷色代表恋恋，固定对应 |
| 危险色 | `#d9534f` | 与琥珀同属暖色但区分明显 |
| 边框 | 1px `#2f3437`，不用阴影 | 透明窗口下阴影需要自绘，暗底收益也低 |
| 圆角 | 窗口 8px，控件 6px | 比现有窗口收紧，减少柔和感 |
| 字体 | 等宽 `JetBrains Mono` → `Cascadia Mono` → `Consolas` → `Menlo` → `DejaVu Sans Mono` → `Noto Sans Mono`；界面字 `Segoe UI` → `PingFang SC` → `Microsoft YaHei UI` → `Noto Sans CJK SC` | 终端以等宽为主视觉，回退链覆盖三个平台 |
| 密度 | 行高 1.5，命令块间距 14px | 长时间阅读不疲劳 |
| 层次 | 底色差加 1px 线分区，不堆卡片 | 降低边框噪声 |

对比度：主文字 `#e8e5de` 在 `#17191a` 上约 12:1，次要文字 `#a8adaf` 约 6:1，元信息 `#8a9195` 约 4.6:1，均满足正文可读。

### §7.2 入口与布局

入口是右键主菜单的一项，与「日志」「对话历史」并列，显示条件为 `config.SHELL_TERMINAL_ENABLED`。它不依赖 `SHELL_ENABLED`：只需要本地命令行时，不必给模型开能力。

无边框窗口，默认 680×480：

| 区域 | 内容 |
|---|---|
| 状态栏 | 34px：左侧 6px 状态圆点 + 「终端」；右侧当前目录（可点击修改）、shell 选择、策略下拉、最小化、关闭 |
| 回显区 | 只读 `QTextEdit`，无边框，占满剩余高度 |
| 输入行 | 底部 40px，无边框，顶部 1px 分隔线 |

- 状态栏兼作拖动区。
- 策略下拉列出四个档位，切换时调用 `config.save("SHELL_MODE", ...)`，与设置页同一份数据。白名单与沙箱根只在设置页编辑，终端不提供编辑入口。
- shell 选择只影响用户输入，取值为平台可选集；模型调用的 `shell` 参数仍由 `SHELL_ALLOW_SHELL_MODE` 控制。选择结果记在窗口状态里，不写回配置。
- 输入行左侧用 `$` 作提示符，颜色跟随来源（用户琥珀、恋恋青绿），默认琥珀。
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
- cwd 非沙箱根时，在尾行前追加一行 `cwd: C:\work`。
- 块之间空 14px，不画分隔线。

### §7.3 配色

| 元素 | 色值 |
|---|---|
| 窗口底 | `#17191a` |
| 状态栏底 | `#1f2224` |
| 输入行底 | `#101213` |
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
- 启动时读取审计文件的最近 200 条记录渲染，重启后仍能看到历史。
- 命令历史 `deque(maxlen=100)`，上下方向键切换，只在内存中。

### §7.4 输入执行

- 用户提交的命令以 `source="user"` 进入执行核心，跳过确认、档位、白名单、危险模式与沙箱。
- 用户命令默认走 shell 模式：Windows 为 `cmd /d /s /c`，POSIX 为 `sh -c`。管道、重定向与变量展开因此可用，这是终端能替代系统终端窗口的前提。状态栏提供 shell 选择，Windows 可选 `cmd` / `pwsh`，POSIX 可选 `sh` / `bash`。
- 用户命令的超时取 `SHELL_MAX_COMMAND_TIMEOUT_S`（默认 120 秒），超时后同样终止进程树；比模型默认的 30 秒宽，避免安装依赖这类命令被过早打断。
- 执行放在独立线程，通过 `Signal(str)` 把分块文本交回主线程追加。
- 用户命令允许任意 cwd。首次打开时的工作目录是用户主目录（`Path.home()`），之后恢复上次使用的目录，记录在 `app_dir()/shell_state.json` 的 `last_cwd`；目录不存在时回退主目录，主目录不可用再回退进程当前目录。
- 状态栏显示当前目录，超过 40 字符时中间省略为 `C:\Users\...\project`，悬停显示完整路径；点击可改到其它目录并写回状态文件。
- 用户输入以 `cd ` 开头且只有一个目录参数时，窗口层直接切换状态栏目录，不启动子进程，回显一行「已切换到 …（仅影响后续命令）」；其它 `cd` 写法按普通命令交给 shell，状态栏不变。
- 模型的默认 cwd 是 `SHELL_SANDBOX_ROOT`，与用户终端的目录互不影响。
- 状态圆点四态：空闲 `#4a4f52`；运行中 `#5fbfa8`，1200ms 周期呼吸；等待闸门 `#d9a441`；执行出错短暂闪 `#d9534f` 一次后回到空闲。
- 状态栏右侧显示计时：空闲为空，运行中「运行中 3.2s」每 0.5 秒刷新，等待时「等待中」。
- 输入行右侧是状态文字与「执行」按钮（28px 高，透明底加 1px 边框），执行期间按钮与输入行置灰。
- 回显区为空时显示两行灰字：`输入命令后回车执行`、`↑ ↓ 翻历史 · Ctrl+L 清屏`。
- 自动滚动默认跟随底部；用户向上滚动后暂停跟随，滚回底部时恢复。判据是竖直滚动条的值等于最大值。
- 快捷键：`Enter` 提交，`Esc` 清空输入，`↑` / `↓` 取历史，`Ctrl+L` 清屏。
- 粘贴内容含换行时只取第一行并丢弃其余部分，状态文字提示「已忽略后续行」。多行脚本需要自行写成单行或用文件执行。
- 用户命令不写 LLM 上下文，只写审计文件与窗口。

### §7.5 审计记录

`app_dir()/shell_history.jsonl`，每行一条 JSON：

```json
{"ts": "2026-11-04T12:00:00", "source": "model", "argv": ["git", "status"],
 "cwd": "C:/work", "exit_code": 0, "duration_ms": 132, "truncated": false, "error": ""}
```

- 追加写在执行结束后进行，失败只记日志，不影响返回。
- 超过 `history_limit` 行时重写保留最近 3000 条。
- 「清空」按钮清空窗口并截断该文件，不弹二次确认。
- 审计是本地文件，不随日志上传；内容可能含密钥，见 §13。

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
| 命令区 | 底色 `#101213`，圆角 6，内边距 12；等宽 12px 显示完整命令行（Windows 用 `list2cmdline`、POSIX 用 `shlex.join` 还原），超过 4 行时内部滚动 |
| 元信息 | 11px `#8a9195`：来源（恋恋 / 用户）、cwd、超时秒数 |
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

`Tab` 触发补全，候选来源按当前 token 的位置决定：

| 位置 | 候选 |
|---|---|
| 首词且以 `/` 开头 | 命令名，按前缀过滤（`/st` → `/state`） |
| 命令的枚举参数 | 该命令声明的枚举值（`/dock ` → `left` / `right`） |
| `/tool ` 之后 | 已注册方法名（`weather__` → `weather__get_current` 等） |
| 其余 token | 当前目录的条目名，类型由首词决定 |
| 首词且不含分隔符 | 当前目录条目与 `PATH` 可执行名的并集 |

路径补全的解析规则：

| 已输入 | 基准 |
|---|---|
| `~` / `~/` | 用户主目录 |
| `..` / `../` | 状态栏目录的上一级 |
| `/`、`C:\` 等绝对路径 | 从磁盘根开始 |
| 其余 | 状态栏目录，不是子进程的 cwd |

- 参数类型按首词决定：`cd`、`chdir`、`pushd` 只列目录；`type`、`cat`、`more`、`less`、`notepad` 只列文件；其余命令列文件与目录。这张表是模块级常量，按需增补。
- 目录候选以当前平台的分隔符结尾（Windows `\`，POSIX `/`），补全后继续按 `Tab` 进入下一级。
- 含空格的候选自动加双引号；已输入引号时保留引号，只替换引号内部。
- 先按已输入前缀过滤，再截断到 500 条，避免大目录把目标挤出候选。
- 大小写按平台：Windows 不敏感，`cd doc` 能补出 `Documents\`；POSIX 敏感。

几个典型输入：

```
C:\Users\me> cd Doc␉           → cd Documents\
C:\Users\me> cd ..\␉           → 列出 C:\Users\
C:\Users\me\Documents> cd "Pro␉ → cd "Program Files"\
C:\Users\me> type no␉          → type notes.txt
```

交互规则：

- 唯一候选直接补全；命令与目录后补一个空格，文件不补。
- 多候选时先补到公共前缀，再显示候选行；继续按 `Tab` 在候选间循环，`Shift+Tab` 反向，`Esc` 退出循环但保留已输入内容。
- 候选行显示在输入行上方，单行，`#8a9195`，最多 12 个，超出显示「… 还有 N 个」；不写入回显区，不进入历史。
- 大小写按平台：Windows 不敏感，POSIX 敏感。
- 当前 token 已被引号包裹时保留引号，只替换内部内容；补全结果含空格时自动加双引号。
- 候选为空时不改变输入，状态栏提示「无匹配」一次。

不做的部分：管道与重定向之后的命令补全、环境变量值补全、`Ctrl+R` 历史搜索、`~user` 展开、glob 展开（`*.py`）、命令选项补全（`git --`）。前三项需要解析 shell 语法，后三项需要额外维护一份命令元数据，都超出「简单命令行环境」的范围。

实现约束：

- 补全逻辑放在 `pet/ui/completion.py`，纯函数 `complete(buffer, cursor, ctx) -> Completion`，`Completion(start, candidates, common_prefix)`，不依赖 Qt，可单独测试。
- 窗口只负责按键、候选行渲染与写回输入框。
- `PATH` 候选首次使用时扫描并缓存 60 秒；目录候选每次读取，先按前缀过滤再截断到 500 条。
- 不使用 `QCompleter`：它是下拉弹窗，与暗色终端风格不一致，也表达不了「公共前缀 + 循环选择」。

## §8 工具契约

```python
TOOL_NAME = "shell"
TOOL_DESCRIPTION = "命令行执行：运行系统命令并返回退出码与输出"
```

`shell` 是元工具，注册在 `ToolRegistry._register_default_tools()` 内，与 `food`、`game`、`recall` 同一层：

```python
def _register_shell_tools(self):
    from pet.shell.runner import ShellRunner
    runner = ShellRunner()
    self.register("shell", "命令行执行：运行系统命令并返回退出码与输出",
                  group="default", meta=True, side_effect=True)
    self.add_method(
        "shell", "run", "...", handler=runner.run,
        args={...}, timeout=runner.method_timeout,
        output=ToolOutputDefinition(schema=SHELL_OUTPUT),
    )
```

`_register_default_tools()` 只在 `config.SHELL_ENABLED` 为真时调用它。`output` 是注册必填项，写成 `ToolOutputDefinition(schema=SHELL_OUTPUT)`；省略会直接抛 `ValueError`。

方法 `run` 的参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `command` | str | 是 | - | 要执行的命令；`shell` 为空时按参数列表拆分 |
| `cwd` | str | 否 | `""` | 工作目录，空为沙箱根 |
| `timeout` | int | 否 | 30 | 命令超时秒数，handler 内夹紧到 `[1, max_command_timeout_s]` |
| `shell` | str | 否 | `""` | 交给系统 shell，枚举含空串：`["", "pwsh", "cmd"]` |

方法描述写明三点：命令只执行一次、需要用户确认、被拒绝时不要重试。`side_effect=True` 让该调用在事件日志里带 `side_effect` 标记，供回放与事件投影区分写操作；它与崩溃报告窗口无直接关系。

输出 schema：

```python
SHELL_OUTPUT = {
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

方法 `list` 零参数，返回当前策略快照，输出复用 `SHELL_OUTPUT`（`exit_code` 不填）：

| 字段 | 内容 |
|---|---|
| `platform` | `Windows` / `Linux` / `macOS` |
| `sandbox_root` | 模型命令允许的工作目录上限，取自 `SHELL_SANDBOX_ROOT` |
| `auto_allow` | 免确认的「程序 + 参数」，附 `installed` 标记，取自 `SHELL_AUTO_ALLOW` |
| `interpreters` | 一律转询问的解释器名单 |
| `deny_categories` | 危险模式覆盖的类别名，不含正则原文 |
| `shell_mode` | `SHELL_ALLOW_SHELL_MODE` 是否开启 |
| `mode` | 当前档位，取自 `SHELL_MODE` |

- 程序是否安装用 `shutil.which` 探测，结果缓存 60 秒，避免每次调用都扫 `PATH`。
- 危险模式只给类别（删除、磁盘、关机、账户、注册表、下载即执行），不返回正则原文，避免提供绕过线索。
- 白名单与档位变化后立即反映；配置未重启时以当前文件内容为准。
- `body` 每行一项，例如 `免确认：git status | git log | dir`。

模型不需要预先记住命令大全，只需要知道本机的白名单、沙箱根与解释器限制。`_SHELL_GUIDE` 只写机制与优先级（专用工具优先、需要确认、不要重试），不列具体命令，避免配置变化导致 system 前缀缓存失效。

终端入口不挂在工具子菜单上：`pet/ui/pet_window.py` 的菜单循环对 `meta` 工具直接 `continue`，`add_menu_action` 注册的子菜单不可达。改为在右键主菜单加一项「终端」，处理器 `_show_terminal_window()` 延迟导入 `pet.ui.terminal_window`，与 `_show_log_window` 的写法一致；不改动 `meta` 跳过分支。

## §9 与现有子系统的交互

| 项 | 行为 |
|---|---|
| 工具可见性 | `meta=True` 且 `default` 分组，模型第一轮就能在 schema 里看到 `shell__run`，不经 `tool_search`；`SHELL_ENABLED` 为假时工具不注册，模型完全看不到 |
| 提示词 | `prompts.py` 生成工具清单时跳过 `meta`，需要单独的 `_SHELL_GUIDE` 段，写明用途、确认机制与「有专用工具时优先用专用工具」；不列具体命令，避免配置变化冲掉 system 前缀缓存 |
| 策略可见性 | `shell__list` 返回平台、沙箱根、白名单与安装情况；`bad_command`、`untrusted_program`、`busy` 的 hint 都指向该方法，模型不确定时先查 |
| 并行调用 | 同轮多个 `shell__run` 并行进入；确认排队单卡展示，执行闸门串行，等待超时返回 `busy` |
| 工具循环 | 一轮内的多条命令会产生多次确认；工具描述要求一次只发一条，拿到结果后再发下一条 |
| 自主行动 | 允许调用，每次仍需确认；用户不在时超时拒绝，模型收到 `denied_by_user` 后不应重试 |
| 贴边与隐藏 | 确认卡片是独立窗口，不依赖恋恋可见；恋恋隐藏时仍能弹出确认与终端 |
| 中断调用 | 在途调用被中断时未决确认全部置为拒绝，队列清空 |
| 退出路径 | `pet/app.py` 的退出函数在 `os._exit` 前清空未决请求；不依赖 `atexit` |
| 事件日志 | `side_effect=True` 只写入 `tool_call` 事件 payload |
| 上下文 | 工具输出按 `x-role` 进入当轮视图；`headline` 进历史，`body` 只在当轮可见 |
| 文档生成 | `tools.md` 静态解析注册字面量，不判断 `SHELL_ENABLED`，`shell` 恒在清单中；生成物是代码结构的事实表，与运行时开关无关 |
| 模块级约束 | `ShellRunner` 与锁都在 `_register_shell_tools()` 内创建，不踩 ARCH009；若确有模块级单例需求，需在契约测试登记 |
| 工具上下文 | `pet/tools/context.py` 不 import `pet.agent`，只调用已绑定对象上的信号与 sink，维持零依赖 |

## §10 配置项

全部配置并入 `pet/config.py` 的 `_KEY_META`，与 `FOOD_ENABLED` 等元工具开关同一套机制：设置页自动渲染、`reference/config.md` 自动生成、`config.save()` 统一落盘。`shell` 是元工具，不使用工具私有配置。

### §10.1 设置页可见项

「行为」页新增一组「终端」控件：

| 键 | 类型 | 默认 | 控件 | 说明 |
|---|---|---|---|---|
| `SHELL_ENABLED` | bool | false | 复选框 | 注册 `shell` 元工具；关闭后模型看不到 `shell__run` |
| `SHELL_TERMINAL_ENABLED` | bool | true | 复选框 | 右键主菜单「终端」项；只影响窗口入口 |
| `SHELL_MODE` | str | `"ask"` | 下拉 | `deny` / `allowlist` / `ask` / `auto` |
| `SHELL_AUTO_ALLOW` | str_list | `[]` | 多行文本 | 白名单，空为平台默认；条目格式见 §5.2 |
| `SHELL_SANDBOX_ROOT` | str | `""` | 文本框 | 模型命令的工作目录根，留空为桌面；用户终端的初始目录是主目录，不受此项影响 |

- 两个开关 `needs_restart: True`；档位、白名单、沙箱根即时生效。
- 白名单复用现有多行文本控件，占位提示写明「逗号分隔，如 git status, git log, dir *」。
- 说明文字提示解释器风险：写进白名单的 `python`、`node` 等价于允许任意代码执行。
- 拆成两个开关的原因是：只需要本地终端时，不必给模型开命令能力；`SHELL_MODE="deny"` 无法替代，它仍占 schema 与提示词位置。

### §10.2 高级项

`hidden: True`，只在 `settings.json` 中可改：

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `SHELL_DENY_PATTERNS` | str_list | `[]` | 危险模式正则，空为内置六类 |
| `SHELL_ALLOW_SHELL_MODE` | bool | false | 是否允许 `shell="pwsh"` / `"cmd"` / `"sh"` |
| `SHELL_ENV_ALLOW` | str_list | `[]` | 额外透传的环境变量名 |
| `SHELL_COMMAND_TIMEOUT_S` | int | 30 | 单条命令的默认超时 |
| `SHELL_MAX_COMMAND_TIMEOUT_S` | int | 120 | 模型可传的超时上限 |
| `SHELL_CONFIRM_TIMEOUT_S` | int | 60 | 确认卡片倒计时 |
| `SHELL_QUEUE_WAIT_S` | int | 10 | 执行闸门等待上限 |
| `SHELL_MAX_CAPTURE_BYTES` | int | 131072 | 每流捕获的字节上限 |
| `SHELL_MAX_OUTPUT_CHARS` | int | 4000 | 进入模型的输出上限（两流合计） |
| `SHELL_HISTORY_LIMIT` | int | 5000 | 审计文件行数上限 |

- `SHELL_DENY_PATTERNS` 留空表示用内置清单，非空整体替换；正则原文不进入 `shell__list` 快照。
- 四项超时参与方法注册，`needs_restart: True`；捕获上限、输出上限与审计行数即时生效。

### §10.3 超时推导

```
method_timeout = SHELL_CONFIRM_TIMEOUT_S + SHELL_MAX_COMMAND_TIMEOUT_S + SHELL_QUEUE_WAIT_S + 5
```

默认 195 秒，在 `register()` 时计算并写入方法注册。三者关系固定：确认与排队都发生在方法超时之前，命令执行的超时上限不超过 `SHELL_MAX_COMMAND_TIMEOUT_S`，因此方法超时不会被正常的确认加执行触发。

`policy` 每次调用都从 `config` 实例读取，`config.save()` 更新属性后立即生效，不存在配置缓存过期的问题。

## §11 文件改动

| 文件 | 职责 |
|---|---|
| `pet/shell/__init__.py` | 包说明与 `TOOL_NAME` / `TOOL_DESCRIPTION` 常量 |
| `pet/shell/core.py` | 引号配对检查、`parse_command`、内建命令路由、读线程与进程树终止、解码与截断 |
| `pet/shell/policy.py` | `check(source, argv, command, cwd)`；档位、白名单参数模式、危险模式、程序解析与目录约束、环境变量 |
| `pet/shell/runner.py` | `ShellRunner`：闸门、确认调用、审计写入、`method_timeout`、`list` 快照 |
| `pet/shell/audit.py` | 追加、读取、裁剪审计记录 |
| `pet/tools/registry.py` | `_register_shell_tools()`；`_register_default_tools()` 按 `SHELL_ENABLED` 调用 |
| `pet/config.py` | 十五个 `SHELL_*` 键，见 §10 |
| `pet/ui/settings_window.py` | 行为页「终端」组：两个复选框、档位下拉、白名单多行文本、沙箱根文本框 |
| `pet/brain/prompts.py` | `_SHELL_GUIDE` 段与注入条件 |
| `pet/ui/shell_theme.py` | 终端的色板、字体回退链、圆角与间距常量；不依赖 `styles.py` |
| `pet/ui/completion.py` | 补全纯逻辑：命令名、枚举参数、方法名、目录条目、`PATH` 合并、公共前缀与引号处理 |
| `pet/ui/terminal_window.py` | 终端窗口、来源配色、缓冲、输入执行、命令历史、审计回填、自动滚动与状态栏、工作目录状态与 `cd` 拦截、补全交互 |
| `pet/ui/confirm_card.py` | 确认卡片：队列展示、倒计时进度条、危险与可写目录提示、按钮与快捷键；`card_position()` 纯函数 |
| `pet/tools/context.py` | `set_confirm_sink()`、`confirm(request)`，保持零依赖 |
| `pet/agent/pet_agent.py` | `confirm_requested` 信号、未决请求登记、中断时统一拒绝 |
| `pet/app.py` | 退出函数在 `os._exit` 前清空未决确认并注销 sink |
| `pet/ui/pet_window.py` | 右键主菜单「终端」项、`_show_terminal_window`、连接确认信号并显示卡片 |
| `pet/ui/__init__.py` | 模块说明补充 `terminal_window` 与 `confirm_card` |
| `tests/test_shell_core.py` | 引号配对、内建路由、大输出不死锁、终止进程树、截断口径、解码 |
| `tests/test_shell_policy.py` | 档位、白名单参数模式、解释器、危险模式、cwd 沙箱、程序劫持、环境变量 |
| `tests/test_shell_runner.py` | 注册条件、超时推导、闸门 `busy`、确认拒绝、`list` 快照内容 |
| `tests/test_shell_confirm.py` | sink 未注册立即拒绝、超时拒绝、中断拒绝、异常按拒绝处理 |
| `tests/test_terminal_window.py` | 来源配色、缓冲上限、命令历史、多行粘贴、自动滚动、输入跳过确认、审计回填 |
| `tests/test_confirm_card.py` | 卡片位置分支、标题序号、快捷键与危险态 |
| `tests/test_completion.py` | 各位置的候选来源、公共前缀、引号与空格处理、大小写策略、条目上限 |
| `tests/test_architecture_contracts.py` | 确认无新增 ARCH009 与 ARCH001 债务；`ShellRunner` 在注册函数内创建 |
| `docs/architecture.md` | §9 模块职责表补 `pet/shell/` 与新增窗口 |
| `docs/reference/tools.md` | `python scripts/gen_docs.py` 重新生成 |
| `docs/reference/config.md` | 同上，两个全局开关进表 |
| `docs/reference/prompt-blocks.md` | 同上，`_SHELL_GUIDE` 进块清单 |
| `docs/reference/modules.md` | 同上，新增模块的一句话职责 |

`tests/test_docs.py` 要求 `pet/shell/` 出现在 `architecture.md` 中，新增包时同步补表。生成器静态解析 `add_method` 字面量，`tools.md` 会无条件列出 `shell` 与 `run`，与 `SHELL_ENABLED` 的取值无关。

## §12 验证方式

自动化：

```bash
python -m pytest tests/test_shell_core.py tests/test_shell_policy.py tests/test_shell_runner.py \
                tests/test_shell_confirm.py tests/test_terminal_window.py \
                tests/test_architecture_contracts.py tests/test_docs.py
python scripts/gen_docs.py --check
```

`tests/test_shell_core.py` 覆盖：

1. 引号配对：未闭合的单引号与双引号返回 `bad_command`；转义引号不算配对；
2. 解析：普通命令、带空格路径、Windows 反斜杠、空串；
3. 内建路由：Windows 下 `dir /a` 改写为 `cmd /d /s /c "dir /a"`，`cd` 与 `del` 不路由；
4. 大输出：命令输出 1MB 时 5 秒内返回，`truncated` 为真，进程无残留；
5. 超时：长命令超时后进程树不存在（按 PID 探测）；`taskkill` 失败路径返回 `kill_failed`；
6. 截断：两流各头 1500 + 尾 500，合计 4000，省略量标注正确；
7. 解码：UTF-8、GBK 字节、非法字节序列三条路径；
8. 平台分支：POSIX 下 `ls` 不被内建路由、argv 展示用 `shlex.join`；`killpg` 抛 `ProcessLookupError` 时按终止成功处理。

`tests/test_shell_policy.py` 覆盖：

1. 档位：`deny` 返回 `shell_disabled`；`allowlist` 对白名单内放行、对外返回 `not_allowlisted`；`ask` 对白名单内外都询问；`auto` 对白名单内放行；
2. 参数模式：`git status` 免确认、`git push` 询问、`git status --short` 免确认；`args: []` 时任何参数都询问；简写字符串与对象形式归一化后等价；用户配置整体替换默认，空数组时默认项不生效；
3. 解释器：`python -V`、`python -c "print(1)"`、`node -e`、`pwsh -Command` 在 `auto` 档一律转询问；`allowlist` 档按白名单判定，未列出返回 `not_allowlisted`；
4. 危险模式：默认清单逐条命中，非法正则被跳过，`auto` 档命中转询问，`allowlist` 档命中返回 `denied_by_pattern`；
5. cwd：相对路径落在沙箱内、`..` 逃逸被拒、绝对路径越界被拒、用户来源不受限；
6. 劫持：沙箱根放置 `git.bat`，`auto` 档返回 `untrusted_program`，解析到系统 `git.exe` 时放行；
7. 环境变量：`LLM_KEY` 与 `*_TOKEN` 不出现，`SHELL_ENV_ALLOW` 中的普通键保留；
8. 超时夹紧：模型传 999 被夹到 `SHELL_MAX_COMMAND_TIMEOUT_S`；
9. 路径大小写：比较函数接受平台参数，Windows 与 macOS 策略下同一路径的不同大小写不判越界，Linux 策略下区分；`..` 逃逸在各平台都被拒。

`tests/test_shell_runner.py` 覆盖：

1. 注册条件：`SHELL_ENABLED` 为假时工具不存在；为真时 `meta`、`side_effect`、`group` 三项正确；
2. 超时推导：`method_timeout` 等于四项配置之和加 5；
3. 闸门：占用期间第二次调用在 `queue_wait_s` 内返回 `busy`；
4. 确认：确认函数返回 `False` 时返回 `denied_by_user` 且执行核心未被调用；未注入确认函数时立即拒绝；
5. 结果：非零退出不带 `error` 键；
6. 审计：每次执行写一条，字段完整；
7. `list` 快照：平台、沙箱根、白名单、解释器、危险类别齐全；`installed` 标记与 `shutil.which` 的桩一致；返回内容不含正则原文。

`tests/test_shell_confirm.py` 覆盖 sink 未注册立即拒绝（耗时小于 50ms）、sink 正常回传、超时拒绝、`reject_pending_confirms` 生效、sink 抛异常按拒绝处理，以及 `pet/tools/context.py` 的模块级没有 `pet.agent` 导入。

`tests/test_terminal_window.py` 覆盖来源前缀与配色分组、5000 块上限、历史去重与上下键取值、多行粘贴截断、自动滚动跟随、用户输入不经确认且以 shell 模式提交、首次打开用主目录、恢复 `last_cwd`、目录失效回退、`cd` 拦截、启动时从审计文件回填。用例用桩替换执行核心，不真的调用命令。

`tests/test_confirm_card.py` 覆盖 `card_position()` 的四种分支（上方足够、上方不足转下方、恋恋在屏幕外落右下角、结果夹进可用区），以及标题序号、`Esc` 拒绝、危险模式下 `Enter` 不触发允许、关闭等同拒绝。

`tests/test_completion.py` 覆盖：首词 `/` 的命令名、命令枚举参数、`/tool` 后的方法名、目录条目、`PATH` 与目录合并、公共前缀计算、唯一候选直接补全、引号保留与含空格时自动加引号、Windows 与 POSIX 的大小写差异、`~` 与 `..` 的基准解析、绝对路径、`cd` 只出目录、`type` 只出文件、大目录先过滤后截断到 500 条。

手动检查清单：

1. 右键恋恋 → 终端，窗口打开；再次点击只前置窗口；`SHELL_TERMINAL_ENABLED` 为假时该项不出现；
2. 模型要求执行 `dir` 时弹出确认卡片，显示改写后的 `cmd /c` 与 cwd，倒计时归零后自动拒绝；
3. 允许后命令执行，输出进入终端窗口与模型下一轮上下文；
4. 拒绝后模型收到 `denied_by_user`，本轮不再重复提交同一命令；
5. 在终端输入 `dir` 直接执行，无确认卡片，来源前缀为 `[用户] $`；
6. 同轮两条命令并行到达时，确认卡片依次显示「命令 1/2」「命令 2/2」，第二条在第一条结束后才执行；
7. `SHELL_MODE="auto"` 时 `git status` 免确认，`git push` 与 `python -c "print(1)"` 仍询问；
8. 命令超时后任务管理器中无残留子进程；
9. 恋恋隐藏或贴边时确认卡片仍能弹出；
10. 应用退出时有未决确认，进程立即结束，不卡住；
11. 模型命令的 `cwd` 传 `..\\..` 时返回 `cwd_denied`；
12. 关闭 `SHELL_ENABLED` 重启后，模型工具列表里没有 `shell__run`，终端窗口仍可用；
13. 重启后终端窗口显示上次的最近命令记录；
14. 在桌面放一个 `git.bat`，`auto` 档执行 `git status` 返回 `untrusted_program`；
15. 询问模型能用什么命令时，它先调用 `shell__list`，回复里给出本机白名单与沙箱根；未安装的程序标注为未安装；
16. 确认卡片在恋恋上方弹出；恋恋靠在屏幕顶部时改到下方；恋恋隐藏时落在鼠标所在屏的右下角；
17. 命令命中危险模式时分类条转红、`Enter` 不生效，点击「仍要执行」后才执行；
18. 终端里贴上多行文本，只执行首行，状态栏提示已忽略后续行；
19. 在 macOS 或 Linux 上执行 `ls` 与 `echo`，不经过 `cmd` 路由、输出正常；Wayland 下确认卡片仍能弹出，位置可能由合成器决定；
20. 设置页「行为」把档位改为 `allowlist`、白名单填 `git status`，模型执行 `git status` 通过、执行 `git push` 返回 `not_allowlisted`，保存后立即生效；
21. 设置页修改白名单与沙箱根后不重启即生效；切换 `SHELL_ENABLED` 与 `SHELL_TERMINAL_ENABLED` 后需重启；
22. 在终端输入 `dir | findstr py` 与 `echo %PATH%`，管道与变量展开生效；白名单为空时 `git status` 仍可执行。

## §13 风险与约束

- **确认疲劳**：同轮多条命令会连续弹卡片，用户容易形成机械点击。缓解手段是工具描述要求一次只发一条、卡片标红高危与可写目录来源、`auto` 档承担高频只读命令；代码层不做「本次会话内全部允许」。
- **shell 逃逸**：`python`、`node`、`pwsh` 都能执行任意代码，白名单与危险模式只对字面命令生效。安全边界实际落在用户确认上，`ask` 档的卡片必须显示完整 argv 而不是摘要。
- **内建命令路由**：改写为 `cmd /c` 后由 cmd 重新解析参数，`list2cmdline` 的引用规则与 cmd 的解析规则存在少量差异，个别含特殊字符的参数可能被改写。内建集合固定为只读命令，影响面可控。
- **审计文件隐私**：命令可能包含路径、令牌、数据库连接串。文件只落本地，不进日志上传，也不做脱敏；「清空」会截断文件。
- **并行确认**：确认队列按到达顺序展示，与模型声明顺序可能不同。卡片标注序号与完整 argv，减少错配。
- **被拒后重试**：模型可能反复提交同一条命令，`denied_by_user` 的 `hint` 与工具描述都写明不要重试，但无法从代码层阻止。
- **executor 超时**：方法超时由配置推导，正常路径不会触发；一旦触发，框架返回固定文案「稍后重试」，与本设计的「不要重试」相反。该文案不可定制，属于已知限制。
- **闸门占用**：用户在终端运行长命令时，模型调用在 `queue_wait_s` 内等待后返回 `busy`，模型可能反复尝试；工具描述要求遇到 `busy` 时告知用户而不是重试。
- **编码差异**：中文 Windows 下部分程序输出 GBK，部分输出 UTF-8，三级解码仍有乱码可能，`errors="replace"` 保证不崩溃。
- **挂起程序**：拦截交互式程序只靠 `stdin=DEVNULL` 与超时，某些程序在前台等待其它事件时仍会占满超时时间。
- **线程语义**：executor 超时后不终止 handler 线程；本设计的确认与执行都自带超时并主动终止子进程，仍存在「用户在最后一刻点击允许」与工具超时重叠的窗口。
- **生成物与开关**：`tools.md`、`prompt-blocks.md` 无条件列出 `shell` 与 `_SHELL_GUIDE`，默认关闭时生成物仍有条目。生成物描述代码结构，不描述运行时开关。
- **平台**：Windows 是主路径，内建路由、`taskkill` 与可执行劫持拦截都在该平台落地；macOS 与 Linux 共用 POSIX 分支，`killpg`、`shlex.join`、Homebrew 目录可信判定与字体回退需要实机验证。Wayland 下卡片定位由合成器决定，可能偏离恋恋。
- **上下文成本**：每次命令输出最多 4000 字符，多轮工具调用会累积；`headline` 进历史、`body` 只进当轮，避免长期占用。

## §14 结论

命令行环境由四块组成：执行核心负责解析、内建路由、读取输出与终止进程树；策略层负责档位、参数模式白名单、危险模式、程序解析与沙箱；界面层负责确认卡片与终端窗口；审计层记录每次执行。模型入口是 `shell__run`，用户入口是右键主菜单的终端窗口，两者共用 `ShellRunner`，区别只在 `source` 决定的策略分支与闸门排队。安全边界放在用户确认上：`ask` 档全部询问、`auto` 档只放行「程序 + 参数模式」、`deny` 档全部拒绝，任何异常路径都归为拒绝。模型能力与终端入口由两个开关分开，只使用本地终端时不需要给模型开命令权限。模型不预置命令大全：本机能做什么由 `shell__list` 提供快照，提示词只约束行为，不承载易变的清单。
