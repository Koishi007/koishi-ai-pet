# 上下文候选池与注入上限合并 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 合并候选池容量上限（`CONTEXT_MAX_ENTRIES`）与每轮注入上限（`CONTEXT_HISTORY_ENTRIES`）为同一个值，消除"淘汰出候选池的历史反而能被摘要看到、留在池子里选不进本轮的历史却永远看不到"的反逻辑现象，把"选不进本轮"的缺口清零，并修复一个被这次合并放大了触发概率的相邻负数切片 bug。

**Architecture:** 不引入新文件、不改变任何调用方签名。改 `pet/brain/base.py::BrainMixin` 的属性定义（合并两个上限、新增池子常态上界 `_MAX_POOL_ENTRIES`）、`_evict_context()` 里的一行防御性 clamp、`pet/brain/context_builder.py` 两处多轮构建入口传的 `max_entries`，以及配置/UI 里两个无代码路径读取的 key。

**Tech Stack:** Python 3.11+、pytest。沿用 `tests/test_context_pipeline.py` 里已有的 `_brain()` / `_entry()` 测试辅助函数，以及本仓库统一使用的 `monkeypatch.setattr(config, "KEY", value)` 惯例（参见 `tests/test_llm_resilience.py`）。

**Spec:** `docs/specs/2026-09-29-context-injection-pool-unify-design.md`

## Global Constraints

- 合并后的统一上限沿用 `CONTEXT_HISTORY_ENTRIES` 现有默认值 **15**，不改变默认的每轮上下文体验（spec §2）。
- `pet/brain/base.py::get_multi_turn_messages` 方法体本次**不改动** - 它的条数裁剪逻辑不是死代码，仍在被真实触发，删掉会丢失"优先保护摘要/系统消息"的行为（spec §3）。
- 不修改 `pet/brain/behavior.py`（`_llm_summarize` / `_flush_pending_summaries` 逻辑不变）。
- `pet/brain/context_builder.py` 两处多轮构建入口的 `max_entries` 传
  `self._brain._MAX_POOL_ENTRIES`（其余一行不动）。
- 不修改 `docs/architecture` 分支上的子系统文档、`_score_entry` 打分权重、`CONTEXT_HALF_LIFE_S`、`_EVICT_BATCH_SIZE`、摘要去重阈值 `_DEDUP_THRESHOLD`、长期记忆系统 `pet/brain/memory.py`（spec §7 明确排除，不在本计划范围内）。

---

## Task 1: 合并候选池上限到 `CONTEXT_HISTORY_ENTRIES`，删除无引用配置

**Files:**
- Modify: `pet/config.py:96` （删除 `CONTEXT_MAX_ENTRIES` 一行）、`pet/config.py:98` （删除 `CONTEXT_MAX_SUMMARIES` 一行）
- Modify: `pet/brain/base.py:28-34` （`_MAX_ENTRIES` 属性改指向、删除 `_MAX_SUMMARIES` 属性）
- Modify: `pet/ui/settings_window.py:699` （删除"备选上下文数量上限"设置行）
- Test: `tests/test_context_pipeline.py`（新增 `TestPoolCapUnified` 测试类）

**Interfaces:**
- Consumes: 无（不依赖其他任务产出）。
- Produces: `BrainMixin._MAX_ENTRIES` 属性此后恒等于 `config.CONTEXT_HISTORY_ENTRIES`（Task 2 会在同一个 `_evict_context()` 方法里继续使用这个属性，行为不变）。

- [x] **Step 1: 在 `tests/test_context_pipeline.py` 末尾追加失败测试**

打开 `tests/test_context_pipeline.py`，在文件末尾（`TestMergeSystemHistory` 类之后）追加：

```python
class TestPoolCapUnified:
    """池子容量与每轮注入上限同源：都取 CONTEXT_HISTORY_ENTRIES。"""

    def test_max_entries_follows_history_entries_config(self, monkeypatch):
        monkeypatch.setattr(config, "CONTEXT_HISTORY_ENTRIES", 5)
        brain = _brain([])
        assert brain._MAX_ENTRIES == 5

    def test_pool_size_stays_bounded_by_history_entries(self, monkeypatch):
        monkeypatch.setattr(config, "CONTEXT_HISTORY_ENTRIES", 5)
        brain = _brain([])
        for i in range(20):
            brain.add_context(role="assistant", content=f"消息{i}")
        # 池子上界 = 配置值 + 批量淘汰软上限（见 spec §3）；
        # 断言直接对照配置值而非 brain._MAX_ENTRIES，避免用被测实现自证其行为。
        assert brain.context_count() <= config.CONTEXT_HISTORY_ENTRIES + BrainMixin._EVICT_BATCH_SIZE

    def test_orphan_config_keys_removed(self):
        with pytest.raises(AttributeError):
            config.CONTEXT_MAX_ENTRIES
        with pytest.raises(AttributeError):
            config.CONTEXT_MAX_SUMMARIES

    def test_max_summaries_property_removed(self):
        assert not hasattr(BrainMixin, "_MAX_SUMMARIES")
```

