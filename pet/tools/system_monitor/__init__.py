from pet.tools.system_monitor.core import get_overview, get_top_processes, get_memory_detail, get_network
from pet.tools.context import TOOL_CTX

TOOL_NAME = "system_monitor"
TOOL_DESCRIPTION = "系统资源监控（CPU、内存、磁盘、电池、进程）"


def _get_overview(**kw):
    TOOL_CTX.speech_random(["检查一下…", "看看状态…", "把把脉…", "电脑怎么样了…"])
    return get_overview(**kw)


def _get_top_processes(**kw):
    TOOL_CTX.speech_random(["看看谁在占资源…", "谁在搞事…", "查查大户…", "谁最忙…"])
    return get_top_processes(**kw)


def _get_memory_detail(**kw):
    TOOL_CTX.speech_random(["看看内存…", "内存怎么样…", "查查内存…", "还剩多少…"])
    return get_memory_detail(**kw)


def _get_network(**kw):
    TOOL_CTX.speech_random(["看看网络…", "网速怎么样…", "查查网络…", "网通不通…"])
    return get_network(**kw)


def register(registry):
    tool = registry.register(TOOL_NAME, TOOL_DESCRIPTION)

    registry.add_method(
        TOOL_NAME, "get_overview",
        "获取系统整体概况（CPU、内存、磁盘、电池、运行时长）",
        handler=_get_overview,
        args={},
    )
    registry.add_method(
        TOOL_NAME, "get_top_processes",
        "获取占用CPU最高的前N个进程",
        handler=_get_top_processes,
        args={
            "count": {"type": "int", "required": False, "default": 5,
                      "desc": "返回进程数量（1-20）"},
        },
    )
    registry.add_method(
        TOOL_NAME, "get_memory_detail",
        "获取详细内存信息（物理内存+交换分区）",
        handler=_get_memory_detail,
        args={},
    )
    registry.add_method(
        TOOL_NAME, "get_network",
        "获取网络流量统计（累计发送/接收字节数）",
        handler=_get_network,
        args={},
    )