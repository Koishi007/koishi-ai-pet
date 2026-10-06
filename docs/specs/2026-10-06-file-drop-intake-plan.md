# 拖入文件交互 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让桌宠窗口接收文件拖放，用户在文件气泡里从三条动作中选择（尝一口 / 看一看 / 收进知识库），由内容嗅探、体积与像素闸门、拒绝名单守住边界；文件正文只进当轮 prompt，属性与记忆沿用既有结算通道。

**Architecture:** 三层落地。`pet/file_intake/` 是纯逻辑包（嗅探、解码链、截断、闸门、名单，不依赖 Qt）；`pet/ui/file_drop_handler.py` 承担判定与回调，依赖 Qt 类型但可脱离 `PetWindow` 单测；`pet/ui/pet_window.py` 的四个拖放事件只做转发。知识库经 `TOOL_REGISTRY.add_file_action` 扩展点接入，核心不 import 任何工具。

**Tech Stack:** Python 3.11+、PySide6 6.11.1、Pillow 12.2.0、pytest（`QT_QPA_PLATFORM=offscreen` 由 `tests/conftest.py:23` 设置）。

**Spec:** `docs/specs/2026-10-06-file-drop-intake-design.md`

## Global Constraints

- 原文件只读：不移动、不删除、不改写；三个拖放事件统一 `setDropAction(Qt.DropAction.CopyAction)` 后 `accept()`，不使用 `acceptProposedAction()`（spec §10）。
- 正文不落盘：`conversation_store`、上下文池、上下文摘要三处只写元信息（文件名、类型、大小），正文经附件参数只装配进当轮 messages（spec §5）。
- 代码不调用动画：姿态与动作由 LLM 输出的 `Action:` 行决定，程序不调用 `PetAnimator` / `PetActions`（spec §3）。
- 判定分层：悬停层只处理无本地路径、`FILE_DROP_ENABLED=false`、鼠标穿透三项；忙态与三类硬边界（数量、体积、拒绝名单）在放下层（spec §3）。
- `pet/file_intake/` 不顶层导入 Qt（`ARCH005` 规则），Qt 相关代码留在 `pet/ui/`。
- 不使用 `TOOL_REGISTRY._tools`（`ARCH006` 已把该访问冻结成历史债），动作列表走新增的公开查询方法。
- 新增 11 个配置键全部 `needs_restart: False` 且 `hidden: True`，不新增设置界面行（spec §8 未列 `pet/ui/settings_window.py`）。
- 每个任务结束跑 `python -m pytest -q` 与 `python scripts/gen_docs.py --check`。

---

## Task 1: 配置键与 `pet/file_intake` 纯逻辑包

**Files:**
- Modify: `pet/config.py`（`_KEY_META` 新增 11 行，紧随 `INTERACT_FED_PROMPT` 之后）
- Create: `pet/file_intake/__init__.py`、`pet/file_intake/types.py`、`pet/file_intake/sniff.py`、`pet/file_intake/text.py`、`pet/file_intake/image.py`
- Create: `tests/test_file_intake.py`
- Modify: `docs/architecture.md`（§9 模块职责表补 `pet/file_intake/` 一行）
- Regenerate: `docs/reference/config.md`

**Interfaces:**
- Consumes: 无。
- Produces（Task 2、3、5、6 依赖这些符号）：
  - `FileRef(path, name, suffix, size, kind)`，`kind ∈ {"text", "image", "binary", "dir"}`
  - `DropVerdict(status, refs, detail)`，`status ∈ {"ok", "too_large", "too_many", "forbidden"}`
  - `check_drop(paths, *, max_files, max_bytes, deny_patterns) -> DropVerdict`
  - `match_deny(name, patterns) -> bool`
  - `sniff(path) -> str`
  - `truncate(text, limit) -> str`
  - `load_text(path, limit) -> tuple[str, str]`（片段、降级说明；说明为空表示读到内容）
  - `load_text_full(path) -> str`
  - `load_image(path, max_pixels) -> PIL.Image | None`

- [x] **Step 1: 写失败测试**

新建 `tests/test_file_intake.py`，包含以下测试类。所有样本文件在 `tmp_path` 现造，不放仓库素材。

