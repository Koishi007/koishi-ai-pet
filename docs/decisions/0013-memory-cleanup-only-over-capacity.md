# 0013 记忆只在超容量时清理

- 状态：已采纳
- 相关代码：`pet/brain/memory.py`（`MemoryStore.maintenance`、`_MemoryRetriever.enforce_capacity`）
- 相关文档：[subsystems/memory.md](../subsystems/memory.md) §4

## 背景

记忆按策略注入上下文，每轮注入量由召回配额决定，与本地存量无关。
定期按天数硬删 L3 把记忆库长期压在低水位，表现为记忆累积不起来。

## 决定

- 定期维护只打分：`_demote_l2_to_l3` 降级、`_promote_by_tool_hits` 晋升，不删除任何记忆；
- 删除只在总数超过 `MEMORY_MAX_CAPACITY` 时由 `enforce_capacity` 执行，候选为除
  L1 + importance=5 外的全部记忆，按有效分从低到高淘汰；
- 事件汇总合并时删除组内旧条目属于写回事务，不受容量条件限制。

## 后果

- 记忆库可以越过过期天数长期累积，清理压力全部集中在容量边界；
- 淘汰只看有效分，不设年龄门槛：L3 半衰期 1~3 天，旧 L3 的有效分趋近 0，
  降级得到的 L3 保留到超容量时也排在候选前列；
- 代价：长期不清理时召回候选池变大，检索质量依赖有效分排序与 MMR 冗余抑制。
