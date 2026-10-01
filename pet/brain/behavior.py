"""与 AI 通信，解析响应为动作序列。"""

import random
import time
from datetime import datetime
import logging
import threading

from openai import BadRequestError

from pet.brain.base import BrainMixin
from pet.brain.context_builder import ContextBuilder
from pet.brain.llm_client import LLMClient
from pet.brain.llm_stats import LlmStats
from pet.brain.output import ActionStep, BehaviorOutput, CancelledError
from pet.brain.parsing import BehaviorParser, parse_behavior, parse_stream_chunks
from pet.action.registry import ACTION_NAMES
from pet.config import config
from pet.brain.llm_retry import llm_retry
from pet.tools.registry import TOOL_REGISTRY

logger = logging.getLogger(__name__)

# 服务商是否不支持 thinking 参数（首次 400 后自动降级，后续请求不再携带）
_thinking_unsupported = False


class Behavior(BrainMixin):

    def __init__(self, memory_store=None, screen_reader=None, vitals=None, mood=None,
                 recent_events_fn=None, once_events_fn=None, progress_fn=None):
        db_path = memory_store._db_path if memory_store else None
        super().__init__(db_path=db_path)
        self._llm = LLMClient()
        # 进展心跳回调：每产生一次实质进展（chunk / 工具轮次 / LLM 返回）通知一次，
        # 供看门狗区分「跑得慢」与「挂死」，避免长流程被误判
        self._progress_fn = progress_fn
        self._lock = threading.RLock()
        self._counter_lock = threading.Lock()  # 仅保护 _rounds_without_user，避免主线程等待 LLM 长锁
        self._rounds_without_user = 0

        self.parser = BehaviorParser(self)
        self.ctx = ContextBuilder(
            memory_store=memory_store, screen_reader=screen_reader,
            vitals=vitals, mood=mood, brain_mixin=self,
            recent_events_fn=recent_events_fn, once_events_fn=once_events_fn,
        )
        self.llm_stats = LlmStats()

        self._active_tool_groups: set[str] = {"default"}

        t = datetime.now().strftime("%H:%M:%S")
        client_type = "None (local)" if not self._llm else f"{type(self._llm.client).__name__}(model={self._llm.model})"
        logger.info(f"[{t}] [Behavior] init: client={client_type}")

    def rebuild_client(self):
        """运行时重建 LLM 客户端（设置界面修改连接配置后调用）。"""
        with self._lock:
            self._llm.rebuild()
        client_type = "None (local)" if not self._llm else f"{type(self._llm.client).__name__}(model={self._llm.model})"
        logger.info(f"[Behavior] rebuild_client: {client_type}")

    def _on_llm_retry(self, exc: BaseException | None = None) -> bool:
        """重试前切换到备选模型方案；返回是否发生了切换。

        由 llm_retry / llm_stream_with_retry 在每次重试前回调，
        覆盖调用报错、读超时与建流超时三种情况。
        """
        if not config.LLM_FALLBACK_ENABLED:
            return False
        switched = self._llm.activate_fallback()
        if switched:
            cause = type(exc).__name__ if exc is not None else "unknown"
            logger.warning(
                f"[Behavior] retry with alternative model: {self._llm.model} "
                f"(profile={self._llm.effective_profile}, cause={cause})"
            )
        return switched

    @property
    def has_vision(self) -> bool:
        return self._llm.has_vision

    def note_progress(self):
        """上报一次管线进展（best-effort，回调异常不影响主流程）。"""
        if not self._progress_fn:
            return
        try:
            self._progress_fn()
        except Exception:
            pass

    def _build_tools_param(self, enable_tools: bool | None = None):
        """根据当前已激活的分组构建 tools 参数；enable_tools 非 None 时覆盖全局开关。"""
        if enable_tools is False:
            return None
        if enable_tools is None and not config.LLM_TOOLS_ENABLED:
            return None
        return TOOL_REGISTRY.to_openai_tools(groups=self._active_tool_groups)

    def _activate_tool_groups_from_search(self, matches: list[dict]):
        """根据搜索返回的匹配结果激活分组。"""
        for item in matches:
            grp = item.get("group")
            if grp and grp != "default" and grp not in self._active_tool_groups:
                self._active_tool_groups.add(grp)
                logger.info(f"[Behavior] activated tool group: {grp} (from search result)")

    def _activate_groups_from_keyword(self, keyword: str):
        """搜索关键词匹配分组名，自动激活命中的分组。"""
        kw = keyword.strip().lower() if keyword else ""
        if not kw:
            return
        for grp in TOOL_REGISTRY.get_groups():
            if grp == "default":
                continue
            if kw in grp.lower() and grp not in self._active_tool_groups:
                self._active_tool_groups.add(grp)
                logger.info(f"[Behavior] activated tool group: {grp} (keyword: {kw})")

    def reset_active_tool_groups(self):
        """互动结束时重置激活状态，仅保留 default 组。"""
        self._active_tool_groups = {"default"}
        logger.debug("[Behavior] reset active tool groups")

    def note_autonomous_round(self):
        """自主行动一轮：累计未与用户互动的轮次。"""
        with self._counter_lock:
            self._rounds_without_user += 1

    def reset_user_interaction(self):
        """用户主动互动（chat/interact）：清零未互动轮次。"""
        with self._counter_lock:
            self._rounds_without_user = 0

    @property
    def rounds_without_user(self) -> int:
        """连续未与用户互动的自主轮次数（供 context 注入关注提示）。"""
        with self._counter_lock:
            return self._rounds_without_user
    
    def autonomous_decide(self, context: str = "", screenshot: bool = True) -> BehaviorOutput:
        t = datetime.now().strftime("%H:%M:%S")
        if not self._llm:
            return self._decide_local()

        messages = self.ctx.build_autonomous_decide(context, screenshot=screenshot)
        is_vision = isinstance(messages[1]["content"], list)
        tag = "autonomous_vision" if is_vision else "autonomous_non_vision"
        ctx_preview = context[:60] if context else "(empty)"
        logger.info(f"[{t}] [Behavior] === LLM REQUEST ({tag}) ===")
        logger.info(f"[{t}] [Behavior]   model: {self._llm.model}, context({len(context)} chars): \"{ctx_preview}\"")
        logger.info(f"[{t}] [Behavior]   history: {self.context_count()} entries")

        return self._retry_if_empty(self._call_llm_and_parse, messages, messages[0]["content"], tag=tag, max_tokens=config.LLM_MAX_TOKENS_AUTONOMOUS)

    def autonomous_decide_stream(self, context: str = "", screenshot: bool = True,
                      on_chunk=None, on_stream_end=None,
                      cancel_check: callable = None) -> BehaviorOutput:
        if not self._llm:
            return self._decide_local()
        if not self._lock.acquire(timeout=0.5):
            logger.warning("[Behavior] autonomous_decide_stream: busy, skip")
            return self._decide_local()
        try:
            messages = self.ctx.build_autonomous_decide(context, screenshot=screenshot)
            is_vision = isinstance(messages[1]["content"], list)
            tag = "autonomous_decide_vision_stream" if is_vision else "autonomous_decide_stream"
            return self._retry_if_empty(self._stream_and_build_output, messages, tag=tag, on_chunk=on_chunk, on_stream_end=on_stream_end, max_tokens=config.LLM_MAX_TOKENS_AUTONOMOUS, cancel_check=cancel_check)
        finally:
            self._lock.release()

    def interact_decide(self, event_hint: str) -> BehaviorOutput:
        if not self._llm:
            return self._interact_decide_local(event_hint)
        messages = self.ctx.build_interact(event_hint)
        return self._retry_if_empty(self._call_llm_and_parse, messages, messages[0]["content"], tag="interact", max_tokens=config.LLM_MAX_TOKENS_INTERACT)

    def interact_decide_stream(self, event_hint: str,
                               on_chunk=None, on_stream_end=None,
                               thinking: bool | None = None,
                               enable_tools: bool | None = None,
                               cancel_check: callable = None) -> BehaviorOutput:
        if not self._llm:
            return self._interact_decide_local(event_hint)
        if not self._lock.acquire(timeout=2):
            logger.warning("[Behavior] interact_decide_stream: busy, skip")
            return self._interact_decide_local(event_hint)
        try:
            messages = self.ctx.build_interact(event_hint)
            return self._retry_if_empty(self._stream_and_build_output, messages, tag="interact", on_chunk=on_chunk, on_stream_end=on_stream_end, max_tokens=config.LLM_MAX_TOKENS_INTERACT, thinking=thinking, enable_tools=enable_tools, cancel_check=cancel_check)
        finally:
            self._lock.release()



    def chat_decide(self, user_message: str, context: str = "", screenshot: bool = True) -> BehaviorOutput:
        t = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{t}] [Behavior] chat_decide(msg={user_message[:50]}, ctx={context[:30]})")

        if not self._llm:
            return self._chat_decide_local(user_message)

        messages = self.ctx.build_chat_decide(user_message, context, screenshot=screenshot)
        is_vision = isinstance(messages[1]["content"], list)
        tag = "chat_vision" if is_vision else "chat_non_vision"
        logger.info(f"[{t}] [Behavior] === LLM REQUEST ({tag}) ===")
        logger.info(f"[{t}] [Behavior]   model: {self._llm.model}")
        logger.info(f"[{t}] [Behavior]   history: {self.context_count()} entries")

        return self._retry_if_empty(self._call_llm_and_parse, messages, messages[0]["content"], tag=tag, max_tokens=config.LLM_MAX_TOKENS_CHAT)

    def chat_decide_stream(self, user_message: str, context: str, screenshot: bool = True,
                           on_chunk=None, on_stream_end=None,
                           thinking: bool | None = None,
                           enable_tools: bool | None = None,
                           cancel_check: callable = None) -> BehaviorOutput:
        if not self._llm:
            return self._chat_decide_local(user_message)
        if not self._lock.acquire(timeout=5):
            logger.warning("[Behavior] chat_decide_stream: busy, timeout")
            return BehaviorOutput(
                actions=[ActionStep("look_around", kwargs={"duration": 5})],
                speech="嚎……等一下，我还在想……",
            )
        try:
            messages = self.ctx.build_chat_decide(user_message, context, screenshot=screenshot)
            is_vision = isinstance(messages[1]["content"], list)
            tag = "chat_decide_vision_stream" if is_vision else "chat_decide_stream"
            return self._retry_if_empty(self._stream_and_build_output, messages, tag=tag, on_chunk=on_chunk, on_stream_end=on_stream_end, max_tokens=config.LLM_MAX_TOKENS_CHAT, thinking=thinking, enable_tools=enable_tools, cancel_check=cancel_check)
        finally:
            self._lock.release()

    def _retry_if_empty(self, fn, *args, **kwargs) -> BehaviorOutput:
        """调用 fn 并在结果为空时重试一次"""
        tag = kwargs.get("tag", "")
        result = fn(*args, **kwargs)
        if not result.actions and not result.speech:
            logger.warning(f"[Behavior] empty LLM response (no actions, no speech), retrying once ({tag})")
            result = fn(*args, **kwargs)
        return result

    @staticmethod
    def _apply_thinking_param(kwargs: dict, thinking: bool | None = None):
        """按配置注入思考模式参数；thinking 非 None 时覆盖全局开关。"""
        if _thinking_unsupported:
            return
        if thinking is None:
            state = "disabled" if config.LLM_THINKING_DISABLED else "enabled"
        else:
            state = "enabled" if thinking else "disabled"
        kwargs.setdefault("extra_body", {})["thinking"] = {"type": state}

    def _create_completion(self, kwargs: dict):
        """发起补全请求；若服务商不支持 thinking 参数（400），自动移除后重试一次。"""
        global _thinking_unsupported
        try:
            return self._llm.client.chat.completions.create(**kwargs)
        except BadRequestError:
            if "extra_body" not in kwargs:
                raise
            logger.warning("[Behavior] 请求被拒绝(400)，可能不支持 thinking 参数，自动降级重试")
            kwargs.pop("extra_body", None)
            resp = self._llm.client.chat.completions.create(**kwargs)
            _thinking_unsupported = True
            return resp

    @llm_retry(tag="Behavior")
    def _llm_call(self, messages: list, max_tokens: int = 4000, tools: list = None,
                  thinking: bool | None = None):
        self.llm_stats.increment()
        t0 = time.perf_counter()
        kwargs = {"model": self._llm.model, "messages": messages, "max_tokens": max_tokens, "temperature": config.LLM_TEMPERATURE}
        if tools:
            kwargs["tools"] = tools
        self._apply_thinking_param(kwargs, thinking)
        resp = self._create_completion(kwargs)
        self.note_progress()
        elapsed = time.perf_counter() - t0
        usage = resp.usage
        if usage:
            logger.info(f"[Behavior] LLM call completed in {elapsed:.2f}s, "
                        f"tokens: prompt={usage.prompt_tokens}, completion={usage.completion_tokens}, total={usage.total_tokens}")
        else:
            logger.info(f"[Behavior] LLM call completed in {elapsed:.2f}s")
        return resp

    def _llm_call_stream(self, messages: list, max_tokens: int = 4000, tools: list = None,
                         thinking: bool | None = None):
        from pet.brain.llm_retry import llm_stream_with_retry

        def _build_kwargs() -> dict:
            # 每次尝试都按当前生效方案重建参数，回退到备选后模型名随之更新
            kwargs = {"model": self._llm.model, "messages": messages, "max_tokens": max_tokens,
                      "temperature": config.LLM_TEMPERATURE, "stream": True,
                      "stream_options": {"include_usage": True}}
            if tools:
                kwargs["tools"] = tools
            self._apply_thinking_param(kwargs, thinking)
            return kwargs

        def _create():
            return self._create_completion(_build_kwargs())

        self.llm_stats.increment()
        return llm_stream_with_retry(
            _create,
            tag="Behavior.stream",
            create_timeout=config.LLM_CREATE_TIMEOUT,
            on_retry=self._on_llm_retry,
        )

    def _log_prompt_size(self, messages: list, tag: str):
        """计算并打印 prompt 规模：文本字符数 + 图片 base64 大小。"""
        text_chars = 0
        image_count = 0
        image_bytes = 0
        image_fmt = ""
        for m in messages:
            content = m["content"]
            if isinstance(content, str):
                text_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_chars += len(part.get("text", ""))
                    elif isinstance(part, dict) and part.get("type") == "image_url":
                        image_count += 1
                        url = part.get("image_url", {}).get("url", "")
                        if "," in url:
                            image_bytes += len(url.split(",", 1)[1])
                            # 从 data:image/jpeg;base64,... 中提取格式
                            if not image_fmt and "data:image/" in url:
                                fmt_start = url.find("data:image/") + len("data:image/")
                                fmt_end = url.find(";", fmt_start)
                                if fmt_end > fmt_start:
                                    image_fmt = url[fmt_start:fmt_end]
        t = datetime.now().strftime("%H:%M:%S")
        parts = [f"prompt_chars: {text_chars}"]
        if image_count:
            parts.append(f"images: {image_count}{' (' + image_fmt + ')' if image_fmt else ''} ({image_bytes // 1024}KB base64)")
        logger.info(f"[{t}] [Behavior]   {', '.join(parts)} ({tag})")

    def _call_llm_and_parse(self, messages: list, system_content: str, tag: str,
                            max_tokens: int = 4000, thinking: bool | None = None,
                            enable_tools: bool | None = None) -> BehaviorOutput:
        t = datetime.now().strftime("%H:%M:%S")
        self._llm.reset_effective()  # 新请求链从首选方案开始
        self._apply_cache_control(messages)
        self._dump_context(tag, messages)
        self._log_prompt_size(messages, tag)
        try:
            tools_param = self._build_tools_param(enable_tools)
            resp = self._llm_call(messages, max_tokens=max_tokens, tools=tools_param,
                                  thinking=thinking, _on_retry=self._on_llm_retry)
            msg = resp.choices[0].message
            content = msg.content or ""
            logger.info(f"[{t}] [Behavior] === LLM RESPONSE ({tag}) ===")
            logger.info(f"[{t}] [Behavior]   finish_reason: {resp.choices[0].finish_reason}")
            logger.info(f"[{t}] [Behavior]   raw: {content}")

            if msg.tool_calls:
                tool_calls_map = {}
                for i, tc in enumerate(msg.tool_calls):
                    tool_calls_map[i] = {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                return self._handle_tool_calls(
                    messages, tool_calls_map, content,
                    tag=tag, max_tokens=max_tokens,
                    max_rounds=config.LLM_TOOL_MAX_ROUNDS,
                    speech_streamed=False, enable_tools=enable_tools,
                )

            result = parse_behavior(content)
            logger.info(f"[{t}] [Behavior]   parsed -> {result}")
            return result
        except Exception as e:
            logger.exception(f"[{t}] [Behavior]   {tag} LLM call failed: {type(e).__name__}: {e}")
            logger.warning(f"[{t}] [Behavior]   falling back to local")
            return self._decide_local()

    def _stream_and_build_output(self, messages: list, on_chunk=None, on_stream_end=None,
                                 tag: str = "", max_tokens: int = 4000,
                                 thinking: bool | None = None,
                                 enable_tools: bool | None = None,
                                 cancel_check: callable = None) -> BehaviorOutput:
        self._llm.reset_effective()  # 新请求链从首选方案开始
        self._apply_cache_control(messages)
        self._dump_context(tag, messages)
        self._log_prompt_size(messages, tag)
        try:
            tools_param = self._build_tools_param(enable_tools)
            stream = self._llm_call_stream(messages, max_tokens=max_tokens, tools=tools_param, thinking=thinking)
            chunk_sent = [False]

            def _spy_chunk(delta: str):
                chunk_sent[0] = True
                if on_chunk:
                    on_chunk(delta)

            raw, tool_calls_map = parse_stream_chunks(
                stream, config.LLM_STREAM_TIMEOUT, sink=self, tag=tag,
                cancel_check=cancel_check, on_chunk=_spy_chunk, on_stream_end=on_stream_end,
            )
            speech_streamed = chunk_sent[0]

            if tool_calls_map:
                # 不在此处调用 on_stream_end：保持气泡流不中断，
                # on_stream_end 仅用于 speech 中断（多行 Speech 分开显示），
                # 不在轮次间调用（流式轮次收尾不再调 on_stream_end）。
                return self._handle_tool_calls(
                    messages, tool_calls_map, raw,
                    on_chunk=on_chunk, on_stream_end=on_stream_end, tag=tag,
                    max_tokens=max_tokens, max_rounds=config.LLM_TOOL_MAX_ROUNDS,
                    speech_streamed=speech_streamed, enable_tools=enable_tools,
                    thinking=thinking,
                )

            return self.parser.parse_behavior(raw)

        except CancelledError:
            logger.info(f"[{tag}] stream cancelled")
            raise
        except Exception as e:
            logger.exception(f"[{tag}] stream failed: {type(e).__name__}: {e}")
            return self._decide_local()

    _META_TOOL_NAMES = frozenset({
        "tool_search__search", "tool_search__list_groups",
        "food__spawn", "food__status",
        "game__list", "game__init", "game__play", "game__stop",
        "recall__search", "recall__browse",
    })
    _META_TOOL_MAX_ROUNDS = 99  # 元工具调用安全上限

    # 只读回忆类工具：结果本就在记忆库里，无需再写 Memory 行
    _RECALL_TOOL_NAMES = frozenset({"recall__search", "recall__browse"})

    def _handle_tool_calls(self, messages, tool_calls_map, first_content,
                            on_chunk=None, on_stream_end=None, tag="",
                            max_rounds=5, max_tokens: int = 4000,
                            speech_streamed: bool = False,
                            enable_tools: bool | None = None,
                            thinking: bool | None = None) -> BehaviorOutput:
        """执行 tool_calls 并循环直到 LLM 不再请求工具。

        tool_search / list_groups 等元工具不消耗 max_rounds 配额，
        仅当至少执行了一个非元工具时，才计入一轮。
        """
        import json as _json
        from pet.tools.executor import ToolExecutor, ToolCall

        executor = ToolExecutor()
        current_messages = list(messages)
        tool_log = []  # 记录工具调用摘要，用于写入上下文
        final_instruction_added = False  # 最终轮精简指令是否已追加

        # 仅当 Speech 被实际流式发送时才标记 speech_streamed=True
        _chunk_invoked = [False]
        _wrapped_chunk = None
        if on_chunk:
            def _wrapped_chunk(delta: str):
                _chunk_invoked[0] = True
                on_chunk(delta)

        real_round = 0  # 实际（非元工具）调用轮次计数
        meta_round = 0  # 元工具调用总轮次（安全防护）
        display_round = 0  # 仅用于日志展示
        used_recall = False  # 整次工具循环里是否用过回忆类工具
        recall_instruction_added = False

        while real_round < max_rounds:
            self.note_progress()  # 每轮工具调用都算进展，长流程不被看门狗误杀
            meta_round += 1
            display_round += 1
            if meta_round > self._META_TOOL_MAX_ROUNDS:
                logger.warning(f"[Behavior] reached META_MAX_ROUNDS={self._META_TOOL_MAX_ROUNDS}, force terminate")
                break

            openai_tool_calls = []
            for idx in sorted(tool_calls_map.keys()):
                tc = tool_calls_map[idx]
                # 清洗 arguments：解析后重新序列化，避免流式拼接残留导致 400
                try:
                    clean_args = _json.dumps(_json.loads(tc["arguments"] or "{}"), ensure_ascii=False)
                except _json.JSONDecodeError:
                    clean_args = "{}"
                tc["arguments"] = clean_args
                openai_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": clean_args},
                })
            assistant_msg = {"role": "assistant", "tool_calls": openai_tool_calls}
            if first_content.strip():
                assistant_msg["content"] = first_content
            current_messages.append(assistant_msg)

            sorted_indices = sorted(tool_calls_map.keys())

            def _exec_tool(idx):
                """执行单个工具调用，返回 (idx, tc, result, tool_brief, result_text, tool_aside)。"""
                tc = tool_calls_map[idx]
                try:
                    args = _json.loads(tc["arguments"] or "{}")
                except _json.JSONDecodeError:
                    args = {}
                # 通用 aside 参数：模型调用工具时可带自言自语，播出后不传给 handler
                tool_aside = args.pop("aside", None)
                if tool_aside:
                    from pet.tools.context import TOOL_CTX
                    logger.info(f"[Behavior] tool_call aside: {tool_aside}")
                    TOOL_CTX.speech(str(tool_aside), duration=2000)
                    TOOL_CTX.push_model_aside_pending()
                try:
                    call = ToolCall(name=tc["name"], args=args)
                    result = executor._execute_one(call)
                finally:
                    if tool_aside:
                        from pet.tools.context import TOOL_CTX
                        TOOL_CTX.pop_model_aside_pending()
                # 在 _normalize 之前提取摘要（_normalize 会 pop summary）
                tool_brief = ""
                if result.success and isinstance(result.data, dict):
                    tool_brief = result.data.get("summary", "")
                result_text = executor._normalize(result.data) if result.success else result.error
                return idx, tc, result, tool_brief, result_text, tool_aside

            tool_results = {}
            # 游戏工具（game__*）有跨调用会话依赖（如 start 必须先于 play），
            # 同轮并行会打乱执行顺序导致未开始/错乱，退化为串行按声明顺序执行
            has_game_tool = any(
                tool_calls_map[idx]["name"].startswith("game__")
                for idx in sorted_indices
            )
            use_parallel = config.LLM_TOOL_PARALLEL and len(sorted_indices) > 1 and not has_game_tool
            if use_parallel:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(sorted_indices)) as pool:
                    futures = {pool.submit(_exec_tool, idx): idx for idx in sorted_indices}
                    for future in concurrent.futures.as_completed(futures):
                        res = future.result()
                        tool_results[res[0]] = res
            else:
                for idx in sorted_indices:
                    res = _exec_tool(idx)
                    tool_results[res[0]] = res

            # 判断本轮是否全为元工具调用（不消耗实际轮次配额）
            all_meta = all(
                tool_calls_map[idx]["name"] in self._META_TOOL_NAMES
                for idx in sorted_indices
            )

            # 按 index 排序后依次 append（保持顺序一致性）
            for idx in sorted_indices:
                _, tc, result, tool_brief, result_text, tool_aside = tool_results[idx]
                current_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_text,
                })
                logger.info(f"[Behavior] tool_round_{display_round} {tc['name']} -> {'OK' if result.success else 'FAIL'}")
                log_entry = f"{tc['name']} → {result.context_brief or tool_brief or result_text[:200]}"
                if tool_aside:
                    log_entry = f"（自言自语：{tool_aside}）{log_entry}"
                tool_log.append(log_entry)

                # 搜索工具执行后自动激活匹配的分组
                if tc["name"] == "tool_search__search":
                    try:
                        args = _json.loads(tc["arguments"] or "{}")
                        keyword = args.get("keyword", "")
                        if result.success and isinstance(result.data, dict):
                            self._activate_tool_groups_from_search(result.data.get("matches", []))
                        # 兜底：keyword 直接匹配组名
                        self._activate_groups_from_keyword(keyword)
                    except Exception:
                        pass

                if tc["name"] in self._RECALL_TOOL_NAMES:
                    used_recall = True

            # 非元工具轮次才计数
            if not all_meta:
                real_round += 1

            # 回忆结果衔接：想起的内容已在库里，避免模型再写一遍 Memory 行
            if used_recall and not recall_instruction_added:
                recall_instruction_added = True
                current_messages.append({
                    "role": "user",
                    "content": "这些是你想起来的记忆，本来就在库里，自然说出来即可，不必再输出 Memory 行"
                })

            # 最终轮精简指令：仅在至少执行过一个非元工具后追加
            if not all_meta and not final_instruction_added:
                remaining = max_rounds - real_round
                low_threshold = -(-max_rounds // 10)  # 10% 向上取整
                low = "，轮次不多了请尽快输出" if remaining <= low_threshold else ""
                current_messages.append({
                    "role": "user",
                    "content": f"工具已执行，可直接输出最终行为（Summary+Speech+Action），无需重复分析；有值得记忆的信息才输出 Memory（剩余工具轮次：{remaining}/{max_rounds}{low}）"
                })
                final_instruction_added = True

            # 再次调用 LLM（每轮重建 tools_param，包含新激活的分组）
            tools_param = self._build_tools_param(enable_tools)
            stream = self._llm_call_stream(current_messages, max_tokens=max_tokens, tools=tools_param, thinking=thinking)
            _chunk_invoked[0] = False
            content, new_tool_calls = self.parser.collect_stream(
                stream, config.LLM_STREAM_TIMEOUT, tag=f"{tag}_round_{display_round}",
                on_chunk=_wrapped_chunk, on_stream_end=on_stream_end,
            )
            if _chunk_invoked[0]:
                speech_streamed = True

            if not new_tool_calls:
                # LLM 不再请求工具，解析最终行为
                result = parse_behavior(content)
                result.speech_streamed = speech_streamed
                if tool_log:
                    self.add_context(role="assistant", content=f"[工具调用] {' | '.join(tool_log)}")
                return result

            first_content = content
            tool_calls_map = new_tool_calls

        logger.warning(f"[Behavior] reached MAX_ROUNDS={max_rounds} (real_rounds={real_round}, meta_rounds={meta_round}), force terminate tool loop")
        result = parse_behavior(first_content)
        result.speech_streamed = speech_streamed
        if tool_log:
            self.add_context(role="assistant", content=f"[工具调用] {' | '.join(tool_log)}")
        return result

    def flush_summaries(self):
        """将上下文淘汰产生的待摘要条目队列统一处理。
        有 LLM 就用 LLM 总结，不可用时兜底拼接。
        """
        items = self.drain_pending_summaries()
        if not items:
            return

        logger.info(f"[Behavior] flushing pending summaries: {len(items)} items")
        summary = None
        if self._llm:
            try:
                summary = self._llm_summarize(items)
            except Exception:
                logger.warning("[Behavior] LLM summarization failed, using fallback")

        if not summary:
            summary = self._build_fallback_summary(items)

        if summary:
            self.add_context(role="system", content=f"[历史摘要] {summary}", is_summary=True)
            logger.info(f"[Behavior] flushed pending summaries: {len(items)} items → {summary[:50]}...")

    def _llm_summarize(self, items: list[str]) -> str | None:
        """用 LLM 将多条历史上下文压缩为一句简洁摘要。"""
        self._llm.reset_effective()  # 独立请求，不继承上一条链的备选回退状态
        messages = self.ctx.build_summary_messages(items)
        resp = self._llm_call(messages, max_tokens=config.LLM_MAX_TOKENS_SUMMARY,
                              _on_retry=self._on_llm_retry)
        raw = resp.choices[0].message.content
        result = (raw or "").strip()
        if not result:
            logger.warning(f"[Behavior] LLM summarize returned empty content, raw={raw!r}, finish_reason={resp.choices[0].finish_reason}")
            return None
        logger.info(f"[Behavior] LLM summarized {len(items)} items → {result}")
        return result

    _LOCAL_ACTIONS = [
        ("sit", "歇一会儿～"),
        ("drive", "骑上我心爱的小摩托～"),
        ("walk", "蹦蹦跳跳真开心！"),
        ("shake_arms", "耶！太好啦！"),
        ("look_around", "那边有什么好玩的？"),
        ("stretch", "唔…伸个懒腰舒服多了～"),
        ("sleep", "呼…呼… zzz…"),
        ("thinking", "让我想想…"),
        ("bathing", "洗个澡清爽一下～"),
    ]

    def _decide_local(self) -> BehaviorOutput:
        action, speech = random.choice(self._LOCAL_ACTIONS)
        t = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{t}] [Behavior] _decide_local → {action} / {speech}")

        # walk 类动作需要方向和距离参数
        if action in ("drive", "walk"):
            direction = random.choice(["left", "right"])
            distance = random.randint(300, 800)
            step = ActionStep(action, args=(direction, distance))
        else:
            step = ActionStep(action)

        return BehaviorOutput(
            actions=[step],
            speech=speech,
            emotion="happy" if action == "shake_arms" else None,
        )

    def _interact_decide_local(self, event_hint: str) -> BehaviorOutput:
        """本地模式下根据交互提示词生成响应。"""
        t = datetime.now().strftime("%H:%M:%S")

        # 检测投喂事件并提取食物名
        if "投喂" in event_hint:
            food_match = re.search(r"投喂了(.+)[。，,]", event_hint)
            food = food_match.group(1) if food_match else "好吃的"

            speeches = [
                f"嗷呜～{food}真好吃！谢谢！",
                f"嗯嗯，{food}好香呀～",
                f"嘿嘿，{food}太棒啦！",
                f"哇，{food}！好开心！",
                f"嚼嚼嚼…{food}美味！",
            ]
            speech = random.choice(speeches)
            logger.info(f"[{t}] [Behavior] _interact_decide_local(feed {food}) → {speech}")

            return BehaviorOutput(
                actions=[ActionStep("shake_arms", kwargs={"duration": 3})],
                speech=speech,
                emotion="love",
                vitals_deltas={"satiety": 1.5, "energy": 0.5},
                mood_deltas={"joy": 1.5, "affection": 1.0},
            )

        # 检测抓取事件
        if "抓起" in event_hint or "抓住" in event_hint:
            speech = random.choice([
                "哎哎？快放我下来～",
                "呜哇，被抓住了！",
                "诶诶诶？！",
            ])
            logger.info(f"[{t}] [Behavior] _interact_decide_local(grab) → {speech}")
            return BehaviorOutput(
                actions=[ActionStep("shake_arms", kwargs={"duration": 3})],
                speech=speech,
                emotion="grim",
            )

        # 检测释放事件
        if "放下" in event_hint or "释放" in event_hint:
            speech = random.choice([
                "呼…终于落地了。",
                "踏实的感觉真好～",
                "嗯哼，还是地上舒服。",
            ])
            logger.info(f"[{t}] [Behavior] _interact_decide_local(release) → {speech}")
            return BehaviorOutput(
                actions=[ActionStep("stretch", kwargs={"duration": 4})],
                speech=speech,
            )

        # 其他交互事件：通用兜底
        action, speech = random.choice(self._LOCAL_ACTIONS)
        logger.info(f"[{t}] [Behavior] _interact_decide_local(generic) → {action} / {speech}")
        step = ActionStep(action, args=(random.choice(["left", "right"]), random.randint(300, 800))) \
            if action in ("drive", "walk") else ActionStep(action)

        return BehaviorOutput(
            actions=[step],
            speech=speech,
            emotion="happy" if action == "shake_arms" else None,
        )

    def _chat_decide_local(self, user_message: str) -> BehaviorOutput:
        return BehaviorOutput(
            actions=[ActionStep("look_around", kwargs={"duration": 5})],
            speech=f"（听到了：{user_message[:10]}...但我还不会回应）",
        )

    def _apply_cache_control(self, messages: list):
        """为 system prompt 添加缓存标记（Anthropic 兼容 API 使用）。

        仅在 config.LLM_CACHE_PROMPT 启用时生效。
        将 system 消息的字符串 content 包装为带 cache_control 的结构化格式。
        OpenAI 原生 API 会忽略该字段，Anthropic 兼容端点则会缓存。
        """
        if not config.LLM_CACHE_PROMPT:
            return
        if not messages or messages[0]["role"] != "system":
            return
        content = messages[0]["content"]
        if not isinstance(content, str):
            return
        messages[0]["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]

    def _dump_context(self, tag: str, messages: list):
        t = datetime.now().strftime("%H:%M:%S")
        logger.debug(f"[{t}] [Behavior] ====== FULL CONTEXT ({tag}) ======")
        for i, m in enumerate(messages):
            if isinstance(m["content"], str):
                logger.debug(f"[{t}] [Behavior] --- msg[{i}] role={m['role']} ---\n{m['content']}")
            else:
                for j, part in enumerate(m["content"]):
                    if part["type"] == "text":
                        logger.debug(f"[{t}] [Behavior] --- msg[{i}] role={m['role']} part[{j}] text ---\n{part['text']}")
                    else:
                        logger.debug(f"[{t}] [Behavior] --- msg[{i}] role={m['role']} part[{j}] {part['type']} len={len(str(part))} --- (binary omitted)")
        logger.debug(f"[{t}] [Behavior] ====== END CONTEXT ({tag}) ======")
