# 拖入文件交互设计

> 桌宠窗口接收拖入的文件，由用户在气泡中选择动作（尝一口 / 看一看 / 收进知识库）；可读内容以嗅探结果为准，解析失败一律降级为文件名与类型；属性变化沿用 LLM 输出的 `Mood:` / `Vitals:` 行，不新增数值写入路径；原文件只读，不移动、不删除、不改写。

## §0 背景与现状

桌宠窗口是无边框透明窗口（`pet/ui/base_window.py`），鼠标事件已用于单击（摸头）、拖拽移动窗口与右键菜单（`pet/ui/pet_window.py:283-349`），仓库中没有任何 Qt 拖放实现：不存在 `setAcceptDrops`、`dragEnterEvent`、`dropEvent`、`QMimeData` 的使用。

用户向桌宠交付内容的现有入口只有文字：喂食气泡提交文本后走 `agent.trigger("interact", hint=interact_fed_prompt(text))`（`pet/app.py:163-167`），聊天气泡提交文本后走 `agent.trigger("chat", message=text)`（`pet/app.py:157-159`）。文件没有入口。

可复用的既有能力：

| 能力 | 现状 | 位置 |
|---|---|---|
| 属性写入 | LLM 输出 `Mood:` / `Vitals:` 行，解析层按字段白名单应用 | `pet/brain/parsing.py:37-68` |
| 交互模板 | `INTERACT_FED_PROMPT` 可覆盖，含文件类型到数值增量的规则 | `pet/brain/prompts.py:412-425`、`pet/config.py:68` |
| 视觉通道 | `prepare_image(image=...)` 接受任意 PIL 图像，编码为 base64 | `pet/agent/screen_reader.py:60-87` |
| 多模态消息体 | `image_url` + `data:<mime>;base64,...` | `pet/brain/context_builder.py:249-254`、`pet/brain/llm_gateway.py:144` |
| 知识库入库 | `add_document(title, content, tags, source)` 内部完成分块与可选向量化 | `pet/tools/knowledge/storage.py:167-222` |
| 事件注入 | `note_event(kind, text)` 常驻、`note_once_event(kind, text)` 只注入一轮 | `pet/agent/pet_agent.py:98-124` |
| 记忆写入 | LLM 输出 `Memory:` 行，由 agent 后台保存 | `pet/agent/pet_agent.py:613-619` |
| 姿态素材 | `assets/actions/` 下 26 套，含 `confuse`、`dejected`、`shy`、`embarrassed`、`rotate`、`unconsciousness`、`thinking` | `pet/action/action.py:425-467` |

缺口有三处：窗口不接收拖放；没有“读到文件内容”的共享实现（`pet/tools/file_ops/core.py:83` 与 `pet/tools/knowledge/panel.py:288` 各自按 UTF-8 读，且前者限制在桌面与文档目录）；交互通道 `build_interact` 只接受纯文本（`pet/brain/context_builder.py:66-72`），图片无法随单次请求送入。

### §0.1 前置验证

拖放能否送达该窗口决定后续改动是否成立，已在本机（Windows、PySide6 6.11.1）用一次性探针脚本验证，脚本不纳入仓库。窗口形态取 `pet/ui/base_window.py:5-14` 的无边框、置顶、`Tool`、半透明，并开启 `setAcceptDrops(True)`。结论：

| 项 | 结论 |
|---|---|
| 窗口形态 | 能收到拖放：拖入 `.txt` 时 `dragEnter` 与 `drop` 均触发，`hasUrls=True`、`proposed=CopyAction`；系统提议复制语义，本设计只读路径，不执行文件操作 |
| `dragMoveEvent` | 必须接受事件：走 `QWidget` 默认的 `ignore()` 后收不到 `drop`，四个拖放事件缺一不可（写法见 §10） |
| 穿透模式 | `WA_TransparentForMouseEvents` 下属性自检通过，拖放行为未人工确认：平台层面窗口在命中测试中被跳过，代码不依赖这一点（§10） |
| 窗口类型判读 | `Tool` 的 flags 值包含 `Popup` 的位（`0x0b` 含 `0x08`），`flags & Popup` 为真不代表窗口是弹出窗口；`pet/ui/pet_window.py:32-36` 的 `Popup` 属于右键菜单基类 `_FlatMenuBase` |
| 平台约束 | 以管理员身份运行的进程收不到非提权资源管理器发起的拖放（Windows UIPI），拖入无响应时先排除这一项 |

## §1 目标

- 窗口接收文件拖放，放下时的可收 / 不可收各有明确结果：可收弹动作气泡，不可收发一次即时反应请求。
- 悬停阶段不产生请求与动画，拖着文件路过窗口没有副作用。
- 内容读取发生在用户确认动作之后，未确认不读取。
- 文本可读性以内容嗅探为准，不维护后缀白名单；后缀只用于附加结构提取与拒绝名单。
- 解析失败一律降级为文件名与类型，交互不中断，且降级原因对用户可见。
- 属性、记忆、知识库三类产出全部走既有通道，不新增写入路径。
- 知识库等可选能力经工具扩展点接入，核心代码不依赖任何工具。

## §2 非目标

- 不移动、删除、改写用户文件：不做回收站清理、不做暂存目录、不给原文件追加批注或涂鸦。
- 不解析音视频内容与压缩包内容；不解析 PDF 与 Office 文档（需要新增依赖）。
- 不支持从桌宠向外拖出。
- 不做精确命中判定，窗口矩形内即视为接收区域。
- 不为文件类型新增专属动画素材，接收与拒绝的姿态由 LLM 从现有动作表中选取。

## §3 交互时序

拖放事件与既有鼠标事件走不同通道，不会触发 `mousePressEvent` / `mouseMoveEvent`，也不与“抓起再甩出”的窗口拖拽冲突。

