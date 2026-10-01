# 记忆系统

桌宠的长期记忆：写在 SQLite 里、按衰减与召回策略挑选、每轮对话注入一小段。
代码集中在 `pet/brain/memory.py`（约 1550 行），另见 `pet/brain/embedding_client.py`（向量）与
`pet/ui/memory_window.py`（管理界面）。

概念定义见 [glossary](../glossary.md) 的「记忆」一节。

## 1. 数据模型

`memories` 表（建表与迁移都在 `memory.py` 的 `_create_table`）：

| 字段 | 含义 |
|---|---|
| `id` | 主键 |
| `category` | 类别：`user_fact` / `user_preference` / `conversation` / `event`（取值来自提示词 `_MEMORY_GUIDE`，**代码不校验白名单**） |
| `content` | 记忆正文 |
| `keywords` | 逗号分隔的关键词串（不是 JSON） |
| `importance` | 1~5，默认 3；5 = 核心身份 |
| `created_at` | ISO 时间串，字典序 = 时间序，因此日期区间可以直接用字符串比较并命中索引 |
| `access_count` | 被 `touch` 的次数（**不含被动注入**） |
| `last_accessed_at` | 最近一次被召回的时间 |
| `has_embedding` | 向量是否与 content 一致（0 = 只能被关键词命中） |
| `level` | `L1` 核心 / `L2` 情景 / `L3` 临时 |
| `tool_hits` | `recall__search` 主动检索计数，按轮衰减，用于晋升 |

索引有两处细节：`idx_l3_access` 是 `(level, last_accessed_at) WHERE level='L3'` 的部分索引，
专门加速 L3 过期清理；而注释写的「复合索引」`idx_level_importance` 实际只有 `importance` 一列，
与 `idx_importance` 重复，查询计划以实际建表语句为准。

向量表 `memories_vec`（sqlite-vec 虚拟表）：`memory_id` + `embedding FLOAT[EMBEDDING_DIM] distance_metric=cosine`。
距离度量默认是 L2，所以代码启动时会检查、必要时 `DROP TABLE` 重建；维度不符也重建，并把
`memories` 的 `has_embedding` 全部清 0。

### 衰减半衰期（`_HALF_LIFE`）

| level | i=5 | i=4 | i=3 | i=2 | i=1 |
|---|---|---|---|---|---|
| L1 | 永不衰减 | 30 天 | 21 天 | 14 天 | 7 天 |
| L2 | 60 天 | 30 天 | 14 天 | 7 天 | 3 天 |
| L3 | 3 天 | 3 天 | 2 天 | 1 天 | 1 天 |

未知 level 回退到 L2 表，未知 importance 回退 45 天（有测试锁定）。

## 2. 写入：从 `Memory:` 行到落库

1. **行识别**：`pet/brain/parsing.py` 的 `BehaviorParser.collect_stream`（流式）与 `parse_behavior`
   （非流式）共用同一套行标签；**非流式只保留第一条**记忆行。
2. **后台线程**：`PetAgent` 把保存丢进 daemon 线程（保存含 embedding 网络调用，不能卡 UI）。
3. **解析**（内联在 `save_from_line` 里，没有独立函数）：
   - 类别写 `[类别] 内容` 或 `类别 内容`，都不匹配则告警丢弃；`|` 之后是可选字段
     `keywords:` / `importance:` / `level:`；
   - 关键词缺省时用 jieba 提词（无 jieba 则正则切词）；
   - 一致性兜底：`L1 且 importance<3 → 3`；`L3 且 importance>4 → 4`；`importance<=2 且非 L1 → L3`。
4. **去重与合并**：
   - 先按关键词捞候选（`LIKE`，最多 20 条；不足 3 条时补最近 10 条）；
   - 相似度 = 2-gram Jaccard × 0.6 + `SequenceMatcher` × 0.4，阈值 `dedup_threshold=0.6`；
   - 命中则合并：取更长正文、关键词并集、level 取较高；**正文变长时 importance 取较大值**；
   - 关键词没命中且开了 embedding 时，再走向量近邻（距离 < `EMBEDDING_DEDUP_THRESHOLD`，默认 0.6）
     做一次语义去重。
5. **落库**：新建行先 `has_embedding=0` 再补向量；合并只在正文变长时重算向量；embedding 生成
   **在锁外**进行，失败就降级关键词检索。
6. **收尾**：每次保存后跑一次容量控制 `enforce_capacity()`。

