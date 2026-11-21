# 点阵画工具设计

> 模型负责语义与构图，工具只做确定性栅格化、色彩约束与最近邻导出。一个 `pixel_art` 工具、十一个方法、带名字并落库的画布，加一扇只读窗口让落笔过程对用户可见，零新增依赖。

## §0 背景与现状

| 项 | 现状 | 位置 |
|---|---|---|
| 工具系统 | 目录即工具，`register(registry)` 注册方法，对外名称 `工具名__方法名`；非 `default` 组需经 `tool_search` 激活 | `pet/tools/__init__.py`、`pet/tools/registry.py` |
| 参数定义 | `args` 只支持 `str` / `int` / `float` / `bool` / `list` / `dict` / `any` 七种扁平类型，可带 `default` / `enum` / `required`；`any` 下发为 `string`；无嵌套 schema、无 `oneOf` | `pet/tools/registry.py:911-957` |
| 分组生命周期 | 非 `default` 组的激活态在每轮互动结束时重置，下一轮首次使用前需重新 `tool_search` 激活 | `pet/brain/behavior.py:186-208` |
| 导入期约束 | 模块级只允许常量、类型与注册表登记；实例创建须推迟到 `register()` 或首次使用 | `tests/test_architecture_contracts.py` ARCH009 |
| 参数校验 | 宽松强转后按 `enum` 判定；schema 外的键原样透传 | `pet/tools/executor.py:204-242` |
| 输出契约 | `output` 注册必填；`x-role` 决定当轮渲染与是否进历史，`headline` 进历史且截 120 字符，`body` / `data` / `hint` / `images` 不进 | `pet/tools/output.py`、`docs/specs/2026-10-22-tool-output-projection-design.md` |
| 图片注入 | `images` 字段走 `role="user"` 消息，仅随下一次请求发送一次；宿主无视觉时不注入 | `pet/brain/tool_loop.py:142-171` |
| 并行执行 | 同一轮工具调用默认并行；名称以 `game__` 开头时退化为串行 | `pet/brain/tool_loop.py:283-287` |
| 有状态工具 | 框架不提供会话对象，模块级单例加懒创建是既有模式（`file_ops`、`timer`） | `pet/tools/file_ops/__init__.py:42-50` |
| 数据库 | 统一 `pet.db`（WAL 加 `busy_timeout`），各工具自建表，用 `CREATE TABLE IF NOT EXISTS` 加 `datetime.now().isoformat()` 文本时间 | `pet/db.py`、`pet/tools/timer/storage.py:15-43` |
| 工具驱动 UI | `TOOL_CTX.game_board` 经 Qt 信号弹出无边框面板，定位在桌宠右侧，可拖动 | `pet/tools/context.py:140-148`、`pet/ui/game_panel.py`、`pet/app.py:307-332` |
| 依赖 | `Pillow>=10.0.0`、`numpy>=1.24.0` 已在主依赖 | `pyproject.toml:26-27` |
| 绘图代码 | 全仓无 `ImageDraw` / `ImageFont` 使用，无任何栅格绘图实现可复用 | 无 |
| 素材规格 | 宠物帧为透明 webp 加同名 json，与像素画 PNG 不同 | `docs/subsystems/assets-pipeline.md` |

外部输入是一份单张像素画工具的设计稿：七个独立工具、像素矩阵与图元算子两套落笔、返回值硬限 50 tokens、32 与 64 档以算子铺面、矩阵只做局部精修。本文是该设计在本项目工具体系下的落地版本，保留其栅格语义与成本取向，改造掉不成立的接口假设。

## §1 目标

- 新增 `pixel_art` 工具，分组 `creative`，十一个方法覆盖建画布、打开与列举改名删除、落笔、后处理、自检、导出、色板。
- 画布带名字，创建时指定；画布数据落 `pet.db`，重启后可继续编辑。
- 画布尺寸锁死 `16` / `32` / `64` 三档正方形，导出统一为 256×256 整数倍，全程最近邻，无插值、无抗锯齿。
- 有限色板：预设 `pico8`（16 色）、`gameboy`（4 色）、`nes`（16 色）、`db32`（32 色），或自定义不超过 32 色；板外颜色拒绝并给出最近索引。
- 单次成图闭环：`create` → `paint` / `set` → `preview` → 局部修正 → `export`。
- 绘画过程对用户可见：每次写入成功后只读画布窗口即时更新，落笔逐步出现，无需用户主动刷新。
- 写操作返回 `revision` / `bbox` / `changed_cells`，不返回像素数据；文本矩阵与图片只经 `preview` 按需取。
- 零新增依赖，实现只用 `Pillow`、`numpy` 与标准库。

## §2 非目标

- 不做独立进程：工具在本进程内运行，与其余工具同构，不引入额外的传输层或守护进程。
- 不做黑盒 `generate(prompt)`：语义到图元的规划留在模型侧，工具不含任何生成模型。
- 不做动画帧、多帧、图层、多画布协作；画布各自独立，不共享图层与历史。
- 不做版本历史与撤销栈：只保留 `revision` 与当前像素，旧画面不落库。
- 不做文字渲染、图标贴图库：`text` 与 `stamp` 需要字体与素材资源，收益低于成本，落笔词汇表不含这两项。
- 不在窗口里绘制：不接受鼠标落笔、拖拽改色、撤销重做，窗口只读。
- 不做多窗口：同一时刻只有一个画布窗口，切换画布经 `open` 换内容。
- 不做像素画到宠物动作素材（透明 webp 加 json）的转换，该规格另属素材管线。
- 不读取外部参考图做自动描摹；参考图转像素不在本工具职责内。

## §3 与原始设计稿的差异

