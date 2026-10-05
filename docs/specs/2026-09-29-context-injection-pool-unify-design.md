# 上下文候选池与每轮注入上限合并设计

> 消除"候选池容量"与"每轮注入上限"两套上限并存导致的反逻辑现象：被永久淘汰出候选池的历史，
> 反而能通过摘要重新被模型看到；仍留在候选池里、只是本轮没被选中的历史，却永远不可见。

## §0 背景

对话历史的存取分两层，代码都在 `pet/brain/base.py` 的 `BrainMixin`：

- **候选池**：`_context`（内存 list，持久化进 SQLite `context_entries` 表）。`add_context()` 写入后
  立即调用 `_evict_context()`，用 `CONTEXT_MAX_ENTRIES`（默认 30，UI 标签"备选上下文数量上限"）
  做容量上限。池子满了，最旧的普通对话被移进 `_pending_summary_queue`，之后由
  `behavior.py::_flush_pending_summaries()` 调 `_llm_summarize()` 压成一句摘要，
  再作为新的 `is_summary=True` 条目写回池子。
- **每轮注入**：`get_multi_turn_messages(max_entries, skip_last, token_budget)`，由
  `context_builder.py` 的 `_build_multi_turn_autonomous` / `_build_multi_turn_chat` 调用，
  两处都传 `max_entries=config.CONTEXT_HISTORY_ENTRIES`（默认 15）+
  `token_budget=config.CONTEXT_TOKEN_BUDGET`（默认 8192）。这是实际拼进 LLM 请求的那一份。

## §1 根因

`CONTEXT_MAX_ENTRIES`（30）与 `CONTEXT_HISTORY_ENTRIES`（15）是两个独立配置、独立生效：

- 池子里排位第 16～30 的条目，**每一轮**都会在 `get_multi_turn_messages` 的"条数淘汰"步骤里
  被判定为超出 `max_entries=15` 而排除 - 但它们还没资格被 `_evict_context` 淘汰出池子
  （池子上限是 30），所以永远不会进 `_pending_summary_queue`。
- 只要对话持续，新条目不断把旧条目往后挤，这些排位 16～30 的条目只会持续老化，
  **从被写入到最终被摘要之间，模型在任何一轮都看不到它** - 直到它被挤到第 31 位、
  真正被 `_evict_context` 淘汰的那一刻，才第一次有机会通过摘要露脸。

结论：候选池里"活着但选不进本轮"的条目没有任何补偿信号；而真正被淘汰、即将永久消失的条目，
反而立刻被摘要"复活"。这与直觉相反，也是用户观察到的现象的精确成因。

副产品发现：`CONTEXT_MAX_SUMMARIES`（config 里默认 5，`hidden=True`）从未被任何逐出逻辑读取，
`_evict_context` 实际用的是另一个动态计算的 `_MAX_HISTORY_SUMMARIES = max(2, CONTEXT_HISTORY_ENTRIES * 0.2)`。
这是历史迭代（`_MAX_SUMMARIES` 曾经是固定上限，后改为按比例动态计算）留下的无引用配置。

## §2 设计：合并为单一上限

**核心决定**：候选池容量与每轮注入上限收敛为同一个值，即 `CONTEXT_HISTORY_ENTRIES`（默认 15，
沿用现有值，不改变默认的每轮上下文体验）。`CONTEXT_MAX_ENTRIES` 作为独立配置项删除。

合并后，一条上下文只有两种命运：

| 状态 | 描述 |
|---|---|
| 仍在池内 | 一定会被 `get_multi_turn_messages` 选入本轮（注入的条数上限取池子的常态上界 `_MAX_ENTRIES + _EVICT_BATCH_SIZE`，见 §3 补记）。例外只有两种：① token 预算（`CONTEXT_TOKEN_BUDGET`）超限，② 工具调用密集、池子突破常态上界时条数裁剪的兜底。两种情况条目都没有离开池子，下一轮条件允许时仍可能入选，**都是有界、自愈的，不是永久丢失** |
| 被池子淘汰 | 保证进入 `_pending_summary_queue`，之后被压成摘要重新回到池子 |

不再存在"留在池子里但永远选不进任何一轮"的第三态 - "当轮偶尔选不进、几轮内自愈"和"永远选不进"是两件不同的事，前者可接受，后者才是反逻辑的根源，本次要解决的是后者。