- [x] **Step 2: 运行测试，确认按预期失败**

Run: `pytest tests/test_context_pipeline.py::TestPoolCapUnified -v`

Expected: 4 个测试全部 FAIL：
- `test_max_entries_follows_history_entries_config`：`assert 30 == 5`（`_MAX_ENTRIES` 现在还读的是 `CONTEXT_MAX_ENTRIES` 的真实默认值 30）
- `test_pool_size_stays_bounded_by_history_entries`：`assert 20 <= 11`（旧代码里池子上限是 30，20 次写入还远不会触发淘汰）
- `test_orphan_config_keys_removed`：`DID NOT RAISE <class 'AttributeError'>`（这两个 key 现在还在 `_KEY_META` 里）
- `test_max_summaries_property_removed`：`assert not True`（`_MAX_SUMMARIES` 属性现在还存在）

- [x] **Step 3: 修改 `pet/config.py`，删除两个无引用 key**

打开 `pet/config.py`，第 96、98 行当前是：

```python
    "CONTEXT_MAX_ENTRIES":       {"type": "int",      "default": 30,             "category": "behavior", "needs_restart": False, "hidden": False, "description": "备选上下文数量上限"},
    "CONTEXT_HISTORY_ENTRIES":   {"type": "int",      "default": 15,              "category": "behavior", "needs_restart": False, "hidden": False, "description": "每轮注入上下文数量上限"},
    "CONTEXT_MAX_SUMMARIES":     {"type": "int",      "default": 5,              "category": "behavior", "needs_restart": False, "hidden": True,  "description": "上下文最大摘要数"},
    "CONTEXT_HALF_LIFE_S":       {"type": "int",      "default": 1800,           "category": "behavior", "needs_restart": False, "hidden": True,  "description": "上下文评分半衰期(秒)"},
```

删除 `CONTEXT_MAX_ENTRIES` 与 `CONTEXT_MAX_SUMMARIES` 两行，改成：

```python
    "CONTEXT_HISTORY_ENTRIES":   {"type": "int",      "default": 15,              "category": "behavior", "needs_restart": False, "hidden": False, "description": "每轮注入上下文数量上限"},
    "CONTEXT_HALF_LIFE_S":       {"type": "int",      "default": 1800,           "category": "behavior", "needs_restart": False, "hidden": True,  "description": "上下文评分半衰期(秒)"},
```

- [x] **Step 4: 修改 `pet/brain/base.py`，合并 `_MAX_ENTRIES` 属性并删除 `_MAX_SUMMARIES`**

第 28-34 行当前是：

```python
    @property
    def _MAX_ENTRIES(self) -> int:
        return config.CONTEXT_MAX_ENTRIES

    @property
    def _MAX_SUMMARIES(self) -> int:
        return config.CONTEXT_MAX_SUMMARIES
```

改成：

```python
    @property
    def _MAX_ENTRIES(self) -> int:
        return config.CONTEXT_HISTORY_ENTRIES
```

（`_MAX_SUMMARIES` 属性整个删除；下面第 36 行开始的 `_MAX_HISTORY_SUMMARIES` 属性不受影响，原样保留。）

- [x] **Step 5: 运行测试，确认全部通过**

Run: `pytest tests/test_context_pipeline.py::TestPoolCapUnified -v`

Expected: 4 个测试全部 PASS。

- [x] **Step 6: 修改 `pet/ui/settings_window.py`，删除对应设置行**

第 699-700 行当前是：

```python
        sched_form.addRow("备选上下文数量上限:", self._line("CONTEXT_MAX_ENTRIES", "30", QIntValidator(10, 100)))
        sched_form.addRow("每轮注入上下文数量上限:", self._line("CONTEXT_HISTORY_ENTRIES", "15", QIntValidator(1, 50)))
```

删除第 699 行，只保留：

```python
        sched_form.addRow("每轮注入上下文数量上限:", self._line("CONTEXT_HISTORY_ENTRIES", "15", QIntValidator(1, 50)))
```

- [x] **Step 7: 全仓搜索确认没有遗漏引用**

Run: `grep -rn "CONTEXT_MAX_ENTRIES\|CONTEXT_MAX_SUMMARIES" pet/ tests/ --include="*.py"`