| 原始设计 | 落地方案 | 原因 |
|---|---|---|
| 七个独立工具 | 一个 `pixel_art` 工具加十一个方法，调用名 `pixel_art__create` 等 | 本项目的工具单位是「目录」，方法共享分组与启用开关，七工具等于七个目录与七次 `tool_search` 发现；除原有的七项外增加 `open`、`list`、`rename` 与 `delete` |
| `paint.ops` 用 `oneOf` 区分图元 | `ops` 声明为 `list`，op 词汇表与字段写进方法 `description`，示例给两条 | 参数层不支持 `oneOf` 与嵌套 schema；执行器对 `list` 内的 dict 原样透传，由 handler 校验 |
| 返回值硬限 50 tokens | `headline` 一句话并固定携带 `revision` 与填充度，`bbox` / `changed_cells` 放 `data` | `x-role` 已保证 `data` 不进历史，无需数值硬限；`headline` 是唯一跨轮可见的字段，历史截断 120 字符 |
| 像素经旁路通道读取，风格约束单独成文 | 像素经 `export(format="json" / "rle")` 取用，风格约束写进 `TOOL_DESCRIPTION` 与方法描述 | 工具契约只有入参与出参，没有旁路读取通道；工具描述就是模型可见的规格 |
| `preview` 只返回图像 | 文本矩阵为默认形态，图像为可选附加 | 无视觉端点（本地模型常见）下图片不注入，模型失去自检通道 |
| `canvas_id` 多画布 | 画布落库，内存只缓存最近 4 张 | 画布是用户资产，重启后仍要能继续编辑；桌面单人使用，缓存 4 张已够覆盖来回切换的用量 |
| 跨轮会话状态 | 模块级单例，`revision` 乐观锁 | 框架无 session 对象 |
| 工具常驻可见 | `creative` 分组，落笔能力由一次 `tool_search` 换来 | `default` 组只放元工具；激活后同一轮内持续可见，跨轮重置，一次成图的连续调用落在同一轮内 |

## §4 工具契约

### §4.1 方法清单

| 方法 | 职责 | 关键参数 | `data` 返回 |
|---|---|---|---|
| `pixel_art__create` | 新建画布，写入库；名字可重复 | `name`, `size`, `preset`, `palette`, `background` | `canvas_id`, `name`, `revision`, `palette_size` |
| `pixel_art__open` | 按 id 把库内已有画布载入内存并设为当前 | `canvas_id` | `canvas_id`, `name`, `size`, `revision`, `filled_cells` |
| `pixel_art__list` | 分页列出现有画布 | `page`, `page_size` | 无（内容在 `body`，分页在 `pagination`） |
| `pixel_art__rename` | 修改画布名 | `canvas_id`, `name` | `canvas_id`, `name` |
| `pixel_art__delete` | 删除画布 | `canvas_id` | `deleted` |
| `pixel_art__set` | 提交离散像素矩阵，整图或局部 | `canvas_id`, `revision`, `rows` 或 `rle`, `region`, `mirror_x` | `revision`, `bbox`, `changed_cells`, `filled_cells` |
| `pixel_art__paint` | 批量图元与过程式算子 | `canvas_id`, `revision`, `ops`, `mirror_x` | `revision`, `bbox`, `changed_cells`, `filled_cells` |
| `pixel_art__postprocess` | 全图或区域级像素画特效 | `canvas_id`, `revision`, `op`, `region`, `params` | `revision`, `bbox`, `changed_cells`, `filled_cells` |
| `pixel_art__preview` | 自检，返回文本矩阵与可选图片 | `canvas_id`, `mode`, `region`, `scale` | 无（内容在 `body` / `images`） |
| `pixel_art__export` | 渲染为文件 | `canvas_id`, `format`, `path`, `scale`, `transparent`, `grid_overlay` | `path`, `width`, `height`, `bytes` |
| `pixel_art__palette` | 查询、替换、量化 | `canvas_id`, `revision`, `action`, `colors` | `palette_size`, `remapped_cells` |

每个方法返回 `headline`；写方法额外返回 `data`（`bbox` 无变化时为 `null`）。`filled_cells` 是写入后画面中非透明格总数，用于跨轮判断整体进度。

`list` 的输出带上 `pagination` 字段（`PAGINATION_SCHEMA`），由框架渲染成「第 2 页，共 23 张，用 page=3 继续」，并随 `headline` 进历史。超过 50 张时模型知道还有后续页，不会误以为画布只有这一批。

### §4.2 参数声明

| 参数 | 声明 | 说明 |
|---|---|---|
| `name` | `str` | 画布名，去首尾空白后 1-24 字符，允许中文；`create` 与 `rename` 必填；允许重名，只作标识，定位一律用 id |
| `size` | `str`, enum `["16","32","64"]`, required | 与枚举字面量对齐；执行器会把 `32` 强转为 `"32"`，但 `"32 "` 不 strip、`32.0` 变成 `"32.0"`，这类输入报错并回列合法值 |
| `canvas_id` | `str`, default none | 省略时作用于 `current_id`；未知 id 报 `no_canvas` 并列出最近 5 张的 id 与名字 |
| `open` 的目标 | `canvas_id`, required | 不支持按名字打开：名字可重复，无法唯一定位；缺省报 `bad_param` 并列出最近 5 张 |
| `page` | `int`, default 1 | `list` 页码，从 1 起，超出末页时钳到末页 |
| `page_size` | `int`, default 20 | `list` 每页条数，范围 1-50 |
| `revision` | `int`, default none | 省略表示不做乐观锁；传入且不等于当前值时拒绝写入 |
| `preset` | `str`, enum `["pico8","gameboy","nes","db32"]`, default `pico8` | 与 `palette` 互斥，`palette` 优先 |
| `palette` | `list`, default none | 元素为 `#RRGGBB`，长度不超过 32，超出报 `palette_full` |
| `background` | `str`, default none | 索引字符 `0-9a-v` 或 `#RRGGBB`；省略为全透明 |
| `rows` | `list`, default none | 每行一个字符串，字符集见 §6.3 |
| `rle` | `str`, default none | 全图行优先游程，见 §6.3；与 `rows` 二选一 |
| `region` | `list`, default none | `[x, y, w, h]`，四个整数，越界拒绝 |
| `ops` | `list`, required | 元素为 dict，op 取值见 §6.4 |
| `mirror_x` | `bool`, default false | 以画布中线为轴镜像落笔 |
| `op` | `str`, enum | 后处理取值见 §7 |
| `params` | `dict`, default none | 后处理参数，键随 op 而定 |
| `mode` | `str`, enum `["text","image","both"]`, default `text` | `image` / `both` 在无视觉时退化为 `text` |
| `scale` | `int`, default none | 省略时 handler 取 `256 // size`；导出上限 64，`preview` 上限 32 |
| `format` | `str`, enum `["png","svg","json","rle"]`, required |  |
| `path` | `str`, default none | 省略时落桌面，给了就走白名单校验，见 §9 |
| `action` | `str`, enum `["get","set","quantize"]`, default `get` |  |
| `colors` | `list`, default none | `set` / `quantize` 必填 |

`scale` 的默认值依赖画布尺寸，注册期只能写常量，因此在 handler 内计算。参数层注入的 `default` 不参与 `enum` 校验，默认值必须本身就是枚举内的合法值（如 `preset` 的 `pico8`）。

