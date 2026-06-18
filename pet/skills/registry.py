"""技能注册表 — 自动发现、注册、描述可用技能。

args 参数格式：
  {"name": {"type": "int", "required": False, "default": 5, "desc": "说明"}}
  type 支持: int / float / str / bool / list / dict
  required=True 表示必选，缺少时校验不通并返回错误给 LLM。
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Any

logger = logging.getLogger(__name__)


@dataclass
class SkillMethod:
    name: str
    description: str
    args: dict = field(default_factory=dict)
    handler: Callable = None
    when: str = ""


@dataclass
class SkillDef:
    name: str
    description: str
    methods: dict[str, SkillMethod] = field(default_factory=dict)
    when: str = ""


class SkillRegistry:
    """全局技能注册表。"""

    def __init__(self):
        self._skills: dict[str, SkillDef] = {}
        self._disabled: set[str] = set()

    def register(self, skill_name: str, description: str) -> "SkillDef":
        skill = SkillDef(name=skill_name, description=description)
        self._skills[skill_name] = skill
        return skill

    def add_method(self, skill_name: str, method_name: str,
                   description: str, handler: Callable, args: dict = None,
                   when: str = ""):
        skill = self._skills[skill_name]
        skill.methods[method_name] = SkillMethod(
            name=method_name, description=description,
            args=args or {}, handler=handler, when=when,
        )

    def get_handler(self, full_name: str) -> Callable | None:
        parts = full_name.split(".", 1)
        if len(parts) != 2:
            return None
        skill_name, method_name = parts
        if not self.is_enabled(skill_name):
            return None
        skill = self._skills.get(skill_name)
        if not skill:
            return None
        method = skill.methods.get(method_name)
        return method.handler if method else None

    @property
    def enabled_skills(self) -> list["SkillDef"]:
        """返回所有已启用的技能定义列表。"""
        return [s for s in self._skills.values() if self.is_enabled(s.name)]

    @property
    def skill_names(self) -> list[str]:
        return list(self._skills.keys())

    def is_enabled(self, skill_name: str) -> bool:
        return skill_name not in self._disabled

    def set_enabled(self, skill_name: str, enabled: bool):
        if enabled:
            self._disabled.discard(skill_name)
            logger.info(f"[Skill] ✓ enabled: {skill_name}")
        else:
            self._disabled.add(skill_name)
            logger.info(f"[Skill] ✗ disabled: {skill_name}")

    @property
    def disabled_set(self) -> set[str]:
        return set(self._disabled)


# 全局单例
SKILL_REGISTRY = SkillRegistry()