| 测试类 | 断言要点 |
|---|---|
| `TestCheckDrop` | 数量超上限返回 `too_many` 且 `refs` 为空；单文件超体积返回 `too_large`；命中拒绝名单返回 `forbidden`；全部通过返回 `ok` 且 `refs` 数量正确 |
| `TestDenyPatterns` | `basename` 的 glob 匹配；`.ENV`、`.PEM` 在大写形态下同样命中；`C:\x\note.pem.txt` 这类"路径里出现模式"不误伤（匹配对象是 basename）；目录名带 `.key` 后缀只按目录名比较 |
| `TestSniff` | UTF-8 的 `.md`、GBK 的 `.txt`、UTF-16LE 带 BOM 的 `.txt` 均判 `text`；含 `\x00` 的文件判 `binary`；`.pyc` 字节判 `binary`；`.png` 判 `image`；目录判 `dir` |
| `TestDecode` | GBK 与 UTF-16LE 能读到中文内容；解码失败返回 `binary` 与降级说明 |
| `TestTruncate` | `len(text) <= limit` 原样返回；`len(text) > limit` 时结果等于首 1000 + 省略标记 + 末 500，标记里的 `N` 等于 `len(text) - 1500`；两段不重叠 |
| `TestQuota` | 两个文件各自用满额度（`check_drop` + `load_text` 组合），不共享池 |
| `TestImageGate` | `max_pixels` 调到 100 的小图被拒（返回 `None`）；`max_pixels` 覆盖到足够大时返回 `PIL.Image`；长边被缩到 1024 以内 |
| `TestDirMeta` | 目录的 `size` 为 0、`kind` 为 `dir`；条目计数上限 200、展示前 20 个名字 |

- [x] **Step 2: 运行测试，确认按预期失败**

Run: `python -m pytest tests/test_file_intake.py -v`

Expected: 全部 FAIL，`ModuleNotFoundError: No module named 'pet.file_intake'`。

- [x] **Step 3: 新增配置键**

在 `pet/config.py` 的 `_KEY_META` 里、`INTERACT_FED_PROMPT` 之后插入：

```python
    "FILE_DROP_ENABLED":         {"type": "bool",  "default": True, "category": "behavior", "needs_restart": False, "hidden": True, "description": "拖入文件交互总开关"},
    "FILE_DROP_READ_CONTENT":    {"type": "bool",  "default": True, "category": "behavior", "needs_restart": False, "hidden": True, "description": "读取文件内容开关，关闭后所有文件只传元信息"},
    "FILE_DROP_MAX_CHARS":       {"type": "int",   "default": 1500, "category": "behavior", "needs_restart": False, "hidden": True, "description": "单文件进入 prompt 的字符上限"},
    "FILE_DROP_TASTE_CHARS":     {"type": "int",   "default": 300,  "category": "behavior", "needs_restart": False, "hidden": True, "description": "尝一口路径的字符上限"},
    "FILE_DROP_MAX_FILE_MB":     {"type": "int",   "default": 10,   "category": "behavior", "needs_restart": False, "hidden": True, "description": "单文件体积上限(MB)"},
    "FILE_DROP_MAX_FILES":       {"type": "int",   "default": 5,    "category": "behavior", "needs_restart": False, "hidden": True, "description": "单次拖入数量上限"},
    "FILE_DROP_MAX_PIXELS":      {"type": "int",   "default": 40000000, "category": "behavior", "needs_restart": False, "hidden": True, "description": "draft 之后的像素上限，判据为 >="},
    "FILE_DROP_DENY_PATTERNS":   {"type": "str_list", "default": [".env*", "*.key", "*.pem", "*.pfx", "*.p12", ".npmrc", ".netrc", ".pgpass", ".git-credentials", "id_rsa*", "id_ed25519*"], "category": "behavior", "needs_restart": False, "hidden": True, "description": "拒收名单，按文件名 glob 匹配"},
    "FILE_DROP_BUBBLE_TIMEOUT_S": {"type": "int",  "default": 12,   "category": "behavior", "needs_restart": False, "hidden": True, "description": "文件气泡无操作收起秒数"},
    "INTERACT_FILE_PROMPT":      {"type": "str",   "default": "",   "category": "behavior", "needs_restart": False, "hidden": True, "description": "尝一口交互的自定义 prompt 模板"},
    "INTERACT_FILE_REJECT_PROMPT": {"type": "str", "default": "",   "category": "behavior", "needs_restart": False, "hidden": True, "description": "拒收台词的自定义 prompt 模板"},
```

- [x] **Step 4: 实现纯逻辑包**

