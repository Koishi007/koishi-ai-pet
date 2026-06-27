"""knowledge 工具 — 轻量 RAG 知识库。支持语义检索、添加、管理知识条目。"""

from __future__ import annotations

import logging
import atexit

from pet.tools.knowledge.storage import KnowledgeStorage
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

TOOL_NAME = "knowledge"
TOOL_DESCRIPTION = "RAG 知识库。可语义检索用户录入的知识、文档片段，支持添加和管理条目。"

_instance = None
_panel = None


def _show_panel():
    """右键菜单「知识库管理」回调 — 弹出管理面板。"""
    global _panel
    from pet.tools.knowledge.panel import KnowledgePanel

    if _panel is not None:
        try:
            alive = _panel.isVisible()
        except RuntimeError:
            alive = False
        if not alive:
            _panel.deleteLater()
            _panel = None

    if _panel is None:
        _panel = KnowledgePanel(_instance)

    _panel.show()
    _panel.raise_()


def _search(query: str, limit: int = 3) -> dict:
    """LLM 调用入口：语义检索知识库。"""
    if not _instance:
        return {"error": "知识库未初始化"}
    limit = max(1, min(limit, 10))
    results = _instance.search(query, limit=limit)
    if not results:
        return {"summary": "未找到相关知识", "results": [], "count": 0}
    lines = [f"找到 {len(results)} 条相关知识:"]
    for r in results:
        score_str = f" ({r.get('score', 0):.2f})" if r.get("score") else ""
        lines.append(f"  [{r.get('title', '未知')}]{score_str}")
    return {
        "summary": "\n".join(lines),
        "results": [
            {"title": r.get("title", ""), "content": r["content"],
             "score": r.get("score", 0)}
            for r in results
        ],
        "count": len(results),
    }


def _add(title: str, content: str, tags: str = "") -> dict:
    """LLM 调用入口：添加知识条目。"""
    if not _instance:
        return {"error": "知识库未初始化"}
    if not title.strip() or not content.strip():
        return {"error": "标题和内容不能为空"}
    TOOL_CTX.speech_random(["记下来…", "学到了…", "存一下…"])
    result = _instance.add_document(title=title, content=content, tags=tags, source="llm")
    result["summary"] = f"已添加知识:「{title}」，共 {result['chunks']} 个分块"
    return result


def _list(page: int = 1) -> dict:
    """LLM 调用入口：列出知识条目。"""
    if not _instance:
        return {"error": "知识库未初始化"}
    data = _instance.list_documents(page=page, page_size=20)
    docs = data["documents"]
    if not docs:
        return {"summary": "知识库为空", "documents": [], "page": page, "total_pages": 0}
    total = (data["total_pages"] - 1) * 20 + len(docs)  # 近似总数
    lines = [f"共 {total} 条知识，第 {data['page']}/{data['total_pages']} 页:"]
    for d in docs:
        tags_str = f" [{d['tags']}]" if d.get("tags") else ""
        lines.append(f"  #{d['id']} {d['title']}{tags_str}")
    return {
        "summary": "\n".join(lines),
        "documents": [{"id": d["id"], "title": d["title"], "tags": d.get("tags", "")} for d in docs],
        "page": data["page"],
        "total_pages": data["total_pages"],
        "has_next": data["has_next"],
    }


def _delete(doc_id: int) -> dict:
    """LLM 调用入口：删除知识条目。"""
    if not _instance:
        return {"error": "知识库未初始化"}
    ok = _instance.delete_document(doc_id)
    if not ok:
        return {"error": f"未找到 id={doc_id} 的知识条目"}
    return {"deleted": True, "doc_id": doc_id, "summary": f"已删除知识 #{doc_id}"}


def register(registry):
    global _instance
    try:
        _instance = KnowledgeStorage()
        atexit.register(_instance.close)
    except Exception as e:
        logger.error(f"[knowledge] Failed to initialize: {e}")
        return

    tool = registry.register(TOOL_NAME, TOOL_DESCRIPTION)

    # ── LLM 方法 ──

    registry.add_method(
        TOOL_NAME, "search",
        "语义检索知识库，返回与查询最相关的知识片段。"
        "当用户询问你之前学过的知识、你录入的笔记、"
        "或你需要参考已存储的文档来回答问题时调用此工具。",
        handler=_search,
        args={
            "query": {"type": "str", "required": True, "desc": "搜索查询文本"},
            "limit": {"type": "int", "required": False, "default": 3,
                      "desc": "返回结果数量(1~10)"},
        },
        timeout=30.0,
    )

    registry.add_method(
        TOOL_NAME, "add",
        "添加一条知识到知识库（自动分块和向量化）。"
        "当用户告诉你需要记住的信息、或对话中产生了值得长期保存的知识时调用。",
        handler=_add,
        args={
            "title": {"type": "str", "required": True, "desc": "知识标题/摘要"},
            "content": {"type": "str", "required": True, "desc": "知识全文内容"},
            "tags": {"type": "str", "required": False, "default": "",
                     "desc": "标签(逗号分隔)"},
        },
        timeout=60.0,
    )

    registry.add_method(
        TOOL_NAME, "list",
        "分页列出知识库中的条目。",
        handler=_list,
        args={
            "page": {"type": "int", "required": False, "default": 1,
                     "desc": "页码(从1开始)"},
        },
    )

    registry.add_method(
        TOOL_NAME, "delete",
        "删除指定知识条目。",
        handler=_delete,
        args={
            "doc_id": {"type": "int", "required": True, "desc": "文档ID"},
        },
    )

    # ── 右键菜单 ──

    registry.add_menu_action(TOOL_NAME, "知识库管理", _show_panel)

    # ── 面板注册 ──

    TOOL_CTX.register_panel(TOOL_NAME, _show_panel)

    logger.info("[knowledge] tool registered")
