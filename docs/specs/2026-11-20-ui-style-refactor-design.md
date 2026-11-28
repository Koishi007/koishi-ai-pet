# UI 样式改造设计（手绘卡通风）

> 一套令牌作为唯一色值来源，一层 QSS 组装公共控件，一层兼容导出保证 28 个文件可以逐面迁移；视觉换成古明地恋配色的粗描边暖纸底，布局、交互与既有测试断言不变。

## §0 背景与现状

视觉稿见 [ui-skin-preview.html](../media/ui-skin-preview.html)，覆盖设计令牌、设置窗口、气泡组、确认卡、待办面板、井字棋面板与控件标本。

| 项 | 现状 | 位置 |
|---|---|---|
| 样式库 | 578 行；13 组 QSS 加终端暗色板；55 处十六进制色值 | `pet/ui/styles.py` |
| 内联样式 | 183 处 `setStyleSheet`，分布在 28 个文件 | `pet/ui/`、`pet/tools/` |
| 色值重复 | 20 个文件共 152 处十六进制色值 | 同上 |
| 工具样式 | 仅待办有独立 `style.py`；知识库与 B 站的 QSS 写在 `panel.py` 内 | `pet/tools/todo/style.py`、`pet/tools/knowledge/panel.py`、`pet/tools/bilibili/panel.py` |
| 自绘窗口 | 14 处 `paintEvent`，圆角墨框、气泡尾巴各写一遍 | `settings_window.py`、`chat_history.py`、`memory_window.py`、`speech_bubble.py` 等 |
| 图标 | 灰阶 PNG（chat / feed / music / setting / volume）加 `checkmark.svg` | `assets/icon/` |
| UI 测试 | 22 个测试实例化窗口，断言控件、输入与行为 | `tests/test_ui_*.py`、`tests/test_settings_ui.py` |

现状的问题集中在三处。色值没有单一来源，同一张白卡片在设置窗口写作 `#ffffff`、在待办写作 `#ffffff`、在游戏面板写作 `#f5f6fa`，改一次配色要逐文件搜索。QSS 常量只覆盖主窗口，气泡、游戏与工具各自内联样式，同一控件在不同面板的圆角、边框与字号不一致。自绘窗口重复实现圆角与描边，新增窗口时容易漏掉，也让窗口外观无法随令牌变化。

## §1 目标

- 新增 `pet/theme.py`，承载色板、圆角、描边、投影、间距与字体链，成为仓库内唯一的十六进制色值来源；放在顶层以避开工具层不得依赖 ui 的约束。
- 新增 `pet/ui/qss.py`，由令牌拼装公共控件样式；`pet/ui/styles.py` 退化为兼容层，旧常量名与函数签名保持不变。
- 新增 `pet/ui/frame.py`，统一窗口级墨框与硬投影；新增 `pet/ui/icons.py`，把灰阶 PNG 染成墨色。
- 28 个文件的内联样式按面迁移到令牌与公共 QSS，工具目录保留自包含的选择器，只引用令牌。
- 落实视觉稿的视觉契约：2px 墨色描边、暖纸底、青绿主色、黄绿完成色、零模糊硬投影、胶囊控件、墨色单色图标。
- 不改布局层级、控件尺寸、信号连接与配置键；`tests/test_ui_*.py` 的断言不改。

## §2 非目标

- 不做主题切换、暗色模式开关与运行期换肤。仓库只有一套皮肤，终端保留自己的暗色板。
- 不改窗口结构与信息层级。视觉稿里删掉的控件是原本就不存在的，既有控件不因换肤增删。
- 不重绘宠物本体素材。`assets/actions/` 的帧动画与 `pet_window.py` 的渲染路径不动。
- 不引入 WebView、Qt Quick、第三方图标库或新依赖，只用 PySide6 现有能力。
- 不做全局字体打包。字体链按系统可用项回退。
- 不追求逐像素还原手绘抖动。描边与硬投影是风格载体，边缘保持直线。

## §3 视觉令牌