`region` 与 `params` 用 `list` / `dict` 承载，是参数层缺少嵌套 schema 的妥协；handler 内做逐项校验，错误带下标（如 `region 第 3 项应为整数`）。`background` 声明为 `str` 而不是 `any`：`any` 下发给模型时是 `string`，模型看不到整数写法。

### §4.3 返回文案

`headline` 是唯一进历史的字段，因此写方法固定携带 `revision` 与画面填充度：这两个值构成跨轮可用的最小状态，其余细节留在当轮的 `body` / `data`。`bbox` 不进 `headline`，因为它是本次变化的范围，跨轮参考价值低且会撑长文案；需要定位时重新 `preview`。

| 场景 | `headline` |
|---|---|
| 建画布 | `新建画布「苹果」16×16，id 3，pico8 16 色，全透明` |
| 打开 | `已打开画布 3「苹果」32×32，revision 12，画面 412/1024` |
| 名字重复 | 「苹果」可以存在多张，彼此以 id 区分，不报错 |
| 落笔成功 | `「苹果」已绘制，revision 3，本次 186 格，画面 412/1024` |
| 落笔无变化 | `「苹果」无像素变化，revision 仍为 3，画面 412/1024` |
| 改名 | `画布 3 已改名为「小苹果」` |
| 列举 | `共 23 张画布，第 1/2 页`，正文逐行列名字、尺寸、revision 与更新时间 |
| 删除 | `已删除画布 3「苹果」` |
| revision 过期 | `revision 过期：当前 4，传入 3，未写入` |
| 行列不符 | `第 5 行长度 15，期望 16；未写入` |
| 板外颜色 | `颜色 #FF00FF 不在色板，最近为 14 (#FF77A8)；未写入` |
| 导出 | `导出 PNG 256×256 至 桌面/pixel_32_20261119-153012.png` |

失败统一返回 `{"error", "code", "hint"}`，`code` 为稳定蛇形标识：`no_canvas`、`stale_revision`、`bad_rows`、`bad_rle`、`bad_region`、`region_too_large`、`bad_color`、`bad_op`、`bad_param`、`palette_full`、`unsupported_format`、`export_failed`。

## §5 状态、持久化与并发

### §5.1 画布对象与内存缓存

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `str` | 库内自增整数转字符串，如 `"3"` |
| `name` | `str` | 1-24 字符，全库唯一 |
| `size` | `int` | `16` / `32` / `64` |
| `preset` | `str` | `pico8` / `gameboy` / `nes` / `custom` |
| `palette` | `list[(r,g,b)]` | 最多 32 条，下标即色索引 |
| `index` | `np.uint8[size,size]` | `0-31` 为色索引，`255` 为透明 |
| `revision` | `int` | 从 0 起，每次成功写入 +1 |

内存只做缓存：最多保留 4 张，LRU 淘汰的是缓存而不是库内数据。库是唯一权威来源，缓存未命中时按 §5.4 从库里读回。

单例同时记录 `current_id`：`create` 与 `open` 都会把它指向新建或打开的那张，窗口随之切换。省略 `canvas_id` 的方法作用于 `current_id`；刚启动、尚未 `create` 或 `open` 时，按 `updated_at` 取库内最新的一张。

`core.py` 用懒创建的单例持有这份缓存与 `threading.Lock`：模块级只放 `_store = None`，首次调用时构造实例。模块级直接写 `OrderedDict()` 或 `threading.Lock()` 会在导入期执行调用，撞 ARCH009 契约。

`revision` 用于防止基于过时画面的写入。

### §5.2 revision 与并行

- `create` 新建画布，`revision` 为 0。
- 成功改变像素或色板后 `revision` 加 1；一次调用内多处修改只加 1。
- `revision` 可省略。省略时不做版本校验，重复提交同一份 `ops` 或 `rows` 的第二次调用得到 `changed_cells=0` 且 `revision` 不变，重试因此幂等；代价是无法识别「基于旧画面」的修正，多轮编辑时由模型自己带上 `revision`。
- 传入 `revision` 且与原值不符时拒绝写入，返回 `stale_revision`，`headline` 给出当前值。
- 像素无变化时 `revision` 不变，`changed_cells` 为 0，`bbox` 为 `null`。
- 同一轮并行调用会打乱写入顺序：`create` 与 `paint` 并行时 `paint` 可能先执行。`pet/brain/tool_loop.py` 的串行判定从硬编码 `game__` 改为前缀元组 `("game__", "pixel_art__")`，理由与游戏工具相同：跨调用会话依赖。

### §5.3 超时

`add_method` 的 `timeout` 默认 30 秒，本工具每个方法显式传 `timeout=15.0`。4096 格的四则运算与 200 条 op 在毫秒级完成，超时只可能来自导出大尺寸 PNG 与磁盘写入。

### §5.4 持久化

画布源数据落项目根目录的 `pet.db`，与 `todos`、`timer_entries` 同一库，经 `pet.db.get_conn` 取连接（已配 WAL、`busy_timeout`）。

```sql
CREATE TABLE IF NOT EXISTS pixel_canvases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    size       INTEGER NOT NULL,
    preset     TEXT    NOT NULL,
    palette    TEXT    NOT NULL,
    pixels     TEXT    NOT NULL,
    revision   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pixel_canvases_updated
    ON pixel_canvases(updated_at DESC);
```

| 决策 | 结论 | 理由 |
|---|---|---|
| 像素存储 | `pixels` 存 §6.3 的 RLE 文本 | 一图一行；4096 格拆行会让一次落笔写 4096 条记录。RLE 在 64 档通常几百字节到 2KB，且与导出格式共用编解码器，往返已有测试 |
| 不存 PNG | 存源数据 | PNG 无法增量编辑，读回还要重新解析；画面随时可由 `index` 重算 |
| 色板 | JSON 数组，如 `["#000000","#1D2B53"]` | 标准库 `json`，不引入依赖；数组下标即色索引 |
| 名字可重复 | 不加 `UNIQUE`，只有 `id` 唯一；`open` 不接受名字 | 按名字定位在多张同名画布下有歧义，标识统一用 id |
| `revision` 入库 | 与像素同一条 UPDATE | 重启后乐观锁仍然有效 |
| 时间 | `datetime.now().isoformat()` | 与 `timer_entries`、`todos` 的写法一致 |

写入顺序固定为三步，避免内存与库分裂：

1. 在锁内基于当前 `index` 算出新数组副本；
2. 用副本编码 RLE，执行单条 `UPDATE pixel_canvases SET pixels=?, preset=?, palette=?, revision=?, updated_at=? WHERE id=?`；
3. 提交成功后替换内存对象。

