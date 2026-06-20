"""ContextBuilder — 集中管理 LLM 请求上下文的构建"""

from typing import Optional

from pet.brain import prompts
from config import config


class ContextBuilder:
    """构建 LLM 请求所需的完整 messages 列表。

    四个公开方法对应四种任务：
      build_autonomous_decide — 自主决策（视觉 / 非视觉自动选择）
      build_chat_decide          — 用户对话
      build_interact          — 即时交互（抓取、释放等）
      build_skill_result      — 技能多轮调用中的结果消息
    """

    def __init__(self, memory_store=None, screen_reader=None, vitals=None, mood=None, brain_mixin=None):
        self._memory_store = memory_store
        self._screen_reader = screen_reader
        self._vitals = vitals
        self._mood = mood
        self._brain = brain_mixin

    # public API

    def build_autonomous_decide(self, window_context: str, screenshot: bool = True) -> list[dict]:
        """自主决策模式的 messages（视觉／非视觉自动选择）"""
        base64_img = self._prepare_image() if screenshot else None
        vision = base64_img is not None
        mode = "autonomous_vision" if vision else "autonomous_non_vision"
        system = self._build_system(mode, "autonomous", user_message=window_context)
        ctx_str = self._build_user_context(window_context)

        if vision:
            return [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": prompts.autonomous_vision_user_prompt(ctx_str)},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_img}"}},
                ]},
            ]
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": prompts.autonomous_non_vision_user_prompt(ctx_str)},
        ]

    def build_chat_decide(self, user_message: str, window_context: str,
                   screenshot: bool = True) -> list[dict]:
        """对话模式的 messages（视觉／非视觉自动选择）。"""
        base64_img = self._prepare_image() if screenshot else None
        vision = base64_img is not None
        mode = "chat_vision" if vision else "chat_non_vision"
        system = self._build_system(mode, "chat", user_message=user_message)
        history = self._build_history()
        user_content = prompts.chat_user_prompt(user_message, window_context + history,
                                                       vision=vision)

        if vision:
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
        """即时交互模式的 messages（抓取、释放等）"""
        system = self._build_system("interact", "interact")
        return [
            {"role": "system", "content": system},
            {"role": "user",   "content": event_hint},
        ]

    def build_skill_result(self, result_text: str,
                           images: list[str] | None = None) -> dict:
        """构建技能执行结果对应的 user message（含可选图片）"""
        text = prompts.skill_result_user_prompt(result_text)
        if images:
            content = [{"type": "text", "text": text}]
            for uri in images:
                content.append({"type": "image_url", "image_url": {"url": uri}})
            return {"role": "user", "content": content}
        return {"role": "user", "content": text}

    def build_skill_round_system(self) -> str:
        """技能多轮调用的精简 system prompt"""
        content = prompts.build_system_prompt("skill", "skill_round")
        if self._memory_store:
            memory_text = self._memory_store.retrieve_context("")
            if memory_text:
                content += f"\n\n[你对主人的记忆]\n{memory_text}"
        return content

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
        """动态 pulse 指令 — 正常时静默，低阈值时输出强制行为指令。"""
        if not self._vitals or not self._mood:
            return ""

        ns = self._vitals.numeric_summary()
        ms = self._mood.numeric_summary()

        # 全部 > 60 → 正常，不输出任何内容（避免"狼来了"效应 + 省 token）
        if (ns['satiety'] > 60 and ns['energy'] > 60
                and ms['affection'] > 60 and ms['joy'] > 60 and ms['sanity'] > 60):
            return ""

        directives = []

        # ── 动态指令（规则不再让 LLM 查表，直接给命令） ──

        # --- 生理 ---
        if ns['energy'] < 20:
            directives.append("⚠ 精力衰竭：禁止所有移动类动作(bounce/drive/walk)，必须用 sit 或 sleep 收尾")
        elif ns['energy'] < 40:
            directives.append("⚠ 精力不足：动作序列中必须包含 sit 或 sleep，减少移动")
        elif ns['energy'] < 55:
            directives.append("精力偏低，优先 sit/sleep 恢复")

        if ns['satiety'] < 20:
            directives.append("⚠ 极度饥饿：台词必须表达虚弱/焦躁，大概率不理用户指令")
        elif ns['satiety'] < 40:
            directives.append("有点饿：台词委婉表达饥饿/想吃东西")

        # --- 心理 ---
        if ms['sanity'] < 20:
            directives.append(
                "⚠ 理智崩溃：台词语无伦次/认知错乱，仅允许 sit/sleep，"
                "但可主动调用技能做出反常行为（如搜索无意义内容、创建奇怪文件、打开莫名其妙的网页）"
            )
        elif ms['sanity'] < 40:
            directives.append(
                "⚠ 理智低落：行为偏保守但有小概率做出「不太正常」的举动，"
                "可主动使用技能搜索奇怪问题或创建内容古怪的文件"
            )
        elif ms['sanity'] < 55:
            directives.append("理智略低，避免过度活跃的动作")

        if ms['joy'] < 30:
            directives.append("心情低落：禁止使用表达开心的动作，台词简短消极")
        elif ms['joy'] < 50:
            directives.append("心情一般，台词偏短，避免过于欢快")

        if ms['affection'] < 40:
            directives.append("好感偏低：语气保持距离，不带亲昵称呼")

        if not directives:
            return ""

        param_intro = (
            "【参数说明】饱食度 0-100（仅喂食可恢复）| "
            "精力 0-100（sit/sleep 可恢复）| "
            "好感度 0-100（对用户亲密度）| "
            "愉悦度 0-100（当下快乐感）| "
            "理智值 0-100（越低越疯癫）"
        )
        return "\n".join([
            f"=== 当前状态（饱食{ns['satiety']:.0f} 精力{ns['energy']:.0f} "
            f"好感{ms['affection']:.0f} 愉悦{ms['joy']:.0f} 理智{ms['sanity']:.0f}）===",
            param_intro,
            "【本轮强制要求 — 违反视为格式错误】",
        ] + [f"- {d}" for d in directives])

    def _prepare_image(self) -> Optional[str]:
        if not config.VISION_ENABLED or not self._screen_reader:
            return None
        return self._screen_reader.prepare_image(vision_scale=config.VISION_SCALE)