Expected: 无输出（有输出则说明还有遗漏的引用点，需先处理再进入下一步）。

- [x] **Step 8: 跑一次全量上下文测试文件，确认没有回归**

Run: `pytest tests/test_context_pipeline.py -v`

Expected: 全部 PASS（包括本任务新增的 4 个，以及文件里原有的所有测试）。

- [x] **Step 9: Commit**

```bash
git add pet/config.py pet/brain/base.py pet/ui/settings_window.py tests/test_context_pipeline.py
git commit -m "fix(context): 合并候选池容量上限到每轮注入上限，消除历史不可见的死区"
```

---

## Task 2: 修复 `_evict_context` 的 `base_limit` 负数切片风险

**Files:**
- Modify: `pet/brain/base.py`（`_evict_context` 方法内的 `base_limit` 计算行，Task 1 完成后该行在文件中的位置不变，仍在 `_evict_context` 方法体内）
- Test: `tests/test_context_pipeline.py`（新增 `TestEvictContextNegativeBaseLimit` 测试类）

**Interfaces:**
- Consumes: 无新接口依赖；与 Task 1 修改同一个文件的邻近方法，建议在 Task 1 完成并提交之后再做，避免同文件的两处改动混在一次 diff 里。
- Produces: 无后续任务依赖此任务的产出（本计划到此结束）。

- [x] **Step 1: 在 `tests/test_context_pipeline.py` 末尾追加失败测试**

在 `TestPoolCapUnified` 类之后追加：

```python
class TestEvictContextNegativeBaseLimit:
    """摘要与工具调用的预留席位超过上限时，普通对话要全部被淘汰进摘要队列，
    不能因为 base_limit 变负而被负数切片误保留。"""

    def test_normal_chats_all_queued_when_reserved_slots_exceed_cap(self, monkeypatch):
        monkeypatch.setattr(config, "CONTEXT_HISTORY_ENTRIES", 3)
        entries = (
            [_entry(f"摘要{i}", summary=True, age_s=100 + i) for i in range(2)]
            + [_entry(f"[工具调用] tool{i}", age_s=50 + i) for i in range(2)]
            + [_entry(f"对话{i}", age_s=i) for i in range(8)]
        )
        brain = _brain(entries)
        brain._evict_context()

        remaining_normal = [
            e for e in brain._context
            if not e.is_summary and not e.content.startswith("[工具调用]")
        ]
        assert remaining_normal == []
        assert len(brain._pending_summary_queue) == 8
```

- [x] **Step 2: 运行测试，确认按预期失败**

Run: `pytest tests/test_context_pipeline.py::TestEvictContextNegativeBaseLimit -v`

Expected: FAIL。`base_limit = 3 - 2(摘要) - 2(工具调用) = -1`，`normal_chats[-1:]`（负数切片）只会把排序后最后 1 条判定为待淘汰，其余 7 条被 `normal_chats[:-1]` 错误保留，因此：
- `remaining_normal == []` 断言失败（实际还剩 7 条）
- `len(pending_summary_queue) == 8` 断言失败（实际只有 1 条被排进队列）

- [x] **Step 3: 修改 `pet/brain/base.py`，给 `base_limit` 加下界**

在 `_evict_context()` 方法内找到：

```python
        base_limit = self._MAX_ENTRIES - len(summaries) - len(tool_calls)
```

改成：

```python
        base_limit = max(0, self._MAX_ENTRIES - len(summaries) - len(tool_calls))
```

- [x] **Step 4: 运行测试，确认通过**

Run: `pytest tests/test_context_pipeline.py::TestEvictContextNegativeBaseLimit -v`

Expected: PASS。`base_limit` 被 clamp 到 0，`normal_chats[0:]` 是全部 8 条（正确地全部进入待摘要队列），`normal_chats[:0]` 是空列表（正确地不保留任何普通对话）。

- [x] **Step 5: 跑一次全量测试文件，确认没有回归**

Run: `pytest tests/test_context_pipeline.py -v`

Expected: 全部 PASS（本任务新增的 1 个 + Task 1 新增的 4 个 + 文件原有的全部测试）。

- [x] **Step 6: 跑一次全量测试套件，确认没有波及其他模块**

Run: `pytest tests/ -v`

Expected: 全部 PASS。

- [x] **Step 7: Commit**

```bash
git add pet/brain/base.py tests/test_context_pipeline.py
git commit -m "fix(context): _evict_context 的 base_limit 加下界，避免负数切片误保留待淘汰对话"
```

---

## Task 3: 每轮注入覆盖池子常态上界，把残留缺口清零