落库失败时返回 `persist_failed`，内存保持旧值，模型可重试。先改内存再落库会在失败时留下「界面已变、库里没变」的分裂状态。

读取路径：

| 场景 | 行为 |
|---|---|
| `open` | 按 id 或名字 `SELECT` 一行，RLE 解码进缓存，`current_id` 指向它，窗口切换显示；不写库，`revision` 与 `updated_at` 不变 |
| 显式 `canvas_id` | 先查内存缓存，未命中按同样方式读回，但**不改变** `current_id` |
| 省略 `canvas_id` | 用 `current_id`；不存在时 `SELECT ... ORDER BY updated_at DESC LIMIT 1` |
| `list` | 只取元数据列，不解码像素；`COUNT(*)` 供分页 |
| `rename` | 只更新 `name`，不动 `revision` 与 `updated_at`，避免改名把画布顶到列表最前 |
| 库为空且省略 id | 报 `no_canvas`，提示先 `create` |
| `delete` | 删库内一行并移出缓存；若删的是 `current_id`，置空 |

`PixelArtStorage(db_path=None)` 与 `TimerStorage` 同构：省略时取 `TOOL_CTX.db_path()`，测试传临时库路径隔离。

容量按 64×64 档估算（RLE 为 ASCII，1 字符 1 字节）：

| 画面 | RLE 长度 | 例子 |
|---|---|---|
| 全透明 | 6 字节 | `4096:.` |
| 单色整幅 | 6 字节 | `4096:a` |
| 典型像素画 | 1.5-3KB | 每行约 8-15 段 |
| 棋盘或噪声（最坏） | 12KB | 每格一段 `1:x`，3 字节 1 格 |

单行还带固定列约 200 字节（名字、色板 JSON、两个时间戳）与两个索引项。500 张典型图约 1.5MB，全按最坏情形估也不到 6MB。

写入放大：每次落笔是一条整行 UPDATE。2.5KB 的行正好一页；12KB 的最坏行溢出到 3-4 个 4KB 页，单次落笔约 16KB 物理写入，由 WAL checkpoint 回收。64 档连续十次落笔的写入量在百 KB 级，不需要额外优化。

不做自动清理，删除由 `delete` 显式触发。

崩溃一致性：WAL 加单条 UPDATE，进程被杀最多丢失当前这次调用的写入，不会留下半张画布。

## §6 栅格语义

### §6.1 坐标与区域

原点左上，x 向右、y 向下，0-based，合法范围为 `0 ≤ x < size`。

- `region` 必须完整落在画布内，越界报 `bad_region`，不做静默剪裁。区域面积上限 1024 格，超出报 `region_too_large`。
- 图元坐标允许部分越界，落笔时逐格剪裁：`rect(x=-4, y=0, w=8, h=2)` 在画布内正常产生 4×2 的区域。这与 `region` 的严格语义不同，理由是图元坐标系常按对称推算，剪裁比报错更符合构图习惯。
- `bbox` 为本次实际变化格子的并集，格式 `[x1, y1, x2, y2]`，右下端点含。

### §6.2 颜色

`ops` 内部的 `c` 由 handler 解析，接受三种写法；顶层参数 `background` 声明为 `str`，只接受后两种，因为 `any` 下发给模型时会变成 `string`。

| 写法 | 示例 | 适用范围 | 语义 |
|---|---|---|---|
| 整数索引 | `4` | 仅 `ops.c` | 色板下标 0 到 15 |
| 单字符索引 | `"a"` | `ops.c`、`background` | 十六进制字符，`0-9` 为 0 到 9，`a-f` 为 10 到 15 |
| 十六进制值 | `"#1D2B53"` | `ops.c`、`background` | 精确匹配色板条目；无精确匹配时报错并给出最近索引 |

`"."` 只用于 `rows` / `rle` 中表示透明，不作为颜色参数。

板外颜色不静默映射：报 `bad_color` 并在 `headline` 里给最近索引，由模型决定改色还是先调 `palette(action="quantize")`。

### §6.3 像素矩阵

`rows` 的约束随画布尺寸变化：

| 场景 | 约束 |
|---|---|
| 16 档且省略 `region` | 恰好 16 行，每行 16 字符 |
| 任意档带 `region` | 行数等于 `h`，每行字符数等于 `w`，`w × h ≤ 1024` |
| 32 / 64 档省略 `region` | 拒绝，报 `region_too_large` 并提示改用 `paint` |

字符集为 `0-9a-v`（颜色索引，单字符可表 32 色）、`.`（透明）、`?`（保持原样）。`?` 用于局部覆盖时跳过不需要改的格子，避免为了改 3 个格子重发整块区域。

`rle` 为全图行优先游程，格式是 `<十进制数量>:<字符>` 连续拼接，如 `12:.4:a` 表示 12 格透明后接 4 格索引 10。分隔符必须存在：字符集本身含 `0-9`，紧拼接时 `44` 既可读作 4 格索引 4，也可读作数量 44 后缺字符；`:` 不在字符集内，解码无歧义。数量为 1 时不可省略。

解码后累计格数必须等于 `size × size`，否则报 `bad_rle` 并给出期望格数与实际格数。

写入是原子的：出现非法字符、长度不符时整次调用不落笔，错误按行收集，最多返回 5 条。

### §6.4 paint 的算子

`ops` 中的 op 取值与字段写入方法描述，模型可见的就是下面这张表：

| 类别 | op | 字段 |
|---|---|---|
| 图元 | `point` | `x`, `y`, `c` |
| 图元 | `line` | `x1`, `y1`, `x2`, `y2`, `c` |
| 图元 | `rect` | `x`, `y`, `w`, `h`, `c`, `fill` 默认 true |
| 图元 | `ellipse` | `x`, `y`, `w`, `h`, `c`, `fill` 默认 false |
| 图元 | `polygon` | `points`（`[[x,y],…]`）, `c`, `fill` 默认 true |
| 图元 | `path` | `points`, `c`（1px 折线） |
| 图元 | `erase` | `x`, `y`, `w`, `h` |
| 图元 | `clear` | 无 |
| 过程式 | `gradient` | `region`, `from`, `to`, `dither`（`none` / `bayer4`）, `mask`（`none` / `circle`） |
| 过程式 | `noise` | `region`, `c`, `ratio`, `seed` |
| 过程式 | `pattern` | `region`, `tile`（`brick` / `checker` / `dots` / `stripes` / `grid`）, `c` |
| 过程式 | `mask` | `region`, `shape`（`circle` / `ellipse` / `rect`）, `c`，区域外擦除 |
| 过程式 | `scatter` | `region`, `c`, `count`, `seed` |