### §3.1 色板

| 令牌 | 值 | 用途 |
|---|---|---|
| `PAPER` | `#F8F1DF` | 窗口底、滚动区、标签栏底 |
| `PAPER_DEEP` | `#F0E7CD` | 分组标题底、禁用底、交替行深色 |
| `SURFACE` | `#FFFDF4` | 卡片、输入框、按钮底 |
| `SURFACE_WARM` | `#FBF6E6` | 次级按钮底、交替行浅色 |
| `INK` | `#1F2E26` | 描边、主文字、图标 |
| `INK_SOFT` | `#3B4C41` | 次级文字、标签 |
| `INK_MUTED` | `#7C8474` | 提示、统计、占位符 |
| `LINE` | `#DCD2B6` | 分隔线、禁用描边 |
| `PRIMARY` | `#47836F` | 主按钮、选中标签、开关开启 |
| `PRIMARY_DEEP` | `#31604F` | 主色文字、hover、井字棋 X |
| `PRIMARY_SOFT` | `#D7E8DF` | 选中行、对话气泡、悬浮底 |
| `ACCENT` | `#9DBE4D` | 完成、勾选、开关滑块、井字棋高亮 |
| `ACCENT_DEEP` | `#6F8F2E` | 完成态文字、统计强调 |
| `ACCENT_SOFT` | `#EBF2CF` | 喂食气泡、完成提示底 |
| `INFO` | `#4E7F8C` | 信息态、井字棋 O |
| `INFO_SOFT` | `#DCEAEC` | 信息提示底 |
| `YELLOW` | `#F8E880` | 标题图标徽章、计时徽章 |
| `BUBBLE_CHAT` | `#CBE6D8` | 对话气泡底色 |
| `BUBBLE_FEED` | `#F7DD93` | 喂食气泡底色 |
| `BUBBLE_MUSIC` | `#CFDFF3` | 音乐气泡底色 |
| `DANGER` | `#BF5A4C` | 删除、危险命令、关闭按钮 hover |
| `DANGER_SOFT` | `#F6DED8` | 危险提示底 |

三个气泡色是独立的一组浅色，按色相区分，不表达成功、警告、危险这类状态，因此不列入语义色；三个气泡图标统一用 `INK`。

文字与底色的固定搭配：米白文字只用于 `PRIMARY` 填充，墨色文字用于 `ACCENT` 与所有浅色底。`YELLOW` 取自 idle 素材的上衣主色，只做徽章底，不承担状态语义。`PRIMARY_DEEP` 承担纸面上的强调文字，避免主色直接当正文色。`INK_MUTED` 只用于 11px 的非关键提示与统计，不承载操作文案。

### §3.2 形状与描边

| 令牌 | 值 | 用途 |
|---|---|---|
| `RADIUS_SM` | 8 | 小控件、棋盘格、菜单项 |
| `RADIUS_MD` | 11 | 按钮、输入框、下拉框 |
| `RADIUS_LG` | 14 | 分组卡、气泡、工具面板 |
| `RADIUS_XL` | 20 | 独立窗口外框 |
| `RADIUS_PILL` | 999 | 标签页、胶囊输入、圆形按钮 |
| `BORDER` | 2 | 所有描边宽度 |
| `SHADOW_WINDOW` | 3 | 窗口硬投影偏移 |
| `TITLE_BAR_H` | 40 | 无边框窗口标题栏高度 |
| `SPACE_1..4` | 4 / 8 / 12 / 16 | 布局间距与内边距 |

窗口投影画在外框内侧：卡片路径按 `(0, 0, -3, -3)` 内缩，投影路径偏移 `(3, 3)`，窗口外框尺寸不变，宠物相对定位与气泡坐标计算不受影响。

### §3.3 字体