| 阶段 | 事件 | 行为 |
|---|---|---|
| 悬停 | `dragEnterEvent` / `dragMoveEvent` | 两个事件都需处理：三类情形 `ignore()`，其余用 `setDropAction(CopyAction)` 后 `accept()`（动作语义见 §10），且 `dragMoveEvent` 不接受则收不到放下（§0.1）。不判体积与名单，不播姿态，不发请求；两个事件都幂等 |
| 离开 | `dragLeaveEvent` | 仅复位内部拖放标志，不触碰动作队列，不改变正在播出的动画或流式台词 |
| 放下 | `dropEvent` | 判定数量、体积、拒绝名单（§6）。通过：以 `CopyAction` 接受（`setDropAction` 后 `accept()`），隐藏 chat / feed / music 三个气泡（沿用 `pet_window.py:287-292` 的做法），弹出文件气泡。不通过：按类型发一次快速交互请求（§6.1），不弹气泡、不读内容 |
| 选择 | 文件气泡按钮 | 先停止超时定时器，再进入忙态，后台线程读取内容，按 §5 分发 |
| 超时 | 12 秒无操作 | 仅在定时器未被取消时到达：收起气泡，不读内容，`note_once_event("file_drop_idle", text)`，由下一轮上下文注入一次 |
| 收尾 | 请求返回 | 结果走 `speech_bubble` 流式输出，属性与记忆按既有结算应用 |

判定分两层，分层依据只有一条：该情形是否需要反应。

| 检查项 | 层 | 命中后 |
|---|---|---|
| 无本地路径 | 悬停 | `ignore()`，无反应 |
| `FILE_DROP_ENABLED = false` | 悬停 | `ignore()`，无反应 |
| 鼠标穿透开启 | 悬停 | `ignore()`，无反应 |
| 忙态（INTERACTING / AUTONOMOUS） | 放下 | 复用文件气泡提示收不下（无动作按钮），写一次性事件 `file_drop_busy`（§6.1） |
| 数量超过 `FILE_DROP_MAX_FILES` | 放下 | 拒收，发 `too_many` 请求 |
| 单文件超过 `FILE_DROP_MAX_FILE_MB` | 放下 | 拒收，发 `too_large` 请求 |
| 文件名命中 `FILE_DROP_DENY_PATTERNS` | 放下 | 拒收，发 `forbidden` 请求 |

规则：**需要反应的判定一律放放下阶段，只有“协议不成立或功能关闭”才在悬停阶段拒绝**。原因是 Qt 的可接受性判定决定事件投递，`dragEnterEvent` 里 `ignore()` 之后不再收到 `dropEvent`；把忙态放进悬停层，`busy` 这一条永远不会触发。放下的处理入口对前三项再检查一次作为防御，命中即 `ignore()`，不产生请求。

姿态不由程序调用动画产生。即时交互模式的感知段已包含动作表（`interact → generate_action_section()`，见 `docs/reference/prompt-blocks.md:45`），LLM 输出的 `Action:` 行由管线统一执行（`pet/agent/pet_agent.py:598-601`），接收与拒收的姿态都由模型在同一次输出里给出。

放下与拒收都不注入常驻事件：hint 已带文件名与类型，同一事实不需要两处注入。只有“用户放下文件后没有选择”需要在后续轮次被想起，因此只有超时写一次性事件。

两种一次性事件（`file_drop_idle` 与 `file_drop_busy`）都必须带 `text`：`note_once_event` 的 kind 不在 `_EVENT_LABELS` 表内时，缺少 text 的条目会被整条丢弃（`pet/brain/context_builder.py:331-334`）。

超时定时器在气泡显示时启动，在按钮点击或气泡收起时取消。点击即取消，因此请求进行中不会写入超时事件，也不存在“事件内容与用户已经选择的事实相反”的状态。定时器只覆盖一种情形：文件已放下、用户未做任何选择。文案按该事实写，带文件名。

### §3.1 文件气泡的生命周期

文件气泡是单例：`PetWindow` 持有一个实例（与 `set_chat_bubble` 等同形的注入方式），每次放下重建内容，不新建窗口。

| 触发 | 行为 |
|---|---|
| 放下通过 | 先隐藏 chat / feed / music 三个气泡，再显示文件气泡并启动超时定时器 |
| 忙态放下 | 复用文件气泡提示收不下：标题「现在忙，先收不下啦」，正文一行文件名清单（最多列 3 个），无动作按钮；不读内容、不发请求，写一次性事件 `file_drop_busy` |
| 按钮点击 | 停止定时器，进入忙态 |
| 取消按钮 | 停止定时器，收起气泡，不读内容、不发请求，不写一次性事件（用户已明确结束本次交互） |
| 12 秒超时 | 收起，写一次 `file_drop_idle` 事件 |
| 抓取桌宠（`mousePressEvent`） | 收起，并入 `pet/ui/pet_window.py:283-294` 的既有隐藏清单 |
| 自主决策或对话交互开始（state 变为 autonomous / interacting） | 收起文件气泡：打开期间未选择的动作已过期，也不打断在跑的脑线程 |
| 程序隐藏桌宠（`hide()`） | 收起，并入 `pet/ui/pet_window.py:606-618` 的既有隐藏清单 |
| 非忙态再次放下 | 复用实例，内容替换为最新一次拖入，定时器重置 |

文件气泡显示期间 `enterEvent` 不显示 chat / feed / music：三个悬空气泡与文件气泡位置相邻，共存会互相遮挡并争抢鼠标。文件气泡收起后恢复原有悬停行为；想立刻恢复可以点一下桌宠（`mousePressEvent` 会收起文件气泡）或点气泡标题行的「取消」。

忙态是脑线程占用：INTERACTING（请求已发出未返回）或 AUTONOMOUS（自主决策进行中），判定在 `pet/ui/pet_window.py:431-436`。拖入不打断自主轮；自主触发的觅食播报不受影响，它由吃到食物的动作触发，此刻状态已回 IDLE。气泡打开但未点按钮不算忙态。

`_trigger_interact` 的冷却按 hint 计数，挂在通道上默认生效（15 秒），调用方可覆盖：`cooldown_ms` 为 0 表示不冷却。文件动作、投喂、觅食、工具播报都传 0：重复交付同一份东西各触发一次，不是异常。抓取、放下、窗口消失沿用默认值，靠冷却合并抖动重放。忙态不走这条通道，与冷却无关。

## §4 内容通道

### §4.1 可读判定

体积、数量与拒绝名单在放下阶段判定（§6）；类型嗅探在放下阶段完成（决定 `kind` 与工具动作是否可用），解码与截断发生在读取阶段，即用户选定动作之后。规则按顺序如下：

1. 目录直接记为 `kind = "dir"`，不进入解码链（§4.6）；
2. 后缀命中图片集先判 `image`：PNG、JPEG 的文件头含 `\x00` 与高位字节，先走文本判定会把图片误判成二进制；
3. 读取文件头 8 KB，带 BOM 且解码成功判 `text`；
4. 无 BOM 时含 `\x00` 字节，或控制字符占比超过 10%，判 `binary`；
5. 其余按 §4.2 解码，成功判 `text`，失败判 `binary`。