规则：

- 过程式 op 必须有 `region`，图元 op 不用。
- `seed` 缺省时取 `hash(canvas_id, op 序号, op 其余字段)`，只依赖参数本身，不依赖 `revision`；同一份 `ops` 重放得到同一图案，重试幂等。
- `mirror_x` 为真时每个 op 执行两次，第二次按 `x' = size - 1 - x` 反射；`region` 同步反射。
- 单次 `ops` 上限 200 条，超出报 `bad_param` 并给出一条建议：拆成多次调用。
- op 按数组顺序执行，后写覆盖先写。

### §6.5 尺寸档与落笔策略

| 档 | 格数 | 推荐落笔 | 理由 | 单次输出 tok |
|---|---|---|---|---|
| 16×16 | 256 | `set` 整图 `rows` | 图案小，行字符串最准最省 | 100-170 |
| 32×32 | 1024 | `paint` 图元为主 | 逐格吐 1024 字符易错位，矩形与椭圆描述构图更可靠 | 200-600 |
| 64×64 | 4096 | `paint` 过程式为主，`set` 局部精修 | 4096 格超出图元规划精度，梯度与噪声由工具确定性生成 | 600-1200 |

同一张图通常只用一种落笔方式；混用时先 `paint` 铺大面，再 `set` 局部细修。此表同时写进 `TOOL_DESCRIPTION`，模型在选工具时即可看到分档建议。

## §7 后处理

| `op` | 参数 | 语义 |
|---|---|---|
| `outline` | `c`（默认 0）, `thickness` 仅支持 1, `region` | 对非透明像素的 4 邻接外部格描边；1px 在 16 档会吃掉细节，描述里注明 16 档慎用 |
| `dither` | `region`, `c` | Bayer 4×4 抖动铺色，用于透明区域的渐变过渡 |
| `mirror_x` / `mirror_y` | `region` | 区域内镜像，右半复制左半 |
| `rotate90` | 无 | 整图顺时针 90 度；非正方形画布不存在，三档都是正方 |
| `shade` | `region`, `steps`（-2 到 2） | 按亮度对区域内非透明像素整体加减明度，结果就近映射回色板 |
| `cleanup` | 无 | 去掉 4 邻接透明或孤立的单格杂点 |
| `invert` | `region` | 按色板索引反转（`palette_size - 1 - i`），透明不变；4 色的 `gameboy` 板同样成立 |

`shade` 与 `invert` 的结果总在色板内，不需要新增颜色；`dither` 的抖动表固定为 4×4 手写常量。

后处理作用于全图时 `region` 省略。判断标准：`paint` 表达一次绘制意图，`postprocess` 表达对既有结果的整块变换。

## §8 预览与画布窗口

画面有两条出口，同一份 `index` 渲染：模型经 `preview` 自检，用户经只读窗口旁观。两条路径互不依赖，模型不调 `preview` 时窗口照常更新。

### §8.1 模型自检

自检是整条链路里最容易被低估的一环，没有它模型只能盲画。

| `mode` | 输出 |
|---|---|
| `text` | `body` 为字符矩阵，行首带两位行号与空格 |
| `image` | `images` 为 PNG，按 `scale` 最近邻放大；无视觉时退化为 `text` |
| `both` | 两者都出 |

文本矩阵的宽度固定为 16 列：

| 档 | 采样 | 说明 |
|---|---|---|
| 16×16 | 1 格 1 字符 | 256 字符，`0-9a-v` 与 `.` |
| 32×32 | 2×2 取众数 | 256 字符，字母表示该块内出现最多的非透明索引 |
| 64×64 | 4×4 取众数 | 同上 |

每行前缀 4 字符，整体约 340 字符，成本与一张 16 档图相当。带 `region` 时输出该区域原分辨率，超过 16×16 的区域按同规则聚合到 16 列。

行号帮助模型定位「帽子偏左」这类偏差，格式如 `03 ................`，其中 `03` 为 y 值。

`grid` 与 `coords` 只影响 `mode=image`：网格线画在放大后的边界上，不改变像素数据；坐标刻度每 4 格标注一次。默认都不开，避免在 256×256 的图上压住内容。

`preview` 不改变 `revision`，也不返回 `data`。

`body` 不进历史：写操作之后的下一轮，模型只剩 `headline` 里的 `revision` 与填充度，看不到上一版矩阵。跨轮「预览、修正」时两条做法：`headline` 固定携带的填充度用于判断整体进度，需要逐格定位时重新调 `preview`。`data` 里的 `bbox` 同样只活当轮。

### §8.2 用户可见窗口

目的是让落笔过程可见：线条与色块逐步出现，而不是只在最后看到一张成品文件。窗口不参与绘制，只提供关闭与导出两个动作。

形态参照 `pet/ui/game_panel.py` 的 `GamePanelBase`：无边框、`WindowStaysOnTopHint` 加 `Qt.Tool`、半透明背景与圆角卡片、标题栏带关闭按钮、首次显示定位在桌宠右侧（屏幕放不下时放左侧）、按住可拖动。定位前先 `adjustSize()`：窗口未显示时布局未生效，直接用 `width()` / `height()` 会按 640×480 判断边界。窗口单独实现为 `pet/tools/pixel_art/panel.py`，不继承 `GamePanelBase`，后者带游戏倒计时与弃权语义。

导出按钮取当前显示的那张画布，直接调用 `export.py` 的纯函数：`format="png"`、`scale=256 // size`、`transparent=True`、`grid_overlay=False`、`path` 省略（落桌面）。这条链路不经模型，也不经 `ToolExecutor`：`executor` 服务于模型调用的参数校验、超时与输出投影，而按钮是本地操作，参数固定、没有模型参数可校验。导出在毫秒级完成，直接在 UI 线程执行；异常被捕获后写状态行，不抛到事件循环。