| 用途 | 字体链 | 字号 |
|---|---|---|
| 中文界面 | `HarmonyOS Sans SC` → `Microsoft YaHei UI` → `PingFang SC` → `Segoe UI` | 12.5px 正文，14px 窗口标题，11px 提示，10.5px 徽章 |
| 数字与路径 | 同中文界面，配合 `font-weight: 800` | 同上 |
| 终端与代码块 | `Cascadia Mono` → `JetBrains Mono` → `Consolas` → `Microsoft YaHei UI` | 11.5px 代码，12px 终端 |

应用启动时用 `QApplication.setFont()` 加 `QFont.setFamilies()` 设置字体链，QSS 只声明字号。QSS 的 `font-family` 不保证按逗号列表回退，样式表里写长串会让部分控件落回系统默认字体。终端继续走 `terminal_mono_font()` 与 `terminal_ui_font()`。

## §4 代码分层

```
pet/
├── theme.py     # 令牌：色板、圆角、描边、投影、间距、字体链、终端暗色板
└── ui/
    ├── qss.py       # 由令牌拼装的公共 QSS，同名常量对齐 styles.py
    ├── frame.py     # paint_ink_frame()：圆角墨框与硬投影
    ├── icons.py     # tinted_icon()：单色 PNG 染色与缓存
    └── styles.py    # 兼容层：旧常量名 re-export，路径常量、气泡行、任务栏与标题按钮保留
```

令牌放在顶层 `pet/theme.py`，不放在 `pet/ui/`：架构红线 ARCH001 禁止 `pet/tools` 顶层 import `pet.ui`，而工具面板需要同一套色值。`pet/theme.py` 只有常量与纯函数，ui 与 tools 都能安全引用。

依赖方向为 `pet.theme ← pet.ui.qss ← pet.ui.styles`，`frame` 与 `icons` 只依赖 `pet.theme`。工具面板只引 `pet.theme` 与自己的 `style.py`，不引 `pet.ui` 的任何模块。

### §4.1 pet/theme.py

只放常量与纯函数，无 Qt 依赖，可在测试中直接导入。

- 色板常量使用语义名，不带控件名：`INK`、`PAPER`、`PRIMARY`，不写 `BUTTON_BORDER`。
- 终端色板保留既有 `TERMINAL_*` 命名，值不变。
- `rgba(hex_value, alpha)` 返回 `QColor` 可用的 `rgba()` 字符串。
- `radius_for(height)` 返回胶囊半径，供圆形按钮与标签页使用。
- `qss_font_stack()` 返回拼好的 `font-family` 值，避免每个 QSS 模板重复长串。

### §4.2 qss.py

按控件族提供常量，命名与 `styles.py` 现有常量一致，迁移时只改导入来源：

| 常量 | 覆盖控件 |
|---|---|
| `WINDOW_QSS` / `PANEL_QSS` | 窗口底、分组卡 |
| `BUTTON_QSS` / `BUTTON_PRIMARY_QSS` / `BUTTON_DANGER_QSS` | 普通、主操作、危险与圆形图标按钮 |
| `INPUT_QSS` / `INPUT_HIGHLIGHT_QSS` / `COMBOBOX_QSS` / `TEXTEDIT_QSS` | 输入、下拉、多行文本、密钥与热键高亮 |
| `CHECKBOX_QSS` / `SCROLLBAR_QSS` / `LIST_QSS` / `MENU_QSS` | 勾选、滚动条、列表、右键菜单 |
| `TAB_BAR_QSS` / `SCROLL_AREA_TRANSPARENT_QSS` | 胶囊标签页、透明滚动容器 |

另提供 `app_stylesheet()` 返回合并结果，供测试一次性解析；运行期仍按窗口调用 `setStyleSheet`，避免全局 QSS 影响宠物透明窗口与终端。`ui_font()` 返回带 `setFamilies` 链的应用级字体，由 `pet/app.py` 在创建 `QApplication` 后设置。

### §4.3 frame.py

```python
def paint_ink_frame(widget, *, fill: str, radius: int = RADIUS_XL,
                    border: int = BORDER, shadow: int = SHADOW_WINDOW) -> None:
```

