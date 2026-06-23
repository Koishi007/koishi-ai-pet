# DeskPet 记忆系统重构：从伪向量到 sqlite-vec

- **Date:** 2026-06-23
- **Status:** Draft
- **Author:** Koishi007

## Overview

将 `pet/brain/memory.py` 从当前基于 jieba 关键词 + 字符 n-gram/SequenceMatcher 的“伪向量”检索，重构为使用 [sqlite-vec](https://github.com/asg017/sqlite-vec) 的真实向量检索；同时保留完整的关键词 fallback 路径，当用户未配置 embedding API 时行为与当前完全一致。

## Motivation

1. **当前方案天花板明显**：关键词匹配无法处理同义改写、语义相近但字面不同的表达；n-gram 相似度只是局部字符重合，不能捕获真正语义。
2. **厂商切换成本低**：只要支持 OpenAI 兼容 `/v1/embeddings` 接口（如智谱、OpenAI、硅基流动等），仅修改 URL/Key/Model 即可切换。
3. **sqlite-vec 轻量**：纯 SQLite 扩展，无需单独向量数据库，适合桌面端单用户场景。

## Architecture

```
MemoryStore（统一接口不变）
├── _retriever: KeywordRetriever | VectorRetriever
├── save / query_core / query_recent / retrieve_context 等公开方法
└── 初始化时根据 EMBEDDING_ENABLED + EMBEDDING_URL/KEY/MODEL 选择 retriever

KeywordRetriever（原逻辑迁移）
├── 操作 memories 表
├── 关键词 LIKE 匹配 + 文本相似度去重
└── 未配置 embedding API 时使用

VectorRetriever（新增）
├── 操作 memories_vec 虚拟表（sqlite-vec）
├── 调用 EmbeddingClient 生成向量
└── 向量召回后按 importance / created_at / access_count 重排

EmbeddingClient
├── OpenAI 兼容 /v1/embeddings 调用
└── 支持 batch 请求与 L2 归一化
```

`PetAgent`、`ContextBuilder` 不需要改动，它们仍然只调用 `MemoryStore` 的公开接口。

## Configuration

在 `config.py` 的 `_KEY_META` 中新增 5 个字段，类别均为 `connection`，在设置 UI 中归类为「记忆设置」分组：

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `EMBEDDING_ENABLED` | `bool` | `false` | 向量记忆总开关 |
| `EMBEDDING_URL` | `str` | `""` | OpenAI 兼容 embedding 端点，例如智谱 `https://open.bigmodel.cn/api/paas/v4` |
| `EMBEDDING_KEY` | `str` | `""` | API Key |
| `EMBEDDING_MODEL` | `str` | `""` | 模型名，例如智谱 `embedding-3` |
| `EMBEDDING_DIM` | `int` | `2048` | 向量维度，智谱 `embedding-3` 为 2048 |

启用条件：

```
EMBEDDING_ENABLED == true
AND EMBEDDING_URL != ""
AND EMBEDDING_KEY != ""
AND EMBEDDING_MODEL != ""
```

设置 UI 要求：
- 新增「记忆设置」分组，放置上述 5 个控件。
- 分组底部加提示：`“修改记忆设置后需重启 DeskPet 生效。”`
- 增加控件后需检查设置窗口整体尺寸与排版，避免拥挤。

## Database Schema

现有 `memories` 表保持不动，供 `KeywordRetriever` 使用：

```sql
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    keywords TEXT NOT NULL,
    importance INTEGER DEFAULT 3,
    created_at TEXT NOT NULL,
    access_count INTEGER DEFAULT 0
);
```

新增列：

```sql
ALTER TABLE memories ADD COLUMN has_embedding INTEGER DEFAULT 0;
```

新增 sqlite-vec 虚拟表：

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec USING vec0(
    memory_id INTEGER PRIMARY KEY,
    embedding FLOAT[<EMBEDDING_DIM>]
);
```

- `memory_id` 与 `memories.id` 一一对应。
- `EMBEDDING_DIM` 从配置读取，用于建表。若配置变更导致维度变化，需由用户手动处理旧向量表（删除 `pet.db` 或由未来迁移脚本处理），本次设计不实现自动迁移。

## EmbeddingClient

新增 `pet/brain/embedding_client.py`：

```python
class EmbeddingClient:
    def __init__(self, url: str, key: str, model: str, dim: int): ...

    def embed(self, texts: str | list[str]) -> list[list[float]]:
        """调用 /v1/embeddings，返回与输入顺序一致的向量列表。"""
```

实现要点：
- 使用已有的 `openai` 依赖创建客户端：`OpenAI(base_url=url, api_key=key)`。
- 请求体：`{"model": model, "input": texts}`。
- 支持 batch（`input` 传 list），减少 API 调用次数。
- 返回向量做 L2 归一化，使 sqlite-vec 的 cosine 距离更稳定。
- 失败时抛出 `EmbeddingError`（自定义异常），上层记录日志并降级。

## Retriever 接口与 MemoryStore 拆分

定义内部抽象：

```python
class _MemoryRetriever:
    def save(self, category: str, content: str, keywords: list[str], importance: int): ...
    def query_core(self, limit: int) -> list[dict]: ...
    def query_recent(self, hours: int, limit: int) -> list[dict]: ...
    def query_by_text(self, text: str, limit: int) -> list[dict]: ...
    def find_similar(self, content: str, keywords: list[str]) -> tuple[dict | None, float]: ...
    def touch(self, ids: list[int]): ...
    def enforce_capacity(self): ...
```

`MemoryStore` 的公开方法变成薄封装，根据配置委托给具体 retriever：

```python
class MemoryStore:
    def __init__(self, db_path=None, dedup_threshold=0.6):
        # 初始化 SQLite 连接、建表
        # 根据 config.EMBEDDING_* 决定 _retriever = VectorRetriever(self._conn, ...) 或 KeywordRetriever(self._conn, ...)

    def save(self, category, content, keywords, importance=3):
        return self._retriever.save(category, content, keywords, importance)

    def retrieve_context(self, user_message: str) -> str:
        # 组合 query_core + query_recent + query_by_text
        ...
```

### KeywordRetriever

将原 `MemoryStore` 中的以下逻辑原样迁移：
- `save` 及 `_find_similar` 去重合并
- `query_core`、`query_recent`、`query_by_keywords`（改名为 `query_by_text`）
- `_touch`、`_enforce_capacity`
- `_extract_keywords`、去重器 `LightweightDeduplicator`

### VectorRetriever

- `save`：先写 `memories` 表（含 `has_embedding=0`），再调用 `EmbeddingClient.embed([content])` 获取向量，写入 `memories_vec` 并更新 `has_embedding=1`。若 embedding API 失败，保留 `memories` 行并记录 warning。
- `query_by_text(text, limit)`：
  1. 对 `text` 调用 `EmbeddingClient.embed([text])` 得到查询向量。
  2. 执行 `SELECT memory_id, distance FROM memories_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?`。
  3. 根据返回的 `memory_id` 从 `memories` 表中取完整记录。
  4. 按 `importance DESC, created_at DESC` 重排后返回前 `limit` 条。
- `query_core(limit)` / `query_recent(hours, limit)`：保留 SQL 的 importance / time 过滤，不走向量。
- `find_similar(content, keywords)`：先通过 `memories_vec` 向量召回候选，再用 lightweight 文本相似度判断是否合并；也可直接复用向量距离阈值，实现时选择更稳定的一种。

## MemoryStore.retrieve_context 组合逻辑

`retrieve_context` 的召回策略保持不变：

1. `query_core(5)` → 重要记忆
2. `query_recent(24, 3)` → 近期记忆
3. `query_by_text(user_message, 3)` → 语义相关记忆（vector 模式下替换原 keyword 匹配）

去重后拼接为原有格式文本返回。

## Error Handling and Fallback

| 场景 | 行为 |
|---|---|
| 未配置 embedding API | 实例化 `KeywordRetriever`，行为与当前一致 |
| 配置存在但 sqlite-vec 加载失败 | warning，回退 `KeywordRetriever` |
| `EmbeddingClient` 初始化失败（如维度非法） | warning，回退 `KeywordRetriever` |
| save 时 embedding API 失败 | 记忆写入 `memories`，`has_embedding=0`，记录 warning |
| retrieve 时 embedding API 失败 | 本次查询降级到 keyword 路径 |
| 运行时修改配置并保存 | 下次启动生效；设置 UI 已提示用户重启 |

**降级原则**：向量功能是可选增强，任何环节失败都不应影响桌宠核心对话流程。

## Migration Strategy

- 不自动回填旧记忆的向量。
- 旧 `memories` 行 `has_embedding` 默认为 0；`VectorRetriever` 召回时只取 `has_embedding=1` 的行。
- 用户切换回 keyword 模式时，所有记忆（含无向量）都可见。
- 未来如需回填，可单独实现后台任务；本次不做。

## Files Changed

| 文件 | 动作 | 说明 |
|---|---|---|
| `pet/brain/memory.py` | 修改 | 拆分为 `MemoryStore` + `KeywordRetriever` + `VectorRetriever` |
| `pet/brain/embedding_client.py` | 新增 | OpenAI 兼容 embedding 客户端 |
| `config.py` | 修改 | 新增 5 个 `EMBEDDING_*` 配置项 |
| `pet/ui/settings_window.py` | 修改 | 新增「记忆设置」分组及控件、重启提示 |
| `requirements.txt` | 修改 | 新增 `sqlite-vec>=0.1.0` |
| `README.md` | 修改 | 补充记忆设置说明 |

## Testing Notes

1. 未配置 embedding 时，`MemoryStore` 行为与当前完全一致（keyword 召回、去重、容量控制）。
2. 配置正确时，保存的记忆能写入 `memories_vec`，语义召回能命中相关内容。
3. 配置正确时，`query_core` / `query_recent` 仍按 importance / time 工作。
4. embedding API 失败时，保存不崩溃；查询能自动降级。
5. 安装 `sqlite-vec` 后启动成功；未安装时回退 keyword。
6. 设置 UI 新增控件后窗口布局正常，保存配置后重启生效。

## Non-Goals

- 不支持本地 CPU embedding 模型（如 sentence-transformers）。
- 不自动迁移/回填旧记忆的向量。
- 不做混合排序（向量 + 关键词加权），向量召回后仅按业务字段重排。
- 不做运行时热切换 retriever，配置修改后需重启。