`pet/file_intake/sniff.py` 的判定规则：

1. `os.path.isdir(path)` 为真先返回 `dir`，不进嗅探。
2. 后缀命中图片集（`.png .jpg .jpeg .webp .gif .bmp .tiff .ico`）判 `image`；图片文件头含 `\x00`，先判 NUL 会把它们误判成二进制。
3. 读文件头 8 KB；带 BOM 且能解码判 `text`。
4. 无 BOM 时含 `\x00` 或控制字符占比超过 10% 判 `binary`。
5. 其余按解码链：`utf-8` 与 `gb18030` 都尝试，按常见字符占比打分取高者、同分取 `utf-8`；全部失败判 `binary`。不要用 `utf-16` 解无 BOM 的数据，它会按本机字节序硬解成合法文本。

前两步的顺序与第 5 步的打分规则见设计文档 §4.1 与 §4.2 的实测反例，实现时不要退化成「BOM → utf-8 → gb18030 先到先得」。

`match_deny(name, patterns)`：取 `os.path.basename` 后 `casefold()`，逐个 `fnmatch.fnmatchcase`；不使用 `os.path.normcase`（POSIX 上是恒等操作）。

`pet/file_intake/text.py` 的截断规则：`len(text) <= limit` 原样返回；否则返回首 `limit * 2 // 3` 字符 + `……（已省略 N 字符）……` + 末 `limit - 首` 字符，`N = len(text) - limit`，标记本身不计入配额。默认额度 1500 即首 1000 与末 500。`load_text` 返回 `(片段, 说明)`，读不到内容时片段为空、说明写明原因（二进制、无读权限、被占用、目录、图片）。

`pet/file_intake/image.py` 的顺序固定为 `Image.open`（只读文件头）→ `draft("RGB", (1024, 1024))` → 判 `size[0] * size[1] >= max_pixels` → 解码 → 缩到长边 1024。`DecompressionBombError` 与 `DecompressionBombWarning` 都按失败处理并返回 `None`；`DecompressionBombWarning` 用 `warnings.catch_warnings()` + `simplefilter("error")` 转成异常。

`pet/file_intake/types.py` 的 `FileRef` 为 frozen dataclass，`size` 对目录记 0。

- [x] **Step 5: 运行测试，确认通过**

Run: `python -m pytest tests/test_file_intake.py -v`

Expected: 全部 PASS。

- [x] **Step 6: 重生成参考文档并登记新包**

```bash
python scripts/gen_docs.py
python -m pytest tests/test_docs.py -q
```

在 `docs/architecture.md` §9 模块职责表里、`pet/tools/` 那行之前插入：

```
| `pet/file_intake/` | 拖入文件的纯逻辑：类型嗅探、解码链、截断与额度、图片闸门与缩放、拒绝名单 | `sniff.py`、`text.py`、`image.py` |
```

Expected: `tests/test_docs.py` 全 PASS（新包必须出现在架构文档里，否则该测试红）。

- [x] **Step 7: 全量测试并提交**

Run: `python -m pytest -q`

```bash
git add pet/config.py pet/file_intake docs/reference/config.md docs/architecture.md tests/test_file_intake.py
git commit -m "feat(filedrop): 新增拖入文件的纯逻辑包与配置键"
```

---

## Task 2: `pet/ui/file_drop_handler.py` 判定与回调

**Files:**
- Create: `pet/ui/file_drop_handler.py`
- Create: `tests/test_file_drop_handler.py`

**Interfaces:**
- Consumes: Task 1 的 `check_drop` / `sniff` / `FileRef` / `DropVerdict`。
- Produces（Task 3、4 依赖）：
  - `paths_from_mime(mime) -> list[str]`
  - `FileDropHandler(hover_state, on_show_bubble, on_reject, on_busy_event)`，方法 `accept_hover(has_local_paths: bool) -> bool` 与 `handle_drop(paths: list[str]) -> None`
  - `hover_state` 是一个无参可调用对象，返回 `(enabled: bool, penetration: bool)`；三个回调分别对应"显示气泡（传 `refs`）"、"发拒收请求（传类型字符串）"、"写忙态一次性事件"

**约束：** handler 不持有 `PetWindow`，三个回调由装配层注入；`handle_drop` 内部按 spec §3 的分层表调用 `check_drop`，忙态由 `hover_state` 之外的第 4 个可调用对象 `is_busy()` 提供。

