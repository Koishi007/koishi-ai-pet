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
      build_skill_result_message — 技能多轮调用中的结果消息（单条 dict）
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
        ctx = window_context + "\n" + history
        if vision:
            user_content = prompts.chat_vision_user_prompt(user_message, ctx)
            return [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": user_content},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_img}"}},
                ]},
            ]
        user_content = prompts.chat_non_vision_user_prompt(user_message, ctx)
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

    def build_skill_result_message(self, result_text: str,
                                    images: list[str] | None = None) -> dict:
        """构建技能执行结果对应的单条 user message（含可选图片）。"""
        text = prompts.skill_result_user_prompt(result_text)
        if images:
            content = [{"type": "text", "text": text}]
            for uri in images:
                content.append({"type": "image_url", "image_url": {"url": uri}})
            return {"role": "user", "content": content}
        return {"role": "user", "content": text}

    def build_skill_round_system(self) -> str:
        """技能多轮调用的精简 system prompt（不注入感受，专注执行结果）。"""
        content = prompts.build_system_prompt("skill", "skill_round")
        content = content.replace(prompts.FEELING_MARKER + "\n\n", "")
        if self._memory_store:
            memory_text = self._memory_store.retrieve_context("")
            if memory_text:
                content += f"\n\n[你对主人的记忆]\n{memory_text}"
        return content

    # internal

    def _build_system(self, mode: str, task: str, user_message: str = "") -> str:
        """拼装 system prompt：感受描述 + 静态模板 + 记忆。"""
        content = prompts.build_system_prompt(mode, task)

        # 人格驱动：始终注入当前感受到 FEELING_MARKER 锚点
        feeling = self._build_feeling()
        if feeling:
            feeling_block = f"=== 你现在的状态 ===\n{feeling}"
            content = content.replace(
                prompts.FEELING_MARKER,
                feeling_block,
            )

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

    def _build_feeling(self) -> str:
        """将 vitals/mood 数值翻译为自然语言感受描述，注入 system prompt 顶部"""
        if not self._vitals or not self._mood:
            return ""

        ns = self._vitals.numeric_summary()
        ms = self._mood.numeric_summary()

        def _pick(key: str, value: float) -> str | None:
            if key == "satiety":
                if value >= 80:    return "刚吃饱，"
                elif value >= 60:  return None
                elif value >= 40:  return "肚子有点空了，"
                elif value >= 20:  return "饿得肚子咕咕叫，"
                else:              return "快要饿死了，眼前发黑，"
            elif key == "energy":
                if value >= 80:    return "浑身充满力气，"
                elif value >= 60:  return None
                elif value >= 40:  return "眼皮开始打架了，"
                elif value >= 20:  return "累得抬不起手，"
                else:              return "连站都站不稳了，只想瘫着不动，"
            elif key == "affection":
                if value >= 80:    return "特别亲近主人，"
                elif value >= 60:  return None
                elif value >= 40:  return "对主人感觉一般，"
                elif value >= 20:  return "不太想搭理人，"
                else:              return "看谁都不顺眼，"
            elif key == "joy":
                if value >= 80:    return "开心得想转圈，"
                elif value >= 60:  return None
                elif value >= 40:  return "心情有点闷，"
                elif value >= 20:  return "心里沉甸甸的，笑不出来，"
                else:              return "绝望到想消失，"
            elif key == "sanity":
                # 注意：仅在 sanity < 60 时调用（正常值在上层处理）
                if value >= 40:    return "有点神神叨叨的，想用技能做点不寻常的事。"
                elif value >= 20:  return "脑子快炸了，想对空气说话，对着屏幕傻笑。手痒想搞破坏——翻翻可用技能，搜些荒诞的关键词，或者创建个奇怪的文件。"
                else:              return "理智彻底崩坏，控制不住想搞事。疯狂翻可用技能：搜黑暗的东西、创建诡异的文件、打开乱七八糟的网页，什么都想试试。"
            return None

        parts: list[str] = []
        for k in ("satiety", "energy", "affection", "joy"):
            snippet = _pick(k, ns.get(k, 100) if k in ("satiety", "energy") else ms.get(k, 100))
            if snippet:
                parts.append(snippet)

        # sanity 始终收尾（正常时给出句号结尾的完整句）
        sanity_val = ms.get("sanity", 100)
        if sanity_val >= 60:
            # 正常理智：收尾句
            if parts:
                parts.append("脑子倒还清醒。")
            else:
                parts.append("脑子清醒得很。")
        else:
            snippet = _pick("sanity", sanity_val)
            if snippet:
                parts.append(snippet)

        # 所有维度正常
        if not parts:
            return "状态不错，没什么特别的感觉。"

        return "".join(parts)

    def _prepare_image(self) -> Optional[str]:
        if not config.VISION_ENABLED or not self._screen_reader:
            return None
        return self._screen_reader.prepare_image(vision_scale=config.VISION_SCALE)