在窗口的 `paintEvent` 内调用，绘制偏移投影与圆角卡片，描边用 `QPen(INK, border)`，抗锯齿开启。调用方负责把根布局的右、下边距加上 `shadow`，避免内容压到投影。`speech_bubble.py` 的气泡尾巴单独绘制，不走该函数。

### §4.4 icons.py

```python
@lru_cache(maxsize=64)
def tinted_icon(path: str, color: str, size: int = 28) -> QIcon:
```

加载灰阶 PNG，用 `CompositionMode_SourceIn` 填充墨色，平滑缩放到目标尺寸。缓存键包含路径、颜色与尺寸。系统托盘图标保持原色，不参与染色。`checkmark.svg` 另存一份墨色版本，供 QSS 的 `image: url()` 使用。

### §4.5 styles.py 兼容层

`styles.py` 保留以下符号，其它模块的导入语句不改：

- 全部 `_COLOR_*` 私有常量与 `WINDOW_RADIUS`，改为从 `theme` 赋值。
- 13 组 QSS 常量改为从 `qss` 重新导出。
- 路径常量、`BUBBLE_ROW_*`、`bubble_column_y()`、`terminal_*` 函数、`make_title_button()`、`make_minimize_button()`、`make_close_button()`、`ensure_taskbar_icon()` 保留在本文件。
- 标题栏按钮换成圆形 2px 墨框，hover 用 `PRIMARY_SOFT`，关闭键 hover 用 `DANGER`。

新代码直接 `from pet.theme import ...`；只有既有模块继续从 `styles` 取用，兼容层在全部迁移完成后仍然保留，避免工具目录与外部脚本断裂。

## §5 QSS 表达边界

| 设计效果 | QSS 能力 | 实现方式 |
|---|---|---|
| 2px 墨色描边 | 支持 | `border: 2px solid INK` |
| 圆角与胶囊 | 支持 | `border-radius`，胶囊取高度的一半 |
| 窗口硬投影 | 不支持 `box-shadow` | `paint_ink_frame()` 画偏移路径 |
| 控件硬投影 | QSS 不支持 | `pet/ui/ink_button.py` 自绘偏移墨影，QSS 只管颜色与描边 |
| 按下反馈 | QSS 无 `transform` | `InkButton` 按压下沉 2px 并收起投影，松开回弹；下沉后命中判定补回原按钮区域 |
| 焦点光晕 | 不支持 | 描边换 `PRIMARY`，底色提亮到 `SURFACE`，不加光晕 |
| 分组标题骑在边框上 | 部分支持 | `QGroupBox::title` 只给 `PAPER_DEEP` 底与内边距，无法给标题描边 |
| 图标染色 | 不支持 | `icons.tinted_icon()` |
| 过渡动画 | 不支持 | 不做，保留现有 `QPropertyAnimation` 的显隐与展开动画 |

控件不使用不对称边宽。首版按右下加厚 2px 模拟硬投影，实机在圆角处出现接缝与明显粗黑边，改为四边等宽描边，层级只用底色与 hover 色差表达；窗口级投影不受影响。

## §6 组件改造清单

### §6.1 窗口层

| 文件 | 现状 | 改造 |
|---|---|---|
| `settings_window.py` | 手写 `paintEvent` 圆角加 1px 黑边；标题按钮内联样式 | 改用 `paint_ink_frame()`；标题栏图标换圆形徽章；胶囊标签页 |
| `chat_history.py` | 5 处色值、自绘圆角 | 窗口框走 `paint_ink_frame()`，日期列表与消息区换令牌 |
| `memory_window.py` | 14 处 `setStyleSheet`、5 处色值 | 列表、筛选、详情、按钮换 `qss` 与令牌 |
| `log_window.py` | 6 处 `setStyleSheet`、1 处色值 | 同上，等宽字体保留 |
| `debug_window.py` | 6 处 `setStyleSheet` | 同上，JSON 区换 `TEXTEDIT_QSS` |
| `pixel_art/panel.py` | 5 处 `setStyleSheet`、4 处色值 | 画布区保持中性底，避免影响取色；工具栏与按钮换令牌 |
| `system_tray.py` | 菜单 2 处色值 | 菜单与动作项换 `MENU_QSS` |
| `pet_window.py` | 拖拽高亮 2 处色值 | 高亮色换 `ACCENT_SOFT` |