- [x] **Step 1: 写失败测试**

新建 `tests/test_file_drop_handler.py`：

| 测试类 | 断言要点 |
|---|---|
| `TestPathsFromMime` | `QMimeData` 里放两个 `QUrl.fromLocalFile`，返回两个本地路径；空 `QMimeData` 返回空列表；只有 `text/plain` 的 `QMimeData` 返回空列表 |
| `TestHoverLayer` | `enabled=False` 时 `accept_hover(True)` 为假；`penetration=True` 时为假；`has_local_paths=False` 时为假；三项都不命中时为真 |
| `TestDropLayer` | 数量超限、体积超限、名单命中分别只调用 `on_reject` 一次且类型为 `too_many` / `too_large` / `forbidden`；通过时只调用 `on_show_bubble` 一次且传入 `refs`；`is_busy()` 为真时调用 `on_busy_event` 且不调用 `on_reject`，也不调用 `on_show_bubble` |

用假回调（`unittest.mock.Mock` 或记录列表）断言调用序列，不构造 `PetWindow`。

- [x] **Step 2: 运行测试，确认按预期失败**

Run: `python -m pytest tests/test_file_drop_handler.py -v`

Expected: FAIL，`ModuleNotFoundError: No module named 'pet.ui.file_drop_handler'`。

- [x] **Step 3: 实现 handler**

分层按 spec §3：`accept_hover` 只看三项（`enabled`、`penetration`、`has_local_paths`）；`handle_drop` 先查 `is_busy()`，再调 `check_drop`，顺序为忙态 → 数量 → 体积 → 名单 → 通过。所有判定都不读文件内容。

- [x] **Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_file_drop_handler.py -v`

Expected: 全部 PASS。

- [x] **Step 5: 全量测试并提交**

Run: `python -m pytest -q`

```bash
git add pet/ui/file_drop_handler.py tests/test_file_drop_handler.py
git commit -m "feat(filedrop): 新增拖放判定层，分层处理悬停与放下"
```

---

## Task 3: `pet/ui/file_bubble.py` 与气泡生命周期

**Files:**
- Create: `pet/ui/file_bubble.py`
- Modify: `pet/ui/pet_window.py`（新增 `set_file_bubble`；`enterEvent` 让位；`mousePressEvent` 与 `hide()` 的隐藏清单补入文件气泡）
- Modify: `pet/ui/__init__.py`（模块说明补一句）

**Interfaces:**
- Consumes: Task 1 的 `FileRef`；Task 6 的 `TOOL_REGISTRY.file_actions()`（Task 6 未完成时先按空列表渲染，只显示两条核心动作）。
- Produces（Task 4、5 依赖）：
  - `FileBubble(pet_window, parent=None)`：`show_files(refs, actions)`、`hide_bubble()`、信号 `action_chosen = Signal(str, object)`（动作 id 与 `refs`）、`idle_timeout = Signal()`（12 秒未操作，超时值取 `FILE_DROP_BUBBLE_TIMEOUT_S`）
  - 单例复用：重复 `show_files` 重建内容并重置定时器，不新建窗口

**约束：** 动作按钮渲染规则见 spec §5 与 §5.1：两条核心动作固定显示，工具动作按 `accepts` 与 `kind` 是否匹配决定是否出现；气泡可见期间每 1 秒重读一次注册表刷新按钮。

- [x] **Step 1: 实现气泡**

跟随与显示结构复用 `pet/ui/music_bubble.py`（`_follow_timer`、`show_bubble` / `hide_bubble` 的成对动画）。三条收起路径在 `pet_window` 侧接线：`mousePressEvent`（`pet/ui/pet_window.py:283-294` 的隐藏清单）、`hide()`（`:606-618` 的隐藏清单）、`enterEvent`（文件气泡可见时不显示 chat / feed / music）。

- [ ] **Step 2: 手动验证生命周期**

Run: `python -m pet`

按 spec §9 手动清单第 19 条检查：拖入后抓起桌宠、从托盘隐藏桌宠，气泡均随之消失；气泡显示期间悬停桌宠，不出现 chat / feed / music；点一下桌宠后悬停行为恢复。

- [x] **Step 3: 全量测试并提交**

Run: `python -m pytest -q`

```bash
git add pet/ui/file_bubble.py pet/ui/pet_window.py pet/ui/__init__.py
git commit -m "feat(filedrop): 新增文件气泡与生命周期接线"
```

---

## Task 4: `pet_window` 四个拖放事件转发

**Files:**
- Modify: `pet/ui/pet_window.py`

**Interfaces:**
- Consumes: Task 2 的 `FileDropHandler` 与 `paths_from_mime`；Task 3 的 `FileBubble`。
- Produces: 窗口侧的四条事件处理；`setAcceptDrops(True)` 在 `__init__` 里恒定开启。

- [x] **Step 1: 接线四个事件**

```python
def dragEnterEvent(self, event):
    if not self._file_drop.accept_hover(bool(event.mimeData().urls())):
        event.ignore()
        return
    event.setDropAction(Qt.DropAction.CopyAction)
    event.accept()

