"""动作系统 — 动作定义与注册(action)、帧动画播放(action_queue)、重力特效(gravity)、注册表(registry)。"""

__all__ = ["PetActions", "ActionQueue"]

# 玩法装配：import 即完成动作产出注册。这些模块只用标准库与 TOOL_CTX，不会拖入 PySide6
from pet.action import calling  # noqa: E402,F401
from pet.action import fishing  # noqa: E402,F401


def __getattr__(name: str):
    """延迟导入具体的动作实现。

    仅依赖 registry 的调用方（如 brain 层的 prompt 组装）不必被拖入 PySide6。
    """
    if name == "PetActions":
        from pet.action.action import PetActions
        return PetActions
    if name == "ActionQueue":
        from pet.action.action_queue import ActionQueue
        return ActionQueue
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
