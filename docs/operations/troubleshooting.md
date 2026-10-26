# 排障手册

排障按「看日志 → 对照现象 → 跑诊断命令 → 上报」的顺序推进。
系统组成与各文件位置见 [architecture.md](../architecture.md)。

## 1. 日志与状态的位置

| 位置 | 内容 | 备注 |
|---|---|---|
| `logs/koishiai.log` | 运行日志 | 按天轮转，保留 3 份 |
| `logs/crash/crash_<时间>_<类型>.json` / `.txt` | 崩溃报告（含线程栈、脱敏配置、磁盘信息） | 只保留最近 10 份 |
| `logs/crash/faulthandler.log` | 原生崩溃（段错误等）的线程栈 | 由 faulthandler 写入 |
| `logs/startup.state` | 启动标记（pid / 状态 / 时间） | 用来判断上次是否异常退出 |
| 调试面板 | 动作与粒子测试、数值直接改写、日志窗口 | 从托盘/右键菜单打开 |
| `pet.db` | 数值、记忆、上下文、对话历史 | 用 `sqlite3` 查，见下 |

按时间点对照日志定位最快：崩溃报告里的时间戳、`logs/startup.state` 的 `started_at`、以及日志自身的行首时间。

## 2. 现象对照表

| 现象 | 可能原因 | 怎么确认 / 怎么办 |
|---|---|---|
| 桌宠不动、动作像被跳过 | 素材缺失、`<动作名>.json` 缺失或损坏、动作没在 registry 注册 | 日志里搜 `无可用帧`、`play` 相关 warning；缺素材的目录不会出现在调试面板的动作列表里 |
| 改了素材/参数没生效 | 帧动画配置有缓存；部分配置项 `needs_restart` | 重启应用；设置界面会提示哪些项需重启 |
| 动作播完迟迟不结束 | 属性动画路径**没有超时保护**，只靠 `finished` 信号 | 看队列日志；必要时重启。带 `duration` 与帧动画路径有超时兜底（`ACTION_TIMEOUT_MS`，默认 90s） |
| 长时间不说话、没有反应 | LLM 请求报错或超时后走了降级 | 日志里找重试与「切换备选模型」；检查 `LLM_URL` / `LLM_KEY` / `LLM_MODEL` 与网络；本地兜底决策会给出简单动作 |
| 托盘提示「LLM调用疑似挂死，已强制恢复」 | 脑线程超过 `BRAIN_STUCK_TIMEOUT`（默认 300s）无进展，看门狗介入 | 属自动恢复，不必重启；若频繁出现，看日志里卡在哪一轮（工具调用/流式响应） |
| 数值一直不变 | 动作不在消耗表里（`shy` / `confused` / `dejected` 等驻留动作不消耗），或模型没输出 `Mood:` / `Vitals:` 行 | 调试面板直接看数值并手动改；动作消耗表见 [vitals-and-mood.md](../subsystems/vitals-and-mood.md) §2 |
| 行为突然变得古怪（乱说话、夸张举动） | 理智低：< `SANITY_CRITICAL_THRESHOLD`（默认 20）会切 `grim` 动画并持续喷黑心，提示词也进入低理智档 | 调试面板把 `sanity` 调回高位；或调低该阈值 |
| 记忆不召回 / 召回得莫名其妙 | 没配 embedding 就只走关键词；召回有 300 秒冷却；管理窗口编辑会刷新「最近访问」从而抬高有效分 | `settings.json` 里配 `EMBEDDING_*`；用下面的 sqlite 命令看实际数据 |
| 反复记同样的东西 | 冷却期内的重复记忆会被拦截并提示模型别重复输出 | 属预期；改 `MEMORY_RECALL_COOLDOWN_S` 可调 |
| 启动就退出 | 重复启动被单实例锁挡住 | 提示框会说明；确认无残留进程后删除 `KoishiAI.lock`（与 `settings.json` 同目录） |
| 更新后起不来 | 依赖没更新成功、或 `update.*.new` 待应用 | 手动 `pip install -e .`；重启一次让 `pet/self_update.py` 替换脚本；日志里有 `[SelfUpdate]` 记录 |
| 窗口探测不准 | 平台后端差异（Win32 / Quartz / X11）或窗口被遮挡 | 日志里有探测结果；非 Windows 平台的实现较少打磨 |
| 测试或 CI 失败 | 见 [CONTRIBUTING.md](../../CONTRIBUTING.md) 的常见问题表 | 文档漂移时运行 `python scripts/gen_docs.py` |

## 3. 常用诊断命令

```bash
python -m pytest -q                     # 全量自测
python scripts/gen_docs.py --check      # 文档是否与代码一致
sqlite3 pet.db "SELECT satiety, energy FROM vitals; SELECT affection, joy, sanity FROM mood;"
sqlite3 pet.db "SELECT level, COUNT(*) FROM memories GROUP BY level;"
sqlite3 pet.db "SELECT datetime(created_at), substr(content,1,50) FROM memories ORDER BY created_at DESC LIMIT 10;"
sqlite3 pet.db "SELECT type, COUNT(*) FROM session_events GROUP BY type;"   # 会话事件分布
```

`pet.db` 在项目根目录；记忆的向量表 `memories_vec` 是 sqlite-vec 虚拟表，普通 CLI 查不了。

## 4. 上报所需信息

1. 版本号（`pyproject.toml` 的 `version`，或托盘/关于里的显示）；
2. 现象与复现步骤，以及「预期 vs 实际」；
3. `logs/koishiai.log` 里对应时间段的片段；
4. 若是崩溃：`logs/crash/` 下最新的 `.json` 报告（内含线程栈与脱敏后的配置）；
5. 相关配置项（**不含 API Key**）。

崩溃报告已经做过脱敏（键名含 key/secret/token 的字段会被打码），发出前仍建议再扫一遍。
