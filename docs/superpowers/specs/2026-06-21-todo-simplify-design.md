# todo 技能简化 设计文档

日期: 2026-06-21
状态: design-approved

---

## 概述

精简 `todo` 技能——去掉截止日期、提醒系统、优先级、分类、备注等字段，退化为一个极简的待办清单：只有标题和完成状态。保留已建成的基础设施（SkillContext、SkillRegistry、Scheduler、右键菜单），保留任务管理面板。

核心交互：用户 → LLM → Skill 记录 → LLM 自行决定是否在回复中提及。不再有主动推送提醒。

---

## 一、数据层（storage.py）

### Schema 变化

```sql
-- 旧表（删除）
-- id | title | status | priority | category | due_date | notes | created_at | completed_at

-- 新表
CREATE TABLE todos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',   -- pending | done
    created_at TEXT NOT NULL
);
```

旧表直接删除建新，无需迁移。

### 方法变化

| 方法 | 变化 |
|------|------|
| `add(title)` | 去掉 priority/category/due_date/notes 参数 |
| `list(status=None)` | 去掉 priority/category/limit 参数，status=None 时不过滤 |
| `complete(id)` → `toggle(id)` | 可切换 pending ↔ done |
| `update(id, title)` | 只支持改标题 |
| `delete(id)` | 不变 |
| `get_due()` | **删除** |
| `get_pending_alarms()` | **删除** |
| `close()` | 保留 |

---

## 二、逻辑层（core.py）

| 方法 | 变化 |
|------|------|
| `add(title)` | 只剩 title 必填参数 |
| `list_todos(status)` | 去掉 priority/category/limit |
| `toggle(id)` | 改名（原 complete），支持 pending ↔ done 切换 |
| `delete(id)` | 不变 |
| `update(id, title)` | 只支持改 title |
| `check_due()` | **删除** |

---

## 三、注册入口（\_\_init\_\_.py）

- 删除 `_add_with_reminder` 和 `_update_with_reset` 包装函数，LLM handler 直接用 `_instance.add` / `_instance.update`
- 删除 `ReminderManager` 导入、`_reminder` 全局变量、`on_bind` 回调
- 删除 `check_due` 方法注册
- 各方法 args 描述精简，去掉 priority/category/due_date/notes
- `list` 方法 args 去掉 priority/category/limit

---

## 四、面板（panel.py）

- 删除筛选栏（status/priority filter row）
- 列表项只显示标题（done 状态加删除线样式），去掉 emoji 优先级标记、⏰ 截止、📁 分类、备注行
- 添加对话框只弹出标题输入框，去掉备注输入和 alarm 注册逻辑
- `_refresh()` 简化为 `_storage.list()` 不传筛选参数
- 底部统计标签保留

---

## 五、删除文件

| 文件 | 原因 |
|------|------|
| `reminder.py` | 提醒系统整体移除 |
| `parser.py` | 日期解析不再需要 |

---

## 六、不动文件

| 文件 | 原因 |
|------|------|
| `style.py` | 面板仍在使用 QSS 样式 |
| `context.py` | 基础设施，保留 |
| `registry.py` | 基础设施，保留 |

---

## 文件改动清单

| 文件 | 操作 |
|------|------|
| `pet/skills/plugins/todo/storage.py` | 修改：新 schema + 精简方法 |
| `pet/skills/plugins/todo/core.py` | 修改：删 check_due，精简参数，complete → toggle |
| `pet/skills/plugins/todo/__init__.py` | 修改：删 reminder 代码，精简 args，方法改名 |
| `pet/skills/plugins/todo/panel.py` | 修改：删筛选栏，简化列表项和对话框 |
| `pet/skills/plugins/todo/reminder.py` | **删除** |
| `pet/skills/plugins/todo/parser.py` | **删除** |

---

## 范围边界

**本次包含：**
- 上述 6 个文件的改动/删除

**本次不包含：**
- 任何新功能
- 基础设施文件改动