二进制与不可读文件仍可被拖入，只传文件名、类型与大小。后缀不参与“是否可读”的判定，代码文件无需逐个登记。

### §4.2 解码链

仓库现有实现只按 UTF-8 读取，中文 Windows 下的 GBK 文本与 PowerShell 重定向产生的 UTF-16LE 会直接抛 `UnicodeDecodeError`。只用标准库，不引入字符集检测依赖。规则分两条：

**带 BOM 时信任 BOM**：`EF BB BF` 用 `utf-8-sig`，`FF FE` / `FE FF` 用 `utf-16`。

**无 BOM 时 `utf-8` 与 `gb18030` 都尝试，按文本合理性打分取高者，同分取 `utf-8`**。合理性 = 常见字符（可打印 ASCII、空白、中日韩与全角标点）占比。两步都不能省，两条都是实测出来的：

| 反例 | 现象 |
|---|---|
| 无 BOM 数据直接交给 `utf-16` | Python 的 `utf-16` 不会因缺少 BOM 报错，而是按本机字节序硬解。GBK 的「中文内容」会被解成 `\ud0d6\uc4ce\U000c11c8` 一类的汉字与韩文，任何二进制都可能被它解成合法文本 |
| `utf-8` 先到先得 | GBK 的两字节序列可能恰好落在 UTF-8 的两字节区间。「一」的 GBK 字节是 `d2bb`，`bytes.decode("utf-8")` 成功并通过校验，解出的是西里尔字母 `һ` |

带 BOM 的 UTF-16LE 文本含 `\x00`，因此 BOM 判定必须先于 §4.1 的 NUL 与控制字符判定，否则 UTF-16 文本会被当成二进制。

### §4.3 截断与额度

单个文件的读取上限是 `FILE_DROP_MAX_CHARS`（默认 1500），两种情形分开：

| 情形 | 进入 prompt 的文本 |
|---|---|
| 长度 ≤ 上限 | 原文，不加标记 |
| 长度 > 上限 | 首 `2/3 额度` + `……（已省略 N 字符）……` + 末 `1/3 额度`，`N = 总长度 - 额度`，标记不计入配额 |

默认额度 1500 对应首 1000 与末 500；额度被调小时按同一比例收窄，尝一口的 300 对应首 200 与末 100。

首尾分配只在超限时启用，两段不会重叠：首尾之和等于额度，重叠的前提是长度小于额度，而该情形走原文路径。首尾各取一段的依据是日志的有效信息在尾部、代码在头部。

额度按文件独立计算，不共享：一次拖入 n 个文件，总额上限是 `n × FILE_DROP_MAX_CHARS`，文件数由 `FILE_DROP_MAX_FILES` 封顶，最坏情形 5 × 1500 = 7500 字符。共享池会让多拖一个文件即稀释已有文件的额度，用户无法预期，故不采用。

尝一口路径的读取上限是 `FILE_DROP_TASTE_CHARS`（默认 300），沿用同一套首尾规则与独立额度，配置值受 `FILE_DROP_MAX_CHARS` 约束。

### §4.4 图片通道

后缀命中图片集（`.png`、`.jpg`、`.jpeg`、`.webp`、`.gif`、`.bmp`、`.tiff`、`.ico`）时走两道闸门：体积闸门 `FILE_DROP_MAX_FILE_MB`（默认 10 MB）在放下阶段判定；像素闸门 `FILE_DROP_MAX_PIXELS`（默认 40000000）在读取阶段判定，判的是 `draft` 之后的尺寸。取值是十进制像素个数，不是 MiB。

顺序是先 `draft` 后判像素。`Image.draft()` 对支持降采样的格式在解码阶段就输出小尺寸，不支持的格式是空操作（`im.size` 不变），因此不需要按格式分支：

```python
im = Image.open(path)          # 只读文件头
im.draft("RGB", (1024, 1024))  # 支持降采样的格式降到 1/2、1/4、1/8；其余格式无操作
if im.size[0] * im.size[1] >= config.FILE_DROP_MAX_PIXELS:
    return meta_only(im.size)  # 降级为元信息，不解码
```

判据取 `>=`，8000×5000 = 40000000 正好落在降级侧，闸门放行的是小于四千万像素的图片。

`draft` 是否生效由格式决定，与 mode 无关：本机对 RGB、L、CMYK 三种 8000×5000 JPEG 实测，`draft("RGB", (1024, 1024))` 之后尺寸均降到 2000×1250，不存在“mode 不匹配即空操作”的情形。

`draft` 取的是满足长边不小于 1024 的最小比例（1/2、1/4、1/8）：实测 4000×4000 的 JPEG 降到 2000×2000，48 MP 的手机直出照片降到千级像素。闸门放在 `draft` 之后因此有两个后果：JPEG 直出照片不受闸门影响，默认值不会误伤；闸门实际约束的是 PNG、GIF 这类无 draft 的格式，需要更省内存时下调该值即可。表中 40 MP 那一行是默认判据下的边界样本，会被闸门降级、不会走到解码，它的 +153 MB 描述的是下调闸门之后才会出现的峰值量级。

像素闸门不能省，本机实测（Pillow 12.2.0，RSS 由 `psutil` 读取）：

| 文件 | 磁盘体积 | 像素 | `Image.open` | 解码 | 解码 RSS 峰值 |
|---|---|---|---|---|---|
| PNG 3000×2000 噪声 | 14.5 MB | 6 MP | 0.6 ms | 86 ms | +23 MB |
| PNG 8000×5000 单色 | 0.1 MB | 40 MP（默认判据下的边界值） | 0.4 ms | 62 ms | +153 MB |
| PNG 16000×10000 单色 | 0.5 MB | 160 MP | 0.4 ms | 259 ms | +611 MB |
| PNG 14000×14000 单色 | 0.6 MB | 196 MP | 抛 `DecompressionBombError` | - | - |

三条结论：磁盘体积与解码内存无关，0.1 MB 的文件可以解出 153 MB，体积闸门挡不住压缩炸弹；Pillow 自带阈值只在超过 2 × `MAX_IMAGE_PIXELS`（178,956,970 像素）时抛错，89 MP 至 179 MP 区间仅发 `DecompressionBombWarning` 且照常解码，“解码失败即降级”覆盖不到该区间；`Image.open` 只读文件头，耗时毫秒级，可作为事前闸门。