| 项 | 约定 |
|---|---|
| 尺寸 | 画布区固定 256×256，水平居中，圆角 8px 加 1px 边框；16 / 32 / 64 三档都放大到 256，与导出口径一致 |
| 放大 | `Qt.FastTransformation` 或按整数倍生成 `QImage`，禁用平滑插值 |
| 透明 | 棋盘底（8px 明暗交替），透明像素与白色像素可区分 |
| 标题 | `像素画 · 画布名`，超宽按 256px 省略，不撑宽窗口 |
| 状态行 | `32×32 · pico8 · revision 3 · 本次 186 格`，画布名只在标题出现 |
| 交互 | 关闭与导出两个按钮；无取色、无绘制、无撤销 |
| 导出按钮 | 用窗口持有的 `canvas_id` 与当前 `revision`，与模型调用共用同一套渲染函数；无画布时禁用 |
| 导出反馈 | 成功后状态行显示文件路径并保持 3 秒，失败显示原因；超宽按常规状态行宽度中间省略，完整内容进 tooltip，不撑宽窗口 |
| 焦点 | `WA_ShowWithoutActivating`，显示与刷新都不抢键盘焦点 |

刷新链路：

~~~text
handler 写入成功
  → core._emit(payload)
  → panel.push_view(payload)             # 工具私有信号桥，排到主线程
  → PixelArtPanel.render(payload)
  → 60ms 合并，重绘
~~~

| 规则 | 说明 |
|---|---|
| payload 内容 | `canvas_id`、`name`、`size`、`revision`、`changed_cells`、`palette_size`、`png`（bytes），只含基础类型 |
| 不传对象 | `Canvas` 在工具线程内可变，跨线程共享会读到半写状态 |
| 编码复用 | `png` 由 `export` 的同一渲染函数生成，窗口画面与导出文件必然一致 |
| 合并刷新 | 60ms 内的多次写入只重绘一次，64 档连续算子不会反复重建 QPixmap |
| 打开时机 | `create` 与 `open` 成功后自动显示；之后经右键菜单「画布」重新打开（`registry.add_menu_action`） |
| 切换 | `open` 另一张时就地换内容，不重建窗口；右键菜单打开时显示 `current_id` 那张 |
| 关闭语义 | 用户关窗不中断绘制，也不再自动弹出，直到下一次 `create` |
| 推送范围 | `create`、`open`、`set`、`paint`、`postprocess`、`palette` 的 `set` / `quantize`、`rename` 都推一次完整 payload；`palette set` 只改色板不改 `index`，不推会让窗口停在旧颜色 |
| 不推送 | `preview`、`export`、`list`、`delete` 不改变画面，不推 |
| 无 UI 环境 | 无 QApplication 时 `push_view` 直接返回；PySide6 缺席时 `_emit` 的 import 失败由 `_push` 吞掉，纯逻辑测试不需要 Qt |
| 只读 | 窗口不改像素，也不改变 `revision`；导出只读当前快照 |

## §9 导出

| `format` | 内容 |
|---|---|
| `png` | `Image.resize((size*scale, size*scale), Image.NEAREST)`；透明背景时保留 alpha |
| `svg` | `<svg viewBox="0 0 size size" shape-rendering="crispEdges">`，按行合并同色 run 输出 `<rect>`，输出宽高为 `size*scale` |
| `json` | `{size, palette, rows: [索引字符串], revision}`，可被后续工具或脚本直接读回 |
| `rle` | 纯文本游程，格式同 §6.3 |

落点规则：

- 省略 `path` 时写入桌面，文件名 `pixel_<size>_<yyyymmdd-hhmmss>.<ext>`。
- 给了 `path` 时按与 `file_ops` 相同的口径校验：`os.path.realpath` 解析后必须位于桌面或文档目录之内，否则报 `export_failed` 并说明原因。校验逻辑提取为 `pet/file_intake/paths.py` 的公共函数，两个工具共用，避免各写一份白名单；`realpath` 是必须的，否则允许目录内的符号链接可以指向外部造成逃逸。
- `path` 为相对路径时相对桌面，不接受 `..` 上跳。
- 不往 `app_dir()` 写业务数据：成品是用户资产，落在桌面便于直接取用；配置类文件仍归 `app_dir()`。
- 扩展名与 `format` 不一致时报错，不自动改写。
- 同名文件不覆盖，追加 `-2` 后缀。
- `scale` 省略时取 `256 // size`，即 16 档 ×16、32 档 ×8、64 档 ×4，统一输出 256×256；上限 64。
- `grid_overlay` 在导出图上画 1px 网格，颜色取反色，仅用于人看，不作为交付物。

`side_effect` 是工具级标记（`registry.register` 的入参），一旦声明，`create` / `paint` / `preview` 也会被记为有副作用。取舍是保留声明：该标记只用于崩溃窗口判定与审计口径，保守多记不会阻断调用；若改成不声明，导出写文件这一真实副作用会在窗口判定里缺席。

## §10 色板

| 预设 | 色数 | 索引 0-31 |
|---|---|---|
| `pico8` | 16 | `#000000` `#1D2B53` `#7E2553` `#008751` `#AB5236` `#5F574F` `#C2C3C7` `#FFF1E8` `#FF004D` `#FFA300` `#FFEC27` `#00E436` `#29ADFF` `#83769C` `#FF77A8` `#FFCCAA` |
| `gameboy` | 4 | `#0F380F` `#306230` `#8BAC0F` `#9BBC0F`，其余索引未使用 |
| `nes` | 16 | `#000000` `#FCFCFC` `#7C7C7C` `#BCBCBC` `#0078F8` `#3CBCFC` `#A4E4FC` `#0000FC` `#0058F8` `#6888FC` `#B8B8F8` `#E40058` `#F878F8` `#F8B8F8` `#FCE4A0` `#F8B800` |
| `db32` | 32 | `#000000` `#222034` `#45283C` `#663931` `#8F563B` `#DF7126` `#D9A066` `#EEC39A` `#FBF236` `#99E550` `#6ABE30` `#37946E` `#4B692F` `#524B24` `#323C39` `#3F3F74` `#306082` `#5B6EE1` `#639BFF` `#5FCDE4` `#CBDBFC` `#FFFFFF` `#9BADB7` `#847E87` `#696A6A` `#595652` `#76428A` `#AC3232` `#D95763` `#D77BBA` `#8F974A` `#8A6F30` |

`palette` 三种动作：

| `action` | 语义 | revision |
|---|---|---|
| `get` | 返回索引与十六进制值，`body` 为紧凑一行 | 不变 |
| `set` | 替换颜色值，索引含义不变；未画到的索引可任意改 | +1 |
| `quantize` | 把已有像素按最近色重映射到新色板，返回 `remapped_cells` | +1 |

`set` 只改颜色值，已画像素的视觉随色板改变；`quantize` 保持视觉近似，改变索引。两者分开是因为换色板与改调色板是两个意图，混在一个参数里模型会用错。

色板长度少于 32 时，超出范围的索引在使用时报 `bad_color`，不隐式扩展到 32 格。