> 动机见 spec §3 补记：池子在 `_MAX_ENTRIES` 到 `_MAX_ENTRIES + _EVICT_BATCH_SIZE` 之间震荡，
> `context_builder.py` 两处调用若固定传 `CONTEXT_HISTORY_ENTRIES`，震荡区间里最旧的最多 6 条
> 每轮都被条数裁剪排除。既然池子本身就是一个有界容器，注入的条数上限就该取池子的常态上界。

**Files:**
- Modify: `pet/brain/base.py`（新增只读属性 `_MAX_POOL_ENTRIES`）
- Modify: `pet/brain/context_builder.py`（`_build_multi_turn_autonomous` / `_build_multi_turn_chat` 两处 `max_entries` 的来源）
- Test: `tests/test_context_pipeline.py`（新增 `TestInjectionCoversWholePool` 测试类）

**Interfaces:**
- Consumes: Task 1 合并后的 `_MAX_ENTRIES`（= `CONTEXT_HISTORY_ENTRIES`）。
- Produces: `BrainMixin._MAX_POOL_ENTRIES`，供 `context_builder` 取注入条数上限。

**Global Constraints 补充**：
- `get_multi_turn_messages` 的方法体仍然**不动**（spec §3 补记），只改调用方传的数值。
- 不给工具调用加条数配额（spec §7 排除的调参范围） - 因此 `_MAX_POOL_ENTRIES` **不是**池子的
  硬上界，注释里需写清这一点，不留"池子必然不超过它"的错误断言。

- [x] **Step 1: 在 `tests/test_context_pipeline.py` 末尾追加失败测试**

在 `TestEvictContextNegativeBaseLimit` 之后追加 `TestInjectionCoversWholePool`：
断言 `_MAX_POOL_ENTRIES == _MAX_ENTRIES + _EVICT_BATCH_SIZE`；断言常态下（20 条普通对话）
池子落在 `(_MAX_ENTRIES, _MAX_POOL_ENTRIES]` 区间且 `get_multi_turn_messages` 能把池内条目
全部注入；用 monkeypatch 替换 `brain.get_multi_turn_messages` 做 spy，断言两个构建入口
传的 `max_entries` 是新上界。

- [x] **Step 2: 运行测试，确认按预期失败**

Run: `pytest tests/test_context_pipeline.py::TestInjectionCoversWholePool -v`

Expected: 3 个测试全部 FAIL - 前两个 `AttributeError: _MAX_POOL_ENTRIES`；
`test_builders_request_whole_pool` 为 `assert [5, 5] == [11, 11]`（两个调用点还在传
`CONTEXT_HISTORY_ENTRIES` 的真实值 5）。

- [x] **Step 3: 在 `pet/brain/base.py` 新增只读属性**

在 `_MAX_ENTRIES` 与 `_MAX_HISTORY_SUMMARIES` 之间加：

```python
    @property
    def _MAX_POOL_ENTRIES(self) -> int:
        """注入时该取多少条：池子在常态下的上界，即 `_evict_context` 的批量淘汰软上限。"""
        return self._MAX_ENTRIES + self._EVICT_BATCH_SIZE
```

（docstring 需写清"不是硬上界"的原因，见上方 Global Constraints 补充。）

- [x] **Step 4: 改 `pet/brain/context_builder.py` 两处调用**

`_build_multi_turn_autonomous`（`skip_last=0`）与 `_build_multi_turn_chat`（`skip_last=1`）里的
`max_entries=config.CONTEXT_HISTORY_ENTRIES` 都改成 `max_entries=self._brain._MAX_POOL_ENTRIES`。

- [x] **Step 5: 运行测试，确认通过**

Run: `pytest tests/test_context_pipeline.py -v`

Expected: 全部 PASS（26 个）。

- [x] **Step 6: 跑一次全量测试套件**

Run: `pytest tests/ -v`

Expected: 全部 PASS（406 passed）。

- [x] **Step 7: Commit**

```bash
git add pet/brain/base.py pet/brain/context_builder.py tests/test_context_pipeline.py
git commit -m "fix(context): 每轮注入按池子常态上界取条数，清零震荡区间的注入缺口"
```

---

## 完成后的手动验证（对应 spec §8）

三个任务都提交后，建议实际跑一次桌宠、产生一轮较长对话（触发至少一次 `_evict_context` 批量淘汰 +
一次 `_llm_summarize`），检查 `logs/koishiai.log` 里 `[BrainMixin] evicted` 与
`[Behavior] flushed pending summaries` 是否按预期衔接（淘汰即排队，排队后很快被摘要）。
这一步是验证真实运行效果，不是自动化测试，不作为独立 Task。

---