`Image.open` 的异常出口只有一档：像素数超过 2 × `MAX_IMAGE_PIXELS` 时抛 `DecompressionBombError`，此时 `im.size` 拿不到，降级产出只有文件名、后缀与体积。`MAX_IMAGE_PIXELS` 到两倍之间 Pillow 只发警告并照常打开，像素闸门按同一上限自行拒绝，不改动进程级的 `warnings` 过滤器（解码在后台线程，`warnings.catch_warnings` 影响的是全局状态）；闸门命中时像素数已知，可以一并带出。

`draft` 的收益实测：40 MP JPEG 的 RSS 峰值从约 150 MB 降到 10 MB（解码结果 2000×1250）。通过闸门的图片统一缩放到长边 1024，动图取首帧。

编码交 `prepare_image(image=...)`（`pet/agent/screen_reader.py:60-87`）。`VISION_ENABLED` 关闭或模型不支持多模态时（`pet/brain/llm_client.py:169`），图片按 §4.5 处理：附件不编码、后台也不解码；整批只有图片时核心动作置灰，文案为「视觉通道已关闭」，同批还有可读文本时动作照常，只是图片部分降级为元信息。

### §4.5 元信息

只传名与类型的情形包含：二进制与不可读文件、超限图片、视听格式、压缩包、Office 文档、PDF。元信息字段为文件名、后缀、大小、修改时间。

### §4.6 目录

`mimeData().urls()` 不区分文件与目录，两者同路径进入。目录的分类结果为 `kind = "dir"`，只读一层：条目计数上限 200，展示前 20 个名字，不递归、不计算体积，因此也不存在软链接成环与遍历耗时问题。条目摘要在气泡显示时由后台线程读取，网络路径或无响应盘符不阻塞界面。

目录不进入解码链，核心动作按元信息演出，`accepts` 为 `"text"` 或 `"image"` 的工具动作不出现。分类与体积、名单判定同在 `pet/file_intake` 的判定层完成，UI 不做类型分支。

## §5 动作集与通道映射

| 按钮 | 通道 | 读取 | 产出 |
|---|---|---|---|
| 尝一口 | `agent.trigger("interact", hint=interact_take_a_bite_prompt(...), record_context=True, context_hint=...)` | 文本 `FILE_DROP_TASTE_CHARS`（默认 300）；图片走视觉 | 味道的描述 + `Mood:` 增量 |
| 看一看 | `agent.trigger("analyze", message=meta)` | 文本 `FILE_DROP_MAX_CHARS`（默认 1500）；图片走视觉附件，不附当前屏幕 | 摘要或要点，动作可选，`Memory:` 行按需产出 |
| 工具动作 | 工具注册的处理函数，内容不进 LLM | 由工具决定 | 由工具自身播报 |

前两项是核心动作，始终出现，不可用时置灰并在文案后缀标注原因（如「看一看（没有可读文本）」）；第三项来自 §5.1 的扩展点，第一档由知识库工具声明一条“收进知识库”。「取消」不是动作：按钮固定显示在标题行，点击只收起气泡（§3.1）。

核心动作不新增 LLM 调用路径：尝一口与投喂同构，看一看复用对话装配（历史、上下文池、记忆都保留）但走独立的 `analyze` 任务段，动作数量不强制（`pet/brain/prompts.py` 的 `_analyze_task`）。

一次拖入多个文件时，核心动作作用于全部文件，合成一次请求：提示词中每个文件一条元信息与片段，每个文件各自用满 §4.3 的额度；工具动作按 §5.1 的约定，一次调用传入全部文件。附件通道一次只带一张图片：多张图里取第一张可解码的，其余图片只按元信息参与提示词。

属性变化由 LLM 输出 `Mood:` / `Vitals:` 行，字段限于 `affection`、`joy`、`sanity`、`satiety`、`energy`（`pet/brain/parsing.py:37-39`）。投喂与尝一口在模板层分工：`INTERACT_FED_PROMPT` 专注生理参数（satiety/energy 的数值规则），`INTERACT_TAKE_A_BITE_PROMPT` 专注味道的想象与心理方向（Mood 的数值规则），并明确不输出 Vitals 行；数值规则都写在模板里，不在 drop 处理代码中直写。

看一看只改心理不改生理：`_analyze_task` 注入 `_MOOD_GUIDE` 与工具 aside，不注入 `_VITALS_GUIDE`，因此它不产生 `Vitals:` 行（与「看一看不是进食」一致）。尝一口与拒收走 `interact` 任务，两个指南都在系统段；但尝一口的 hint 明确不要 Vitals 行，拒收的增量只有 sanity/joy，两条路径实际都不产生生理变化。

图片与正文都只进当轮 messages，不落盘；各路径写入上下文池、聊天历史与记忆库的范围见 §5.2。

正文经新增的附件参数装配进当轮 messages：`build_interact` 增加附件参数（当前为纯文本），`build_chat_decide` 增加附件参数并支持关闭截图（当前图片只来自 `_prepare_image`，`pet/brain/context_builder.py:57-64`），`build_analyze_decide` 默认不带截图：分析只看交付物，图片只从附件通道进来。历史与摘要本来就取不到图片（`context_builder.py:265-267` → `pet/brain/base.py:230-245`），正文走同一条边界。对话通道在同一轮存在附件图片时不再附加截图，避免同时出现两张图。

进 prompt 的正文与进持久化的正文不是同一份：元信息进持久化，正文只在当轮。代价是有意接受的：下一轮用户追问“刚才那个文件里说了什么”时，历史里只有元信息，模型看不到正文，只能凭元信息与自己上一轮的回复作答。

另有一条绕行路径要堵：桌宠的回复本身也会落库（`pet/agent/pet_agent.py:584-586`、`:593`）。analyze 段要求总结要点而不摘录原句：用户段第 3 步「用符合人格的话说出来，不要复述原文」与 `_analyze_task` 第 2 条「不逐句复述、不整段引用原文」，避免正文经回复二次进入持久化。

记忆只在看一看路径产生：即时交互模式的核心规则明令禁止输出 `Memory:` 行（`pet/brain/prompts.py:237`），尝一口与拒收都不会写记忆；分析任务的 `Memory:` 行由 LLM 决定、agent 无条件保存（`pet/agent/pet_agent.py:589-630`），撤回入口是记忆管理窗口的多选删除（`pet/ui/memory_window.py:732-752`）。`_analyze_task` 的第 6 条约束记忆只写“用户交付了什么”，不写对象里的内容。

