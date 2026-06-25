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
        return self._build_multi_turn_autonomous(system, window_context, vision, base64_img)

    def build_chat_decide(self, user_message: str, window_context: str,
                   screenshot: bool = True) -> list[dict]:
        """对话模式的 messages（视觉／非视觉自动选择）。"""
        base64_img = self._prepare_image() if screenshot else None
        vision = base64_img is not None
        mode = "chat_vision" if vision else "chat_non_vision"
        system = self._build_system(mode, "chat", user_message=user_message)
        return self._build_multi_turn_chat(system, user_message, window_context, vision, base64_img)

    def build_interact(self, event_hint: str) -> list[dict]:
        """即时交互模式的 messages（抓取、释放等）"""
        system = self._build_system("interact", "interact")
        return [
            {"role": "system", "content": system},
            {"role": "user",   "content": event_hint},
        ]

    _MAX_WINDOWS = 10  # 窗口探测上下文最多输出的窗口数

    @staticmethod
    def build_window_context(pet_x: int, pet_y: int, pet_hwnd: int = 0) -> str:
        """探测屏幕窗口，生成供 LLM 使用的窗口上下文文本。"""
        try:
            from pet.brain.window_detector import get_visible_windows, is_window_occluded
            from PySide6.QtWidgets import QApplication
            windows = get_visible_windows()
        except Exception:
            return ""

        pet_w, pet_h = 125, 125
        dpr = QApplication.primaryScreen().devicePixelRatio() if QApplication.primaryScreen() else 1.0

        # 收集有效窗口并打分
        scored = []
        for win in windows:
            left, top, right, bottom = tuple(v / dpr for v in win["rect"])
            w, h = right - left, bottom - top
            title = win["title"].strip()
            if not title or len(title) > 50:
                continue
            if abs(left - pet_x) < 10 and abs(top - pet_y) < 10 and w == pet_w and h == pet_h:
                continue
            if w < 200 or h < 100:
                continue
            if is_window_occluded(win["hwnd"], threshold=0.8, skip_hwnd=pet_hwnd):
                continue

            dx_walk = (left + w // 2) - (pet_x + pet_w // 2)  # 目标: 窗口中部
            dy_top = top - (pet_y + pet_h)
            dist = abs(dx_walk)
            jump_px = abs(dy_top)

            # 打分：距离近 + 尺寸大 + 可跳跃 = 高优先级
            dist_score = 1000.0 / (dist + 1.0)
            size_score = min(w * h / 100000.0, 5.0)
            if jump_px <= 400:
                reach_score = 2.0
            elif jump_px <= 800:
                reach_score = 1.0
            else:
                reach_score = 0.0
            total = dist_score + size_score + reach_score

            direction = "右" if dx_walk > 0 else "左"
            if jump_px <= 400:
                reachable = "可跳"
            elif jump_px <= 800:
                reachable = "勉强可跳"
            else:
                reachable = "禁止跳跃（距离过高）"

            scored.append((total, title, left, top, right, bottom, w, h,
                          direction, dist, jump_px, reachable))

        # 按分降序，取前 N
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:ContextBuilder._MAX_WINDOWS]

        lines = ["=== 窗口探测（系统 API，坐标精确） ==="]
        lines.append(f"桌宠位置: 左{pet_x} 上{pet_y} (宽{pet_w} 高{pet_h})")

        if not top:
            lines.append("未发现适合跳转的窗口。")
        else:
            for i, (score, title, left, top, right, bottom, w, h,
                    direction, dist, jump_px, reachable) in enumerate(top, 1):
                lines.append(
                    f"{i}. \"{title}\" ｜ "
                    f"范围: 左{left} 上{top} 右{right} 下{bottom} (宽{w} 高{h}) ｜ "
                    f"相对桌宠: {direction}走{dist}px, 上跳{jump_px}px 到窗口顶 "
                    f"({reachable})"
                )
            if len(scored) > ContextBuilder._MAX_WINDOWS:
                lines.append(f"... 及另外 {len(scored) - ContextBuilder._MAX_WINDOWS} 个窗口（相关性较低，已省略）")

        return "\n".join(lines)

    # ── 多轮消息构建 ──

    def _build_multi_turn_autonomous(self, system: str, window_context: str,
                                     vision: bool, base64_img: str | None) -> list[dict]:
        """多轮消息模式：自主决策。"""
        token_budget = config.CONTEXT_TOKEN_BUDGET
        history_msgs = self._brain.get_multi_turn_messages(
            max_entries=8, skip_last=0, token_budget=token_budget,
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