> 两个 0.6 量纲不同：`dedup_threshold` 是**文本相似度**，`EMBEDDING_DEDUP_THRESHOLD` 是**向量距离**，两者不可混用。

## 3. 召回：`retrieve_context`

配额按 `MEMORY_RECALL_COUNT`（默认 15）切成三份：核心 30%、近期 20%、相关性 50%（MMR）。

| 槽 | 取什么 | 关键细节 |
|---|---|---|
| 核心 | `importance=5 AND level IN ('L1','L2')` 保底，再按有效分补足 | 保底超出配额时按「最久未访问」轮转，避免尾部记忆饿死；补充阶段要求有效分 ≥ 3.5 |
| 近期 | 24 小时内创建（窗口硬编码） | 按时间倒序 |
| 相关性 | 候选池按查询词检索后做 MMR | λ = 0.7：`0.7 × 相关性 − 0.3 × 与已选的最大相似度` |

- **有效分（effective importance）**：`min(5, importance × decay × recency_factor)`；
  `decay = 0.5 ** (age_days / 半衰期)`；`recency_factor = 1 + 0.5 × 0.5**(age_days(last_accessed)/0.5)`，
  即最近访问过的记忆最多 +50% 加成，且**不随次数累积**。
- **冗余抑制**：核心槽内相似度 ≥ 0.45 的重复只占一个座位；被抑制的 id 会下推给近期槽的 SQL
  （`NOT IN`）与 MMR 过滤，**避免它们被 touch**。
- **向量不可用时的降级链**：初始化失败（开关关、URL/KEY/模型空、sqlite-vec 加载失败）→ 关键词检索；
  单次向量查询异常 → 关键词；个别 `has_embedding=0` 的行只能被关键词命中。
- **关键词检索**：查询词 = jieba 关键词 + 标点切分短语（长度 2~8）取前 8；每词对 `keywords`/`content`
  做 `LIKE`；重排时 `keywords` 命中记 2 分、`content` 记 1 分，再叠加有效分。
- **向量重排**：`W_sim × 相似度 + W_imp × eff/5 + W_recency × 1/(1+age_h/24)`，权重是
  `MEMORY_RERANK_WEIGHT_{SIM,IMP,RECENCY}`（0.7 / 0.2 / 0.1）。

### 冷却与「为什么被动注入不 touch」

- 召回过的 id 进 `mark_recalled`，冷却 `MEMORY_RECALL_COOLDOWN_S`（默认 300 秒，进程内存，重启即清）；
- 冷却期内若 LLM 又写出相似度 ≥ 0.85 的记忆行会被丢弃，并往下一轮上下文追加「请勿重复输出 Memory 行」；
- 冷却期内重复保存同一内容会把 `importance` +1（上限 5）；
- `access_count` / `last_accessed_at` **只由 `touch` 更新**，而 `touch` 只对真正入选的行调用。

`MemoryStore.random_events()`（供「你惦记着的事」随机注入旧事）只读不 touch：
一旦 touch，随机抽中的记忆会凭 `recency_factor` 多出 1.5 倍加成并进入核心槽，形成
「随机→加成→更常被选」的自我强化；同时 `access_count` 被污染还会影响 L3→L2 晋升与容量淘汰。
这条行为有测试锁定（注入后 `access_count == 0`）。

## 4. 维护：`maintenance`

slow tick 触发（默认每 5 分钟）：

| 频率 | 动作 |
|---|---|
| 每次 | L3 过期硬删（`MEMORY_L3_EXPIRE_DAYS`，默认 3 天；先探测再写，无过期行不落盘） |
| 每 6 次（≈30 分钟） | `_demote_l2_to_l3()`：有效分 < 2.2 的 L1→L2、L2→L3（`importance=5` 的 L1 除外），并把 importance 压到 ≤4 维持「L3 最高 4」 |
| 每 6 次 | `_promote_by_tool_hits()`：先全表 `tool_hits × 0.9` 衰减；`≥40` 提到 importance 4、`≥120` 提到 5（命中即清零，不跳级，只对 L1/L2） |
| 每 6 次 | 超容量时 `enforce_capacity()` |

容量淘汰顺序（`MEMORY_MAX_CAPACITY`，默认 200）：先删 L3 过期项 → 再删「1 天前且访问 ≤1 次」的 L3 →
仍超容量时按有效分从低到高淘汰非 `importance=5` 的 L1/L2，必要时兜底淘汰 L2 中 importance=5 的；
**L1 + importance=5 永不淘汰**。

