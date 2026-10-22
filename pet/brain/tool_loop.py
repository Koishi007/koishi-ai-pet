"""工具轮次：执行 LLM 请求的工具并循环，直到模型不再请求工具。

留在 brain 内不上提 agent：轮次预算、分组激活与最终行为解析都属于「一次决策」的编排，
会话能力通过 ToolSession 注入。
"""

import concurrent.futures
import json
import logging
from typing import Optional, Protocol

from pet.config import config
from pet.brain.events import args_digest, new_group_id
from pet.brain.output import BehaviorOutput
from pet.brain.parsing import parse_behavior
from pet.tools.executor import ToolCall, ToolExecutor
from pet.tools.registry import TOOL_REGISTRY

logger = logging.getLogger(__name__)


class ToolSession(Protocol):
    """工具轮次需要的会话能力：轮次策略、工具开关、分组激活与流式回传。

    三个策略常量由会话提供，测试与将来的多策略实现都改这里，不改轮次实现。
    `executor` 返回 `ToolResult`，`stream` 返回 (原始输出文本, tool_calls 表)。
    """

    # 元工具（工具发现/觅食/游戏类）不消耗实际工具调用轮次
    meta_tool_names: frozenset

    # 元工具调用安全上限：元工具不占配额，需要独立上限防死循环
    meta_tool_max_rounds: int

    # 只读回忆类工具：结果本就在记忆库里，无需再写 Memory 行
    recall_tool_names: frozenset

    # 模型是否支持视觉；工具返回的图片只在此为真时进入下一轮消息
    has_vision: bool

    def tools_param(self, enable_tools: Optional[bool]) -> Optional[list]: ...

    def activate_groups(self, name: str, result, arguments: dict) -> None: ...

    def note_progress(self) -> None: ...

    def speak_aside(self, text: str) -> None: ...

    def end_aside(self) -> None: ...

    def add_context(self, role: str, content: str, is_summary: bool = False) -> None: ...

    def executor(self) -> ToolExecutor: ...

    def stream(self, messages: list, max_tokens: int, tools, thinking: Optional[bool], *,
               tag: str, on_chunk=None, on_stream_end=None): ...


def _image_source(result, tool_name: str) -> str:
    """图片的来源说明：优先用工具写的 context_brief，其次路径或 URL。"""
    if result.meta.get("context_brief"):
        return result.meta["context_brief"]
    if isinstance(result.output, dict):
        for key in ("path", "url"):
            value = result.output.get(key)
            if value:
                return str(value)
    return tool_name