### §6.2 气泡组

| 文件 | 改造 |
|---|---|
| `speech_bubble.py` | 自绘气泡：底 `SURFACE` 加 8% 主色，2px 墨框，圆角 14；尾巴改为同色填充加墨色描边，尾巴高度不变 |
| `chat_bubble.py` / `feed_bubble.py` / `music_bubble.py` | 32px 圆形按钮改 2px 墨框；底色分别用 `BUBBLE_CHAT` / `BUBBLE_FEED` / `BUBBLE_MUSIC`，图标统一 `INK`；输入框胶囊化，busy 态用 `PAPER_DEEP` |
| `file_bubble.py` | 6 处内联样式换令牌 |
| `confirm_card.py` | 见 §6.5 |
| `emotion.py` | 仅 emoji 文本，无样式改动 |

### §6.3 游戏面板

`game_panel.py` 基类统一背景、标题、倒计时与关闭键；四个子面板只保留各自的内容样式。

| 文件 | 改造 |
|---|---|
| `game_panel.py` | 背景 `SURFACE`，2px 墨框，圆角 14；标题 14px 粗体；倒计时用 `PRIMARY_SOFT` 胶囊 |
| `tic_tac_toe_panel.py` | 棋盘格 `SURFACE` 加 2px 墨框；X 用 `PRIMARY_DEEP`，O 用 `INFO`；不可点格用 `PAPER_DEEP` |
| `higher_lower_panel.py` | 数字基准 34px 加粗；下注按钮换主按钮与次按钮；赔率文案保留 |
| `rps_panel.py` / `twenty_questions_panel.py` | 选项按钮换令牌，选中态用 `ACCENT` 加墨色文字 |

### §6.4 工具面板

| 目录 | 现状 | 改造 |
|---|---|---|
| `tools/todo/` | `style.py` 已有 QSS，`panel.py` 内还有 8 处色值 | QSS 留在 `style.py`，改引令牌；面板内色值改常量；列表加交替行与选中色 |
| `tools/knowledge/` | 12 处 `setStyleSheet`、13 处色值写在 `panel.py` | QSS 抽到同目录 `style.py`，面板只留组装代码 |
| `tools/bilibili/` | 21 处 `setStyleSheet`、14 处色值写在 `panel.py` | 同上 |
| 其余工具 | 无面板或无样式 | 不改 |

### §6.5 确认卡与终端

确认卡浮在桌面上，与气泡同级，改为浅色卡片：墨框、纸底、等宽命令块、`PRIMARY` 允许键、危险命令时允许键换 `DANGER` 并显示「仍要执行」。倒计时条用 `PRIMARY`，剩余秒数用胶囊徽章。

终端窗口、终端视图与终端输入保持暗色，只把圆角、按钮与滚动条语言对齐。终端色板移入 `theme.py`，值不变；`confirm_card.py` 不再引用 `TERMINAL_*`。

## §7 工具样式自包含约定

- 工具可以 `from pet.theme import PRIMARY, RADIUS_MD` 取令牌，不得导入 `pet.ui` 的任何模块。
- 每个有面板的工具在同目录放 `style.py`，QSS 选择器与布局意图留在工具内，主界面改版不会连带改动工具结构。
- `style.py` 内不出现十六进制字面量，颜色一律来自令牌。
- 工具面板的窗口外框与标题栏允许复制少量 QSS（约 10 行），换取目录可独立删除；复制的是令牌引用，不是色值。
- 新增工具按该约定执行，详见 [tool-development.md](../tool-development.md)。

## §8 实施顺序

