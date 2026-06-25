from pet.tools.file_ops.core import FileOpsTool

TOOL_NAME = "file"
TOOL_DESCRIPTION = "文件操作（读写、列目录，限桌面/文档）"

_instance = FileOpsTool()


def register(registry):
    tool = registry.register(TOOL_NAME, TOOL_DESCRIPTION)

    registry.add_method(
        TOOL_NAME, "list_dir",
        "列出指定目录内容（限桌面/文档）",
        handler=_instance.list_dir,
        args={
            "path": {"type": "str", "required": False, "default": "~/Desktop",
                     "desc": "目录路径，默认桌面"},
        },
    )
    registry.add_method(
        TOOL_NAME, "read_file",
        "读取文本文件前500字符（限桌面/文档）",
        handler=_instance.read_file,
        args={
            "path": {"type": "str", "required": True, "desc": "文件路径"},
            "max_chars": {"type": "int", "required": False, "default": 500,
                          "desc": "最大读取字符数"},
        },
    )
    registry.add_method(
        TOOL_NAME, "write_note",
        "在桌面创建一个文本笔记",
        handler=_instance.write_note,
        args={
            "filename": {"type": "str", "required": True, "desc": "文件名（含扩展名）"},
            "content": {"type": "str", "required": True, "desc": "写入内容"},
        },
    )
    registry.add_method(
        TOOL_NAME, "write_file",
        "写入/覆盖/追加文件内容（限桌面/文档。追加时 mode='a'，覆盖 mode='w'）",
        handler=_instance.write_file,
        args={
            "path": {"type": "str", "required": True, "desc": "文件路径"},
            "content": {"type": "str", "required": True, "desc": "写入内容"},
            "mode": {"type": "str", "required": False, "default": "w",
                     "desc": "写入模式: w=覆盖, a=追加",
                     "enum": ["w", "a"]},
        },
    )