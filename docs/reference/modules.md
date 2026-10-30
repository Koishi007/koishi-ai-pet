<!-- 由 scripts/gen_docs.py 生成，请勿手工编辑 -->

# 模块清单

`pet/` 下每个 Python 文件的一行职责与主要定义（取自模块 docstring 与顶层定义）。
包级别的心智模型见 [架构总览](../architecture.md)。

| 文件 | 职责 | 主要定义 |
|---|---|---|
| `pet/__init__.py` | Koishi AI Pet 包分层说明。 |  |
| `pet/__main__.py` | python -m pet 入口 |  |
| `pet/action/__init__.py` | 动作系统 — 动作定义与注册(action)、帧动画播放(action_queue)、重力特效(gravity)、注册表(registry)。 |  |
| `pet/action/action.py` | 复合行为（移动、弹跳等），通过 PetAnimator 播放帧动画。 | `coerce_int()` `PetActions` |
| `pet/action/action_queue.py` | 行为队列控制器。 | `ActionQueue` |
| `pet/action/calling.py` | 来电通知：calling 动作开始时给用户弹一条系统通知 | `notify_call()` |
| `pet/action/fishing.py` | 钓鱼判定：桌宠执行 fishing 动作时掷一次骰子，决定这轮是否有收获。 | `roll_catch()` `format_result()` `_FishingOutcome` |
| `pet/action/gravity.py` | 模拟桌宠受重力下落，可站立在其他窗口上。 | `GravitySystem` |
| `pet/action/outcome.py` | 动作产出（outcome） | `Outcome` `register()` `outcome_for()` `registered_actions()` |
| `pet/action/registry.py` | 可被 LLM 调用的动作定义。 | `edge_asset()` `target_sequence_duration()` `min_action_count()` `default_duration()` `has_duration()` `duration_range()` |
| `pet/agent/__init__.py` | Agent 调度层 — PetAgent 编排全流程，Scheduler 三速定时调度，StateMachine 状态机，ScreenReader 截图采集。 |  |
| `pet/agent/pet_agent.py` | PetAgent — 编排 Brain，通过 Signal 驱动 UI。 | `BrainWorker` `PetAgent` |
| `pet/agent/scheduled_tasks.py` | 定时任务注册与回调 | `ScheduledTasks` |
| `pet/agent/scheduler.py` | 多频率 Tick 调度器 | `Scheduler` |
| `pet/agent/screen_reader.py` | 屏幕截图 | `ScreenReader` |
| `pet/agent/state.py` | 轻量状态机 | `PetState` `StateTransition` `StateMachine` |
| `pet/app.py` | KoishiAI 桌面宠物 — 主入口 | `_FileActionDispatcher` `main()` |
| `pet/auto_start.py` | 开机自启管理 — 跨平台支持 Windows / macOS / Linux。 | `set_auto_start()` |
| `pet/brain/__init__.py` | Brain 层 — Behavior 自主/对话决策，LLMClient LLM封装，prompts 模板， |  |
| `pet/brain/base.py` | 一条上下文记录；`seq` 指向事件日志，仅调试快照使用。 | `ContextEntry` `BrainMixin` |
| `pet/brain/behavior.py` | 与 AI 通信的一次决策编排：三条管线（自主/对话/交互）的入口、锁与降级。 | `Behavior` `_BehaviorToolSession` |
| `pet/brain/budget.py` | 单次请求的输出预算：统一硬上限 + 动态配额（基线 / 思考预留 / 工具预留） | `thinking_active()` `output_budget()` |
| `pet/brain/context_builder.py` | LLM 请求上下文的构建 | `ContextBuilder` |
| `pet/brain/conversation_store.py` | 对话历史持久化存储 — 记录所有 speech 输出和用户 chat 输入，按天切分，至多保存 7 天。 | `ConversationStore` |
| `pet/brain/embedding_client.py` | OpenAI 兼容的嵌入向量客户端。 | `EmbeddingError` `EmbeddingClient` |
| `pet/brain/events.py` | 会话事件日志：只追加的事件存储，`Message[]` 由 `projection.project` 从这里投影。 | `Event` `truncate_bytes()` `new_group_id()` `args_digest()` `EventStore` |
| `pet/brain/linux_detector.py` | Linux X11 窗口枚举 —— 基于 python-xlib + EWMH，与 Win32/macOS 版保持相同接口。 | `is_window_alive()` `get_window_rect()` `is_window_occluded()` `get_visible_windows()` |
| `pet/brain/llm_client.py` | OpenAI-compatible LLM client（支持首选/备选两套模型方案） | `normalize_profile()` `active_llm_profile()` `other_profile()` `resolve_llm_profile()` `LLMClient` |
| `pet/brain/llm_gateway.py` | LLM 调用封装：补全请求、流式建流与重试参数，供决策编排直接调用。 | `LlmGateway` |
| `pet/brain/llm_retry.py` | LLM 调用重试与异常分类。 | `is_retryable()` `retryer_for()` `llm_retry()` `CreateStreamTimeout` `llm_stream_with_retry()` |
| `pet/brain/llm_stats.py` | LLM 调用计数器 | `LlmStats` |
| `pet/brain/local_fallback.py` | 本地兜底决策：LLM 不可用或抢锁失败时的降级产出。 | `decide_local()` `interact_decide_local()` `chat_decide_local()` |
| `pet/brain/mac_detector.py` | macOS 窗口枚举 —— 基于 Quartz CGWindow API，与 Win32 版保持相同接口。 | `is_window_alive()` `get_window_rect()` `is_window_occluded()` `get_visible_windows()` |
| `pet/brain/memory.py` | SQLite 持久化记忆存储 | `LightweightDeduplicator` `_MemoryRetriever` `KeywordRetriever` `VectorRetriever` `MemoryStore` `get_memory_store()` |
| `pet/brain/memory_merge.py` | 事件记忆汇总：整点把同主题的 event 与 conversation 记忆合并成一条 event。 | `parse_groups()` `run_merge()` |
| `pet/brain/output.py` | LLM 决策的输出契约：行为输出、字段白名单与取消信号，供解析、存储与编排共同引用。 | `normalize_emotion()` `CancelledError` `ActionStep` `BehaviorOutput` `sanitize_assistant_fields()` |
| `pet/brain/parsing.py` | LLM 输出解析：把文本行 / 流式 chunk 收敛成 BehaviorOutput。 | `BehaviorSink` `parse_deltas()` `parse_mood_line()` `parse_vitals_line()` `parse_action_line()` `InlineTagNormalizer` |
| `pet/brain/projection.py` | 事件日志到 `Message[]` 的投影：纯函数，只依赖事件、Profile 与 Budget。 | `Profile` `Budget` `_Group` `_Item` `project()` `degrade_messages()` |
| `pet/brain/prompts.py` | 系统提示词分层组装 | `_Lazy` `invalidate_action_section()` `action_range()` `history_note()` `build_attention_hint()` `build_system_prompt()` |
| `pet/brain/repair.py` | 格式修复重试：缺少必填字段时改写 messages 再跑一次。 | `action_bounds()` `action_requirement()` `missing_required()` `retry_if_incomplete()` `repair_messages()` |
| `pet/brain/summary.py` | 摘要流水线的执行端：把上下文淘汰产生的待摘要条目压成一句写回上下文。 | `SummaryHooks` `flush_summaries()` `summarize_with_llm()` |
| `pet/brain/tool_channel.py` | 工具通道选择：默认 native，端点明确拒绝时逐档降级并缓存结果。 | `is_tool_rejection()` `next_channel()` `channel_for()` `mark_downgraded()` `reset_cache()` `current_profile()` |
| `pet/brain/tool_loop.py` | 工具轮次：执行 LLM 请求的工具并循环，直到模型不再请求工具。 | `ToolSession` `run_tool_loop()` |
| `pet/brain/win_detector.py` | Win32 窗口枚举 | `is_window_alive()` `get_window_rect()` `is_window_occluded()` `get_visible_windows()` |
| `pet/brain/window_detector.py` | 窗口枚举 — 根据平台分发到 Win32 / Quartz / X11 后端 |  |
| `pet/config.py` | 配置系统：`_KEY_META` 是所有配置项的唯一真源，`settings.json` 存用户覆盖（路径见 `pet/settings.py`）。 | `Config` |
| `pet/crash_reporter.py` | 崩溃信息收集与持久化 | `CrashReporter` `get_guard()` `install()` `mark_started()` `clear_marker()` |
| `pet/db.py` | 数据库路径管理 — 集中管理 pet.db 路径与统一连接配置。 | `get_db_path()` `get_conn()` |
| `pet/file_intake/__init__.py` | 拖入文件的纯逻辑：类型嗅探、解码链、截断与额度、图片闸门、拒绝名单、目录摘要。 |  |
| `pet/file_intake/image.py` | 图片读取：先 draft 后判像素闸门，通过后再解码与缩放。 | `load_image()` `load_image_with_reason()` |
| `pet/file_intake/sniff.py` | 类型嗅探、解码链、拒绝名单与目录摘要。 | `read_head()` `decode_bytes()` `sniff()` `match_deny()` `dir_summary()` `make_ref()` |
| `pet/file_intake/text.py` | 文本读取与截断。 | `truncate()` `load_text()` `load_text_full()` |
| `pet/file_intake/types.py` | 拖入文件的数据结构。 | `FileRef` `DropVerdict` |
| `pet/food/__init__.py` | food 层 — 需求驱动的本能行为（觅食）。 |  |
| `pet/food/food.py` | 觅食本能 — 需求驱动的自主觅食行为（satiety 低时触发）。 | `pick_emoji()` `name_of()` `FoodManager` |
| `pet/game/__init__.py` | 游戏层 — 五个回合制小游戏的导出与整包注册。 | `register_all()` |
| `pet/game/gamebase.py` | 游戏对局基类与全局对局容器 GAME。 | `Game` `GameBase` |
| `pet/game/guess_number.py` | 猜数字游戏 — 1-100 随机目标，7 次内猜中算赢。 | `GuessNumberGame` |
| `pet/game/higher_lower.py` | 猜高低对赌游戏 — 0-9 十张牌，双方各押「高/低」，按赔率计分，先得 6 分者获胜。 | `side_counts()` `odds()` `judge()` `HigherLowerGame` |
| `pet/game/rps.py` | 猜拳游戏 — 石头剪刀布，三局两胜。 | `RockPaperScissorsGame` |
| `pet/game/tic_tac_toe.py` | 井字棋游戏 — 3x3 棋盘，开局随机决定先后手（X 先手）。 | `TicTacToeGame` |
| `pet/game/twenty_questions.py` | 二十问游戏 — 用户心想一个东西，桌宠通过最多 20 个是/否问题猜出它。 | `TwentyQuestionsGame` |
| `pet/pulse/__init__.py` | Pulse 层 — 生理与心理状态引擎：vitals(饱食度/精力)、mood(好感/愉悦/理智)，含数值衰减与 SQLite 持久化。 |  |
| `pet/pulse/mood.py` | 情绪 — 心理数值引擎：好感度、愉悦度、理智值 | `MoodThresholds` `MoodDecayConfig` `Mood` |
| `pet/pulse/vitals.py` | 体征 — 生理数值引擎：饱食度、精力 | `Thresholds` `Vitals` |
| `pet/relaunch.py` | 进程重启：按当前运行方式拉起一个新实例。 | `relaunch_command()` `spawn_relaunch()` |
| `pet/self_update.py` | 启动时应用遗留的 update 脚本更新 | `apply_pending_update_scripts()` |
| `pet/settings.py` | 用户设置持久化 — JSON 文件读写。 | `app_dir()` `settings_path()` `load_user_settings()` `save_user_setting()` `delete_user_settings()` |
| `pet/single_instance.py` | 单实例限制 — 防止桌宠被重复启动导致状态/数据库冲突。 | `SingleInstanceGuard` |
| `pet/tools/__init__.py` | 工具加载器 — 自动发现 + 自动安装依赖 + 配置选择性加载。 | `load_tools()` |
| `pet/tools/bilibili/__init__.py` | bili 工具 — 刷 B 站：搜索、读字幕、点赞投币评论、给主人发私信。 | `register()` |
| `pet/tools/bilibili/core.py` | B 站接口客户端：wbi 签名、请求、解析、写操作。 | `credential_path()` `invalid_mark_path()` `mark_credential_invalid()` `clear_credential_invalid()` `credential_invalid()` `is_bvid()` |
| `pet/tools/bilibili/duty.py` | 一轮刷 B 站：搜索、判重、读字幕，随机取几条返回给模型。 | `load_config()` `update_config()` `normalize_keywords()` `parse_keywords()` `set_keywords()` `set_dm_uid()` |
| `pet/tools/bilibili/filters.py` | 评论与私信内容过滤。 | `describe()` `normalize()` `similarity()` `check_text()` |
| `pet/tools/bilibili/login.py` | B 站扫码登录：生成二维码、轮询状态、提取凭证。 | `generate()` `poll()` |
| `pet/tools/bilibili/panel.py` | B 站账号面板：扫码登录、功能开关、搜索关键词、私信 UID、退出登录。 | `_QrLabel` `_LoginWorker` `AccountPanel` `show_panel()` |
| `pet/tools/bilibili/schedule.py` | 定时提醒：到点给模型一个念头，由提示词决定用什么工具。 | `parse_window()` `parse_pause_windows()` `invalid_pause_parts()` `in_window()` `is_paused()` `next_out_of_pause()` |
| `pet/tools/bilibili/storage.py` | B 站工具的持久化层：判重表、每日轮次、写操作台账。 | `BiliStorage` |
| `pet/tools/browser/__init__.py` | （模块未提供 docstring） | `register()` |
| `pet/tools/browser/core.py` | 浏览器工具：每次调用冷启动 Chromium，用完即关。 | `BrowserTool` |
| `pet/tools/context.py` | 工具上下文 — 暴露宠物能力供工具主动调用。 | `ToolContext` |
| `pet/tools/executor.py` | 工具执行器 — 解析 LLM 输出中的 Tool JSON，路由执行，返回结果。 | `ToolCall` `ToolResult` `ToolExecutor` |
| `pet/tools/file_ops/__init__.py` | （模块未提供 docstring） | `register()` |
| `pet/tools/file_ops/core.py` | 文件操作工具：列目录、读写文本、读取图片，限桌面与文档目录。 | `FileOpsTool` |
| `pet/tools/knowledge/__init__.py` | knowledge 工具 — 轻量 RAG 知识库。只读检索，写入/删除仅限面板操作。 | `register()` |
| `pet/tools/knowledge/chunker.py` | 轻量文本分块器 — 按段落 + 字数窗口切分。 | `chunk_text()` |
| `pet/tools/knowledge/panel.py` | 知识库管理面板 — 添加、导入文件、搜索、删除。 | `KnowledgePanel` `show_panel()` |
| `pet/tools/knowledge/storage.py` | 知识库存储层 — SQLite + sqlite-vec 向量检索。 | `KnowledgeStorage` |
| `pet/tools/output.py` | 工具输出的声明与投影：schema 约束规范值，render 产出内部内容块，由编码层压平成 wire 格式。 | `TextBlock` `ImageBlock` `ToolOutputDefinition` `validate()` `render_turn()` `render_text()` |
| `pet/tools/registry.py` | 工具注册表 — 自动发现、注册、描述可用工具。 | `ToolMethod` `ToolDef` `ToolRegistry` `food_tool_available()` |
| `pet/tools/system_monitor/__init__.py` | （模块未提供 docstring） | `register()` |
| `pet/tools/system_monitor/core.py` | （模块未提供 docstring） | `get_overview()` `get_top_processes()` `get_memory_detail()` `get_network()` |
| `pet/tools/timer/__init__.py` | timer 工具 — 倒计时定时器，到时间宠物主动提醒。支持持久化重启后恢复。 | `register()` |
| `pet/tools/timer/core.py` | 定时器核心 — 基于 Scheduler 的倒计时提醒，重启后可恢复。 | `TimerTool` |
| `pet/tools/timer/storage.py` | 定时器持久化 — SQLite 存储，重启后可恢复未完成的定时器。 | `TimerStorage` |
| `pet/tools/todo/__init__.py` | todo 工具 — 极简单代办事项管理。 | `register()` |
| `pet/tools/todo/core.py` | TodoList 核心处理逻辑 — LLM 可见方法实现。 | `TodoListTool` |
| `pet/tools/todo/panel.py` | Todo 管理面板 | `TodoPanel` `show_panel()` |
| `pet/tools/todo/storage.py` | Todo 持久化存储 — SQLite 数据层。 | `TodoStorage` |
| `pet/tools/todo/store.py` | todo 工具实例的持有者 — 工具入口与面板共享同一实例，避免面板回指工具包。 | `init_instance()` `get_instance()` |
| `pet/tools/todo/style.py` | （模块未提供 docstring） |  |
| `pet/tools/weather/__init__.py` | （模块未提供 docstring） | `register()` |
| `pet/tools/weather/core.py` | 通过 Open-Meteo 免费 API 获取实时天气和预报。 | `get_current()` `get_forecast()` |
| `pet/tools/web_search/__init__.py` | 支持 SearXNG（自建）、DeepSeek 搜索 API、Tavily 和 Bing Web Search API。 | `register()` |
| `pet/tools/web_search/core.py` | 支持 SearXNG（自建）、DeepSeek 搜索 API、Tavily 和 Bing Web Search API 四种后端。 | `_PinnedHTTPSAdapter` `check_connectivity()` `search()` `fetch()` |
| `pet/ui/__init__.py` | UI 层 — pet_window 宠物主窗口，speech_bubble/chat_bubble/feed_bubble/file_bubble 气泡组件，file_drop_handler 拖放判定， |  |
| `pet/ui/base_window.py` | （模块未提供 docstring） | `TransparentWindow` |
| `pet/ui/chat_bubble.py` | 桌宠聊天交互组件 | `ChatBubble` |
| `pet/ui/chat_history.py` | 对话历史窗口 — 以对话气泡形式展示用户与桌宠的对话记录。 | `ChatBubbleDelegate` `ChatHistoryWindow` |
| `pet/ui/debounce.py` | 按钮与提交的防抖：一次物理操作只产生一次请求。 | `Debounce` |
| `pet/ui/debug_window.py` | 调试面板 | `DebugWindow` |
| `pet/ui/edge_dock.py` | 屏幕边缘贴边的纯几何与策略计算。 | `pick_dock_side()` `dock_x()` `edge_mask_rect()` |
| `pet/ui/emotion.py` | 桌宠情绪气泡显示 emoji 表情 | `known_emotions()` `emotion_to_emoji()` `EmotionBubble` |
| `pet/ui/feed_bubble.py` | 桌宠喂食交互组件 | `FeedBubble` |
| `pet/ui/file_bubble.py` | 文件气泡 - 拖入文件后显示摘要与动作按钮，单例复用。 | `describe_ref()` `FileBubble` |
| `pet/ui/file_drop_handler.py` | 拖放判定层：悬停与放下两层的判定、回调分发。 | `paths_from_mime()` `busy_event_text()` `FileDropHandler` |
| `pet/ui/food_window.py` | 觅食的食物悬浮窗 — 纯展示组件，生命周期由 FoodManager 管理。 | `FoodWindow` |
| `pet/ui/game_panel.py` | 游戏面板基类 — 通用无边框窗口框架 + 统一渲染入口 + 用户手动收场。 | `GamePanelBase` |
| `pet/ui/higher_lower_panel.py` | 猜高低面板 — 继承 GamePanelBase，负责牌堆展示与下注提交。 | `HigherLowerPanel` |
| `pet/ui/log_window.py` | 日志窗口 | `LogRelay` `LogWindowHandler` `LogWindow` |
| `pet/ui/memory_window.py` | 记忆管理窗口 | `MemoryWindow` |
| `pet/ui/music_bubble.py` | 桌宠音乐控制气泡 - 操控系统媒体播放 | `MusicBubble` |
| `pet/ui/particle.py` | 桌宠粒子特效 | `Particle` `_SpiralGlyph` `ParticleWidget` |
| `pet/ui/pet_animations.py` | 桌宠帧动画模块 —— 基于 JSON 配置的帧序列播放。 | `PetAnimator` |
| `pet/ui/pet_window.py` | 扁平圆角菜单基类 — Windows 下自绘圆角背景，macOS 走原生。 | `_FlatMenuBase` `StickyMenu` `_SpriteLabel` `PetWindow` |
| `pet/ui/rps_panel.py` | 猜拳面板 — 继承 GamePanelBase，负责出拳展示与点击提交。 | `RpsPanel` |
| `pet/ui/settings_window.py` | 设置界面 — 用户配置的图形界面。 | `_LLMTestWorker` `_EmbeddingTestWorker` `_MemoryMergeWorker` `_ModelsFetchWorker` `_VoiceTestWorker` `MarkdownEdit` |
| `pet/ui/speech_bubble.py` | 桌宠对话气泡：同一时刻只讲一句，多出来的排队 | `SpeechBubble` |
| `pet/ui/styles.py` | QSS 样式库 —— 扁平化圆角风格。 | `bubble_column_y()` `make_title_button()` `make_minimize_button()` `make_close_button()` `ensure_taskbar_icon()` |
| `pet/ui/swing.py` | 拖拽惯性钟摆：窗口加速度驱动贴图绕抓取点摆动，松手后自由衰减。 | `step_pendulum()` `SwingController` |
| `pet/ui/system_tray.py` | （模块未提供 docstring） | `SystemTrayManager` |
| `pet/ui/tic_tac_toe_panel.py` | 井字棋棋盘面板 — 继承 GamePanelBase，负责棋盘渲染与点击落子。 | `TicTacToePanel` |
| `pet/ui/twenty_questions_panel.py` | 二十问面板 — 桌宠提问猜东西，用户在面板点击"是/否/不确定"作答， | `TwentyQuestionsPanel` |
| `pet/ui/widgets.py` | 通用控件。 | `NoWheelComboBox` |
| `pet/version_check.py` | 启动时版本检查 | `get_local_version()` `_CheckWorker` `UpdateChecker` |
| `pet/version_utils.py` | 版本号解析与比较的纯逻辑。 | `strip_v()` `ver_newer()` |
| `pet/voice/__init__.py` | 语音层 — 全局热键、麦克风采集与讯飞听写编排。 |  |
| `pet/voice/hotkey_manager.py` | 全局热键管理器，使用 pynput 监听按键。 | `HotkeyManager` |
| `pet/voice/mic_capture.py` | 麦克风 PCM 采集模块 | `MicCapture` |
| `pet/voice/voice_session.py` | 语音会话编排：麦克风采集 → 讯飞识别 | `VoiceSession` |
| `pet/voice/xunfei_stt.py` | 讯飞语音听写 (iat) WebSocket API 封装 | `XunfeiSTT` |

共 141 个模块。