两条边界：

- **记忆系统不做摘要压缩**。摘要属于上下文系统（`context_entries` / `context_meta` +
  `pet/brain/summary.py`），与 `memories` 表没有数据交集。
- 维护本身没有网络 I/O、也没有单独的耗时保护，靠「容量上限 200」这个前提保持轻量。

## 5. 开关与可见性

| 配置项 | 作用 |
|---|---|
| `EMBEDDING_ENABLED` / `EMBEDDING_URL` / `EMBEDDING_KEY` / `EMBEDDING_MODEL` | 向量检索总开关与端点（任一为空即降级关键词） |
| `EMBEDDING_DIM` | 向量维度；改动会触发 `memories_vec` 重建 |
| `EMBEDDING_DEDUP_THRESHOLD` | 语义去重的向量距离阈值（hidden） |
| `MEMORY_MAX_CAPACITY` / `MEMORY_RECALL_COUNT` / `MEMORY_L3_EXPIRE_DAYS` | 容量、召回条数、L3 过期天数 |
| `MEMORY_EVENT_RECALL_COUNT` | 「你惦记着的事」随机注入条数（0 = 关闭） |
| `MEMORY_RECALL_COOLDOWN_S` | 召回冷却（hidden） |
| `MEMORY_RERANK_WEIGHT_*` | 向量重排权重（hidden） |

- **记忆管理窗口**（托盘右键「记忆管理」）：按级别/重要度/日期筛选、搜索（防抖）、分页（默认 50/页）、
  查看并编辑级别与重要度与关键词与正文、多选删除；详情里显示的「有效分」就是召回排序用的那个值。
- **模型侧**：`recall__search`（按线索检索，limit 1~10）与 `recall__browse`（分页翻阅，10/页）
  两个元工具，不占工具轮次；返回结果已在库里，模型不需要再输出 Memory 行。
- **命令行检查**（普通 `sqlite3` 能用，因为不涉及向量表）：

```bash
sqlite3 pet.db "SELECT level, COUNT(*) FROM memories GROUP BY level;"
sqlite3 pet.db "SELECT id, category, level, importance, access_count, substr(content,1,40) FROM memories ORDER BY created_at DESC LIMIT 20;"
```

`memories_vec` 是 sqlite-vec 虚拟表，需要加载扩展才能查，CLI 里通常查不了。

## 6. 不变量与陷阱

改动记忆系统时的前提：

1. **`random_events` 不加 `touch`**（见 §3），有测试锁定。
2. **`has_embedding` 是「向量与正文一致」的标志位**：任何修改 `content` 的代码路径都要同步维护它，
   否则检索会用旧向量。管理窗口的 `update_memory` 就是这么做的（改正文才重算向量，失败则置 0）。
3. **管理窗口不做一致性校验**：`update_memory` 没有 clamp，可以造出 `L3 + importance=5` 这类组合，
   而维护逻辑假设 L3 最高 4；另一方面它**每次保存都会写 `last_accessed_at`**，等于额外获得一次 +50% 加成。
4. **被抑制的 id 必须在 SQL 端排除**：`suppressed` / `exclude_ids` 的下推是为了让它们不被 `touch`，
   挪回 Python 端过滤会改变有效分排序。
5. **schema 演进靠 PRAGMA 增量迁移**：加列必须写迁移，否则老库直接报错；level 迁移会把
   `importance<=2` 的行刷成 L3（老库没有 L1）。
6. **改 `EMBEDDING_DIM` 或换模型会废弃全部旧向量**，且**没有存量回填逻辑** - 旧记忆此后只能被关键词命中，
   除非它的内容被再次编辑或合并。
7. **召回参数很敏感**：MMR λ=0.7、核心槽门槛 3.5、冗余阈值 0.45、近期窗口 24 小时、候选池 `max(n+3, 8)`、
   3:2:5 配额、三个重排权重，改动前需评估对「召回视角是否变窄」的影响。
8. `tool_hits` 是**近期热度**（每轮 ×0.9、`CAST AS INTEGER`，1 会直接归零），不是历史累计值。
9. 冷却与拦截记录是进程内存态，重启即清空：「刚召回的记忆重启后又被重存」是预期行为。

相关测试：`tests/test_memory.py`（半衰期、解析规则、去重器、`random_events` 不 touch）、
`tests/test_context_notes.py`（旧事注入章节的行为）。