### 生命周期（改动后）

1. `add_context()` 追加 → `_evict_context()` 用统一上限判断是否淘汰。
2. 存活期内，每轮 `get_multi_turn_messages()` 都会尝试选入（受 token 预算约束）。
3. 被 `_evict_context()` 淘汰的普通对话进 `_pending_summary_queue` →
   `_flush_pending_summaries()` → `_llm_summarize()` → 新的摘要条目重新进入池子，回到步骤 2。

### 代码改动

**`pet/config.py`**：删除两个 key。

```diff
-    "CONTEXT_MAX_ENTRIES":       {"type": "int",      "default": 30,             "category": "behavior", "needs_restart": False, "hidden": False, "description": "备选上下文数量上限"},
     "CONTEXT_HISTORY_ENTRIES":   {"type": "int",      "default": 15,              "category": "behavior", "needs_restart": False, "hidden": False, "description": "每轮注入上下文数量上限"},
-    "CONTEXT_MAX_SUMMARIES":     {"type": "int",      "default": 5,              "category": "behavior", "needs_restart": False, "hidden": True,  "description": "上下文最大摘要数"},
     "CONTEXT_HALF_LIFE_S":       {"type": "int",      "default": 1800,           "category": "behavior", "needs_restart": False, "hidden": True,  "description": "上下文评分半衰期(秒)"},
```

**`pet/brain/base.py`**：`_MAX_ENTRIES` 改指向 `CONTEXT_HISTORY_ENTRIES`；删除 `_MAX_SUMMARIES` 属性。

```diff
     @property
     def _MAX_ENTRIES(self) -> int:
-        return config.CONTEXT_MAX_ENTRIES
+        return config.CONTEXT_HISTORY_ENTRIES
-
-    @property
-    def _MAX_SUMMARIES(self) -> int:
-        return config.CONTEXT_MAX_SUMMARIES
```

**`pet/ui/settings_window.py`**：删除"备选上下文数量上限"这一行，"每轮注入上下文数量上限"保留
（用户不需要知道内部曾经分池子/注入两层）。

```diff
-        sched_form.addRow("备选上下文数量上限:", self._line("CONTEXT_MAX_ENTRIES", "30", QIntValidator(10, 100)))
         sched_form.addRow("每轮注入上下文数量上限:", self._line("CONTEXT_HISTORY_ENTRIES", "15", QIntValidator(1, 50)))
```

## §3 不改动：`get_multi_turn_messages` 的条数裁剪保持原样

`_evict_context()` 并不是"超过上限立刻裁到上限"，它有一个**批量淘汰的软上限**：

```python
_EVICT_BATCH_SIZE = 6    # 每次淘汰时触发摘要的软上限
...
base_limit = self._MAX_ENTRIES - len(summaries) - len(tool_calls)
soft_limit = base_limit + self._EVICT_BATCH_SIZE
if len(normal_chats) > soft_limit:      # 只有超过 base_limit+6 才会触发
    ...                                   # 触发时一次性裁回 base_limit
```

也就是说池子大小实际在 `_MAX_ENTRIES` 到 `_MAX_ENTRIES + 6` 之间震荡，不是硬顶在 `_MAX_ENTRIES`。
这是有意为之的批处理（避免每加一条普通对话就触发一次 LLM 摘要）。

**结论**：`get_multi_turn_messages` 的"条数超限"分支**不是死代码**，在池子处于震荡区间
（`_MAX_ENTRIES` 到 `+6` 之间，一个淘汰周期里大部分时间都在这个区间）时会被真实触发，
"优先淘汰普通对话、按分数保护摘要/系统消息"这段兜底逻辑一直在被使用，**不修改**。

只合并上限时，"选不进"的缺口最多 6 条（`_EVICT_BATCH_SIZE`），且必定在最多 6 轮之内被下一次
批量淘汰扫进摘要队列：缺口有界且很快自愈，永久不可见、没有摘要通道的根源被解决。缺口严格等于 0
不是必要目标（见 §2 表格），清零见 §3 补记。

### §3 补记：把注入的条数上限提到池子常态上界，缺口清零

