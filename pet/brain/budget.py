"""单次请求的输出预算：统一硬上限 + 动态配额（基线 / 思考预留 / 工具预留）"""

from pet.config import config

# 每档 (基线, 思考预留)：基线覆盖结构化字段输出，思考单独一档，不挤占内容
_MODES: dict[str, tuple[int, int]] = {
    "interact": (512, 1536),
    "chat": (2048, 4096),
    "analyze": (2048, 4096),
    "autonomous": (2048, 4096),
    "summary": (512, 512),
}

# 首轮带工具时追加：文件正文、笔记全文都写在 tool arguments 里
_TOOLS_RESERVE = 2048

# 统一硬上限：任何组合都不越过
_CEILING = 8192


def thinking_active(thinking: bool | None = None) -> bool:
    """本次请求是否带思考：None 跟随全局开关，与请求参数的注入规则同源。"""
    if thinking is None:
        return not config.LLM_THINKING_DISABLED
    return bool(thinking)


def output_budget(mode: str, *, thinking: bool | None = None,
                  has_tools: bool = False) -> int:
    """按模式与请求形态算输出预算，封顶在 _CEILING。"""
    base, thinking_reserve = _MODES[mode]
    total = base
    if thinking_active(thinking):
        total += thinking_reserve
    if has_tools:
        total += _TOOLS_RESERVE
    return min(total, _CEILING)