def run_tool_loop(messages: list, tool_calls_map: dict, first_content: str, session: ToolSession, *,
                  on_chunk=None, on_stream_end=None, tag: str = "",
                  max_rounds: int = 5, max_tokens: int = 4000,
                  speech_streamed: bool = False,
                  enable_tools: Optional[bool] = None,
                  thinking: Optional[bool] = None) -> BehaviorOutput:
    """tool_search / list_groups 等元工具不消耗 max_rounds 配额，
    仅当至少执行了一个非元工具时，才计入一轮。
    """
    executor = session.executor()
    current_messages = list(messages)
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
        session.note_progress()  # 每轮工具调用都算进展，长流程不被看门狗误杀
        meta_round += 1
        display_round += 1
        if meta_round > session.meta_tool_max_rounds:
            logger.warning(f"[Behavior] reached META_MAX_ROUNDS={session.meta_tool_max_rounds}, force terminate")
            break

        openai_tool_calls = []
        for idx in sorted(tool_calls_map.keys()):
            tc = tool_calls_map[idx]
            # 清洗 arguments：解析后重新序列化，避免流式拼接残留导致 400
            try:
                clean_args = json.dumps(json.loads(tc["arguments"] or "{}"), ensure_ascii=False)
            except json.JSONDecodeError:
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
        tool_results = _execute_round(tool_calls_map, sorted_indices, executor, session)

        # 判断本轮是否全为元工具调用（不消耗实际轮次配额）
        all_meta = all(
            tool_calls_map[idx]["name"] in session.meta_tool_names
            for idx in sorted_indices
        )

        # 按 index 排序后依次 append（保持顺序一致性）
        round_images = []
        round_log = []
        for idx in sorted_indices:
            _, tc, result, tool_brief, result_text, tool_aside = tool_results[idx]
            current_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_text,
            })
            logger.info(f"[Behavior] tool_round_{display_round} {tc['name']} -> {'OK' if result.ok else 'FAIL'}")
            log_entry = f"{tc['name']} → {result.meta.get('context_brief') or tool_brief or result_text[:200]}"
            if tool_aside:
                log_entry = f"（自言自语：{tool_aside}）{log_entry}"
            images = result.meta.get("images") or []
            if images and session.has_vision:
                # 图片只在当轮上下文里，工具记录留一条可再次读取的引用
                log_entry = f"{log_entry}（图片已读取，可再次调用查看）"
                for image in images:
                    round_images.append((
                        f"data:{image.get('mime', 'image/png')};base64,{image.get('base64', '')}",
                        image.get("caption") or _image_source(result, tc["name"]),
                    ))
            round_log.append(log_entry)

            # 搜索工具执行后自动激活匹配的分组
            try:
                arguments = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            session.activate_groups(tc["name"], result, arguments)

            if tc["name"] in session.recall_tool_names:
                used_recall = True

        if not _record_tool_events(session, tool_calls_map, sorted_indices, tool_results):
            # 事件写入失败时，本轮退回文本记录，不留空档
            session.add_context(
                role="assistant", content=f"[工具调用] {' | '.join(round_log)}")

        # tool 角色只承载文本，图片另起一条 user 消息附在紧接的一次请求里
        image_msg = None
        if round_images:
            parts = [{"type": "text", "text": "附图：本轮工具读取到的画面。"}]
            for uri, source in round_images:
                parts.append({"type": "text", "text": f"来源：{source}"})
                parts.append({"type": "image_url", "image_url": {"url": uri}})
            image_msg = {"role": "user", "content": parts}
            current_messages.append(image_msg)

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
                "content": f"工具已执行，可直接输出最终行为（Summary+Speech(可多个)+Action），无需重复分析；有值得记忆的信息才输出 Memory（剩余工具轮次：{remaining}/{max_rounds}{low}）"
            })
            final_instruction_added = True

        # 再次调用 LLM（每轮重建 tools_param，包含新激活的分组）
        _chunk_invoked[0] = False
        round_tools = session.tools_param(enable_tools)
        logger.info(f"[Behavior] tool_round_{display_round} tools={len(round_tools or [])}")
        content, new_tool_calls = session.stream(
            current_messages, tools=round_tools,
            max_tokens=max_tokens, thinking=thinking,
            tag=f"{tag}_round_{display_round}", on_chunk=_wrapped_chunk,
            on_stream_end=on_stream_end,
        )
        if image_msg is not None:
            # 图片只随这一次请求发送，后续轮次不再重复上传
            current_messages = [m for m in current_messages if m is not image_msg]
        if _chunk_invoked[0]:
            speech_streamed = True

        # 每轮输出都是语义独立的一段：不在轮次边界断句，下一轮的 Speech
        # 会接在没有收尾的上一句里，两句拼进同一个气泡。
        # 调用方的 on_stream_end 自带「本句是否已开头」的守卫，没有副作用。
        if on_stream_end:
            on_stream_end()

        if not new_tool_calls:
            # LLM 不再请求工具，解析最终行为
            result = parse_behavior(content)
            result.speech_streamed = speech_streamed
            return result

        first_content = content
        tool_calls_map = new_tool_calls

    logger.warning(f"[Behavior] reached MAX_ROUNDS={max_rounds} (real_rounds={real_round}, meta_rounds={meta_round}), force terminate tool loop")
    result = parse_behavior(first_content)
    result.speech_streamed = speech_streamed
    return result