def dragMoveEvent(self, event):
    # 不接受则收不到 dropEvent（探针实测，见 spec §0.1）
    event.setDropAction(Qt.DropAction.CopyAction)
    event.accept()

def dragLeaveEvent(self, event):
    event.accept()

def dropEvent(self, event):
    event.setDropAction(Qt.DropAction.CopyAction)
    event.accept()
    self._file_drop.handle_drop(paths_from_mime(event.mimeData()))
```

三个事件都固定 `CopyAction`，不使用 `acceptProposedAction()`（spec §10）。

- [ ] **Step 2: 手动验证拖放**

Run: `python -m pet`

按 spec §9 手动清单第 4、5、7、9 条检查：超限拒收并回应、拖过多文件拒收一次、拖着文件反复划过不产生请求与动画、穿透开启时无效且关闭后恢复。

- [x] **Step 3: 全量测试并提交**

Run: `python -m pytest -q`

```bash
git add pet/ui/pet_window.py
git commit -m "feat(filedrop): 窗口接收拖放并转发到判定层"
```

---

## Task 5: 交互模板与附件参数

**Files:**
- Modify: `pet/brain/prompts.py`（新增 `interact_file_prompt(...)`、`interact_file_reject_prompt(reason)` 与内置模板）
- Modify: `pet/brain/context_builder.py`（`build_interact` 与 `build_chat_decide` 增加附件参数）
- Modify: `pet/agent/pet_agent.py`（`trigger("analyze")` 与 chat 共用 `_trigger_dialogue` / `_dialogue_pipeline`，透传附件，正文不进 `message`）
- Modify: `pet/app.py`（接线：`FileBubble.action_chosen` → 读取 → `agent.trigger(...)`）
- Create: `tests/test_file_attachment.py`
- Regenerate: `docs/reference/prompt-blocks.md`

**Interfaces:**
- Consumes: Task 3 的 `FileBubble.action_chosen`；Task 1 的 `load_text` / `load_image`。
- Produces：
  - `file_read_prompt(meta) -> str`、`interact_file_prompt(names) -> str`、`interact_file_reject_prompt(reason, names=()) -> str`
  - `build_interact(event_hint, attachment_text=None, attachment_image=None)`、`build_chat_decide(user_message, window_context, screenshot=True, attachment_text=None, attachment_image=None)`、`build_analyze_decide(user_message, window_context, screenshot=False, attachment_text=None, attachment_image=None)`
  - `PetAgent.trigger("analyze", message=..., log_message=None, attachment_text=None, attachment_image=None)`、`trigger("chat", ...)` 与 `trigger("interact", hint=..., ...)` 的同类参数

**约束：** 元信息（文件名、类型、大小）走 `message` 与 `context_hint`，正文只进附件参数；附件是 `PIL.Image` 时由 `ContextBuilder` 用 `self._screen_reader.prepare_image(image=...)` 编码，对话通道同一轮不再附截图，分析通道（看一看）不带截图。

- [x] **Step 1: 写失败测试**

新建 `tests/test_file_attachment.py`（不构造 agent，用一个桩 brain 提供 `get_multi_turn_messages` 与 `_MAX_POOL_ENTRIES`）：

| 测试 | 断言要点 |
|---|---|
| `test_attachment_text_only_in_current_turn` | `build_chat_decide(..., attachment_text="MARKER")` 的结果里，`MARKER` 只出现在最后一条 user 消息，历史条目里没有 |
| `test_attachment_image_replaces_screenshot` | 传入 `attachment_image` 且 `VISION_ENABLED` 为真时，最后一条 user 是包含 `image_url` 的列表，且图片内容来自附件（与 `_prepare_image` 的桩返回值不同） |
| `test_interact_prompt_contains_meta_not_body` | `interact_file_prompt` 的输出含文件名与类型；正文片段按额度截断 |
| `test_reject_prompt_by_reason` | 四种类型各自产出不同文案，且模板要求限定 `Mood:` / `Vitals:` 增量（见 spec §6.1 的规则表） |

- [x] **Step 2: 运行测试，确认按预期失败**

Run: `python -m pytest tests/test_file_attachment.py -v`

Expected: FAIL，`TypeError: build_chat_decide() got an unexpected keyword argument 'attachment_text'`。

- [x] **Step 3: 实现参数与接线**

`pet/app.py` 的接线按 spec §5：尝一口走 `trigger("interact", hint=interact_file_prompt(...), record_context=True, context_hint=<元信息>, thinking=False, enable_tools=False, is_play_loading=False, delay_ms=150)`；看一看走 `trigger("analyze", message=file_read_prompt(<元信息>), log_message=<元信息>, attachment_text=<正文>)`；两者都不把正文写进 `message` / `context_hint`。读取在 daemon 线程执行，完成后回主线程调用 trigger（`PIL.Image` 对象在此移交所有权，UI 侧之后不再触碰）。

- [x] **Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_file_attachment.py -v`