## §11 文件改动

| 文件 | 动作 | 内容 |
|---|---|---|
| `pet/tools/pixel_art/__init__.py` | 新增 | `TOOL_NAME`、`TOOL_DESCRIPTION`（含分档落笔建议）、`TOOL_GROUP="creative"`、十一组输出 schema 常量（`list` 复用 `PAGINATION_SCHEMA`）、`register(side_effect=True)`、每个方法显式 `timeout=15.0`、右键菜单项 |
| `pet/tools/pixel_art/core.py` | 新增 | 单例缓存（懒创建 + 锁）、`current_id`、LRU 淘汰、十一个方法的 handler、revision 校验、输出组装 |
| `pet/tools/pixel_art/storage.py` | 新增 | `pixel_canvases` 建表、单行读写、RLE 编解码落库、`list` / `delete` |
| `pet/tools/pixel_art/canvas.py` | 新增 | `Canvas` 数据模型、色板常量与最近色查找、`rows` / `rle` 解析、`bbox` 计算、文本矩阵渲染 |
| `pet/tools/pixel_art/ops.py` | 新增 | 图元栅格化、过程式算子、后处理算子 |
| `pet/tools/pixel_art/export.py` | 新增 | PNG / SVG / JSON / RLE 编码、落点处理、窗口用的 PNG 编码 |
| `pet/file_intake/paths.py` | 新增 | 桌面与文档根目录解析（含 Windows 已知文件夹）、目录白名单加 `realpath` 校验，供 `file_ops` 与 `export` 共用 |
| `pet/tools/file_ops/core.py` | 修改 | `_check_path` 改为调用共享函数，行为不变 |
| `pet/tools/pixel_art/panel.py` | 新增 | 只读窗口：NEAREST 渲染、棋盘底、状态行、导出按钮、置顶与拖动、60ms 合并刷新 |
| `pet/tools/context.py` | 修改 | 新增 `pet_geometry()`，供面板贴桌宠定位 |
| `pet/agent/pet_agent.py` | 修改 | 删去 `canvas_view_requested` 信号，跨线程画面投递收进工具包 |
| `pet/app.py` | 修改 | 删去像素画窗口的实例化与接线，工具界面只经注册表登记 |
| `pet/brain/tool_loop.py` | 修改 | 串行前缀由 `game__` 字符串改为元组常量 |
| `docs/README.md` | 修改 | 文档地图登记本文 |
| `docs/architecture.md` | 修改 | 模块职责表补 `pixel_art` 包（`tests/test_docs.py` 检查覆盖） |
| `docs/reference/tools.md` | 生成 | `python scripts/gen_docs.py` 刷新 |
| `docs/reference/modules.md` | 生成 | 同上；`gen_docs.py` 遍历 `pet/**/*.py`，新增模块必然改变它，只刷 `tools.md` 会让 `--check` 失败 |
| `tests/test_pixel_art.py` | 新增 | 见 §12 |

不新增 `requirements.txt`，`Pillow` 与 `numpy` 已在主依赖；不新增 `config.json`，没有私有配置项。

## §12 验证方式

单测直接调 `core` 的纯函数，不经 LLM，覆盖下列不变量：

| 用例 | 断言 |
|---|---|
| 建画布 | 尺寸、色板长度、初始 `revision=0`、背景为透明 |
| 命名 | 空白名与超过 24 字符被拒绝；同名可建多张且 id 不同；中文名可存取 |
| 持久化往返 | 写入后清空缓存、新建 storage 实例，按 `canvas_id` 读回的 `index`、`palette`、`preset`、`name`、`revision` 与写入前一致 |
| 重启恢复 | 关闭连接后重开，省略 `canvas_id` 时命中 `updated_at` 最新的一张 |
| 写库失败 | 注入异常或只读连接，返回 `persist_failed` 且内存里的 `revision` 未变 |
| 列举与删除 | `list` 按 `updated_at` 降序、不含像素列；`page_size=5` 时 12 张画布的第二页返回 5 条且 `has_next` 为真；`delete` 后 `list` 少一行，缓存不再命中 |
| 改名 | `rename` 后 `headline` 更新为新名字；改成与其它画布同名成功，各自 id 不变；`revision` 与 `updated_at` 不变 |
| 打开 | 清空缓存后 `open` 按 id 成功，`current_id` 随之改变，后续省略 id 的写入作用于它；`revision` 与 `updated_at` 不变 |
| 打开失败 | 未知 id 报 `no_canvas` 并列出最近 5 张；省略 `canvas_id` 报 `bad_param` |
| `set` 整图 | 16 行合法输入后 `changed_cells` 等于非透明格数，`bbox` 覆盖绘制范围 |
| `set` 局部与 `?` | 未改格子保持原值，`changed_cells` 只计实际变化 |
| 行列错误 | 返回 `bad_rows`，`headline` 含正确行号与期望长度，且画布未被修改 |
| `rle` 往返 | `export(rle)` 的文本经 `set(rle=…)` 还原为同一 index 数组；含两位数索引时按 `数量:字符` 切分，不把 `12:a` 读成数量 12 后接 `a` 丢失 |
| 4 色板反转 | `gameboy` 板上 `invert` 得到 `3 - i`，不产生越界索引 |
| 导出落点 | 省略 `path` 落桌面且文件名含尺寸与时间戳；桌面与文档内可写，两者之外的绝对路径被拒绝，目录内指向外部的链接被拒绝 |
| 越界 region | 报 `bad_region`；图元部分越界时正常剪裁且计数正确 |
| 幂等 | 同参数连写两次，第二次 `changed_cells=0`、`revision` 不变 |
| 乐观锁 | 过期 `revision` 报 `stale_revision` 且不写入 |
| 板外颜色 | 报 `bad_color`，提示里含最近索引 |
| 镜像 | `mirror_x` 后左右两半逐格相等 |
| 过程式确定性 | 同一 `seed` 两次 `noise` / `scatter` 结果一致；省略 `seed` 时同一 `ops` 重放也一致 |
| 导出 PNG | 16 档 `scale=16` 输出 256×256；放大后每 16×16 块颜色单一，无中间色 |
| 导出 JSON | 读回后与内存 `index` 一致 |
| 文本预览 | 三档均为 16 列，行数与内容符合采样规则 |
| 后处理 | `outline` 只在透明邻接处新增像素；`cleanup` 不删除成块笔画 |
| 窗口 payload | 用假 sink 接收 `push_view`，断言字段齐全、`png` 为可解码 PNG |
| 窗口渲染 | 离屏构造 `QImage` 后逐格比对颜色，确认放大为整数倍且无中间色 |
| 按钮导出 | 用按钮的固定参数调用导出函数，落桌面且文件名符合规则；无画布时返回可读错误而不是抛异常 |

