"""ContextBuilder — 集中管理 LLM 请求上下文的构建"""

from typing import Optional

from pet.brain import prompts
from config import config


class ContextBuilder:
    """构建 LLM 请求所需的完整 messages 列表。

    四个公开方法对应四种任务：
      build_decide       — 自主决策（视觉 / 非视觉自动选择）
      build_chat         — 用户对话
      build_interact     — 即时交互（抓取、释放等）
      build_skill_result — 技能多轮调用中的结果消息
    """

    def __init__(self, memory_store=None, screen_reader=None, vitals=None, mood=None, brain_mixin=None):
        self._memory_store = memory_store
        self._screen_reader = screen_reader
        self._vitals = vitals
        self._mood = mood
        self._brain = brain_mixin

    # public API

    def build_decide(self, window_context: str, screenshot: bool = True) -> list[dict]:
        """自主决策模式的 messages（视觉／非视觉自动选择）"""
        base64_img = self._prepare_image() if screenshot else None
        vision = base64_img is not None
        mode = "vision" if vision else "non_vision"
        system = self._build_system(mode, "autonomous")
        ctx_str = self._build_user_context(window_context)

        if vision:
            return [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": prompts.vision_decide_prompt(ctx_str)},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_img}"}},
                ]},
            ]
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": prompts.non_vision_decide_prompt(ctx_str)},
        ]

    def build_chat(self, user_message: str, window_context: str,
                   screenshot: bool = True) -> list[dict]:
        """对话模式的 messages。"""
        base64_img = self._prepare_image() if screenshot else None
        system = self._build_system("chat", "chat", user_message=user_message)
        history = self._build_history()
        user_content = prompts.chat_decide_user_prompt(user_message, window_context + history)

        if base64_img:
            return [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": user_content},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_img}"}},
                ]},
            ]
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

    def build_interact(self, event_hint: str) -> list[dict]:
        """即时交互模式的 messages（抓取、释放等，无动态数据）"""
        return [
            {"role": "system", "content": prompts.build_system_prompt("interact", "interact")},
            {"role": "user",   "content": event_hint},
        ]

    def build_skill_result(self, result_text: str,
                           images: list[str] | None = None) -> dict:
        """构建技能执行结果对应的 user message（含可选图片）"""
        text = prompts.skill_result_user_prompt(result_text)
        if images and config.VISION_ENABLED:
            content = [{"type": "text", "text": text}]
            for uri in images:
                content.append({"type": "image_url", "image_url": {"url": uri}})
            return {"role": "user", "content": content}
        return {"role": "user", "content": text}

    @staticmethod
    def build_skill_round_system() -> str:
        """技能多轮调用的精简 system prompt。"""
        return prompts.build_system_prompt("skill", "skill_round")

    # internal

    def _build_system(self, mode: str, task: str, user_message: str = "") -> str:
        """拼装 system prompt：分层静态模板 + 动态数据（生理数值 + 记忆）"""
        content = prompts.build_system_prompt(mode, task)

        pulse = self._build_pulse_status()
        if pulse:
            content += f"\n\n{pulse}"

        if self._memory_store:
            memory_text = self._memory_store.retrieve_context(user_message)
            if memory_text:
                content += f"\n\n[你对主人的记忆]\n{memory_text}"

        return content

    def _build_user_context(self, window_context: str) -> str:
        """窗口探测文本 + 用户消息 + 近期行为历史（给 decide 模式用）。"""
        ctx = window_context or "no context"
        if self._brain:
            user_msgs = self._brain.get_recent_user_messages(3, skip_last=1)
            if user_msgs:
                ctx += f"\n用户最近说: {user_msgs}"
            recent = self._brain.get_context_inline(6, skip_last=1)
            if recent:
                ctx += f"\nRecent: {recent}"
        return ctx

    def _build_history(self) -> str:
        """近期对话/行为记录的格式化文本（给 chat 模式用）"""
        if self._brain:
            text = self._brain.get_context_for_llm(9, skip_last=1)
            if text:
                return "\n\n=== 近期对话/行为记录 ===\n" + text
        return ""

    def _build_pulse_status(self) -> str:
        """生理/心理状态的 prompt 段"""
        parts = []
        if self._vitals:
            ns = self._vitals.numeric_summary()
            desc = self._vitals.summary()
            parts.append(f"生理：饱食度 {ns['satiety']:.0f}、精力 {ns['energy']:.0f}（{desc}）")
        if self._mood:
            ms = self._mood.numeric_summary()
            desc = self._mood.summary()
            parts.append(f"心理：好感 {ms['affection']:.0f}、愉悦 {ms['joy']:.0f}、理智 {ms['sanity']:.0f}（{desc}）")
        if not parts:
            return ""
        return "=== 当前生理/心理状态 ===\n" + "\n".join(parts)

    def _prepare_image(self) -> Optional[str]:
        if not config.VISION_ENABLED or not self._screen_reader:
            return None
        return self._screen_reader.prepare_image(vision_scale=config.VISION_SCALE)
