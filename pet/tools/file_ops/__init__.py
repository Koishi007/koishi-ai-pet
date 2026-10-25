from pet.tools.file_ops.core import FileOpsTool
from pet.tools.context import TOOL_CTX
from pet.tools.output import IMAGES_SCHEMA, PAGINATION_SCHEMA, ToolOutputDefinition

TOOL_NAME = "file_ops"
TOOL_DESCRIPTION = "文件操作（读写、列目录、查看图片，限桌面/文档）"
TOOL_GROUP = "file"

LIST_DIR_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "一句话结论，进历史记录"},
        "body": {"type": "string", "x-role": "body", "description": "文件名列表，每行一项"},
        "pagination": {**PAGINATION_SCHEMA},
    },
}

READ_FILE_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "一句话结论，进历史记录"},
        "body": {"type": "string", "x-role": "body", "description": "文本内容"},
        "images": IMAGES_SCHEMA,
        "agent_hint": {"type": "string", "x-role": "hint",
                       "description": "如何继续读取，如用 offset 翻页"},
    },
}

WRITE_FILE_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "一句话结论，进历史记录"},
    },
}

_instance: FileOpsTool | None = None


def _get_instance() -> FileOpsTool:
    """工具实例首次使用时创建。"""
    global _instance
    if _instance is None:
        _instance = FileOpsTool()
    return _instance


def _list_dir(**kw):
    TOOL_CTX.speech_random(["看看有什么…", "翻翻看…", "瞄一眼…", "看看都有什么…"])
    return _get_instance().list_dir(**kw)


def _read_file(**kw):
    TOOL_CTX.speech_random(["读读看…", "看看写了什么…", "瞄一眼…", "读一下…"])
    return _get_instance().read_file(**kw)


def _write_note(**kw):
    TOOL_CTX.speech_random(["记东西…", "写下来…", "记一下…", "别忘了…"])
    return _get_instance().write_note(**kw)


def _write_file(**kw):
    TOOL_CTX.speech_random(["嗯…写写看…", "写一下…", "动笔了…", "写写看…"])
    return _get_instance().write_file(**kw)


def register(registry):
    tool = registry.register(TOOL_NAME, TOOL_DESCRIPTION, side_effect=True)

    registry.add_method(
        TOOL_NAME, "list_dir",
        "列出指定目录内容（限桌面/文档，分页每页50项）",
        handler=_list_dir,
        args={
            "path": {"type": "str", "required": False, "default": "~/Desktop",
                     "desc": "目录路径，默认桌面"},
            "page": {"type": "int", "required": False, "default": 1,
                     "desc": "页码，从1开始"},
        },
        output=ToolOutputDefinition(schema=LIST_DIR_OUTPUT),
    )
    registry.add_method(
        TOOL_NAME, "read_file",
        "读取文件（文本文件、图片，路径限桌面/文档）。文本分页读取：每次最多1000字符，用 offset 继续，offset 上限5000；图片不分页，直接返回图像，可查看。",
        handler=_read_file,
        args={
            "path": {"type": "str", "required": True, "desc": "文件路径"},
            "max_chars": {"type": "int", "required": False, "default": 1000,
                          "desc": "本次读取最大字符数"},
            "offset": {"type": "int", "required": False, "default": 0,
                       "desc": "读取起始位置（字符偏移），从0开始，上限5000"},
        },
        output=ToolOutputDefinition(schema=READ_FILE_OUTPUT),
    )
    registry.add_method(
        TOOL_NAME, "write_note",
        "在桌面创建一个文本笔记",
        handler=_write_note,
        args={
            "filename": {"type": "str", "required": True, "desc": "文件名（含扩展名）"},
            "content": {"type": "str", "required": True, "desc": "写入内容"},
        },
        output=ToolOutputDefinition(schema=WRITE_FILE_OUTPUT),
    )
    registry.add_method(
        TOOL_NAME, "write_file",
        "写入/覆盖/追加文件内容（限桌面/文档。追加时 mode='a'，覆盖 mode='w'）",
        handler=_write_file,
        args={
            "path": {"type": "str", "required": True, "desc": "文件路径"},
            "content": {"type": "str", "required": True, "desc": "写入内容"},
            "mode": {"type": "str", "required": False, "default": "w",
                     "desc": "写入模式: w=覆盖, a=追加",
                     "enum": ["w", "a"]},
        },
        output=ToolOutputDefinition(schema=WRITE_FILE_OUTPUT),
    )