### §5.1 工具声明的文件动作

知识库是可选工具：`TOOLS_ENABLED` 未包含它时，加载器不会导入该模块（`pet/tools/__init__.py:133-135`），加载本身也在后台线程异步执行（`:158-173`），右键菜单可随时关闭它（`pet/ui/pet_window.py:421-423`）。核心代码直接引用 `KnowledgeStorage` 会让 UI 依赖可选工具，并把工具私有依赖拉进 UI 线程。

扩展点与既有 `add_menu_action` 同构（声明侧 `pet/tools/registry.py:57-62`，消费侧 `pet/ui/pet_window.py:411-431`）：

```python
registry.add_file_action(
    tool_name, action_id, label, handler, accepts="text",
)
```

| 项 | 约定 |
|---|---|
| 声明位置 | 工具在自己的 `register()` 内声明，此时已持有可用实例 |
| 入参 | `handler(files: list[FileRef])`，`FileRef` 含 path、kind、suffix、size |
| 调用次数 | 一次点击调用一次，传入全部文件；工具自行循环处理，并在结束后播报一次汇总，不逐文件播报 |
| 返回值 | `{"ok": bool, "summary": str}`，失败时 `summary` 作为降级提示 |
| `kind` 取值 | `text`、`image`、`binary`、`dir`，由核心的读取判定产生 |
| `accepts` 取值 | `text`（只要 `kind = "text"`）、`image`（只要 `kind = "image"`）、`any`（接受全部，含 `binary` 与 `dir`）；不声明等价于 `any` |
| 渲染 | 文件气泡按已注册且启用的工具声明的动作逐条渲染按钮；`accepts` 与 `kind` 不匹配，或 `FILE_DROP_READ_CONTENT` 关闭且动作声明需要内容时，动作不出现 |
| `needs_content` | 默认 `true`；置为 `false` 的动作不随 `FILE_DROP_READ_CONTENT` 隐藏，供只需名称与路径的动作使用 |
| 刷新 | 气泡可见期间按 1 秒间隔重读注册表并更新按钮，复用跟随定时器；工具加载完成后不需要再次拖入，也不引入注册表事件机制 |
| 缺失场景 | 工具未注册（`TOOLS_ENABLED` 排除或未安装）、被右键菜单关闭、或仍在后台加载时，按钮不出现；核心不做存在性检查，也不写 try/except 分支 |
| 边界归属 | 体积、数量、拒绝名单、可读判定仍由核心执行，handler 只在文件通过判定后收到 `FileRef` |
| 全文读取 | 工具用 `pet/file_intake` 的读取实现取全文，方向为 tools 依赖核心 |
| 播报 | 工具在完成后经 `TOOL_CTX.request_interact(hint=...)` 或 `TOOL_CTX.speech(...)` 播报，核心不代写台词。`request_interact` 透传 `delay_ms`、`cooldown_ms`、`thinking`、`enable_tools`（`pet/tools/context.py`） |
| 私有访问 | 动作列表由新增的公开查询方法提供，不复用 `TOOL_REGISTRY._tools`。（`tests/test_architecture_contracts.py:1019-1020`），新增同类访问会失败 |

扩展点对任何工具开放，核心只负责渲染动作与执行边界判定。

### §5.2 上下文池的写入与回忆

三条动作通道的结果经 `_on_brain_result` 统一结算（`pet/agent/pet_agent.py:602-666`）：assistant 台词与 Summary 各写一条上下文池，台词落聊天历史（`conversation_store` 的 pet 行），`Memory:` 行后台写入记忆库，`Mood:` / `Vitals:` 增量应用到状态。各路径写入上下文池的范围：

| 路径 | 池 user 行 | 池 assistant 行 | 聊天历史 pet 行 | 记忆库 | 数值 |
|---|---|---|---|---|---|
| 尝一口 | 元信息（`context_hint`，`record_context=True`） | 台词 + Summary | 台词 | 不写（interact 禁止 `Memory:` 行） | Mood |
| 看一看 | 元信息（`message`，`log_message` 缺省取 `message`） | 台词 + Summary | 台词 | `Memory:` 行由模型决定 | Mood |
| 工具动作播报 | 不写（`record_context=False`） | 台词 + Summary | 台词 | 不写 | 无 |
| 拒收 | 不写（同上） | 台词 + Summary | 台词 | 不写 | Mood（sanity/joy） |

回忆规则：

- 上下文池只在 chat 与 analyze 的请求里拼进 messages（`get_multi_turn_messages`，条数与 token 预算裁剪，摘要以 system 备注合并进主 system）；interact 单轮（`build_interact` 只有 system 与 user 两条）不读池。
- 池持久化到 db，重启后恢复；`conversation_store` 只供聊天历史窗口展示，不进 prompt。
- 正文任何路径都不进池；全文唯一的持久化是工具动作的知识库入库，之后经 RAG 检索进入上下文。
- 拒收与工具播报不写 user 行：下次对话模型只看得到桌宠当时的台词，看不到「用户拖过文件」这一事实。
- 工具动作的返回 `summary` 经 `TOOL_CTX.speech` 气泡直出，不进 LLM，也不进池与历史。
- 忙态（INTERACTING / AUTONOMOUS）放下不发请求：写一次性事件 `file_drop_busy`，在下一轮 `[最近发生了什么]` 注入一次后清除。

## §6 拒绝与降级规则

| 情况 | 判定时机 | 行为 |
|---|---|---|
| 单文件大于 `FILE_DROP_MAX_FILE_MB` | 放下 | 拒收，快速请求类型 `too_large` |
| 一次多于 `FILE_DROP_MAX_FILES` 个 | 放下 | 拒收，快速请求类型 `too_many` |
| 文件名命中 `FILE_DROP_DENY_PATTERNS` | 放下 | 拒收，快速请求类型 `forbidden`，不进入内容通道 |
| 忙态中再次拖入 | 放下 | 拒收，写一次性事件 `file_drop_busy`；不发即时请求，原因见 §6.1 |
| 无本地路径（纯文本或网络碎片） | 悬停 | `ignore()`，不产生请求；拖放协议不成立，事件不会送达放下阶段 |
| 目录 | 读取 | `kind = "dir"`，只读一层（§4.6） |
| 二进制或解码失败 | 读取 | 降级为元信息，回复中说明读不出内容 |
| 图片解析失败：`DecompressionBombError`、像素超闸门、解码失败 | 读取 | 降级为元信息；`Image.open` 阶段失败的像素数未知，其余带上尺寸 |
| 文本超过读取上限 | 读取 | 按 §4.3 截断，不属于异常 |
| 文件被占用、无读权限 | 读取 | 降级为元信息 |
| 工具文件动作失败 | 执行 | 按返回的 `summary` 提示；核心动作与其他工具动作不受影响 |
| 穿透模式开启 | 悬停（放下入口复检） | 事件入口显式检查穿透并 `ignore()`，不产生请求；平台层面事件可能根本不投递，代码不依赖这一点（§10），关闭穿透后无需重启即可恢复 |