手工核对：

1. 对话中要求画一张 16×16 的苹果，观察 `paint` 分档建议是否被采纳、`preview` 文本矩阵能否支撑一次修正。
2. 64 档用 `gradient` 加 `pattern` 画天空与地面，确认无越界异常、导出耗时在 1 秒内。
3. 无视觉端点下调用 `preview(mode="both")`，确认退化为文本且不报错。
4. 检查桌面上的 PNG 为硬边像素，放大无模糊；`%APPDATA%/KoishiAI/` 下不出现 `pixel_art` 目录。
5. 32 档画角色：每次 `paint` 后窗口在 60ms 内更新，能看到线条逐步出现；关窗后模型继续绘制不报错，右键菜单可重新打开并显示当前画面。
6. 拖动窗口到屏幕另一侧，再让模型落笔，确认位置不变、始终置顶、不抢输入框焦点。
7. 点窗口「导出」，桌面出现与窗口画面一致的 PNG，状态行短暂显示路径；重新 `create` 后按钮导出的是新画布。
8. 重启应用后 `list` 仍能看到此前的画布，省略 `canvas_id` 继续 `paint` 时 `revision` 从上次的值递增。

## §13 风险

| 项 | 影响 | 处理 |
|---|---|---|
| `ops` 无 schema 约束 | 模型编造 op 或字段名 | 描述里给完整取值表与两条示例；handler 对未知 op 报 `bad_op` 并列出合法值 |
| 文本预览成本 | 64 档若全量输出为 4096 字符，接近一次落笔开销 | 固定 16 列聚合，始终约 340 字符 |
| 画面不进历史 | `body` 与 `data` 只活当轮，跨轮只剩 `headline` | `headline` 固定带 `revision` 与填充度；需要逐格信息时重调 `preview` |
| 并行调用乱序 | `create` 与 `paint` 同轮发出时落笔失败 | 串行前缀覆盖 `pixel_art__`；失败信息里提示「画布未建，先调 create」 |
| 分组激活重置 | 跨轮首次使用前工具不可见，模型可能直接编造调用名 | `TOOL_DESCRIPTION` 写明用途与关键词，便于 `tool_search` 命中；一次成图的多步调用落在同一轮内，重置只发生在互动结束 |
| 无视觉端点 | `images` 静默丢弃 | 先判 `TOOL_CTX.has_vision()`，退化为文本矩阵并在 `headline` 说明 |
| `shade` / `quantize` 映射 | 就近色选择可能破坏像素画的有限色风格 | 只在明确调用时执行；默认不隐式量化 |
| 1px 描边在 16 档 | 轮廓吃掉主体细节 | 描述中注明 16 档慎用，由模型决策 |
| 导出路径 | 绝对路径可指向任意位置 | `realpath` 解析后必须位于桌面或文档目录，与 `file_ops` 共用同一校验函数；省略 `path` 落桌面，相对路径不允许 `..` 上跳 |
| 跨线程 UI | 工具线程直接改控件会崩溃 | 只经 Qt 信号投递 payload，重绘在 UI 线程；payload 全为基础类型 |
| 刷新开销 | 64 档连续算子反复编码 PNG 拖慢工具线程 | 写入成功后才编码一次并复用导出函数；窗口侧 60ms 合并重绘 |
| 窗口打扰 | 每笔都弹窗抢注意力 | 只在 `create` 自动显示，用户关闭后不再自动弹出 |
| 按钮绕过 executor | 无参数校验与超时兜底 | 参数在按钮内固定、不接受模型输入；异常捕获后写状态行，导出为毫秒级本地操作 |
| 库内增长 | 画布只增不删 | 单张最多约 2KB；`list` 给模型可见性，`delete` 提供回收手段；不做自动清理，避免误删用户作品 |
| 落库失败 | 内存与库不一致 | 先落库后换缓存，失败返回 `persist_failed` 且内存保持旧值 |
| 重名 | 模型用同名新建，之后按名字指代会混 | 不加唯一约束，`create` 返回 `canvas_id`，工具描述要求后续调用一律带 id |

## §14 阶段

| 阶段 | 内容 | 前置 |
|---|---|---|
| 1 | `create` / `open` / `list` / `rename` / `delete` / 落库 / `set` / `paint`（图元）/ `export png` / `preview text` | 无 |
| 2 | 只读画布窗口（含导出按钮；非工具文件只动 `context.py` 的 `pet_geometry()`） | 阶段 1 |
| 3 | 过程式算子、`postprocess`、`palette`、`export svg/json/rle` | 阶段 2 |
| 4 | `preview image`、网格与坐标、16 档整图快捷路径的提示词调优 | 阶段 3 |

阶段 1 是纯逻辑闭环，可独立验收；窗口收在工具包内，非工具文件只有 `context.py` 的 `pet_geometry()`，单独成阶段便于回滚。用户在阶段 2 之后就能在绘制过程中看到画面。

## §15 落地结论

按 §14 的阶段 1 与阶段 2 落地，`pet/tools/pixel_art/` 下八个模块，窗口在 `pet/tools/pixel_art/panel.py`，单测见 `tests/test_pixel_art.py` 与 `tests/test_pixel_art_panel.py`。

| 项 | 实现 | 与设计的差异 |
|---|---|---|
| 算子 seed | `zlib.crc32` 对 op 的规范化 JSON | 不依赖 `revision`，同一份 ops 重放结果一致 |
| `palette` 落库 | `set` / `quantize` 单独一条 UPDATE | `set` 不改 `index`，走统一的提交路径会因「无变化」跳过写库 |
| 窗口抑制 | payload 带 `force_show`，仅 `create` / `open` / 右键菜单置真 | 「用户关窗后不自动弹出」需要区分推送来源 |
| 窗口导出 | 按钮直接调 `core.export(canvas_id, format="png")`，参数固定 | 用户操作不经 `ToolExecutor`；异常写状态行，3 秒后恢复 |
| RLE 扩展名 | `.txt` | 设计只规定格式，未规定后缀 |
| 右键菜单 | `refresh_view` 重推当前画布；库里没有画布时推 `{"empty": True}` 空态 | 设计只写了「重新打开」，空态是后补的入口行为 |

未实现：`copy`（另存一版）、外部图片导入、SVG 之外的矢量格式。`preview image` 与网格坐标已随窗口一并完成。