清零的落点在**调用方**：`context_builder.py` 的两处多轮构建入口传
`max_entries=BrainMixin._MAX_POOL_ENTRIES`（= `_MAX_ENTRIES + _EVICT_BATCH_SIZE` 的只读属性）。
池子能装到 21 条，注入上限若卡在 15，震荡区间里最旧的最多 6 条每轮都被排除、最多要等 6 轮才被
下一次批量淘汰扫进摘要队列；传池子上界后，常态下池内条目全部入选本轮，缺口清零。
`get_multi_turn_messages` 内部一行未动（本节结论不变：那段裁剪逻辑留着当安全网）。

**关键细节**：`_MAX_ENTRIES + _EVICT_BATCH_SIZE` **不是池子的硬上界**，它是"普通对话"
配额的软上限。`_evict_context` 里 `base_limit = max(0, _MAX_ENTRIES - len(summaries) - len(tool_calls))`，
而工具调用（最近 `CONTEXT_HALF_LIFE_S` 内的 `[工具调用]` 条目）**没有独立条数配额** - 实测连写
25 条工具调用，池子就是 25 条，超过 21。所以准确表述是：

| 条件 | 池子上界 | `get_multi_turn_messages` 的条数裁剪 |
|---|---|---|
| 摘要数 + 近期工具调用数 ≤ `_MAX_ENTRIES`（常态） | `_MAX_ENTRIES + _EVICT_BATCH_SIZE` | 数值上不可能触发，纯防御 |
| 摘要数 + 近期工具调用数 > `_MAX_ENTRIES`（工具调用密集） | 无固定上界 | **仍在真实触发**，丢弃最旧的若干条 |

第二行那种情况下的"当轮缺席"同样是有界的：工具调用条目过了 `CONTEXT_HALF_LIFE_S` 就整条消失
（不进摘要队列），普通对话在配额被挤到 0 时会被全部淘汰进摘要队列（§4 修的正是这个），
所以最坏是被压到工具调用变老的这 30 分钟，不是永久。要把它也清零就得给工具调用加条数配额，
那是 §7 明确排除的调参范围，不在本次。

## §4 相邻修复：`_evict_context` 的负数切片风险

`base_limit = self._MAX_ENTRIES - len(summaries) - len(tool_calls)`：如果"摘要保留数
（`_MAX_HISTORY_SUMMARIES`，约 3）+ 最近 `CONTEXT_HALF_LIFE_S`（默认 30 分钟）内的工具调用条目数"
超过统一上限，`base_limit` 会变负。`normal_chats[base_limit:]`（取待淘汰项）与
`normal_chats[:base_limit]`（取保留项）在 `base_limit` 为负时是 Python 的负数切片语义
（"从倒数第 N 个开始"），不等于"淘汰全部普通对话"的意图，会导致淘汰了不该淘汰的那一批。

触发门槛约 12 条工具调用/30 分钟（摘要数 + 近期工具调用数超过统一上限 15），因此一并修复，
只加一处 `max(0, ...)`：

```diff
-        base_limit = self._MAX_ENTRIES - len(summaries) - len(tool_calls)
+        base_limit = max(0, self._MAX_ENTRIES - len(summaries) - len(tool_calls))
```

`base_limit=0` 时 `normal_chats[0:]` 是全部普通对话（正确地全部送入待摘要队列），
`normal_chats[:0]` 是空列表（正确地不保留任何普通对话） - 语义恢复正常。

## §5 改动清单

| 文件 | 改动 |
|---|---|
| `pet/config.py` | 删除 `CONTEXT_MAX_ENTRIES`、`CONTEXT_MAX_SUMMARIES` 两个 key |
| `pet/brain/base.py` | `_MAX_ENTRIES` 改指向 `CONTEXT_HISTORY_ENTRIES`；删除 `_MAX_SUMMARIES` 属性；`_evict_context` 的 `base_limit` 加 `max(0, ...)`；新增只读属性 `_MAX_POOL_ENTRIES`（`get_multi_turn_messages` 不改，见 §3） |
| `pet/brain/context_builder.py` | 两处多轮构建入口的 `max_entries` 由 `CONTEXT_HISTORY_ENTRIES` 改为 `self._brain._MAX_POOL_ENTRIES`（见 §3 补记） |
| `pet/ui/settings_window.py` | 删除"备选上下文数量上限"设置行 |
| `tests/test_context_pipeline.py` | 见 §6 |