放下阶段只拦硬条件（数量、体积、拒绝名单、忙态）；解析类失败一律降级，且提示文案出现在文件气泡或回复中，不静默。

拒绝名单按文件名做 glob 匹配，不局限于后缀：`.npmrc`、`.netrc`、`.git-credentials`、`id_rsa` 这类无扩展名的点文件用后缀表拦不住。匹配取 `os.path.basename` 后 `casefold()`，再交给 `fnmatch.fnmatchcase`；不用 `os.path.normcase`，它在 POSIX 上是恒等操作，那样写出来的大小写不敏感只在 Windows 成立。

### §6.1 拒收的快速请求

放下阶段判定失败时，三类拒收按类型与涉及的文件名发起一次即时交互请求，调用形态与觅食一致（`pet/food/food.py:365-380`）；`busy` 不走这条通道，见本节末尾：

```python
agent.trigger(
    "interact",
    hint=interact_file_reject_prompt(reason, names),
    delay_ms=150,
    record_context=False,
    is_play_loading=False,
    thinking=False,
    enable_tools=False,
)
```

| 项 | 约束 |
|---|---|
| 请求形态 | 无工具、无思考、不播放加载姿态、不写对话上下文，四项与觅食一致 |
| hint 变化维度 | 按拒收类型与涉及的项名变化：`too_large`、`too_many`、`forbidden` 各自带上项名，模板里最多列 3 个，超出补总数。`busy` 不走本通道，原因见下 |
| 重复请求 | 文件动作传 `cooldown_ms=0`，不参与通道冷却：同一批文件重复拖入各触发一次。界面侧由按钮防抖兜住一次物理点击内的连击（`pet/ui/debounce.py`） |
| 互动反应开关 | 不读取 `_event_reaction`。该开关默认关闭（`pet/ui/pet_window.py:169`），只覆盖抓取、放下、窗口消失三处；拒收与投喂（`pet/app.py:163-167`）同侧，都由用户主动交付触发 |
| 姿态与动作 | 代码不指定姿态，但拒收请求必然产生动作：`_interact_task()` 强制输出 1 至 2 个 `Action`（`pet/brain/prompts.py:218-239`“只输出 1-2 个 Action”，动作名从动作表选取），由管线执行并进入动作队列（`pet/agent/pet_agent.py:598-601`），因此拒收可能打断当前动画，与抓取、放下同类 |
| 属性变化 | 同样会输出 `Mood:` / `Vitals:` 行，增量规则见下表，模板不写死数值 |
| 台词模板 | `interact_file_reject_prompt(reason, names)` 与配置键 `INTERACT_FILE_REJECT_PROMPT`，自定义模板的占位符是 `{reason}` 与 `{names}`，形式与 `interact_fed_prompt` 一致（`pet/brain/prompts.py:412-425`） |

忙态拒收不走即时请求：即时交互与聊天两条通道在 `INTERACTING` 状态下都会丢弃请求（`pet/agent/pet_agent.py:276-279`、`:356-358`），发出去也会被扔掉。忙态改为 `note_once_event("file_drop_busy", text)`，在下一次构造上下文时注入一次，由桌宠自行提一句；拖放本身在放下阶段被拒，不弹气泡、不读内容。

即时交互模式的任务段包含 `_MOOD_GUIDE` 与 `_VITALS_GUIDE`（`pet/brain/prompts.py:241`），三类会发请求的拒收因此也会输出 `Mood:` / `Vitals:` 行，被解析层按白名单应用（`pet/brain/parsing.py:37-68`）。模板按下表限定增量：

| 拒收类型 | Vitals | Mood |
|---|---|---|
| `too_large` | 不变 | `sanity-1~3` |
| `too_many` | 不变 | `sanity-1~3` |
| `forbidden` | 不变 | `sanity-2~5`，`joy-0~2` |

`busy` 不在表内：它写一次性事件，不产生 LLM 输出，因此没有属性增量。

两条不变量：拒收不改 `satiety` 与 `energy`，因为没有进食；拒收不改 `affection`，误拖不构成负面事件，好感只由真正发生的事驱动。拒收的性质是“用户主动交付了不合适的东西”，不是操作失败，表现限于轻微心理不适。

## §7 配置项

| 键 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `FILE_DROP_ENABLED` | bool | true | 拖入交互总开关 |
| `FILE_DROP_READ_CONTENT` | bool | true | 内容读取开关，关闭后所有文件只传元信息 |
| `FILE_DROP_MAX_CHARS` | int | 1500 | 单文件进入 prompt 的字符上限 |
| `FILE_DROP_TASTE_CHARS` | int | 300 | 尝一口路径的字符上限，受 `FILE_DROP_MAX_CHARS` 约束 |
| `FILE_DROP_MAX_FILE_MB` | int | 10 | 单文件体积上限 |
| `FILE_DROP_MAX_FILES` | int | 5 | 单次拖入数量上限 |
| `FILE_DROP_MAX_PIXELS` | int | 40000000 | `draft` 之后的像素上限，判据为 `>=`，取值是十进制像素数（§4.4） |
| `FILE_DROP_DENY_PATTERNS` | str_list | [".env*", "*.key", "*.pem", "*.pfx", "*.p12", ".npmrc", ".netrc", ".pgpass", ".git-credentials", "id_rsa*", "id_ed25519*"] | 拒收名单，按文件名 glob 匹配 |
| `FILE_DROP_BUBBLE_TIMEOUT_S` | int | 12 | 文件气泡无操作收起时间 |
| `INTERACT_TAKE_A_BITE_PROMPT` | str | "" | 尝一口模板覆盖，空值使用内置模板 |
| `INTERACT_FILE_REJECT_PROMPT` | str | "" | 拒收台词模板覆盖，空值使用内置模板 |
| `LLM_MAX_TOKENS_ANALYZE` | int | 4096 | 分析任务的输出上限（`LLM_MAX_TOKENS_*` 一族，与 `LLM_MAX_TOKENS_CHAT` 同级） |