| 阶段 | 内容 | 验收 |
|---|---|---|
| 1 | `theme.py`、`qss.py`、`frame.py`、`icons.py`；`styles.py` 改兼容导出 | 现有 UI 测试全绿；设置与待办可正常打开，无 QSS 解析告警 |
| 2 | 设置窗口与通用控件（`settings_*`） | `tests/test_settings_ui.py` 通过；四页签目视无裁切 |
| 3 | 气泡组（`speech_bubble`、`chat/feed/music/file_bubble`） | 气泡测试通过；悬停、展开、收起、贴边目视正常 |
| 4 | 游戏面板基类与四个子面板 | 井字棋、猜拳、猜高低、二十问各开一局 |
| 5 | 工具面板（待办、知识库、B 站）与像素画窗口 | 各面板打开、增删改查一次 |
| 6 | 确认卡、终端圆角与按钮、托盘菜单 | `tests/test_confirm_card.py`、`tests/test_terminal_window.py` 通过 |
| 7 | 清理：删除失效常量与内联样式，补图标染色调用，重跑文档生成 | 全仓无旧色值；`python scripts/gen_docs.py --check` 通过 |

每个阶段可独立提交，提交前跑该阶段相关的测试；阶段 7 跑全量。

## §9 验证与护栏

新增 `tests/test_ui_theme.py`，只做机械检查：

- 令牌值是合法的 `#RRGGBB`，必需令牌齐全。
- `app_stylesheet()` 应用到 `QApplication` 时不产生 `Could not parse stylesheet` 警告，用 `qInstallMessageHandler` 捕获。
- `styles.py` 的兼容符号仍可导入，旧常量值与 `theme` 一致。
- `pet/tools/*/style.py` 不含十六进制字面量。
- `tinted_icon()` 返回非空 `QIcon`，同一参数两次调用命中缓存。
- 圆形按钮的半径等于高度的一半，用 `radius_for()` 的返回值断言。

既有 22 个 UI 测试不改断言。手工检查覆盖：设置四页签、聊天展开与收起、喂食、音乐展开、记忆窗口、日志窗口、待办面板、井字棋、确认卡。

## §10 风险与取舍

| 决策 | 选择 | 理由 |
|---|---|---|
| 工具样式位置 | 留在工具目录，只引令牌 | 工具目录可独立删除；共享组件 QSS 会把工具与主界面绑死 |
| 确认卡配色 | 浅色卡片 | 浮在桌面上，与气泡同级；终端窗口仍暗色，两者不同时出现 |
| 图标 | 现有 PNG 染色 | 免去重绘资产；灰阶 PNG 染墨后满足单色描边风 |
| 主题切换 | 不做 | 单皮肤，减少分支与测试面 |
| 阴影 | 仅窗口用 `paintEvent` | QSS 无 `box-shadow`；控件不做假投影避免粗边 |

| 风险 | 影响 | 缓解 |
|---|---|---|
| QSS 语法错误静默失效 | 控件回退到默认外观 | 集中常量；解析告警测试；每阶段目视 |
| 固定尺寸与新增边距冲突 | 内容裁切 | 投影只内缩 3px；布局边距用令牌；先改一扇窗再推广 |
| 图标染色边缘发灰 | 图标显脏 | `SourceIn` 加预乘 alpha；缓存缩放结果 |
| 深色终端与浅色卡片割裂 | 观感不统一 | 终端只保留暗色，确认卡独立浅色，不共用窗口 |

## §11 落地后的文档维护

新增模块带一句话 docstring，落地后执行 `python scripts/gen_docs.py` 更新 [reference/modules.md](../reference/modules.md)。

## §12 实际差异

实施结果与本文的偏差集中记录在此，后续调整以本节为准。

