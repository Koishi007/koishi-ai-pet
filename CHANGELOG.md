# 变更记录

本文件由 `scripts/gen_changelog.py` 从 git 历史生成（脚本只在源码仓库里），
按 [Conventional Commits](https://github.com/Koishi007/koishi-ai-pet/blob/master/CONTRIBUTING.md) 前缀分组；
起点 tag 为 `v1.5.0`，更早的历史没有规范化的提交信息、未回溯（见 GitHub Releases）。

发布前刷新一次：`python scripts/gen_changelog.py`，流程见
[docs/operations/release.md](https://github.com/Koishi007/koishi-ai-pet/blob/master/docs/operations/release.md)。

## v1.6.0 — 2026-10-02

**新功能**
- **scripts**: 变更记录的待发布小节按 pyproject 版本号渲染（eb377ca）
- **action**: 动作产出机制与钓鱼玩法（a8477f7）
- **anim**: 新增失落动作与紫黑螺旋粒子（fde0ffd）
- **anim**: 呼吸加上等体积缩放，位移与缩放可分别配置（0e1c233）
- **anim**: 静止类动作加呼吸位移，并加固帧动画播放的超时与配置容错（e33a2c6）
- **recall**: 补全回忆工具引导，明确空结果与结果用法（8a44f52）
- **context**: 新增「你惦记着的事」章节，统一需求、作息与旧事记忆注入（25918c1）

**修复**
- **ui**: 粒子清屏改用 Clear 合成模式，eraseRect 填的是不透明白（b7914cc）
- **ui**: 粒子播完先擦净再隐藏，消除下次 show 时的残影闪现（ec89f42）
- **brain**: 400 只在指向 thinking 时降级重试，其余原样抛出（b7b4f7c）
- **brain**: 流式收尾丢弃缺 function name 的工具调用（14fe2a4）
- **ui**: 呼吸位移改为浮点连续值，消除站立时 1px 方波式上下闪动（b7356d8）
- **ui**: 呼吸自绘路径按 DPR 换算贴图尺寸，修复站立时向左闪动（a5b90a4）
- **tools**: file_ops 工具名对齐目录名，摘掉 ARCH003 登记的设计债（83aad26）
- **brain**: 摘要无 LLM 时不打误导日志，轮次策略改由会话提供（b9a36f3）
- **brain**: 兼容冒号后不带空格的紧凑标签写法（b956dc0）
- **brain**: 流式收尾不补 sit，空响应重试恢复生效（3e79628）
- **brain**: 非流式补全重试时恢复备选方案切换（0aa8da2）
- **prompt**: 感受锚点后移到静态块末尾，需求做法去重（f6bdd98）
- **context**: 每轮注入按池子常态上界取条数，清零震荡区间的注入缺口（352c4fb）
- **context**: _evict_context 的 base_limit 加下界，避免负数切片误保留待淘汰对话（ac52560）
- **context**: 合并候选池容量上限到每轮注入上限，消除历史不可见的死区（e3debcf）
- **scripts**: 生成脚本输出强制 UTF-8，修 Windows 上的 UnicodeEncodeError（773f9e9）
- **action**: 产出结算移至动作正常结束（26f0c7d）
- **file_ops**: 路径校验解析 symlink/junction 逃逸并补测试（d48b3c0）
- **timer**: 修正离线恢复时已到点/未到点定时器的处理（d36501d）
- **pulse**: sit 不再恢复精力、sleep 恢复速率减半，好感回归基线降至 50（bef25b7）
- **agent**: 修复脑线程取消后被析构触发 Qt abort 崩溃（a3b03fc）
- **timer**: 离线到期定时器恢复时锁内补发导致自死锁，改为锁外补发（59e805b）

**调优**
- **prompt**: 记忆输出写进本轮流程，user_prompt 显式提示 Memory 行（256b597）
- **food**: food__status 按状态给出明确行动引导（b7a4a53）
- **vitals**: 动作精力消耗减半，缓解精力下降过快（f4d2b3c）

**重构**
- **brain**: LLM 调用封装抽 pet/brain/llm_gateway.py（7882857）
- **brain**: 摘要执行端抽 pet/brain/summary.py（2890be7）
- **brain**: 本地兜底决策抽 pet/brain/local_fallback.py（1445b72）
- **brain**: 工具轮次抽 pet/brain/tool_loop.py（796dc88）
- **brain**: 输出解析收敛到 pet/brain/parsing.py（27536f2）
- **brain**: 输出契约下沉 pet/brain/output.py（1a35d28）
- **arch**: food 层改注入窗口工厂，消除对 UI 的反向依赖（58c8747）
- **arch**: 清偿 recall/pet_agent 的跨对象私有访问（ac6b0d8）
- **arch**: 断开 todo 面板环并删除面板注册死通道（6c910ff）
- **arch**: 断开 brain 包内导入环并清理重复延迟 import（83edfa4）
- 分离纯版本逻辑并延迟 pet.action 的 Qt 导入（4661e9a）
- **agent**: 清理历史遗留的 SLEEPING 状态与死方法（ad3116d）

**样式**
- **tools**: knowledge 面板入口补 storage 类型标注（8831af6）
- **idle**: 待机呼吸幅度与节奏对齐思考动画（fa489e5）

**文档**
- **release**: 发布顺序改为打 tag 前刷新变更记录（d26676f）
- **readme**: 工具表与小节同步 file_ops 更名（00f5ccd）
- **arch**: §11 小节改名 ARCH 规则清单，删 §14 已还清的 Behavior 拆分说明（9c1c6d2）
- **agents**: 补「不写变更史」并清理注释与文档里的同一类叙述（df5eafd）
- **arch**: 去掉 Behavior 拆分段落里的过程叙述（4befaf5）
- **arch**: 同步 Behavior 拆分后的模块归属（4c74b2c）
- **governance**: 统一手写文档文风并新增文风检查（17bc56b）
- **arch**: 登记由测试守住的红线与设计债冻结清单（4f81871）
- **arch**: 新增架构契约测试设计，补齐规则依据（799691a）
- **context**: 补充 Task 3 设计补记与文档同步，修正池子上界的表述（333b729）
- **context**: 同步候选池上限合并的文档，登记 specs 目录（2592d96）
- 刷新 CHANGELOG（生成头改用在线链接）（b368d2a）
- 发布包内文档改用在线链接（95600fe）
- 收敛重复内容并标注文档角色（0f2a8c9）
- 建立文档工程并补充分层设计标准（e9f67b3）
- README 同步代码现状，mimo 推荐模型更新至 v2.6-flash（0618568）

**测试**
- **anim**: 呼吸位移浮点化后同步更新 1px 振幅用例（a13d4c3）
- **brain**: 补流式取消的关流、线程回收与锁归还用例（ef5d00c）
- **brain**: 补流式入口的空响应重试用例（0e212be）
- **arch**: test_pure_utils 超时失败信息与冒烟测试对齐（0dd66c4）
- **arch**: test_pure_utils 子进程顶替崩溃钩子并加超时（02f2099）
- **arch**: 冒烟测试顶替崩溃钩子、加超时并补环当事模块入口（7c96321）
- **arch**: 修复契约测试六处审查问题（8b625e9）
- **arch**: 新增架构契约测试，把结构红线变成可执行的 pytest（99d1663）
- **context_notes**: 消除 TestNeedsNote 对运行时段的依赖（80eba6b）
- 补测试运行说明与 CI，清理临时目录与真实 sleep（fc38184）
- **scripts**: 安装/更新脚本测试迁移到 pytest 并合并参数化（cda2c98）
- 新增单元测试套件与 pytest 配置（dev 可选依赖）（a852d0a）

**杂项**
- **anim**: 呼吸缩放只留给 sleep，其余动作回到纯位移（323254b）
- 测试目录改用 export-ignore 排除，移除 .gitignore 中的 test 规则（c3df1b0）

**其他**
- Update particle.py（808e3a9）
- 降低鱼粒子上浮高度避免顶部被裁切（de37ba9）
- 修复鱼粒子水平居中，并将调试面板粒子特效改为扫描注册表（f0a7d08）
- 钓鱼判定增加命中日志（f48405f）
- 钓到鱼时新增鱼emoji上浮粒子特效（d7c35d0）
- 调整钓鱼命中概率为60%并改为recent窗口期注入（93bfe66）
- 调整个人认知提示词（a7e9770）

## v1.5.4 — 2026-09-27

**新功能**
- **prompt**: 新增输入可信边界与记忆写入约束（cdae6ed）
- **context**: 新增「最近发生了什么」事件流与工具通用事件接口（58abfe9）
- **prompt**: 放开沉默限制，新增自我生活引导与人格台词范例（841d9c4）
- **llm**: 新增首选/备选模型方案与失败回退切换（3af07ac）
- **prompt**: 约束 Speech 可理解性并放宽屏幕评论要求（60c96da）

**修复**
- **context**: 低理智状态改为无害表达，移除破坏性引导（fdbbc3b）
- **ui**: 修复模型列表选择后未回填模型名称（2455dae）

**重构**
- **event**: 摔落事件仅由站立窗口丢失触发，移除落地记录（696787e）
- **food**: 觅食事件归并到通用事件机制（1f524ba）

**样式**
- **prompt**: 省略号统一为半角三点（0b86c7d）

**文档**
- 项目功能清单精简，自我生活与模型容灾并入自主行动，事件记忆并入持久记忆（b617ee6）

**杂项**
- 版本号 1.5.4，README 补充双模型方案、事件记忆与 note_event 接口（8481ef9）

## v1.5.3 — 2026-09-11

**新功能**
- **prompt**: 鼓励模型依据截图与文本线索主动调用 recall（48caddb）
- **memory**: 核心槽保底轮转与工具检索晋升（30673b3）
- PyPI mirror fallback in setup/update scripts and self-apply pending update scripts（b65ca6c）
- **memory**: filter memory browsing by creation date range（57d699d）
- register recall memory-retrieval meta tool（400476c）
- add MemoryStore singleton and recall search support（51ccd06）

**修复**
- **prompt**: 耗时动作示例改用与可用动作一致的参数写法，去掉 duration=（f7264e2）
- **ui**: 透传 speech duration，工具 aside 气泡显示 2s（b46b1e1）
- release SQLite write lock on uncommitted updates and heal poisoned connections（6dd2772）
- bound stream-connect phase and add brain-stuck watchdog（b07d8bf）
- **tools**: truncate long tool results in executor logs（ae9f647）
- **memory**: guard query term merging for whole-sentence queries（bb848a2）

**重构**
- rename knowledge group memory to knowledge and update docs（25c07d9）

**文档**
- **prompt**: 觅食示例精简为就地写法，避免与 tool 描述重复强调（41374b1）

**杂项**
- 版本号 1.5.3，推荐模型改为 deepseek-flash / mimo-v2.5，去掉模型名 placeholder（6ad7f02）
- **memory**: 精简核心槽与工具检索晋升的注释（a6ca4ff）

**其他**
- Update self_update.py（a61e99f）
- End games neutrally on user timeout or close, no forfeit loss（ce1f90f）
- 调整prompt（63d30ef）
- 调整prompt（ad17267）
- 调整工具使用描述（d9334c8）
- Update prompts.py（a86812d）

## v1.5.2 — 2026-09-01

**其他**
- Update pyproject.toml（ce0904a）
- Fix false negative in setup.bat 64-bit check: missing sys import（370a121）
- Guide mood output after games; tune mood decay rates（e961637）
- Add timestamp prefix to head pat context note（ac7946d）
- Refactor system prompt assembly; move identity guide to first line（4cf4880）
- 调整提示词（00f8f5e）
- Rename game_board_panel to tictac_panel（272a448）
- Add single-instance lock to prevent multi-instance conflicts（4d32271）

## v1.5.1 — 2026-08-29

**修复**
- 合并历史 system 消息到主 prompt，适配 ollama qwen3 模板（0ff49a7）

**其他**
- Update pyproject.toml（16f9768）
- Adjust meta tool max rounds to 99（c6253b9）
- Emphasize continue-play instruction in twenty questions play returns（6a1dc22）
- Add twenty questions game with panel; raise tool max rounds to 30（ef2c0a3）
- Adjust mood decay: grace window 600s to 60s, joy regression rate 1.5 to 2.5（53bf2de）
- README: add DeepSeek-V4-Flash-Vision-Exp as recommended vision model（28b8128）
- Inject head-pat note into context remarks when user petted within a mid_tick window（c83b84b）
- Ensure each emotion in sequence plays at least 1.5s（122da9b）
- 调整部分提示词（5d22485）
- Fix database is locked causing UI freeze: unified SQLite connections, enable WAL + busy_timeout, fast-fail saves on main thread（f86a61d）
- 修复macos安装失败的问题，更新python版本要求（1167f5b）
- Guide aside to follow pet personality（83d4396）
- Support multiple emotions and remove emotion-linked particles（e993082）
- Update README.md（b10a835）
- Deduplicate self-feeding context via system path only（483be30）

## v1.5.0 — 2026-08-21

**新功能**
- unify game summary perspective; remove panel auto-hide; end-of-game guidance（38b7120）
- add game__start lifecycle and unique arg names; fix review findings（45494aa）
- add rock-paper-scissors game and refactor game panels（e590e0d）
- add tic-tac-toe game with interactive board（314bcf6）
- **prompt**: guide model to avoid repeating tool_call speech in final output（46a68e3）
- **game**: dynamic game__play args schema from registered games（25278d4）
- **speech**: record tool_call speech into conversation history（761f232）
- **speech**: log tool_call speech at info level（769041c）
- **prompt**: guide model to use speech param in tool calls（efc4851）
- **tool**: add universal speech param to all tool calls（8e4e484）
- **game**: add guess_number game with speech output on play（6b917ea）
- **game**: add turn-based game base with play/list/stop meta tool（9ef32dd）
- **attention**: inject user-neglect state into autonomous prompt by rounds（55a3cab）
- **mood**: add natural decay toward baseline for joy/affection（8bff8c0）
- disable tools for manual feeding interaction（287e5be）
- support per-trigger thinking and tools override for instant interactions（89acb1a）
- **food**: use AABB collision for eating detection, tick 200ms（f17016e）
- **crash**: collect faulthandler log for native crashes on abnormal exit（6b500bf）
- 觅食食物改为全屏随机位置，需结合 walk/drive/bounce 取食（1ef86cb）
- 觅食游戏——桌宠自主生成食物并吃掉（52ea8ea）

**修复**
- disable board cells during pet turn; auto-close board after game ends（41429f8）
- 修复interact/chat撞上LLM调用时主线程卡死，记忆保存移至后台线程（0ac26bb）
- **speech**: use counter+lock for model speech suppression, fix race（1ae69a7）
- **tool**: log full tool result without truncation（08ee87d）
- **agent**: cooperative cancel for brain thread, avoid UI freeze（1586103）
- **mood**: remove sanity floor clamp, sanity fully event-driven（47c7ae6）
- mark meta tools via ToolDef.meta and hide from right-click tool menu（7dbe79f）
- thinking 恢复直接播放（循环动画入队会阻塞队列至超时）（d291457）
- 等待动画改为入队执行，falling 时由队列暂停/落地恢复（861365f）
- 觅食进食时悬空不播等待动画，避免覆盖 falling（3a2f6c3）
- 修复觅食游戏 tick 不启动导致快照永不就绪（87d903e）
- 修复觅食游戏的 spawn 返回语义、动画泄漏与开关联动问题（ba905e0）

**重构**
- **game**: move speech lines to game hooks, keep base generic（4dae7e0）
- **food**: extract food game to pet/food module（0a0f140）
- wait_anim 统一为 _play_wait_anim，chat 同步支持（3f1b9ca）

**杂项**
- 精简跨线程相关注释（9c1f70b）
- 精简 food__status 工具描述（331311b）
- 精简 food__spawn 工具描述（cd5ecb7）

**其他**
- Update pyproject.toml（fb82ad4）
- Fix IndentationError in game play summary（d33456d）
- 调整aside的概念（1cb68bc）
- Rename tool call speech param to aside（84d1999）
- Update prompts.py（8b59e71）
- Rename game start tool to init（8c43a89）
- Clean up redundant comments and show countdown as text（127399a）
- 调整desc（d5a8185）
- Update registry.py（7d38d52）
- 优化提示词（264fe78）
- 调整提示词（66a7b0e）
- 调整提示词（596f41b）
- 修改元工具调用上限（ead2f34）
- 更新提示词（1d0324b）
- 修改食物消失默认时间（702fe5e）
- 精简注释（f234174）