配置项写入 `pet/config.py` 的 `_KEY_META`，并按仓库约定重新生成 `docs/reference/config.md`。

## §8 文件改动清单

| 文件 | 改动 |
|---|---|
| `pet/ui/pet_window.py` | `setAcceptDrops(True)` 恒定开启；四个拖放事件只做转发，`dragMoveEvent` 需接受事件，否则收不到放下（§0.1）；动作语义见 §10；`enterEvent` 在文件气泡显示期间不显示 chat / feed / music；隐藏清单补入文件气泡（`mousePressEvent` 与 `hide()`）；持有文件气泡单例 |
| `pet/ui/file_drop_handler.py`（新增） | 拖放处理层：从 `QMimeData` 取路径、调用 `pet/file_intake` 判定、经回调驱动气泡与请求（忙态出口分一次性事件与气泡提示两个回调）；不依赖 `PetWindow` 实例，可单测 |
| `pet/ui/file_bubble.py`（新增） | 文件气泡：文件名、后缀、大小摘要，核心动作按钮按固定两条渲染，工具动作按钮按注册表快照渲染，不可用的核心动作置灰并标注原因，标题行固定一个「取消」按钮，忙态放下复用窗口提示收不下（无动作按钮），跟随桌宠位置，复用 `music_bubble.py` 的跟随与显示结构；单例复用，超时定时器归它管理；按钮防抖 |
| `pet/ui/debounce.py`（新增） | 按钮与提交框的防抖：一次物理操作只产生一次请求 |
| `pet/file_intake/`（新增包） | 纯逻辑：嗅探、解码链、截断、图片处理、体积与数量校验、拒绝名单、模板参数组装 |
| `pet/tools/registry.py` | `ToolDef` 增加 `file_actions` 字段、`add_file_action(...)` 与公开的查询方法 |
| `pet/tools/knowledge/__init__.py` | 在 `register()` 内声明文件动作，入库在后台线程执行 |
| `pet/tools/context.py` | `request_interact` 透传 `thinking` 与 `enable_tools`（见 §5.1） |
| `docs/tool-development.md` | 登记文件动作扩展点 |
| `pet/brain/prompts.py` | 新增 `analyze` 感知段与 `_analyze_task`、`analyze_*_user_prompt`、`interact_take_a_bite_prompt(...)`、`interact_file_reject_prompt(reason, names)` 与内置模板 |
| `pet/brain/context_builder.py` | `build_analyze_decide` 与 `build_interact`、`build_chat_decide` 的附件参数 |
| `pet/brain/behavior.py` | 新增 `analyze_decide_stream` |
| `pet/agent/pet_agent.py` | 文件读取与工具动作在后台线程执行；`trigger("analyze")` 与 chat 共用 `_dialogue_pipeline`，正文不进 `message` 与 `context_hint`；`log_message` 决定进历史与上下文池的那一份；超时事件走 `note_once_event` |
| `pet/config.py` | 新增 §7 的配置键 |
| `pet/ui/__init__.py` | 模块说明补充文件气泡 |
| `docs/architecture.md` | 模块职责表登记新包（`tests/test_docs.py` 要求覆盖全部包） |
| `docs/README.md` | 登记本文档 |
| `tests/test_file_intake.py`（新增） | 纯逻辑单测 |
| `tests/test_file_drop_handler.py`（新增） | 拖放判定层单测，不构造 `PetWindow`，见 §9 |

`pet/file_intake/` 保持纯逻辑，不顶层导入 Qt（`ARCH005` 规则），Qt 相关代码留在 `pet/ui/file_bubble.py`。

## §9 验证方式

系统事件投递的验证结论见 §0.1：拖放能收到，`dragMoveEvent` 不接受则收不到放下。穿透模式的拖放行为未人工确认，由手动清单第 9 条覆盖。

自动化：

```bash
python -m pytest tests/test_file_intake.py tests/test_file_drop_handler.py tests/test_file_bubble.py tests/test_ui_debounce.py tests/test_app_file_actions.py tests/test_knowledge_file_action.py
python -m pytest tests/test_architecture_contracts.py tests/test_docs.py
python scripts/gen_docs.py --check
```

`tests/test_file_intake.py` 覆盖纯逻辑：文本嗅探（UTF-8、GBK、UTF-16LE、含 NUL 的文件）、截断的两条路径与省略计数、独立额度、体积与数量上限、拒绝名单的 glob 匹配、像素闸门、目录分类、工具文件动作的注册与查询（只测注册表，不加载具体工具）。

`tests/test_file_drop_handler.py` 覆盖判定层，不构造 `PetWindow`：`PetWindow` 依赖 agent、动作队列、素材加载与屏幕探测，offscreen 下构造它等于为几行判定拉起大半个应用。判定因此住在 `pet/ui/file_drop_handler.py`，只依赖路径列表、配置与四个回调（显示气泡、发拒收请求、写一次性事件、忽略），测试直接喂 `QMimeData` 或路径列表，断言回调序列：无路径不回调、悬停三项在悬停层被拒、忙态只写事件不发请求、三类硬边界各自触发对应类型、通过时调用显示气泡。`tests/conftest.py:23` 已设 `QT_QPA_PLATFORM=offscreen`，`QMimeData` 可直接构造（该处是 `setdefault`，外部已设同名变量时沿用外部值）。`PetWindow` 上的事件转发与操作系统的事件投递不在这一层覆盖，由 §0.1 的验证与手动清单负责。

`tests/test_app_file_actions.py` 覆盖装配层：`pet/app.py` 只在进程入口被导入，此前没有测试触及；用桩 dispatcher 触发核心动作与工具动作两条分支，断言后台线程启动、正文送达、工具动作的返回值与异常都变成用户可见的提示。

`tests/test_file_bubble.py` 覆盖气泡的动作可用性：视觉关闭时只有图片的批次置灰并标注「视觉通道已关闭」，混合批次保留文本动作；工具动作按 `accepts` 与内容开关出现或消失；渲染出的按钮与可用性同步（不可用项 `disabled` 且带置灰样式），「取消」在所有动作都不可用时仍可用，点击只收起气泡、不触发动作。