| 项 | 视觉稿 | 实现 | 原因 |
|---|---|---|---|
| 令牌位置 | `pet/ui/theme.py` | `pet/theme.py` | ARCH001 禁止工具目录顶层 import `pet.ui` |
| 窗口投影 | 外扩 3px | 卡片内缩，窗口外框尺寸不变 | 不改变宠物相对定位与气泡坐标计算 |
| 工具窗口投影 | 与主窗口一致 | QSS 圆角边框，无投影 | 工具不得 import `pet.ui.frame`，保持目录自包含 |
| 控件投影 | 统一零模糊投影 | `InkButton` 自绘墨影与按压下沉，QSS 描边仍四边等宽 | 边框加厚模拟在圆角处有接缝，投影改自绘 |
| 按钮投影覆盖 | 全部按钮 | 气泡、确认卡、游戏面板、设置窗与设置页、标题栏圆钮已接 `InkButton`；内容窗口其余按钮、工具面板、终端仍 QSS | 工具目录自包含、终端保持暗色，内容窗口第二批 |
| 分组标题 | 描边胶囊 | 只给 `PAPER_DEEP` 底与内边距，不描边 | `QGroupBox::title` 不支持边框 |
| 标签页 | 全圆胶囊 | 圆角 13px | 与 28px 标签高度匹配，避免 Qt 对超大半径的裁剪差异 |
| 标题按钮 | 29px 圆钮 | 34px 圆钮，游戏面板 26px | 与 40px 标题栏和既有点击热区匹配 |
| 命令确认卡 | 浅色卡片 | 浅色卡片，顶部危险色条改为 3px 内嵌条 | 与卡片的圆角留白一致 |
| 图标 | 墨线重绘 | 现有灰阶 PNG 染色，另存 `checkmark_ink.svg` | 免去重绘资产，棋盘与列表图标观感一致 |
| 窗口标题图标 | 统一墨线 | 日志、记忆、调试、历史新增 `assets/icon/ink/*.svg` | 应用图标是彩色不透明图，整体染色后只剩一个实心方块 |
| 字体链 | QSS `font-family` 列表 | `QApplication.setFont()` 加 `setFamilies()` | QSS 不保证按列表回退，调试面板曾落到宋体，与雅黑混排 |
| 气泡尾巴 | 与本体一体 | 尾底压进本体 7px 后再 `united()` | 共边布尔并集可能保留第二条横边，重叠区域可消除接缝 |
| 气泡按钮 hover | 提亮 | 按底色明度推导：浅底压深、深底提亮 | 统一用 `PRIMARY_SOFT` 会让深色按钮 hover 发白 |
| 表格圆角 | 表格自身圆角 | 去掉表格圆角，窗口内留 14px 外边距 | QTableWidget 的 viewport 不裁剪到圆角，表头方角会顶出卡片 |
| 托盘菜单 | 单层圆角卡片 | Windows 的 `MENU_QSS` 只留菜单项，卡片由 `paintEvent` 绘制；其余平台用带底色的 `MENU_SOLID_QSS` | QMenu 自带面板会与自绘卡片叠成双层，且透明背景在非 Windows 上无窗口绘制 |
| 终端 | 暗色保留 | 色板移入 `theme.py`，圆角与按钮语言未改 | 终端是内容面，暗色对比更适合命令行 |
| 工具样式 | 新建 `style.py` | 待办沿用，知识库与 B 站新增 | 三个面板的 QSS 均可由令牌重建 |
| 气泡按钮 | 38px 圆钮 | 32px | 行距常量按 40px 节拍，贴边翻列按此计算 |
| 悬停提示 | 带 45° 小三角 | 墨底圆角块，无箭头 | QSS 不支持伪元素 |
| 标本开关与虚线按钮 | Switch、ghost 虚线按钮 | 不实现 | 界面无消费方，控件不因换肤增删 |
| 进度条斜纹 | 斜纹填充 | 音量滑块自绘 45 度斜带 | QSS 没有重复渐变；`VolumeSlider` 只让 QSS 提供几何，凹槽与斜纹在 `paintEvent` 里画 |

验证结果：全仓十六进制色值只剩 `theme.py` 与系统托盘图标的白色绘制；新增 `tests/test_ui_theme.py` 六条护栏；`tests/test_ui_*.py`、设置、确认卡、终端、托盘、像素画窗口等 UI 测试全部通过。
