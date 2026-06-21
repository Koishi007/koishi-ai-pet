# 设置界面设计规格

> 替代 .env 的桌面应用设置 UI，支持跨平台持久化和即时生效。

## §1 架构与配置持久化

### 配置文件

- **位置**：`QStandardPaths.AppDataLocation` 下的 `settings.json`
  - Windows: `%APPDATA%\DeskPet\settings.json`
  - macOS: `~/Library/Application Support/DeskPet/settings.json`
- **格式**：JSON，只存用户显式修改过的字段（非全量 dump）
- **加载优先级**：`.env`（出厂默认）→ `settings.json`（用户覆盖）
- 运行时 `Config` 实例持有当前有效值；设置界面修改时，更新 `Config` 属性 + 写入 `settings.json`

### Config 类改造（config.py）

```python
class Config:
    def __init__(self):
        load_dotenv()             # .env 作为底层默认
        self._load_env()          # 从环境变量加载默认值
        self._load_user_settings() # 从 settings.json 覆盖

    def save(self, key, value) -> tuple[bool, list[str]]:
        """保存单个设置到 settings.json 并即时生效。
        返回 (是否已应用, 需要重启的key列表)。
        """

    def _load_user_settings(self):
        """从 settings.json 读取覆盖值，更新自身属性。"""
```

- `_user_settings: dict` 持有 `settings.json` 覆盖值
- `save()` 返回 `(applied: bool, needs_restart: list[str])`，设置界面据此决定是否提示重启

### 新增文件

- `pet/settings.py` — `settings_path()`、`load_user_settings()`、`save_setting()`
- `pet/ui/settings_window.py` — 设置界面
- `pet/ui/styles.py` — 新增 `TAB_BAR_QSS`

### 改动文件

- `config.py` — 构造函数增加 `_load_user_settings()`；新增 `save()` 方法
- `pet/ui/system_tray.py` — 菜单增加「设置」项
- `main.py` — 关闭时清理设置窗口

## §2 设置界面布局

**窗口规格**：520×600，无边框圆角（与 debug_window 一致：`FramelessWindowHint` + `WA_TranslucentBackground`），自定义标题栏可拖拽。

**控件类型**：所有输入字段使用 QLineEdit（数值字段做类型校验），不用 QSpinBox。

### Tab 1：连接

| 配置项 | 环境变量 | 说明 |
|--------|---------|------|
| Brain 模式 | BRAIN | 下拉框 local/llm/ollama |
| API 地址 | LLM_URL / OLLAMA_BASE_URL | 根据 Brain 模式切换 |
| API Key | LLM_KEY | 密码模式，旁边有显示/隐藏 toggle |
| 模型名称 | LLM_MODEL | |
| 请求超时(秒) | LLM_TIMEOUT | |
| 最大重试次数 | LLM_MAX_RETRIES | |
| 重试延迟(秒) | LLM_RETRY_DELAY | |
| 最大重试延迟(秒) | LLM_RETRY_MAX_DELAY | |
| Prompt 缓存 | LLM_CACHE_PROMPT | QCheckBox |
| 「测试连接」按钮 | — | 点击后在子线程执行 LLM 调用 |

### Tab 2：行为

| 配置项 | 环境变量 | 说明 |
|--------|---------|------|
| 调度器 Fast 间隔(ms) | SCHEDULER_FAST_MS | |
| 调度器 Mid 间隔(ms) | SCHEDULER_MID_MS | |
| 调度器 Slow 间隔(ms) | SCHEDULER_SLOW_MS | |
| 自动启动 Fast | SCHEDULER_AUTO_START_FAST | QCheckBox |
| 自动启动 Mid | SCHEDULER_AUTO_START_MID | QCheckBox |
| 自动启动 Slow | SCHEDULER_AUTO_START_SLOW | QCheckBox |
| 空闲超时(ms) | SCHEDULER_IDLE_TIMEOUT_MS | |
| 动作超时(ms) | ACTION_TIMEOUT_MS | |
| 理智临界值 | SANITY_CRITICAL_THRESHOLD | |
| 交互 prompt - 抓取 | INTERACT_GRABBED_PROMPT | QTextEdit |
| 交互 prompt - 放下 | INTERACT_RELEASED_PROMPT | QTextEdit |
| 交互 prompt - 窗口消失 | INTERACT_WINDOW_DISAPPEARED_PROMPT | QTextEdit |

### Tab 3：外观

| 配置项 | 环境变量 | 说明 |
|--------|---------|------|
| Vision 开关 | VISION_ENABLED | QCheckBox |
| 截图缩放比例 | VISION_SCALE | |
| 技能插件启用 | SKILLS_ENABLED | 逗号分隔，* 为全部 |
| 宠物宽度 | PET_WIDTH | 需重启 |
| 宠物高度 | PET_HEIGHT | 需重启 |
| FPS | PET_FPS | 需重启 |
| 气泡最大宽度 | BUBBLE_MAX_WIDTH | 需重启 |
| 气泡字号 | BUBBLE_FONT_SIZE | 需重启 |
| 隐藏控制台 | HIDE_CONSOLE | 需重启 |
| 显示托盘图标 | SHOW_TRAY | 需重启 |

### Tab 4：人格

| 配置项 | 环境变量 | 说明 |
|--------|---------|------|
| 宠物人格 prompt | PET_PERSONALITY | 大文本框 |

### 底部操作栏

- 「保存」按钮（BUTTON_PRIMARY_QSS）
- 「重置为默认」按钮（BUTTON_QSS）
- 保存后如有需重启的配置项，弹出提示：「部分设置将在下次启动后生效」

## §3 即时生效机制

| 配置类别 | 即时生效方式 |
|---------|-------------|
| LLM 连接类 | 重建 LLM client → `self.brain._rebuild_client()` |
| 调度器 | `agent.scheduler.update_config()` 重置 timer |
| 交互 prompt | 直接赋值 `config.INTERACT_XXX`，下次调用即时读取 |
| 人格 prompt | 直接赋值 `config.PET_PERSONALITY` |
| Vision 开关/缩放 | 赋值 `config.VISION_*`，下次截图即时生效 |
| 技能插件 | **需重启**（运行中卸载不安全） |
| 外观参数 (尺寸/FPS/气泡/控制台/托盘) | **需重启**（窗口初始化后不宜运行时改） |

返回值 `(applied, needs_restart)` 由 `Config.save()` 根据 key 分类决定。

## §4 入口与交互流

### 托盘菜单入口

在 `SystemTrayManager._show_menu()` 中，于「隐藏/显示」和「退出」之间插入「⚙ 设置」菜单项。

### SettingsWindow 生命周期

- 单例模式（`_instance`），避免重复打开
- 打开时从 `config` 读取当前值填充各控件
- 点「保存」→ 逐项调用 `config.save()` → 根据返回的 `needs_restart` 决定是否弹提示
- 点「重置为默认」→ 删除 `settings.json` 中当前 tab 对应字段 → 重载 `.env` 默认值 → 刷新控件

### API Key 安全

- QLineEdit 设 `setEchoMode(QLineEdit.EchoMode.Password)`
- 旁边显示/隐藏 toggle 按钮
- 内存和磁盘均为明文（与 .env 一致，不做额外加密）