`tests/test_knowledge_file_action.py` 覆盖知识库文件动作：播报的 hint 只列成功入库的文件名，读不出内容的文件不出现也不触发请求。

手动检查清单：

1. 拖入 UTF-8 的 `.md` 与 GBK 的 `.txt`，看一看均能取到内容；
2. 拖入图片，`VISION_ENABLED` 开启时能按内容反应，关闭时按钮置灰且说明原因，置灰与可用按钮肉眼可区分；
3. 拖入 `.exe`，看一看按钮置灰并标注「没有可读文本」，尝一口只念文件名，回复说明读不出内容；
4. 拖入 11 MB 文件，放下时拒收，桌宠用一句台词回应；
5. 一次拖入 6 个文件，拒收一次并回应一次；
6. 连续拖入同一个超限文件两次，两次都被拒收并各回应一次；快速连点气泡按钮只触发一次请求；
7. 拖着文件在窗口上反复划过再移开，不产生任何请求与动画；
8. 拖入后 12 秒不操作，气泡收起且不产生 LLM 请求，下一轮上下文出现一次性事件；
9. 托盘开启鼠标穿透后拖入无效，关闭后恢复；
10. 拖入 `.env` 与 `.npmrc`，两次都被拒收，且各回应一次；
11. 拖入约 0.2 MB 的 8000×8000 单色 PNG（64 MP），按像素闸门降级为元信息，界面不卡顿；再拖入一张 48 MP 的 JPEG，走 `draft` 正常送进视觉通道；
12. 拖入一个目录，只显示条目摘要，不出现需要内容的动作；
13. 收进知识库后，知识库面板可见该文档，来源为 `file_drop`；
14. 文件正文不落盘：造一个含唯一标记串的文本文件，看一看之后在聊天历史窗口与 `pet.db` 里搜索该标记，均无命中，而当轮日志能看到正文已装配；
15. 从 `TOOLS_ENABLED` 排除 `knowledge` 后启动，拖入文本文件，气泡只出现两个核心动作；
16. 右键菜单关闭知识库工具后再次拖入，该动作不再出现，核心动作正常；
17. 启动后立即拖入（工具仍在后台加载）界面不卡顿，气泡保持打开时按钮自行出现；
18. 忙态：看一看进行中再拖入一个文件，不产生新请求，气泡显示忙态提示（无动作按钮）；自主决策进行中拖入同样按忙态处理，且不打断自主行为；随后某一轮上下文出现 `file_drop_busy`；等请求结束后再拖入，气泡内容替换为最新一次拖入；
19. 文件气泡的收起路径：拖入后抓起桌宠、从托盘隐藏桌宠、自主决策触发、点标题行的「取消」，气泡均随之消失；取消只收起气泡，不产生请求，下一轮上下文不出现 `file_drop_idle`；气泡显示期间悬停桌宠，不出现 chat / feed / music。

## §10 风险与约束

- **穿透模式**：托盘开启鼠标穿透后（`pet/ui/pet_window.py:598-619`），`WS_EX_TRANSPARENT` 或 `WA_TransparentForMouseEvents` 会让窗口在命中测试中被跳过，事件可能根本不投递。代码不依赖这一点：事件入口显式检查穿透状态并 `ignore()`，穿透关闭后无需重启即可恢复接收。
- **提示词注入**：文件内容进入 prompt 通道，等价于把不可信文本交给模型。模板中需要固定约束：文件内容视为“物品”而非指令来源。
- **内存峰值**：二进制嗅探只读头部 8 KB；图片按 §4.4 的像素闸门与 `draft` 路径处理，不依赖体积上限，也不做全量读入。
- **工具加载窗口**：工具加载在后台线程进行（`pet/app.py:126`、`pet/tools/__init__.py:158-173`），启动后数秒内注册表可能为空。气泡可见期间按 1 秒间隔重读注册表，加载完成后按钮自行出现，不需要重新拖入，也不引入等待与轮询。
- **长期记忆的写入与撤回**：“看一看”走聊天管线，`Memory:` 行由 LLM 决定并自动保存，属于既有行为而非本设计新增。撤回入口是记忆管理窗口的多选删除（`pet/ui/memory_window.py:732-752`）；模板约束记忆只描述用户交付了什么，不摘录文件正文。
- **平台事件投递**：Windows 下提权进程收不到非提权资源管理器的拖放（UIPI），表现是拖入完全无响应。先用一个同类探针区分“窗口形态不支持”与“进程权限不匹配”（§0.1）。
- **拖放动作语义**：三个拖放事件都不使用 `acceptProposedAction()`。Windows 下资源管理器提议 `CopyAction`（§0.1），但 X11/Wayland 的文件管理器在同一挂载点内拖动、或用户按住 Shift 时会提议 `MoveAction`；接受该动作意味着源端按“移动”处理，用户的原文件可能被移走或删除，而本设计从不实际接收文件。统一 `setDropAction(Qt.DropAction.CopyAction)` 后 `accept()`，把语义固定为复制。
- **平台覆盖**：实测只覆盖 Windows；macOS 与 Linux 依赖 Qt 的同一套拖放事件与判定逻辑，没有可用的验证环境，未实测。穿透实现在 Windows 走 `WS_EX_TRANSPARENT`，其它平台走 `WA_TransparentForMouseEvents`（`pet/ui/pet_window.py:620-641`），两条路径都让窗口在命中测试中被跳过；属性自检只覆盖后者（§0.1）。
- **拒绝名单只看文件名**：名单按 basename 的 glob 匹配，拦不住名字无害而内容是凭据的文件（恢复码、密码导出、导出的聊天记录）。第一档只在名字层拦截，不识别内容；需要更强保护时把这类路径写进名单，或后续增加“本次只报名字、不读内容”的单次选项。

## §11 结论

拖入交互的代码面集中在五处：窗口的四个拖放事件转发、一个拖放处理层、一个文件气泡、一个纯逻辑包、一个工具动作扩展点。属性与记忆由 LLM 输出行产出，知识库经扩展点接入，核心不引用任何工具模块；边界规则（体积、数量、拒绝名单、像素闸门、解码失败降级）集中在纯逻辑包内，可脱离 Qt 单测；判定层由 `file_drop_handler` 的单测覆盖，窗口只做事件转发；操作系统的事件投递已由 §0.1 的验证覆盖。
