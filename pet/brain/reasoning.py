"""推理强度的中性档位：取值域、端点预设与归一化。

协议字段由各 provider 翻译；这里定义配置里允许出现的值，以及按模型名推断端点接受哪些档位。
"""

EFFORT_IDS = ("minimal", "low", "medium", "high", "xhigh", "max")

# 模型名前缀 -> 端点接受的档位，顺序即匹配优先级；空元组表示该端点没有档位控制
MODEL_EFFORT_PRESETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gpt-5.2", ("low", "medium", "high", "xhigh")),
    ("gpt-5.1", ("low", "medium", "high", "xhigh")),
    ("gpt-5", ("minimal", "low", "medium", "high")),
    ("o1", ("low", "medium", "high")),
    ("o3", ("low", "medium", "high")),
    ("o4", ("low", "medium", "high")),
    ("deepseek", ("low", "high", "max")),
    ("mimo", ("low", "medium", "high")),
    ("glm-5.3", ("low", "high", "max")),
    ("glm-5.2", ("high", "max")),
    ("glm", ()),
    ("qwen4", ("low", "high", "xhigh")),
    ("qwen3.8", ("low", "medium", "xhigh")),
    ("qwen", ()),
)


def normalize(value: str | None) -> str:
    """去空白转小写；不在 EFFORT_IDS 内返回空串，空串表示不下发该字段。"""
    key = (value or "").strip().lower()
    return key if key in EFFORT_IDS else ""


def parse_efforts(value: str | None) -> tuple[str, ...] | None:
    """逗号分隔的档位清单；空串返回 None，未知取值丢弃。"""
    if not (value or "").strip():
        return None
    parsed: list[str] = []
    for token in (value or "").replace("，", ",").split(","):
        key = normalize(token)
        if key and key not in parsed:
            parsed.append(key)
    return tuple(parsed)


def preset_efforts(model: str | None) -> tuple[str, ...] | None:
    """按模型名前缀匹配预设，模型名包含该片段即命中；没有命中返回 None。"""
    name = (model or "").strip().lower()
    if not name:
        return None
    for prefix, efforts in MODEL_EFFORT_PRESETS:
        if prefix in name:
            return efforts
    return None


def effective_efforts(declared: tuple[str, ...], override: str | None,
                      model: str | None) -> tuple[str, ...]:
    """端点实际可用的档位：配置清单优先，其次模型预设，最后通道声明；按声明顺序收窄。"""
    base = parse_efforts(override)
    if base is None:
        base = preset_efforts(model)
    if base is None:
        base = declared
    allowed = set(base)
    return tuple(effort for effort in declared if effort in allowed)
