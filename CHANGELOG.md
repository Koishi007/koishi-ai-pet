# 变更记录

本文件由 `scripts/gen_changelog.py` 从 git 历史生成（脚本只在源码仓库里），
按 [Conventional Commits](https://github.com/Koishi007/koishi-ai-pet/blob/master/CONTRIBUTING.md) 前缀分组；
起点 tag 为 `v1.5.0`，更早的历史没有规范化的提交信息、未回溯（见 GitHub Releases）。

发布前刷新一次：`python scripts/gen_changelog.py`，流程见
[docs/operations/release.md](https://github.com/Koishi007/koishi-ai-pet/blob/master/docs/operations/release.md)。

## v1.8.0 — 2026-11-28

**新功能**
- **voice**: 热键改左 Alt 并新增单击与双击触发方式（5329486）
- **ui**: 气泡尾巴改为直边三角并收窄根部（fe292b2）
- **ui**: 气泡蔷薇改为 20px 并骑上右下角边框（63dcdfd）
- **ui**: 说话气泡改手绘纸片风，加第三只眼与蔷薇（1fe1a08）
- **brain**: 两套方案完全独立，按调用模式分键（d36b01a）
- **brain**: 备选方案按完整连接回退并动态计算推理档位（2ef84c4）
- **brain**: 供应商抽象与通用推理强度设置（746a2ad）
- **anim**: 工具调用动作帧与 waiting/工具帧素材（a85870d）
- **ui**: 按钮墨影按压体系与渲染稿对齐（faa5a38）
- **ui**: 手绘卡通风样式统一与令牌化（5eb4636）
- **brain**: 工具调用事件保存完整参数并移除 args_digest（9e4b44f）
- **pixel_art**: 画布名允许重复，open 只按 id（99e1ee6）
- **pixel_art**: 无画布时右键菜单展示空态（c847b7c）
- **pixel_art**: 点阵画工具与只读画布窗口（4bb27b3）
- **terminal**: 恋恋状态命令、单命令帮助与分层补全（0f906d1）
- **terminal**: 终端窗口与受控命令执行（897f44c）
- **ui**: 全局下拉框禁用滚轮（9adbd2f）
- **ui**: 宠物缩放即时生效并修正重启提示（5d53443）
- **ui**: 托盘菜单提供重启（29f798b）
- **config**: 桌宠大小改为挡位设置，宽高按挡位映射推导（7ccd354）

**修复**
- **voice**: 设置页改热键后即时生效（a7b221f）
- **terminal**: Windows 参数拆分支持参数中间引号（bca06a9）
- **ui**: 测试连接按当前调用模式读取模型与推理强度控件（8848ad6）
- **web_search**: 域名解析校验只作用于本地抓取（69eabc2）
- **ui**: 恢复按钮按压下沉，下沉后补回原命中区域（25628fe）
- **ui**: 按钮点击区域固定，回车统一落默认角色（3891207）
- **pixel_art**: 线段先裁剪再栅格化，镜像按每个 op 落笔效果展开（16393d2）
- **brain**: 参数 artifact 原样保存，回收时保留参数引用（b561e23）
- UI样式修复（c02d638）
- **ui**: 游戏面板空闲时隐藏倒计时徽章（3ef480c）
- **ui**: 记忆筛选栏右侧下拉框纵向对齐（27ed454）
- **ui**: 记忆窗口表格不再溢出圆角并放大默认宽度（66456d1）
- **pixel_art**: 导出提示按状态行宽度省略（cc70233）
- **pixel_art**: 定位前先按实际尺寸计算（7d0aa97）
- **memory**: 清理只在超容量时按有效分淘汰，定期维护只打分（185aeca）
- **terminal**: 系统目录按可写性过滤，补齐卡片键盘限制（6f8131c）
- **terminal**: 补元字符与写选项拦截，确认队列按身份推进（0e0c387）
- **vitals**: 睡觉的精力恢复速度提升到每 tick +0.15（c8003e1）
- **ui**: 确认卡片可拖动并默认出现在恋恋侧面（e29163d）
- **terminal**: 终端窗口未打开时保留回显并在打开后补发（7108793）
- **cli**: 策略输出统一改用工作目录根（7c294e3）
- **terminal**: 工作目录根跟随终端目录并按需下发 shell 参数（1dac09b）
- 重启标记按进程归属清除，参数转换不再经 float 丢精度（3a589de）
- **ui**: 保存提示统一为部分设置需重启（86fc0c3）
- **tools**: 参数兼容只做无损转换，bool 不再当数值、小数不截断（e2f2f81）
- **file_ops**: 文本分页改按字符定位，修复多字节文件解码失败（f93d6d8）

**重构**
- **ui**: 工具面板内聚与确认框统一（acb8f11）
- **settings**: 拆分设置窗口并清偿两条 ARCH002 债务（9a82814）
- **cli**: 去除兼容垫片并把工具命令统一归入工具分组（f8427e8）
- **terminal**: 修复事件协议竞态并收敛分层职责（0e192e1）

**样式**
- **ui**: 收缩气泡源码里的注释（1bdae4c）
- **ui**: 说话气泡顶部黄绿渐变带加深到看得出明度（6fdbf62）
- **web_search**: 收敛抓取校验的注释（481e98a）
- **ui**: 气泡提示行缩进对齐输入框光标（98a6b21）
- 收敛本轮新增注释，去掉理由与现象叙述（2ddc6d3）
- **pixel_art**: 画布居中并收紧窗口排版（32b1c18）

**文档**
- 同步 1.7.0 以来的功能与页面改动（fcb8458）
- README 补齐像素画与文件拖入，修正结构树与工具清单（9dc884e）
- **reference**: 重新生成模块表，补入 _InnerButton（1eb3d80）
- **spec**: 新增桌宠命令行环境设计与实施计划（88c498e）

**测试**
- **tests**: 清理重复用例与文案断言（42c6c11）
- **terminal**: 补全与解释器用例去掉平台隐含假设（62e1672）

**杂项**
- **config**: 工具调用最大轮次默认值调整为 99（1557b0d）
- 配置调整（ec3c4ea）
- 精简 .gitignore 并清理模板注释（31db27c）

**其他**
- chore：部分素材更新（b8d101c）
- 调整文案（65d43f2）

## v1.7.0 — 2026-10-28

**新功能**
- **bilibili**: qrcode 缺失时阻塞工具加载（e40699e5）
- **ui**: 切到新会话移到托盘菜单并加确认（e70a1f64）
- **bilibili**: browse 支持 bvid 参数，集齐 per_run 即返回（fee4815b）
- **bilibili**: 工具目录行提示拿到 BV 时用 browse 只传 BV（33e56ef0）
- **bilibili**: BV 直通，指定 BV 时不再搜索关键词（436188d6）
- **bilibili**: @ 语义重构与定时提醒收敛（1d044ae2）
- **brain**: 模型输出结构化落库，工具与投影契约收敛（a6b1da59）
- **settings**: slow_tick 默认 10 分钟，设置页收敛连接与数值项（6d1f4c69）
- **prompt**: 历史后补字段说明，收拢重复的动作引导（cc5b4f10）
- **memory**: 事件汇总纳入 conversation，产物固定 event（5f7edc64）
- **brain**: 同回合摘要与台词合并成一条历史消息（63137a99）
- **bilibili**: 面板增加定时任务间隔设置（df14a75e）
- **tools**: 工具输出契约接入运行时观测（a54bfc04）
- **tools**: food / game 声明输出契约，全部工具纳入（e3775557）
- **tools**: bilibili / tool_search / recall 声明输出契约（5b190664）
- **tools**: timer / todo / weather / system_monitor / web_search 声明输出契约（db424f33）
- **tools**: browser / file_ops / knowledge 声明输出契约（阶段 2）（2ed9bc5f）
- **tools**: 输出契约声明层与渲染投影（阶段 1）（ebc5e2b1）
- **brain**: 会话历史改为只追加事件日志与纯函数投影（ba115761）
- **tools**: 新增 bilibili 账号工具：刷视频、互动、定时提醒（443e9926）
- **ui**: 抢占时已输出的台词继续播完（3d4fe1b9）
- **ui**: 记忆设置新增汇总模型连接测试（b94f1ee8）
- **memory**: 整点汇总事件记忆，旧事注入取同主题最新（c67dd21f）
- **file_ops**: 支持读取图片并接入视觉通道（9d11fa69）
- **ui**: 退出与菜单贴边加淡入淡出（85f9c375）
- **edge**: 屏幕边缘吸附与贴边提示词（6a3a397b）
- **assets**: shake_arms_wall 合并为两帧动作，idle_wall 补闭眼帧（dfd6a610）
- **assets**: 加入 11 个屏幕侧边停靠姿势素材（*_wall）（c3cd2a1c）
- **web_search**: 新增 Tavily 搜索与抓取后端（a04cae73）
- **web_search**: 新增 DeepSeek 搜索后端，明确与 browser 的搜索分工（43ea7879）

**修复**
- **brain**: 遮挡链排除工具窗口与透明覆盖层，补齐 Win32 原型声明（01501a86）
- **bilibili**: 评论锁延迟到首次使用，满足模块级无副作用（33392ec7）
- **bilibili**: 评论冷却与发送加锁，复核写回保留楼中楼 root（e33299e0）
- **bilibili**: 复核按 done、unverified、hidden 排序，避免隐藏记录占满批次（3c4f40a1）
- **bilibili**: 评论改用公开视角复核，收窄写限制并去掉自动 @（4324f6e3）
- **bilibili**: 右键子菜单改名设置并同步文档（bd1f0b56）
- **bilibili**: 面板提示登录失效（d0c2d8c6）
- **bilibili**: 处理 #60 评审意见（fc747dbe）
- **bilibili**: 评论发出后延时复核可见性（99f44ebd）
- 处理 #59 评审意见（29b38165）
- **brain/bilibili/ui**: 处理 #57 评审意见（1b5b176f）
- **brain/bilibili**: 评审修复适配结构化输出架构（c063e555）
- **brain**: 摘要去重消费覆盖区间，参数回显消歧（cba0fef9）
- **tools**: 冻结打包跳过工具依赖 pip 安装（78b1a0c5）
- **app**: 锁不可用时按失败原因告警，文件日志提前落盘（41fdd810）
- **ui**: 汇总进行中关闭设置页不再阻塞（95e758da）
- **brain**: 台词分段落库并在修复轮丢弃半截台词（e797493f）
- **brain**: 修复轮回传上一轮原文并只补缺失字段（97280cfa）
- **bilibili**: 私信只按每日额度限流，间隔下限降到 10 分钟（7ceddbe9）
- **prompt**: 动作数量按任务区间生成并强化必填引导（f6d085a8）
- **brain**: 剥掉 Summary 行首自造的绝对时间标签（9c7cc04d）
- **brain**: 修复轮校验必填字段并按任务提示动作数量（fb667805）
- **brain**: 工具轮 aside 统一渲染成 Speech 行（8df37392）
- **brain**: 上下文 dump 补 tool_calls 并压成单行（55d59e30）
- **brain**: 空解析进入修复轮，历史行按协议渲染，清空上下文删除历史（6c59e20d）
- **tools**: 修复输出契约缺陷并改为严格校验（4c6fe6b3）
- **bilibili**: 请求超时记 unknown，解析容忍脏字段（2bdb6c09）
- **brain**: 事件写入失败退回文本记录，摘要覆盖区间随队列显式传递（d2d8c2a1）
- **brain**: 工具组时序、预算配额与工具搜索索引（9894131f）
- **projection**: 两遍构建工具组，aside 不再落成孤立 assistant 消息（f546e8d3）
- **brain**: 启动时从事件重建调试面板的上下文快照（9dd12510）
- **brain**: 修复 content 为 null 时上下文转储崩溃，tool_search 支持全名与可读摘要（f7dd4442）
- **brain**: 上下文构建失败时记录堆栈并退回本地兜底（0b4d89c2）
- **brain**: 事件日志缺陷修复与增长控制（d02502e8）
- **bilibili**: 评论请求对齐 Web 端风控参数，复核识别阿瓦隆隐藏（53f58ce7）
- **tools**: tool_search 支持多关键词并纳入方法说明（749e8820）
- **ui**: 记忆连接测试互斥，关闭时清理汇总线程（e9357043）
- **ui**: 流式台词信号带调用令牌，转发处过滤迟到的旧流（16247561）
- **brain**: 工具图片只随紧接的一次请求发送（fe7746f0）
- **memory**: 写回前复核成员内容，无效组不占用 id（e681b2e6）
- …另有 12 条同类改动

**调优**
- **anim**: grim/finger_heart/confuse/shy/embarrassed 补呼吸位移（b414d887）
- **anim**: 呼吸位移 1px 调至 2px（146d4238）
- **web_search**: 工具与 fetch 描述去掉后端名称（e2cec54f）
- **web_search**: auto 路由改为 Tavily 优先，连通探测覆盖所有已配置后端（6dc07056）

**重构**
- **brain**: 工具轮双事件与 snapshot 折叠（eb00fbab）
- **tools**: 失败提示由工具自带，提示字段统一为 hint（96aec646）
- **game**: 内部键名统一到输出契约（31108f7b）
- **tools**: 删除输出契约的兼容路径（ef204f17）
- **tools**: bilibili 与 game 就地返回规范值（53b97135）
- **web_search**: deep_search 拆为 search + fetch，对齐业界工具划分（d01b008f）

**文档**
- **bilibili**: README 补充定时任务自定义说明（d1c7f91f）
- 架构文档同步 slow_tick 默认间隔为 600s（7a2c6f18）
- 调整部分注释（b0844400）
- 工具输出声明与投影设计（6d6f7bfd）
- **specs**: 工具通道改为 native 首选，新增错误反馈降档与传输编码层（db0fdca4）
- **specs**: 新增会话事件日志与消息投影设计（c9b89357）
- **memory**: 触发规则按 marker 增量描述（2577623a）
- **memory**: 同步汇总游标、写回复核与同主题取最新（f5965fe7）
- **edge**: 贴边设计文档与提示词说明对齐实现（6e04aff7）
- **memory**: 同步事件汇总的增量触发与清理日志（78073d78）
- **ui**: 调整事件汇总设置项的说明文案（e2705b12）
- **file_ops**: 调整 read_file 的工具说明（9bcb5a08）
- 刷新 modules.md 的 web_search 函数列表（0ca2bd2c）

**测试**
- **web_search**: SSRF 拒绝用例改为断言请求未发出（6e8cb749）
- 去掉测试中的产品文案断言，改为行为与结构断言（45b3cc7c）

**杂项**
- 变更记录补入 1.7.0 遮挡修复（48fc1ddd）
- 1.7.0 发布准备，版本号、变更记录与文档同步（f615f24c）
- **bilibili**: 精简间隔校验的 docstring（8f24c8dc）
- **memory**: 汇总日志截断时标注省略号与剩余字数（bfbd4724）

**其他**
- merge master into batch/3-ui-polish（48b22feb）
- merge master into batch/2-memory-fileops（bf85a3f4）
- tune：统一dock事件的描述（bda5f7c9）

## v1.6.1 — 2026-10-16

**新功能**
- **brain**: 输出预算收归代码，按模式/思考/工具配额并只在 length 截断时关思考（64835e76）
- **ui**: 拖拽惯性摆动设置移到通用页签，category 同步改 appearance（c098860b）
- **ui**: 拖拽惯性钟摆，抓起时贴图绕抓取点摆动（0beebb4d）
- **notify**: 托盘通知总开关，并给动作产出增加开始即结算的时机（calling 来电通知）（baf852fd）
- **brain**: 重试提示回传被丢弃的原文，并让模型先确认走协议输出还是函数调用（156e9ca3）
- **game**: 新增猜高低对赌游戏（11f47cae）
- **action**: 新增 hug 拥抱动作（e57f304f）
- **agent**: 右键菜单新增「中断当前调用」（27149d46）
- **ui**: 调试面板新增状态机流转展示（8fcce72a）
- **brain**: system prompt 注入非元工具清单，弱模型先 tool_search 再调用（7666ba28）
- **filedrop**: 拖入文件交互全链路接入（9b1dec37）
- **ui**: 单击摸头加 Q 弹形变（b05a5995）
- **anim**: fall_down 落地时注入事件「你摔了一跤」（2e6d3f1d）
- **anim**: 新增 fall_down 动作，下落超时后落地跌倒、播完再恢复队列（71ddbb67）
- **anim**: 新增无意识化动作，待机超时自动播放并停留在末帧（6ae9fa3f）

**修复**
- **brain**: 空响应兜底补 sit 时保留已解析字段（254f6e01）
- **ui**: 摆动开关即时生效，拖拽中途停顿不再停表（b7c0c3dd）
- **ui**: 落地时残余摆角快速归正，不再拖到落地之后（eb13824d）
- **ui**: 摆动绘制取负角，修正身体偏向与惯性一致（6f72f2f9）
- **file_ops**: 路径无法解析时按拒绝处理（c7c4e49b）
- **ui**: calling 来电通知不再被事件去重抑制（969ce5ad）
- **brain**: 未解析正文回传限制样本长度（c68c9327）
- **game**: 平局改用中性结束提示（637a56f4）
- **action**: 丢弃模型带出的标点参数，并给动作数值参数兜底，修掉 walk 的 TypeError 崩溃（dd64d561）
- **game**: 井字棋台词不再把棋盘带进气泡（903573d0）
- **ui/speech**: 修复多轮 tool_call 下气泡串行与顶替（59ff5b97）
- **agent**: _cleanup_thread 只清理发出 finished 的当前线程（fb4fde23）
- **brain**: 未解析响应直接打印正文，去掉遮盖（30e72f6c）
- **brain**: 空响应重试改为纠错重试，未解析响应留遮盖式结构预览（80857f13）
- **agent**: _cleanup_thread 只清理发出 finished 的当前线程（f294a055）
- **agent/action**: 调用令牌替代共享取消标志，修复队列 start 后暂停态残留（42f7ddda）
- **action**: 修复清空队列后暂停态残留导致的动作永久冻结（500c0ba6）
- **agent**: 取消调用不再走错误分支，只留一条 INFO（883cb36c）
- **brain**: 兼容无换行的内嵌字段标签解析（fe515142）
- **prompt/test**: analyze 示例补齐 3 个 Action，游戏注册测试隔离共享 GAME（e67f7e80）
- **review**: 按 CodeRabbit 的 4 条 request change 修正（34f05e4b）
- **ui**: 取不到系统音量端点时降级，音乐气泡构造不再失败（e9f32823）
- **brain**: 标签冒号容错全角，空解析在日志留下原文（16c27c1f）
- **action**: walk/drive 参数范围与实现对齐（2729fd7d）
- **food**: food__status 引导给出实际距离，去掉误导性的 500-1000 范围（1312ff19）
- mid 定时任务向自主管线传窗口快照（a43d6f83）
- 修气泡列重叠与窗口尺寸越界，窗口上下文改主线程取快照（9e2f954c）
- **ui**: 分辨率与 DPI 缩放兼容性修复（904b8f51）
- **anim**: 校准 fall_down 四帧水平对齐（f0f59bb2）
- **anim**: 无意识化判定改用 LLM 交互状态，不再依赖 loading 点点（f27c02af）

**调优**
- **ui**: 拖拽摆动加入速度分量并调大加速度增益（d88ea403）
- **brain**: 工具轮次的空 raw 与未解析字段提示改按预期形态静默（978b774b）
- **prompt**: 各任务放开多句 Speech，analyze 动作对齐 chat，输出上限统一 30 字（a598bfee）
- **food**: 需要跳跃时按距离分段引导，避免 walk+bounce 的横向叠加误差（84e32d37）
- **anim**: fall_down 跌倒事件改为只注入一轮（1af75a01）
- **action**: 调整fall_down动作时长（ef59c569）
- **anim**: 无意识化待机阈值默认改为 60 秒（bede885f）

**重构**
- **brain**: 空响应修复重试拆出 repair 模块，并修正审查意见（1dd00f04）
- **config**: 通知开关概念去托盘化，设置项改为允许发送通知（37b0bf15）
- **game**: 指令通道独立为 model_hint，summary 不再播出（ce14a824）
- **agent**: 结果与错误改由绑定槽排队回 PetAgent 所在线程（5cc94ebc）
- **arch**: 清偿 allowlist 41 条债务，装配入口与惰性化收敛（9decc21c）
- **anim**: fall_down 事件文案改走事件表，note_once_event 支持留空查表（2e6cee8a）

**样式**
- **filedrop**: 文件气泡文案调整（6129ba3f）
- **ui**: 调试面板与记忆窗口滚动条统一为设置页样式（ff0262c3）

**文档**
- **game**: 猜高低设计文档下注公开时机对齐实现（bd040868）
- **readme**: 补上猜高低游戏与托盘通知开关（17c7649e）
- 同步 calling 描述改动并精简产出文档串（5f70bcab）
- 重新生成 modules.md 与代码对齐（8cdc7cef）
- 新增猜高低对赌游戏设计文档（4374772d）
- README 菜单表补「中断当前调用」，粒子映射补 hug（89ad6081）
- 测试表述统一为「全量测试通过」，清理违反规范的注释（482f331c）
- 文档和注释的治理（d9988b0c）
- **assets**: 补充全量素材对比图（82e901e3）

**测试**
- **ui**: 气泡队列测试改手动驱动定时器（becbee07）

**杂项**
- 版本号 1.6.1，刷新变更记录（5196621f）
- 去掉多余注释（91e06188）
- 精简注释，只保留做什么（55d16cdc）
- **anim**: 替换 look_around 两帧素材（034771d9）
- 合入 CodeRabbit 配置（818e3e67）
- 开启 CodeRabbit request_changes_workflow（d82aa5ff）
- .coderabbit.yaml 加入 export-ignore，不进源码包分发（cb873bec）
- 新增 CodeRabbit 配置，base 为 master 或 dev 时启用自动 review（4cc40272）
- **anim**: 替换 fall_down 四帧素材（4ee70da6）
- **anim**: 调整 fall_down 首帧素材（3f7ab1c9）
- **anim**: 调整fall_down部分素材（d38db25c）
- **anim**: 调整fall_down部分素材（6a77a57f）
- **anim**: 调整fall_down首帧图片（5bb4fdc4）
- **anim**: 更新 idle、rotate、shake_arms、sit 的部分帧素材（d170e45d）
- **anim**: 替换全部动作素材为最新渲染版（9006f62b）

## v1.6.0 — 2026-10-03

**新功能**
- **scripts**: 变更记录的待发布小节按 pyproject 版本号渲染（eb377ca2）
- **action**: 动作产出机制与钓鱼玩法（a8477f75）
- **anim**: 新增失落动作与紫黑螺旋粒子（fde0ffdd）
- **anim**: 呼吸加上等体积缩放，位移与缩放可分别配置（0e1c2334）
- **anim**: 静止类动作加呼吸位移，并加固帧动画播放的超时与配置容错（e33a2c68）
- **recall**: 补全回忆工具引导，明确空结果与结果用法（8a44f52f）
- **context**: 新增「你惦记着的事」章节，统一需求、作息与旧事记忆注入（25918c11）

**修复**
- **ui**: 粒子清屏改用 Clear 合成模式，eraseRect 填的是不透明白（b7914cc8）
- **ui**: 粒子播完先擦净再隐藏，消除下次 show 时的残影闪现（ec89f424）
- **brain**: 400 只在指向 thinking 时降级重试，其余原样抛出（b7b4f7c2）
- **brain**: 流式收尾丢弃缺 function name 的工具调用（14fe2a49）
- **ui**: 呼吸位移改为浮点连续值，消除站立时 1px 方波式上下闪动（b7356d8e）
- **ui**: 呼吸自绘路径按 DPR 换算贴图尺寸，修复站立时向左闪动（a5b90a4c）
- **tools**: file_ops 工具名对齐目录名，摘掉 ARCH003 登记的设计债（83aad26b）
- **brain**: 摘要无 LLM 时不打误导日志，轮次策略改由会话提供（b9a36f3f）
- **brain**: 兼容冒号后不带空格的紧凑标签写法（b956dc0e）
- **brain**: 流式收尾不补 sit，空响应重试恢复生效（3e796289）
- **brain**: 非流式补全重试时恢复备选方案切换（0aa8da2e）
- **prompt**: 感受锚点后移到静态块末尾，需求做法去重（f6bdd98f）
- **context**: 每轮注入按池子常态上界取条数，清零震荡区间的注入缺口（352c4fb8）
- **context**: _evict_context 的 base_limit 加下界，避免负数切片误保留待淘汰对话（ac525609）
- **context**: 合并候选池容量上限到每轮注入上限，消除历史不可见的死区（e3debcf5）
- **scripts**: 生成脚本输出强制 UTF-8，修 Windows 上的 UnicodeEncodeError（773f9e92）
- **action**: 产出结算移至动作正常结束（26f0c7d4）
- **file_ops**: 路径校验解析 symlink/junction 逃逸并补测试（d48b3c0b）
- **timer**: 修正离线恢复时已到点/未到点定时器的处理（d36501d8）
- **pulse**: sit 不再恢复精力、sleep 恢复速率减半，好感回归基线降至 50（bef25b73）
- **agent**: 修复脑线程取消后被析构触发 Qt abort 崩溃（a3b03fc8）
- **timer**: 离线到期定时器恢复时锁内补发导致自死锁，改为锁外补发（59e805b6）

**调优**
- **prompt**: 记忆输出写进本轮流程，user_prompt 显式提示 Memory 行（256b5979）
- **food**: food__status 按状态给出明确行动引导（b7a4a531）
- **vitals**: 动作精力消耗减半，缓解精力下降过快（f4d2b3c2）

**重构**
- **brain**: LLM 调用封装抽 pet/brain/llm_gateway.py（78828573）
- **brain**: 摘要执行端抽 pet/brain/summary.py（2890be74）
- **brain**: 本地兜底决策抽 pet/brain/local_fallback.py（1445b72d）
- **brain**: 工具轮次抽 pet/brain/tool_loop.py（796dc883）
- **brain**: 输出解析收敛到 pet/brain/parsing.py（27536f2d）
- **brain**: 输出契约下沉 pet/brain/output.py（1a35d28d）
- **arch**: food 层改注入窗口工厂，消除对 UI 的反向依赖（58c8747c）
- **arch**: 清偿 recall/pet_agent 的跨对象私有访问（ac6b0d82）
- **arch**: 断开 todo 面板环并删除面板注册死通道（6c910ff6）
- **arch**: 断开 brain 包内导入环并清理重复延迟 import（83edfa4f）
- 分离纯版本逻辑并延迟 pet.action 的 Qt 导入（4661e9a1）
- **agent**: 清理历史遗留的 SLEEPING 状态与死方法（ad3116df）

**样式**
- **tools**: knowledge 面板入口补 storage 类型标注（8831af65）
- **idle**: 待机呼吸幅度与节奏对齐思考动画（fa489e50）

**文档**
- **release**: 发布顺序改为打 tag 前刷新变更记录（d26676f0）
- **readme**: 工具表与小节同步 file_ops 更名（00f5ccd3）
- **arch**: §11 小节改名 ARCH 规则清单，删 §14 已还清的 Behavior 拆分说明（9c1c6d21）
- **agents**: 补「不写变更史」并清理注释与文档里的同一类叙述（df5eafde）
- **arch**: 去掉 Behavior 拆分段落里的过程叙述（4befaf56）
- **arch**: 同步 Behavior 拆分后的模块归属（4c74b2cf）
- **governance**: 统一手写文档文风并新增文风检查（17bc56bc）
- **arch**: 登记由测试守住的红线与设计债冻结清单（4f818719）
- **arch**: 新增架构契约测试设计，补齐规则依据（799691a4）
- **context**: 补充 Task 3 设计补记与文档同步，修正池子上界的表述（333b7290）
- **context**: 同步候选池上限合并的文档，登记 specs 目录（2592d962）
- 刷新 CHANGELOG（生成头改用在线链接）（b368d2a3）
- 发布包内文档改用在线链接（95600fe2）
- 收敛重复内容并标注文档角色（0f2a8c90）
- 建立文档工程并补充分层设计标准（e9f67b30）
- README 同步代码现状，mimo 推荐模型更新至 v2.6-flash（06185682）

**测试**
- **anim**: 呼吸位移浮点化后同步更新 1px 振幅用例（a13d4c3c）
- **brain**: 补流式取消的关流、线程回收与锁归还用例（ef5d00c4）
- **brain**: 补流式入口的空响应重试用例（0e212be4）
- **arch**: test_pure_utils 超时失败信息与冒烟测试对齐（0dd66c47）
- **arch**: test_pure_utils 子进程顶替崩溃钩子并加超时（02f2099e）
- **arch**: 冒烟测试顶替崩溃钩子、加超时并补环当事模块入口（7c96321d）
- **arch**: 修复契约测试六处审查问题（8b625e9a）
- **arch**: 新增架构契约测试，把结构红线变成可执行的 pytest（99d1663d）
- **context_notes**: 消除 TestNeedsNote 对运行时段的依赖（80eba6b1）
- 补测试运行说明与 CI，清理临时目录与真实 sleep（fc381842）
- **scripts**: 安装/更新脚本测试迁移到 pytest 并合并参数化（cda2c989）
- 新增单元测试套件与 pytest 配置（dev 可选依赖）（a852d0aa）

**杂项**
- 版本号 1.6.0，刷新变更记录（923f7647）
- **anim**: 呼吸缩放只留给 sleep，其余动作回到纯位移（323254b2）
- 测试目录改用 export-ignore 排除，移除 .gitignore 中的 test 规则（c3df1b07）

**其他**
- Update particle.py（808e3a9d）
- 降低鱼粒子上浮高度避免顶部被裁切（de37ba94）
- 修复鱼粒子水平居中，并将调试面板粒子特效改为扫描注册表（f0a7d08d）
- 钓鱼判定增加命中日志（f48405f0）
- 钓到鱼时新增鱼emoji上浮粒子特效（d7c35d06）
- 调整钓鱼命中概率为60%并改为recent窗口期注入（93bfe66b）
- 调整个人认知提示词（a7e97701）

## v1.5.4 — 2026-09-27

**新功能**
- **prompt**: 新增输入可信边界与记忆写入约束（cdae6edd）
- **context**: 新增「最近发生了什么」事件流与工具通用事件接口（58abfe9f）
- **prompt**: 放开沉默限制，新增自我生活引导与人格台词范例（841d9c4f）
- **llm**: 新增首选/备选模型方案与失败回退切换（3af07acd）
- **prompt**: 约束 Speech 可理解性并放宽屏幕评论要求（60c96dac）

**修复**
- **context**: 低理智状态改为无害表达，移除破坏性引导（fdbbc3bf）
- **ui**: 修复模型列表选择后未回填模型名称（2455dae0）

**重构**
- **event**: 摔落事件仅由站立窗口丢失触发，移除落地记录（696787ec）
- **food**: 觅食事件归并到通用事件机制（1f524ba5）

**样式**
- **prompt**: 省略号统一为半角三点（0b86c7d8）

**文档**
- 项目功能清单精简，自我生活与模型容灾并入自主行动，事件记忆并入持久记忆（b617ee60）

**杂项**
- 版本号 1.5.4，README 补充双模型方案、事件记忆与 note_event 接口（8481ef90）

## v1.5.3 — 2026-09-11

**新功能**
- **prompt**: 鼓励模型依据截图与文本线索主动调用 recall（48caddb4）
- **memory**: 核心槽保底轮转与工具检索晋升（30673b31）
- PyPI mirror fallback in setup/update scripts and self-apply pending update scripts（b65ca6c6）
- **memory**: filter memory browsing by creation date range（57d699db）
- register recall memory-retrieval meta tool（400476ce）
- add MemoryStore singleton and recall search support（51ccd066）

**修复**
- **prompt**: 耗时动作示例改用与可用动作一致的参数写法，去掉 duration=（f7264e2f）
- **ui**: 透传 speech duration，工具 aside 气泡显示 2s（b46b1e1a）
- release SQLite write lock on uncommitted updates and heal poisoned connections（6dd27729）
- bound stream-connect phase and add brain-stuck watchdog（b07d8bf0）
- **tools**: truncate long tool results in executor logs（ae9f647f）
- **memory**: guard query term merging for whole-sentence queries（bb848a29）

**重构**
- rename knowledge group memory to knowledge and update docs（25c07d94）

**文档**
- **prompt**: 觅食示例精简为就地写法，避免与 tool 描述重复强调（41374b1f）

**杂项**
- 版本号 1.5.3，推荐模型改为 deepseek-flash / mimo-v2.5，去掉模型名 placeholder（6ad7f024）
- **memory**: 精简核心槽与工具检索晋升的注释（a6ca4ff8）

**其他**
- Update self_update.py（a61e99f1）
- End games neutrally on user timeout or close, no forfeit loss（ce1f90f4）
- 调整prompt（63d30ef7）
- 调整prompt（ad172679）
- 调整工具使用描述（d9334c81）
- Update prompts.py（a86812d3）

## v1.5.2 — 2026-09-01

**其他**
- Update pyproject.toml（ce0904aa）
- Fix false negative in setup.bat 64-bit check: missing sys import（370a121d）
- Guide mood output after games; tune mood decay rates（e961637d）
- Add timestamp prefix to head pat context note（ac7946d8）
- Refactor system prompt assembly; move identity guide to first line（4cf48804）
- 调整提示词（00f8f5e3）
- Rename game_board_panel to tictac_panel（272a4487）
- Add single-instance lock to prevent multi-instance conflicts（4d322710）

## v1.5.1 — 2026-08-29

**修复**
- 合并历史 system 消息到主 prompt，适配 ollama qwen3 模板（0ff49a7a）

**其他**
- Update pyproject.toml（16f9768d）
- Adjust meta tool max rounds to 99（c6253b92）
- Emphasize continue-play instruction in twenty questions play returns（6a1dc22e）
- Add twenty questions game with panel; raise tool max rounds to 30（ef2c0a3a）
- Adjust mood decay: grace window 600s to 60s, joy regression rate 1.5 to 2.5（53bf2dee）
- README: add DeepSeek-V4-Flash-Vision-Exp as recommended vision model（28b8128f）
- Inject head-pat note into context remarks when user petted within a mid_tick window（c83b84bd）
- Ensure each emotion in sequence plays at least 1.5s（122da9b7）
- 调整部分提示词（5d22485c）
- Fix database is locked causing UI freeze: unified SQLite connections, enable WAL + busy_timeout, fast-fail saves on main thread（f86a61d2）
- 修复macos安装失败的问题，更新python版本要求（1167f5bb）
- Guide aside to follow pet personality（83d43963）
- Support multiple emotions and remove emotion-linked particles（e993082f）
- Update README.md（b10a8359）
- Deduplicate self-feeding context via system path only（483be30b）

## v1.5.0 — 2026-08-21

**新功能**
- unify game summary perspective; remove panel auto-hide; end-of-game guidance（38b71206）
- add game__start lifecycle and unique arg names; fix review findings（45494aae）
- add rock-paper-scissors game and refactor game panels（e590e0d0）
- add tic-tac-toe game with interactive board（314bcf62）
- **prompt**: guide model to avoid repeating tool_call speech in final output（46a68e35）
- **game**: dynamic game__play args schema from registered games（25278d44）
- **speech**: record tool_call speech into conversation history（761f2323）
- **speech**: log tool_call speech at info level（769041cf）
- **prompt**: guide model to use speech param in tool calls（efc48519）
- **tool**: add universal speech param to all tool calls（8e4e4841）
- **game**: add guess_number game with speech output on play（6b917ea9）
- **game**: add turn-based game base with play/list/stop meta tool（9ef32dd3）
- **attention**: inject user-neglect state into autonomous prompt by rounds（55a3cab3）
- **mood**: add natural decay toward baseline for joy/affection（8bff8c05）
- disable tools for manual feeding interaction（287e5be8）
- support per-trigger thinking and tools override for instant interactions（89acb1a8）
- **food**: use AABB collision for eating detection, tick 200ms（f17016ed）
- **crash**: collect faulthandler log for native crashes on abnormal exit（6b500bf2）
- 觅食食物改为全屏随机位置，需结合 walk/drive/bounce 取食（1ef86cbd）
- 觅食游戏——桌宠自主生成食物并吃掉（52ea8ea4）

**修复**
- disable board cells during pet turn; auto-close board after game ends（41429f81）
- 修复interact/chat撞上LLM调用时主线程卡死，记忆保存移至后台线程（0ac26bb8）
- **speech**: use counter+lock for model speech suppression, fix race（1ae69a7d）
- **tool**: log full tool result without truncation（08ee87d7）
- **agent**: cooperative cancel for brain thread, avoid UI freeze（15861031）
- **mood**: remove sanity floor clamp, sanity fully event-driven（47c7ae63）
- mark meta tools via ToolDef.meta and hide from right-click tool menu（7dbe79f2）
- thinking 恢复直接播放（循环动画入队会阻塞队列至超时）（d291457d）
- 等待动画改为入队执行，falling 时由队列暂停/落地恢复（861365f8）
- 觅食进食时悬空不播等待动画，避免覆盖 falling（3a2f6c3d）
- 修复觅食游戏 tick 不启动导致快照永不就绪（87d903e1）
- 修复觅食游戏的 spawn 返回语义、动画泄漏与开关联动问题（ba905e00）

**重构**
- **game**: move speech lines to game hooks, keep base generic（4dae7e0e）
- **food**: extract food game to pet/food module（0a0f140d）
- wait_anim 统一为 _play_wait_anim，chat 同步支持（3f1b9caf）

**杂项**
- 精简跨线程相关注释（9c1f70b5）
- 精简 food__status 工具描述（331311b1）
- 精简 food__spawn 工具描述（cd5ecb76）

**其他**
- Update pyproject.toml（fb82ad4b）
- Fix IndentationError in game play summary（d33456dc）
- 调整aside的概念（1cb68bc9）
- Rename tool call speech param to aside（84d19998）
- Update prompts.py（8b59e71d）
- Rename game start tool to init（8c43a891）
- Clean up redundant comments and show countdown as text（127399aa）
- 调整desc（d5a8185a）
- Update registry.py（7d38d522）
- 优化提示词（264fe78b）
- 调整提示词（66a7b0e0）
- 调整提示词（596f41ba）
- 修改元工具调用上限（ead2f34b）
- 更新提示词（1d0324b9）
- 修改食物消失默认时间（702fe5e2）
- 精简注释（f234174f）
