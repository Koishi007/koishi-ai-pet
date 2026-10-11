"""knowledge 工具 — 轻量 RAG 知识库。只读检索，写入/删除仅限面板操作。"""

from __future__ import annotations

import logging
import atexit

from pet.file_intake import load_text_full
from pet.tools.knowledge.storage import KnowledgeStorage
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

TOOL_NAME = "knowledge"
TOOL_DESCRIPTION = "RAG 知识库。可语义检索用户手动录入的知识和文档，只读。"
TOOL_GROUP = "knowledge"

_instance = None
_HINT_NAMES_MAX = 5


def _show_panel():
    """右键菜单「知识库管理」回调 — 弹出管理面板。"""
    # 面板依赖 Qt，延迟到实际弹出时导入
    from pet.tools.knowledge.panel import show_panel

    show_panel(_instance)


def _search(query: str, limit: int = 3) -> dict:
    """LLM 调用入口：语义检索知识库。"""
    if not _instance:
        return {"error": "知识库未初始化"}
    TOOL_CTX.speech_random(["让我想想…", "翻翻笔记…", "我记得…", "查一下…"])
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


def _list(page: int = 1) -> dict:
    """LLM 调用入口：列出知识条目。"""
    if not _instance:
        return {"error": "知识库未初始化"}
    TOOL_CTX.speech_random(["看看有什么…", "翻翻知识库…", "都记了些什么…"])
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


def _ingest_files(files) -> dict:
    """文件动作：把文件正文作为文档入库。气泡侧在后台线程调用一次，收到全部文件。"""
    if not _instance:
        return {"ok": False, "summary": "知识库没准备好"}
    added = []
    for ref in files:
        content = load_text_full(ref.path)
        if not content.strip():
            continue
        added.append(_instance.add_document(title=ref.name, content=content,
                                            tags="", source="file_drop"))
    if not added:
        return {"ok": False, "summary": "这些文件里没有能读出来的文字"}
    names = "、".join(item["title"] for item in added[:_HINT_NAMES_MAX])
    TOOL_CTX.request_interact(
        hint=f"用户把「{names}」交给你收进知识库了，一共 {len(added)} 份，"
             f"根据你的人格用一句或多句短句（总量≤30字）回应",
        cooldown_ms=0,  # 不做冷却：入库多少次就回应多少次
        thinking=False, enable_tools=False)
    return {"ok": True, "summary": f"已收进知识库 {len(added)} 份"}


def register(registry):
    global _instance
    try:
        _instance = KnowledgeStorage()
        atexit.register(_instance.close)
    except Exception as e:
        logger.error(f"[knowledge] Failed to initialize: {e}")
        return

    tool = registry.register(TOOL_NAME, TOOL_DESCRIPTION)


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
        TOOL_NAME, "list",
        "分页列出知识库中的条目。",
        handler=_list,
        args={
            "page": {"type": "int", "required": False, "default": 1,
                     "desc": "页码(从1开始)"},
        },
    )


    registry.add_menu_action(TOOL_NAME, "知识库管理", _show_panel)

    registry.add_file_action(TOOL_NAME, "ingest", "收进知识库", _ingest_files, accepts="text")


    logger.info("[knowledge] tool registered")
