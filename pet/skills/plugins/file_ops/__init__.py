from pet.skills.plugins.file_ops.core import FileOpsTool

SKILL_NAME = "file"
SKILL_DESCRIPTION = "文件操作（读写、列目录，限桌面/文档）"

_instance = FileOpsTool()


def register(registry):
    skill = registry.register(SKILL_NAME, SKILL_DESCRIPTION)
    skill.when = "用户让你查看桌面/文档文件、读取文件内容、或写笔记时"

    registry.add_method(
        SKILL_NAME, "list_dir",
        "列出指定目录内容（限桌面/文档）",
        handler=_instance.list_dir,
        when="用户问\"桌面上有什么\"\"文档目录里有什么文件\"时",
        args={
            "path": {"type": "str", "required": False, "default": "~/Desktop",
                     "desc": "目录路径，默认桌面"},
        },
    )
    registry.add_method(
        SKILL_NAME, "read_file",
        "读取文本文件前500字符（限桌面/文档）",
        handler=_instance.read_file,
        when="用户说\"帮我看看这个文件\"\"读一下xxx文件\"时",
        args={
            "path": {"type": "str", "required": True, "desc": "文件路径"},
            "max_chars": {"type": "int", "required": False, "default": 500,
                          "desc": "最大读取字符数"},
        },
    )
    registry.add_method(
        SKILL_NAME, "write_note",
        "在桌面创建一个文本笔记",
        handler=_instance.write_note,
        when="用户说\"帮我记一下\"\"写个备忘\"\"记笔记\"时",
        args={
            "filename": {"type": "str", "required": True, "desc": "文件名（含扩展名）"},
            "content": {"type": "str", "required": True, "desc": "写入内容"},
        },
    )
    registry.add_method(
        SKILL_NAME, "write_file",
        "写入/覆盖指定文件内容（限桌面/文档下的已有或无文件）",
        handler=_instance.write_file,
        when="用户说\"帮我写进去\"\"修改这个文件\"\"覆盖xxx内容\"时",
        args={
            "path": {"type": "str", "required": True, "desc": "文件路径"},
            "content": {"type": "str", "required": True, "desc": "写入内容"},
        },
    )