Expected: 全部 PASS。

- [x] **Step 5: 重生成提示词参考文档并全量测试**

```bash
python scripts/gen_docs.py
python -m pytest -q
```

- [ ] **Step 6: 手动验证正文不落盘**

按 spec §9 手动清单第 14 条：造一个含唯一标记串的文本文件，看一看之后在聊天历史窗口与 `pet.db` 里搜索该标记，均无命中，而当轮日志能看到正文已装配。

- [x] **Step 7: Commit**

```bash
git add pet/brain/prompts.py pet/brain/context_builder.py pet/agent/pet_agent.py pet/app.py docs/reference/prompt-blocks.md tests/test_file_attachment.py
git commit -m "feat(filedrop): 三条动作通道与附件参数，正文只进当轮"
```

---

## Task 6: 知识库工具的文件动作扩展点

**Files:**
- Modify: `pet/tools/registry.py`（`ToolDef.file_actions` 字段、`add_file_action(...)`、公开查询方法 `file_actions()`）
- Modify: `pet/tools/knowledge/__init__.py`（`register()` 内声明文件动作）
- Modify: `pet/tools/context.py`（`request_interact` 透传 `thinking` 与 `enable_tools`）
- Modify: `tests/test_file_drop_handler.py`（追加注册表测试类）
- Modify: `docs/tool-development.md`（登记扩展点）

**Interfaces:**
- Consumes: Task 1 的 `load_text_full`。
- Produces：
  - `ToolRegistry.add_file_action(tool_name, action_id, label, handler, accepts="any")`
  - `ToolRegistry.file_actions() -> list[dict]`：只返回已注册且已启用的工具声明的动作，每项含 `tool`、`id`、`label`、`handler`、`accepts`；不使用 `_tools` 私有访问
  - 工具 handler 签名 `handler(files: list[FileRef]) -> dict`，返回 `{"ok": bool, "summary": str}`

- [x] **Step 1: 追加失败测试**

在 `tests/test_file_drop_handler.py` 追加 `TestFileActions`：未注册时 `file_actions()` 为空；注册后返回动作且 `accepts` 原样带出；`set_enabled(tool, False)` 后该动作不再出现；`file_actions()` 的名字里不出现 `_tools`（用 `monkeypatch` 删除属性后仍可调用，确保走的是公开方法）。

- [x] **Step 2: 运行测试，确认按预期失败**

Run: `python -m pytest tests/test_file_drop_handler.py::TestFileActions -v`

Expected: FAIL，`AttributeError: 'ToolRegistry' object has no attribute 'file_actions'`。

- [x] **Step 3: 实现扩展点与知识库声明**

`pet/tools/knowledge/__init__.py` 在 `register()` 内、`add_menu_action` 之后声明：

