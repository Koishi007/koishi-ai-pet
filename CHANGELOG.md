# 变更记录

本文件由 `scripts/gen_changelog.py` 从 git 历史生成（脚本只在源码仓库里），
按 [Conventional Commits](https://github.com/Koishi007/koishi-ai-pet/blob/master/CONTRIBUTING.md) 前缀分组；
起点 tag 为 `v1.5.0`，更早的历史没有规范化的提交信息、未回溯（见 GitHub Releases）。

发布前刷新一次：`python scripts/gen_changelog.py`，流程见
[docs/operations/release.md](https://github.com/Koishi007/koishi-ai-pet/blob/master/docs/operations/release.md)。

## v1.7.0 — 2026-10-27

**新功能**
- **bilibili**: qrcode 缺失时阻塞工具加载（e40699e）
- **ui**: 切到新会话移到托盘菜单并加确认（e70a1f6）
- **bilibili**: browse 支持 bvid 参数，集齐 per_run 即返回（fee4815）
- **bilibili**: 工具目录行提示拿到 BV 时用 browse 只传 BV（33e56ef）
- **bilibili**: BV 直通，指定 BV 时不再搜索关键词（436188d）
- **bilibili**: @ 语义重构与定时提醒收敛（1d044ae）
- **brain**: 模型输出结构化落库，工具与投影契约收敛（a6b1da5）
- **settings**: slow_tick 默认 10 分钟，设置页收敛连接与数值项（6d1f4c6）
- **prompt**: 历史后补字段说明，收拢重复的动作引导（cc5b4f1）
- **memory**: 事件汇总纳入 conversation，产物固定 event（5f7edc6）
- **brain**: 同回合摘要与台词合并成一条历史消息（63137a9）
- **bilibili**: 面板增加定时任务间隔设置（df14a75）
- **tools**: 工具输出契约接入运行时观测（a54bfc0）
- **tools**: food / game 声明输出契约，全部工具纳入（e377555）
- **tools**: bilibili / tool_search / recall 声明输出契约（5b19066）
- **tools**: timer / todo / weather / system_monitor / web_search 声明输出契约（db424f3）
- **tools**: browser / file_ops / knowledge 声明输出契约（阶段 2）（2ed9bc5）
- **tools**: 输出契约声明层与渲染投影（阶段 1）（ebc5e2b）
- **brain**: 会话历史改为只追加事件日志与纯函数投影（ba11576）
- **tools**: 新增 bilibili 账号工具：刷视频、互动、定时提醒（443e992）
- **ui**: 抢占时已输出的台词继续播完（3d4fe1b）
- **ui**: 记忆设置新增汇总模型连接测试（b94f1ee）
- **memory**: 整点汇总事件记忆，旧事注入取同主题最新（c67dd21）
- **file_ops**: 支持读取图片并接入视觉通道（9d11fa6）
- **ui**: 退出与菜单贴边加淡入淡出（85f9c37）
- **edge**: 屏幕边缘吸附与贴边提示词（6a3a397）
- **assets**: shake_arms_wall 合并为两帧动作，idle_wall 补闭眼帧（dfd6a61）
- **assets**: 加入 11 个屏幕侧边停靠姿势素材（*_wall）（c3cd2a1）
- **web_search**: 新增 Tavily 搜索与抓取后端（a04cae7）
- **web_search**: 新增 DeepSeek 搜索后端，明确与 browser 的搜索分工（43ea787）

**修复**
- **brain**: 遮挡链排除工具窗口与透明覆盖层，补齐 Win32 原型声明（01501a8）
- **bilibili**: 评论锁延迟到首次使用，满足模块级无副作用（33392ec）
- **bilibili**: 评论冷却与发送加锁，复核写回保留楼中楼 root（e33299e）
- **bilibili**: 复核按 done、unverified、hidden 排序，避免隐藏记录占满批次（3c4f40a）
- **bilibili**: 评论改用公开视角复核，收窄写限制并去掉自动 @（4324f6e）
- **bilibili**: 右键子菜单改名设置并同步文档（bd1f0b5）
- **bilibili**: 面板提示登录失效（d0c2d8c）
- **bilibili**: 处理 #60 评审意见（fc747db）
- **bilibili**: 评论发出后延时复核可见性（99f44eb）
- 处理 #59 评审意见（29b3816）
- **brain/bilibili/ui**: 处理 #57 评审意见（1b5b176）
- **brain/bilibili**: 评审修复适配结构化输出架构（c063e55）
- **brain**: 摘要去重消费覆盖区间，参数回显消歧（cba0fef）
- **tools**: 冻结打包跳过工具依赖 pip 安装（78b1a0c）
- **app**: 锁不可用时按失败原因告警，文件日志提前落盘（41fdd81）
- **ui**: 汇总进行中关闭设置页不再阻塞（95e758d）
- **brain**: 台词分段落库并在修复轮丢弃半截台词（e797493）
- **brain**: 修复轮回传上一轮原文并只补缺失字段（97280cf）
- **bilibili**: 私信只按每日额度限流，间隔下限降到 10 分钟（7ceddbe）
- **prompt**: 动作数量按任务区间生成并强化必填引导（f6d085a）
- **brain**: 剥掉 Summary 行首自造的绝对时间标签（9c7cc04）
- **brain**: 修复轮校验必填字段并按任务提示动作数量（fb66780）
- **brain**: 工具轮 aside 统一渲染成 Speech 行（8df3739）
- **brain**: 上下文 dump 补 tool_calls 并压成单行（55d59e3）
- **brain**: 空解析进入修复轮，历史行按协议渲染，清空上下文删除历史（6c59e20）
- **tools**: 修复输出契约缺陷并改为严格校验（4c6fe6b）
- **bilibili**: 请求超时记 unknown，解析容忍脏字段（2bdb6c0）
- **brain**: 事件写入失败退回文本记录，摘要覆盖区间随队列显式传递（d2d8c2a）
- **brain**: 工具组时序、预算配额与工具搜索索引（9894131）
- **projection**: 两遍构建工具组，aside 不再落成孤立 assistant 消息（f546e8d）
- **brain**: 启动时从事件重建调试面板的上下文快照（9dd1251）
- **brain**: 修复 content 为 null 时上下文转储崩溃，tool_search 支持全名与可读摘要（f7dd444）
- **brain**: 上下文构建失败时记录堆栈并退回本地兜底（0b4d89c）
- **brain**: 事件日志缺陷修复与增长控制（d02502e）
- **bilibili**: 评论请求对齐 Web 端风控参数，复核识别阿瓦隆隐藏（53f58ce）
- **tools**: tool_search 支持多关键词并纳入方法说明（749e882）
- **ui**: 记忆连接测试互斥，关闭时清理汇总线程（e935704）
- **ui**: 流式台词信号带调用令牌，转发处过滤迟到的旧流（1624756）
- **brain**: 工具图片只随紧接的一次请求发送（fe7746f）
- **memory**: 写回前复核成员内容，无效组不占用 id（e681b2e）
- …另有 12 条同类改动

**调优**
- **anim**: grim/finger_heart/confuse/shy/embarrassed 补呼吸位移（b414d88）
- **anim**: 呼吸位移 1px 调至 2px（146d423）
- **web_search**: 工具与 fetch 描述去掉后端名称（e2cec54）
- **web_search**: auto 路由改为 Tavily 优先，连通探测覆盖所有已配置后端（6dc0705）

**重构**
- **brain**: 工具轮双事件与 snapshot 折叠（eb00fba）
- **tools**: 失败提示由工具自带，提示字段统一为 hint（96aec64）
- **game**: 内部键名统一到输出契约（31108f7）
- **tools**: 删除输出契约的兼容路径（ef204f1）
- **tools**: bilibili 与 game 就地返回规范值（53b9713）
- **web_search**: deep_search 拆为 search + fetch，对齐业界工具划分（d01b008）

**文档**
- 架构文档同步 slow_tick 默认间隔为 600s（7a2c6f1）
- 调整部分注释（b084440）
- 工具输出声明与投影设计（6d6f7bf）
- **specs**: 工具通道改为 native 首选，新增错误反馈降档与传输编码层（db0fdca）
- **specs**: 新增会话事件日志与消息投影设计（c9b8935）
- **memory**: 触发规则按 marker 增量描述（2577623）
- **memory**: 同步汇总游标、写回复核与同主题取最新（f5965fe）
- **edge**: 贴边设计文档与提示词说明对齐实现（6e04aff）
- **memory**: 同步事件汇总的增量触发与清理日志（78073d7）
- **ui**: 调整事件汇总设置项的说明文案（e2705b1）
- **file_ops**: 调整 read_file 的工具说明（9bcb5a0）
- 刷新 modules.md 的 web_search 函数列表（0ca2bd2）

**测试**
- **web_search**: SSRF 拒绝用例改为断言请求未发出（6e8cb74）
- 去掉测试中的产品文案断言，改为行为与结构断言（45b3cc7）

**杂项**
- 1.7.0 发布准备，版本号、变更记录与文档同步（f615f24）
- **bilibili**: 精简间隔校验的 docstring（8f24c8d）
- **memory**: 汇总日志截断时标注省略号与剩余字数（bfbd472）

**其他**
- merge master into batch/3-ui-polish（48b22fe）
- merge master into batch/2-memory-fileops（bf85a3f）
- tune：统一dock事件的描述（bda5f7c）

## v1.6.1 — 2026-10-16

**新功能**
- **brain**: 输出预算收归代码，按模式/思考/工具配额并只在 length 截断时关思考（64835e7）
- **ui**: 拖拽惯性摆动设置移到通用页签，category 同步改 appearance（c098860）
- **ui**: 拖拽惯性钟摆，抓起时贴图绕抓取点摆动（0beebb4）
- **notify**: 托盘通知总开关，并给动作产出增加开始即结算的时机（calling 来电通知）（baf852f）
- **brain**: 重试提示回传被丢弃的原文，并让模型先确认走协议输出还是函数调用（156e9ca）
- **game**: 新增猜高低对赌游戏（11f47ca）
- **action**: 新增 hug 拥抱动作（e57f304）
- **agent**: 右键菜单新增「中断当前调用」（27149d4）
- **ui**: 调试面板新增状态机流转展示（8fcce72）
- **brain**: system prompt 注入非元工具清单，弱模型先 tool_search 再调用（7666ba2）
- **filedrop**: 拖入文件交互全链路接入（9b1dec3）
- **ui**: 单击摸头加 Q 弹形变（b05a599）
- **anim**: fall_down 落地时注入事件「你摔了一跤」（2e6d3f1）
- **anim**: 新增 fall_down 动作，下落超时后落地跌倒、播完再恢复队列（71ddbb6）
- **anim**: 新增无意识化动作，待机超时自动播放并停留在末帧（6ae9fa3）

**修复**
- **brain**: 空响应兜底补 sit 时保留已解析字段（254f6e0）
- **ui**: 摆动开关即时生效，拖拽中途停顿不再停表（b7c0c3d）
- **ui**: 落地时残余摆角快速归正，不再拖到落地之后（eb13824）
- **ui**: 摆动绘制取负角，修正身体偏向与惯性一致（6f72f2f）
- **file_ops**: 路径无法解析时按拒绝处理（c7c4e49）
- **ui**: calling 来电通知不再被事件去重抑制（969ce5a）
- **brain**: 未解析正文回传限制样本长度（c68c932）
- **game**: 平局改用中性结束提示（637a56f）
- **action**: 丢弃模型带出的标点参数，并给动作数值参数兜底，修掉 walk 的 TypeError 崩溃（dd64d56）
- **game**: 井字棋台词不再把棋盘带进气泡（903573d）
- **ui/speech**: 修复多轮 tool_call 下气泡串行与顶替（59ff5b9）
- **agent**: _cleanup_thread 只清理发出 finished 的当前线程（fb4fde2）
- **brain**: 未解析响应直接打印正文，去掉遮盖（30e72f6）
- **brain**: 空响应重试改为纠错重试，未解析响应留遮盖式结构预览（80857f1）
- **agent**: _cleanup_thread 只清理发出 finished 的当前线程（f294a05）
- **agent/action**: 调用令牌替代共享取消标志，修复队列 start 后暂停态残留（42f7ddd）
- **action**: 修复清空队列后暂停态残留导致的动作永久冻结（500c0ba）
- **agent**: 取消调用不再走错误分支，只留一条 INFO（883cb36）
- **brain**: 兼容无换行的内嵌字段标签解析（fe51514）
- **prompt/test**: analyze 示例补齐 3 个 Action，游戏注册测试隔离共享 GAME（e67f7e8）
- **review**: 按 CodeRabbit 的 4 条 request change 修正（34f05e4）
- **ui**: 取不到系统音量端点时降级，音乐气泡构造不再失败（e9f3282）
- **brain**: 标签冒号容错全角，空解析在日志留下原文（16c27c1）
- **action**: walk/drive 参数范围与实现对齐（2729fd7）
- **food**: food__status 引导给出实际距离，去掉误导性的 500-1000 范围（1312ff1）
- mid 定时任务向自主管线传窗口快照（a43d6f8）
- 修气泡列重叠与窗口尺寸越界，窗口上下文改主线程取快照（9e2f954）
- **ui**: 分辨率与 DPI 缩放兼容性修复（904b8f5）
- **anim**: 校准 fall_down 四帧水平对齐（f0f59bb）
- **anim**: 无意识化判定改用 LLM 交互状态，不再依赖 loading 点点（f27c02a）

**调优**
- **ui**: 拖拽摆动加入速度分量并调大加速度增益（d88ea40）
- **brain**: 工具轮次的空 raw 与未解析字段提示改按预期形态静默（978b774）
- **prompt**: 各任务放开多句 Speech，analyze 动作对齐 chat，输出上限统一 30 字（a598bfe）
- **food**: 需要跳跃时按距离分段引导，避免 walk+bounce 的横向叠加误差（84e32d3）
- **anim**: fall_down 跌倒事件改为只注入一轮（1af75a0）
- **action**: 调整fall_down动作时长（ef59c56）
- **anim**: 无意识化待机阈值默认改为 60 秒（bede885）

**重构**
- **brain**: 空响应修复重试拆出 repair 模块，并修正审查意见（1dd00f0）
- **config**: 通知开关概念去托盘化，设置项改为允许发送通知（37b0bf1）
- **game**: 指令通道独立为 model_hint，summary 不再播出（ce14a82）
- **agent**: 结果与错误改由绑定槽排队回 PetAgent 所在线程（5cc94eb）
- **arch**: 清偿 allowlist 41 条债务，装配入口与惰性化收敛（9decc21）
- **anim**: fall_down 事件文案改走事件表，note_once_event 支持留空查表（2e6cee8）

**样式**
- **filedrop**: 文件气泡文案调整（6129ba3）
- **ui**: 调试面板与记忆窗口滚动条统一为设置页样式（ff0262c）

**文档**
- **game**: 猜高低设计文档下注公开时机对齐实现（bd04086）
- **readme**: 补上猜高低游戏与托盘通知开关（17c7649）
- 同步 calling 描述改动并精简产出文档串（5f70bca）
- 重新生成 modules.md 与代码对齐（8cdc7ce）
- 新增猜高低对赌游戏设计文档（4374772）
- README 菜单表补「中断当前调用」，粒子映射补 hug（89ad608）
- 测试表述统一为「全量测试通过」，清理违反规范的注释（482f331）
- 文档和注释的治理（d9988b0）
- **assets**: 补充全量素材对比图（82e901e）

**测试**
- **ui**: 气泡队列测试改手动驱动定时器（becbee0）

**杂项**
- 版本号 1.6.1，刷新变更记录（5196621）
- 去掉多余注释（91e0618）
- 精简注释，只保留做什么（55d16cd）
- **anim**: 替换 look_around 两帧素材（034771d）
- 合入 CodeRabbit 配置（818e3e6）
- 开启 CodeRabbit request_changes_workflow（d82aa5f）
- .coderabbit.yaml 加入 export-ignore，不进源码包分发（cb873be）
- 新增 CodeRabbit 配置，base 为 master 或 dev 时启用自动 review（4cc4027）
- **anim**: 替换 fall_down 四帧素材（4ee70da）
- **anim**: 调整 fall_down 首帧素材（3f7ab1c）
- **anim**: 调整fall_down部分素材（d38db25）
- **anim**: 调整fall_down部分素材（6a77a57）
- **anim**: 调整fall_down首帧图片（5bb4fdc）
- **anim**: 更新 idle、rotate、shake_arms、sit 的部分帧素材（d170e45）
- **anim**: 替换全部动作素材为最新渲染版（9006f62）

## v1.6.0 — 2026-10-03

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
- 版本号 1.6.0，刷新变更记录（923f764）
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
