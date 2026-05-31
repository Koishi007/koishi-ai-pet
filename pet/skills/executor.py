"""技能执行器 — 解析 LLM 输出中的 Skill JSON，路由执行，返回结果。"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from pet.skills.registry import SKILL_REGISTRY

logger = logging.getLogger(__name__)

_TYPE_MAP = {
    "int": int,
    "float": (int, float),
    "str": str,
    "bool": bool,
    "list": list,
    "dict": dict,
    "any": object,
}


@dataclass
class SkillCall:
    name: str
    args: dict


@dataclass
class SkillResult:
    name: str
    success: bool
    data: Any = None
    error: str = ""
    image_b64: str | None = None  # 插件可返回 base64 图片，供下一轮 LLM 多模态读取


class SkillExecutor:

    def execute(self, calls: list[SkillCall]) -> list[SkillResult]:
        results = []
        for call in calls:
            results.append(self._execute_one(call))
        return results

    def _execute_one(self, call: SkillCall) -> SkillResult:
        method = self._lookup_method(call.name)
        if method is None:
            logger.warning(f"[SkillExecutor] unknown skill: {call.name}")
            return SkillResult(name=call.name, success=False, error=f"unknown skill: {call.name}")

        validated_args, err = self._validate_args(call.args, method.args)
        if err:
            logger.warning(f"[SkillExecutor] arg validation failed: {call.name}: {err}")
            return SkillResult(name=call.name, success=False, error=err)

        try:
            data = method.handler(**validated_args)
            logger.info(f"[SkillExecutor] \u2713 {call.name} \u2192 {str(data)[:100]}")
            image_b64 = None
            if isinstance(data, dict):
                image_b64 = data.pop("__image__", None)  # 插件的截图走 __image__ 旁路，不混入文本结果
            return SkillResult(name=call.name, success=True, data=data, image_b64=image_b64)
        except TypeError as e:
            logger.error(f"[SkillExecutor] ✗ {call.name} TypeError: {e}")
            return SkillResult(name=call.name, success=False, error=f"参数不匹配: {e}")
        except Exception as e:
            logger.error(f"[SkillExecutor] ✗ {call.name} failed: {e}")
            return SkillResult(name=call.name, success=False, error=str(e))

    @staticmethod
    def _lookup_method(full_name: str):
        parts = full_name.split(".", 1)
        if len(parts) != 2:
            return None
        skill = SKILL_REGISTRY._skills.get(parts[0])
        if not skill:
            return None
        return skill.methods.get(parts[1])

    @staticmethod
    def _validate_args(provided: dict, schema: dict) -> tuple[dict, str]:
        validated = {}
        provided = provided or {}

        for key, spec in schema.items():
            type_name = spec.get("type", "any")
            required = spec.get("required", False)
            default = spec.get("default")

            if key in provided:
                value = provided[key]
                expected_type = _TYPE_MAP.get(type_name, object)
                if expected_type is not object and not isinstance(value, expected_type):
                    return {}, f"参数 {key!r} 类型错误，期望 {type_name}，实际 {type(value).__name__}"
                validated[key] = value
            elif required:
                return {}, f"缺少必需参数 {key!r} ({type_name})"
            elif default is not None:
                validated[key] = default

        for key, value in provided.items():
            if key not in validated:
                validated[key] = value

        return validated, ""

    @staticmethod
    def parse_skill_lines(content: str) -> list[SkillCall]:
        calls = []
        for line in content.split("\n"):
            line = line.strip()
            if line.lower().startswith("skill:"):
                raw = line.split(":", 1)[1].strip()
                try:
                    obj = json.loads(raw)
                    calls.append(SkillCall(
                        name=obj.get("name", ""),
                        args=obj.get("args", {}),
                    ))
                except json.JSONDecodeError:
                    logger.warning(f"[SkillExecutor] invalid JSON: {raw[:80]}")
        return calls

    @staticmethod
    def _normalize(data: Any) -> str:
        if isinstance(data, dict):
            summary = data.pop("summary", None)
            json_str = json.dumps(data, ensure_ascii=False)
            return f"{summary}\n{json_str}" if summary else json_str
        elif isinstance(data, str):
            return data
        return str(data)

    @classmethod
    def format_results(cls, results: list[SkillResult]) -> tuple[str, list[str]]:
        lines = []
        images = []
        for r in results:
            if r.success:
                lines.append(f"[✓ {r.name}]\n{cls._normalize(r.data)}")
                if r.image_b64:
                    images.append(r.image_b64)
                    lines.append("(附图: 已提供截图，可直接观察内容)")
            else:
                lines.append(f"[✗ {r.name}] 失败: {r.error}")
        return "\n\n".join(lines), images