```python
def _ingest_files(files):
    """文件动作：把文件正文作为文档入库（后台线程调用）。"""
    added = []
    for ref in files:
        content = load_text_full(ref.path)
        if not content.strip():
            continue
        result = _instance.add_document(
            title=ref.name, content=content, tags="", source="file_drop")
        added.append(result)
    if not added:
        return {"ok": False, "summary": "这些文件里没有能读出来的文字"}
    TOOL_CTX.request_interact(
        hint=f"用户把文件交给你收进知识库了，共 {len(added)} 份，用一句话回应",
        thinking=False, enable_tools=False)
    return {"ok": True, "summary": f"已收进知识库 {len(added)} 份"}
```

`accepts="text"`；handler 由气泡侧在 daemon 线程调用，工具自己循环处理全部文件并只播报一次（spec §5.1 的调用次数约定）。

- [x] **Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_file_drop_handler.py -v`

Expected: 全部 PASS。

- [x] **Step 5: 记录扩展点**

在 `docs/tool-development.md` 增加一节，写明 `add_file_action` 的声明位置、`handler` 契约、`accepts` 取值（`text` / `image` / `any`）、返回值约定与"工具缺席表现为按钮不存在"。同时在 `docs/architecture.md` §12 常见改动入口表补一行"加一个文件动作"。

- [ ] **Step 6: 手动验证工具缺席与入库**

按 spec §9 手动清单第 13、15、16、17 条：入库后知识库面板可见该文档且来源为 `file_drop`；`TOOLS_ENABLED` 排除 `knowledge` 后气泡只显示两条核心动作；右键关闭该工具后动作消失；启动即拖入时按钮随后自行出现。

- [x] **Step 7: 全量测试并提交**

Run: `python -m pytest -q`

```bash
git add pet/tools/registry.py pet/tools/knowledge/__init__.py pet/tools/context.py docs/tool-development.md docs/architecture.md tests/test_file_drop_handler.py
git commit -m "feat(filedrop): 工具文件动作扩展点与知识库入库"
```

---

## Task 7: 收尾

**Files:**
- Modify: `docs/README.md`（若本文档或设计文档尚未登记）
- Delete: `scripts/spike_dragdrop.py`
- Modify: `docs/specs/2026-10-06-file-drop-intake-design.md`（把 §0.1 的"探针可删除"改为已删除，并补一句平台覆盖仍只含 Windows）

- [ ] **Step 1: 跑一遍手动清单**

按 spec §9 的 19 条逐项检查，重点四条：第 6 条（重复拖入各触发 + 连击防抖）、第 11 条（像素闸门降级 + 48 MP JPEG 走 draft）、第 12 条（目录只显示条目摘要）、第 18 条（忙态写一次性事件）。

- [x] **Step 2: 删除探针并更新设计文档**

```bash
git rm scripts/spike_dragdrop.py
```

- [x] **Step 3: 收尾验证**

```bash
python -m pytest -q
python scripts/gen_docs.py --check
```

Expected: 全部通过，退出码 0。

- [x] **Step 4: Commit**

```bash
git add -A docs/ scripts/
git commit -m "chore(filedrop): 收尾清理探针脚本与文档"
```

---

## 验收对照

实现完成后，spec §9 的自动化部分应满足：

| 命令 | 覆盖 |
|---|---|
| `python -m pytest tests/test_file_intake.py` | 嗅探、解码链、截断、额度、体积与数量上限、名单、图像闸门、目录分类 |
| `python -m pytest tests/test_file_drop_handler.py` | 分层判定、回调序列、工具动作注册与查询 |
| `python -m pytest tests/test_file_bubble.py` | 动作可用性（视觉/内容开关）、置灰文案、目录摘要回填 |
| `python -m pytest tests/test_ui_debounce.py` | 防抖窗口与气泡按钮的连击保护 |
| `python -m pytest tests/test_file_attachment.py` | 附件只进当轮、图片替代截图、模板内容与拒收数值规则、分析任务规则 |
| `python -m pytest tests/test_app_file_actions.py` | 装配层两条动作分支、正文送达、工具返回值提示、message 与 log_message 分离 |
| `python -m pytest tests/test_knowledge_file_action.py` | 知识库文件动作的 hint 名单与读不出内容时的降级 |
| `python -m pytest tests/test_docs.py tests/test_architecture_contracts.py` | 文档索引与架构红线（不新增私有访问、纯逻辑包不拖入 Qt） |
| `python scripts/gen_docs.py --check` | 配置、提示词、工具参考文档与代码一致 |

手动部分（19 条）覆盖操作系统事件投递、气泡生命周期、知识库入库与工具缺席四种状态，以及正文不落盘的人工核对。