不涉及 `pet/brain/behavior.py`（`_llm_summarize`/`_flush_pending_summaries` 逻辑不变）。

## §6 测试改动

- `TestGetMultiTurnMessages::test_count_eviction_drops_oldest_dialogue_first` **不改动**：
  `get_multi_turn_messages` 本身没有变化（见 §3），这个测试继续覆盖它的真实行为。
- **新增** 验证候选池上限已生效的测试：通过 `add_context()` 真实调用路径（而不是直接赋值
  `_context`）连续写入足够多的普通对话，断言最终 `context_count()` 回落到
  `CONTEXT_HISTORY_ENTRIES + _EVICT_BATCH_SIZE` 以内，且不再依赖已删除的 `CONTEXT_MAX_ENTRIES`。
- **新增** 覆盖 §4 修复的测试：构造 "`summaries` 数 + 最近 `tool_calls` 数 > 统一上限" 的场景，
  断言 `_evict_context` 后普通对话被正确全部移入 `_pending_summary_queue`，而不是被负数切片
  错误保留。
- **新增** 覆盖 §3 补记的测试：断言 `_MAX_POOL_ENTRIES == _MAX_ENTRIES + _EVICT_BATCH_SIZE`；
  断言常态下（20 条普通对话跑一遍淘汰）池子不超过上界且 `get_multi_turn_messages` 能把池内条目
  全部注入；用 monkeypatch 替换 `brain.get_multi_turn_messages` 做 spy，断言
  `_build_multi_turn_autonomous` / `_build_multi_turn_chat` 两处传的 `max_entries` 是新上界
  （而不是 `CONTEXT_HISTORY_ENTRIES`）。
- 现有测试中依赖 `CONTEXT_MAX_ENTRIES`/`CONTEXT_MAX_SUMMARIES` 默认值的断言需要核对
  （目前搜索确认没有，仅 `config.py`/`base.py`/`settings_window.py` 三处引用）。

## §7 不在本次范围内

- token 预算（`CONTEXT_TOKEN_BUDGET`）触发的单轮裁剪：条目仍留在池内、下一轮可能重新入选，
  不属于本次要解决的"永久不可见"问题，不新增摘要通道。
- `_score_entry` 的打分权重、`CONTEXT_HALF_LIFE_S`、`_EVICT_BATCH_SIZE`、摘要去重阈值
  （`_DEDUP_THRESHOLD`）等既有调参不改变。
- 长期记忆系统（`pet/brain/memory.py`）完全不涉及，两个系统本就没有数据交集。

## §8 风险与验证

- **行为变化**：普通对话进入摘要的节奏变快（池子装满更快），是"淘汰=摘要"这个不变式被严格执行
  后的自然结果；工具调用密集时段这个效应更明显（§4 已同步修复负数切片问题，不会出现"该淘汰的
  没淘汰"）。
- **验证方式**：`pytest tests/test_context_pipeline.py` 全绿；手动跑一轮长对话（触发至少一次
  `_evict_context` 淘汰 + 一次 `_llm_summarize`），检查 `logs/koishiai.log` 里
  `[BrainMixin] evicted` 与 `[Behavior] flushed pending summaries` 的时序衔接是否符合预期
  （淘汰即排队，排队后很快被摘要，不再有条目卡在"选不进也没被摘要"的中间态）。

## 附录：已考虑但否决的方案

- **方案 B - 保留两层结构，让"选不进本轮"的丢弃也推进摘要队列**：能达到同样的效果（消除不可见
  的中间态），但会让摘要触发频率从"池子满一次摘要一批"变成"几乎每轮都可能触发"，LLM 调用成本
  上升；且同一条内容可能先因"选不进"被摘要一次、之后又因"被淘汰"再摘要一次，需要额外做跨阶段
  去重。复杂度和成本换来的收益与 §2 的合并方案相同，故否决。
- **方案 C - 彻底取消摘要兜底，池子淘汰即纯遗忘**：逻辑上最干净（不再有任何"事后被想起"的通道），
  长期信息完全交给独立的 Memory 系统兜底。但会拿掉一个经过多次迭代打磨的产品特性
  （对话久了会淡成一句模糊摘要，而不是上下文突然中断 - `base.py` 的历史记录里能看到至少 6 次围绕
  这个机制的调参提交），是比"修复不一致"更大的产品行为改动，故否决。