def _tool_brief(result, tool_brief: str, result_text: str = "") -> str:
    """结果短句：context_brief → summary → 结果文本首行，去掉 JSON 后截 120 字符。"""
    text = (result.meta.get("context_brief") or tool_brief or "").strip()
    if not text and not result.ok:
        text = str(result.error_message or "").strip()
    if not text and result_text:
        # 纯文本结果才取首行；JSON 结果没有可读结论，跳过
        head = result_text.strip().splitlines()[0].strip() if result_text.strip() else ""
        if head and not head.startswith(("{", "[")):
            text = head
    if text.startswith(("{", "[")):
        text = ""
    if not text:
        text = "操作已完成" if result.ok else "操作失败"
    return text[:120]


def _record_tool_events(session, tool_calls_map: dict, sorted_indices: list,
                        tool_results: dict) -> bool:
    """把本轮工具调用写成同组事件；失败返回 False 由调用方退回文本记录。"""
    recorder = getattr(session, "record_tool_events", None)
    if recorder is None:
        return False
    group_id = new_group_id()
    calls, results, asides = [], [], []
    for idx in sorted_indices:
        _, tc, result, tool_brief, result_text, tool_aside = tool_results[idx]
        try:
            args = json.loads(tc["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {}
        args.pop("aside", None)
        calls.append({"id": tc["id"], "name": tc["name"],
                      "args_digest": args_digest(args),
                      "side_effect": TOOL_REGISTRY.is_side_effect(tc["name"])})
        results.append({"tool_call_id": tc["id"], "ok": bool(result.ok),
                        "brief": _tool_brief(result, tool_brief, result_text),
                        "raw": result_text})
        if tool_aside:
            asides.append((tc["id"], str(tool_aside)))
    try:
        return bool(recorder(group_id, calls, results, asides))
    except Exception:
        logger.exception("[Behavior] record tool events failed")
        return False


def _execute_round(tool_calls_map: dict, sorted_indices: list, executor: ToolExecutor,
                   session: ToolSession) -> dict:
    """执行一轮工具调用：并行或串行，返回 {index: 结果元组}。"""
    # 游戏工具（game__*）有跨调用会话依赖（如 start 必须先于 play），
    # 同轮并行会打乱执行顺序导致未开始/错乱，退化为串行按声明顺序执行
    has_game_tool = any(
        tool_calls_map[idx]["name"].startswith("game__")
        for idx in sorted_indices
    )
    use_parallel = config.LLM_TOOL_PARALLEL and len(sorted_indices) > 1 and not has_game_tool

    tool_results = {}
    if use_parallel:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(sorted_indices)) as pool:
            futures = {
                pool.submit(_exec_tool, tool_calls_map, idx, executor, session): idx
                for idx in sorted_indices
            }
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                tool_results[res[0]] = res
    else:
        for idx in sorted_indices:
            res = _exec_tool(tool_calls_map, idx, executor, session)
            tool_results[res[0]] = res
    return tool_results


def _exec_tool(tool_calls_map: dict, idx: int, executor: ToolExecutor, session: ToolSession):
    """执行单个工具调用，返回 (idx, tc, result, tool_brief, result_text, tool_aside)。"""
    tc = tool_calls_map[idx]
    try:
        args = json.loads(tc["arguments"] or "{}")
    except json.JSONDecodeError:
        args = {}
    # 通用 aside 参数：模型调用工具时可带自言自语，播出后不传给 handler
    tool_aside = args.pop("aside", None)
    if tool_aside:
        logger.info(f"[Behavior] tool_call aside: {tool_aside}")
        session.speak_aside(str(tool_aside))
    try:
        call = ToolCall(name=tc["name"], args=args)
        result = executor.execute_one(call)
    finally:
        if tool_aside:
            session.end_aside()
    # 摘要供日志与上下文使用；_normalize 不改动原 dict，顺序无所谓
    tool_brief = ""
    if result.ok and isinstance(result.output, dict):
        tool_brief = result.output.get("summary", "")
    result_text = executor.normalize(result.output) if result.ok else result.error_message
    return idx, tc, result, tool_brief, result_text, tool_aside