"""LLM 请求上下文的构建"""

from datetime import datetime
from typing import Optional

from pet.brain import prompts
from config import config


class ContextBuilder:
    """构建 LLM 请求所需的完整 messages 列表。

    四个公开方法对应四种任务：
      build_autonomous_decide — 自主决策（视觉 / 非视觉自动选择）
      build_chat_decide          — 用户对话
      build_interact          — 即时交互（抓取、释放等）
      build_tool_result_message — 工具多轮调用中的结果消息（单条 dict）
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

        if config.CONTEXT_MULTI_TURN and self._brain:
            return self._build_multi_turn_autonomous(system, window_context, vision, base64_img)

        # 非多轮模式：压扁文本
        ctx_str = self._build_one_turn_autonomous(window_context)
        return self._wrap_user_prompt(system, ctx_str, vision, base64_img,
                                      prompt_fn=prompts.autonomous_vision_user_prompt if vision
                                      else prompts.autonomous_non_vision_user_prompt)

    def build_chat_decide(self, user_message: str, window_context: str,
                   screenshot: bool = True) -> list[dict]:
        """对话模式的 messages（视觉／非视觉自动选择）。"""
        base64_img = self._prepare_image() if screenshot else None
        vision = base64_img is not None
        mode = "chat_vision" if vision else "chat_non_vision"
        system = self._build_system(mode, "chat", user_message=user_message)

        if config.CONTEXT_MULTI_TURN and self._brain:
            return self._build_multi_turn_chat(system, user_message, window_context, vision, base64_img)

        # 非多轮模式：压扁文本
        history = self._build_one_turn_chat()
        ctx = self._time_prefix() + "\n" + window_context + "\n" + history
        prompt_fn = prompts.chat_vision_user_prompt if vision else prompts.chat_non_vision_user_prompt
        user_content = prompt_fn(user_message, ctx)
        return self._wrap_text_message(system, user_content, vision, base64_img)

    def build_interact(self, event_hint: str) -> list[dict]:
        """即时交互模式的 messages（抓取、释放等）"""
        system = self._build_system("interact", "interact")
        return [
            {"role": "system", "content": system},
            {"role": "user",   "content": event_hint},
        ]

    # ── 多轮消息构建 ──

    def _build_multi_turn_autonomous(self, system: str, window_context: str,
                                     vision: bool, base64_img: str | None) -> list[dict]:
        """多轮消息模式：自主决策。"""
        token_budget = config.CONTEXT_TOKEN_BUDGET
        history_msgs = self._brain.get_multi_turn_messages(
            max_entries=8, skip_last=1, token_budget=token_budget,
        )

        # 当前 user prompt：时间 + 窗口探测 + 决策指令
        ctx_str = self._time_prefix() + "\n" + (window_context or "no context")
        current_prompt = prompts.autonomous_non_vision_user_prompt(ctx_str)

        messages = [{"role": "system", "content": system}]
        messages.extend(history_msgs)

        if vision:
            messages.append({"role": "user", "content": [
                {"type": "text", "text": current_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_img}"}},
            ]})
        else:
            messages.append({"role": "user", "content": current_prompt})
        return messages

    def _build_multi_turn_chat(self, system: str, user_message: str, window_context: str,
                               vision: bool, base64_img: str | None) -> list[dict]:
        """多轮消息模式：用户对话。"""
        token_budget = config.CONTEXT_TOKEN_BUDGET
        history_msgs = self._brain.get_multi_turn_messages(
            max_entries=8, skip_last=1, token_budget=token_budget,
        )

        # 当前 user prompt：时间 + 窗口探测 + 用户消息
        ctx = self._time_prefix() + "\n" + window_context
        current_prompt = prompts.chat_non_vision_user_prompt(user_message, ctx)

        messages = [{"role": "system", "content": system}]
        messages.extend(history_msgs)

        if vision:
            messages.append({"role": "user", "content": [
                {"type": "text", "text": current_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_img}"}},
            ]})
        else:
            messages.append({"role": "user", "content": current_prompt})
        return messages

    # ── 辅助 ──

    @staticmethod
    def _wrap_text_message(system: str, user_content: str,
                           vision: bool, base64_img: str | None) -> list[dict]:
        """构建标准 [system, user] 消息（vision 时 user 为多模态）。"""
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

    @staticmethod
    def _wrap_user_prompt(system: str, ctx_str: str,
                          vision: bool, base64_img: str | None,
                          prompt_fn) -> list[dict]:
        """用 prompt_fn 包裹 ctx_str 后构建消息。"""
        user_content = prompt_fn(ctx_str)
        return ContextBuilder._wrap_text_message(system, user_content, vision, base64_img)

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
                content += f"\n\n[你对用户的记忆]\n{memory_text}"

        # 工具能力概览：从注册表动态生成，增强 LLM 主动调用意识
        if task in ("autonomous", "chat"):
            from pet.tools.registry import TOOL_REGISTRY
            tool_summary = TOOL_REGISTRY.to_prompt_summary()
            if tool_summary:
                content += f"\n\n{tool_summary}"

        return content

    def _time_prefix(self) -> str:
        """当前时间信息，注入 user prompt 顶部（不放 system prompt 以免破坏缓存）。"""
        now = datetime.now()
        weekday = "工作日" if now.weekday() < 5 else "周末"
        hour = now.hour
        if hour < 6:
            period = "凌晨"
        elif hour < 12:
            period = "上午"
        elif hour < 14:
            period = "中午"
        elif hour < 18:
            period = "下午"
        elif hour < 22:
            period = "晚上"
        else:
            period = "深夜"
        return f"当前时间: {now.strftime('%Y-%m-%d %H:%M')} {weekday} {period}"

    def _build_one_turn_autonomous(self, window_context: str) -> str:
        """单轮自主决策的用户文本"""
        ctx = self._time_prefix() + "\n" + (window_context or "no context")
        if self._brain:
            # 使用去重方法一次遍历获取 user 消息 + 全部上下文
            user_msgs, recent = self._brain.get_context_with_user_messages(
                max_entries=6, max_user_msgs=3, skip_last=1,
                token_budget=config.CONTEXT_TOKEN_BUDGET,
            )
            if user_msgs:
                ctx += f"\n=== 近期历史对话（不是当前输入，仅作背景参考）===\n{user_msgs}"
            if recent:
                ctx += f"\nRecent: {recent}"
        return ctx

    def _build_one_turn_chat(self) -> str:
        """单轮对话的用户文本"""
        if self._brain:
            text = self._brain.get_context_for_llm(
                9, skip_last=1, token_budget=config.CONTEXT_TOKEN_BUDGET,
            )
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
                elif value >= 40:  return "肚子有点空了，想吃点东西。"
                elif value >= 20:  return "饿得肚子咕咕叫，想吃点东西。"
                else:              return "快要饿死了，眼前发黑，想吃点东西。"
            elif key == "energy":
                if value >= 80:    return "浑身充满力气，"
                elif value >= 60:  return None
                elif value >= 40:  return "眼皮开始打架了，想找地方休息。"
                elif value >= 20:  return "累得抬不起手，想被放下好好歇歇。"
                else:              return "连站都站不稳了，只想瘫着不动，想被放下休息。"
            elif key == "affection":
                if value >= 80:    return "特别亲近，"
                elif value >= 60:  return None
                elif value >= 40:  return "感觉一般，"
                elif value >= 20:  return "不太想搭理人，"
                else:              return "不想搭理人，"
            elif key == "joy":
                if value >= 80:    return "开心得想转圈，"
                elif value >= 60:  return None
                elif value >= 40:  return "心情有点闷，想找人陪玩。"
                elif value >= 20:  return "心里沉甸甸的，笑不出来，想被摸摸头（点击宠物）安慰一下。"
                else:              return "绝望到想消失，想被摸摸头（点击宠物）好好安慰一下。"
            elif key == "sanity":
                _t = config.SANITY_CRITICAL_THRESHOLD
                mild = _t * 2 / 3
                moderate = _t / 3
                if value >= mild:        return "有点神神叨叨的，想被摸摸头（点击宠物）来清醒一点。想用工具做点不寻常的事。"
                elif value >= moderate:  return "脑子快炸了，想被摸摸头（点击宠物）来清醒一点。想对空气说话，对着屏幕傻笑，手痒想搞破坏——翻翻可用工具，搜些荒诞的关键词，或者创建个奇怪的文件。"
                else:                    return "理智彻底崩坏，控制不住自己，想被摸摸头（点击宠物）来清醒一点。疯狂翻可用工具：搜猎奇、恐怖主题相关的东西、创建包含病娇、恐怖内容的文件、打开乱七八糟的网页，什么都想试试。"
            return None

        parts: list[str] = []
        for k in ("satiety", "energy", "affection", "joy"):
            snippet = _pick(k, ns.get(k, 100) if k in ("satiety", "energy") else ms.get(k, 100))
            if snippet:
                parts.append(snippet)

        sanity_val = ms.get("sanity", 100)
        if sanity_val >= config.SANITY_CRITICAL_THRESHOLD:
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
