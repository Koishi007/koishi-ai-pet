"""Game 基类"""

import logging
import time

from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)


class Game:
    """回合制游戏接口。

    台词钩子（win_speech/lose_speech/stop_speech）默认返回 None，
    由具体游戏覆写提供符合自身性格的台词；容器用 summary 兜底。
    """

    def name(self) -> str:
        raise NotImplementedError

    def description(self) -> str:
        """规则说明，供 game__list 展示。"""
        return ""

    def new_state(self) -> dict:
        """初始化会话状态（首次 play 时创建）。"""
        return {}

    def args_schema(self) -> dict:
        """本游戏 play 所需的参数 schema（格式同工具 args：{参数名: {type, required, description}}）。"""
        return {}

    def play(self, state: dict, **params) -> dict:
        """推进一回合，返回结果 dict（含 ended 标记）。"""
        raise NotImplementedError

    def win_speech(self) -> str | None:
        return None

    def lose_speech(self) -> str | None:
        return None

    def stop_speech(self) -> str | None:
        return None

    def forfeit_speech(self) -> str | None:
        """用户主动结束游戏时的台词（默认复用 stop_speech）。"""
        return self.stop_speech()


class GameBase:
    """回合制游戏容器：按需创建会话，play 推进，ended 自动清理。"""

    SESSION_TTL = 120  # 会话闲置过期秒数（秒），模型提前退出后防止 session 泄漏

    def __init__(self):
        self._sessions: dict[str, dict] = {}
        self._games: dict[str, Game] = {}

    def register(self, game: Game):
        self._games[game.name()] = game

    def list_games(self) -> dict:
        games = list(self._games)
        return {
            "summary": "暂无可玩的游戏" if not games else "可玩游戏：" + ", ".join(
                f"{n}（{self._games[n].description()}）" for n in games),
            "success": True,
            "games": games,
        }

    def submit(self, game_name: str, payload: dict) -> bool:
        """UI 线程调用：提交玩家动作（任意 dict）并唤醒等待中的 play。

        线程安全：dict 写入在 GIL 下原子完成，wait_event.set() 唤醒脑线程。
        仅当游戏会话存在、且当前正处于等待玩家动作时生效。
        """
        state = self._sessions.get(game_name)
        if state is None or state.get("_cancelled"):
            return False
        ev = state.get("wait_event")
        if ev is None:
            return False  # 该游戏不支持交互式等待
        with state["move_lock"]:
            if state.get("pending_move") is not None:
                return False
            state["pending_move"] = payload
        ev.set()
        return True

    def _arg_owners(self) -> dict[str, str]:
        """参数名 → 所属游戏名 的映射（参数名全局唯一，用于归属校验）。"""
        owners = {}
        for game in self._games.values():
            for arg in game.args_schema():
                if arg in owners:
                    logger.warning(f"[Game] 参数名 {arg} 同时被 {owners[arg]} 与 {game.name()} 声明，归属校验将以后者为准")
                owners[arg] = game.name()
        return owners

    def cancel_all(self):
        """程序退出/丢弃旧脑线程时取消所有进行中的会话，唤醒等待中的 play。

        遍历用 list() 副本，避免 UI 线程并发 pop 导致迭代时字典变更异常；
        同时隐藏对应游戏面板，避免残留 UI。
        """
        for name, state in list(self._sessions.items()):
            state["_cancelled"] = True
            ev = state.get("wait_event")
            if ev is not None:
                ev.set()
            try:
                TOOL_CTX.hide_game_board(name)
            except Exception:
                pass
        self._sessions.clear()

    def names(self) -> list[str]:
        """已注册的游戏名列表。"""
        return list(self._games)

    def play_args_schema(self) -> dict:
        """合并所有游戏的 play 参数 schema（供 game__play 工具动态生成）。

        各游戏可能有互斥的必填参数（如猜数字 guess 与猜拳 rps_move），
        平铺合并后无法同时满足 required，故统一降级为可选，由各游戏 play 内部自行校验缺失。
        """
        schema: dict = {
            "game_name": {
                "type": "str",
                "required": True,
                "description": "想玩的游戏名",
            },
        }
        if self._games:
            schema["game_name"]["enum"] = list(self._games)
        for game in self._games.values():
            for arg_name, spec in game.args_schema().items():
                merged = dict(spec)
                merged["required"] = False  # 合并后必填参数互斥，统一降级由 play 内校验
                merged["description"] = f"[仅 {game.name()} 需要] {spec.get('description', '')}"
                schema[arg_name] = merged
        return schema

    def init(self, game_name: str) -> dict:
        """显式开启（或重新开始）一局游戏，创建全新会话。

        所有游戏必须先调用 game__init 再 game__play；上次会话（如有）被强制丢弃，
        并清理旧对局残留面板。返回结果带 ended=False 表示对局已就绪。
        """
        game = self._games.get(game_name)
        if game is None:
            return {
                "summary": f"没有叫 {game_name} 的游戏，可玩：{', '.join(self._games) or '无'}",
                "success": False,
                "ended": True,
            }
        old = self._sessions.pop(game_name, None)
        if old is not None:
            # 取消并唤醒可能仍阻塞在等待中的旧 play，
            # 否则旧 play 超时返回后会按名误删刚创建的新会话
            old["_cancelled"] = True
            ev = old.get("wait_event")
            if ev is not None:
                ev.set()
        state = game.new_state()
        state["last_active"] = time.time()
        self._sessions[game_name] = state
        try:
            TOOL_CTX.hide_game_board(game_name)  # 清掉旧对局残留面板
        except Exception:
            pass
        return {
            "summary": f"{game_name} 游戏初始化完成，但游戏尚未开始。"
                       f"（对模型）请立即继续调用 game__play(game_name=\"{game_name}\" 及该游戏出招参数) "
                       f"开启第一回合并持续推进，不要在此输出最终答复；只有某次 game__play 返回 ended=True 才代表游戏结束。",
            "success": True,
            "ended": False,
        }

    def play(self, game_name: str, **params) -> dict:
        game = self._games.get(game_name)
        if game is None:
            return {
                "summary": f"没有叫 {game_name} 的游戏，可玩：{', '.join(self._games) or '无'}",
                "success": False,
                "ended": True,
            }
        # 参数归属校验：传入的参数必须属于该游戏，避免模型把别家参数传过来
        # 参数名全局唯一（guess/ttt_move/rps_move），通过 参数名→游戏 映射判定
        owners = self._arg_owners()
        game_args = sorted(game.args_schema())
        unknown = [k for k in params if owners.get(k) != game_name]
        if unknown:
            return {
                "summary": f"参数 {', '.join(unknown)} 不属于 {game_name}，"
                           f"{game_name} 需要的参数：{', '.join(game_args) or '无'}。"
                           f"（对模型）请用正确的参数重新调用 game__play 继续游戏，不要输出最终答复。",
                "success": False,
                "ended": False,  # 本回合未执行，游戏状态不变，模型可纠正后重试
            }
        state = self._sessions.get(game_name)
        if state is not None and time.time() - state.get("last_active", 0) > self.SESSION_TTL:
            # 会话闲置超时（如模型提前退出未结束），视为已结束
            logger.info(f"[Game] {game_name} session expired after {self.SESSION_TTL}s")
            self._sessions.pop(game_name, None)
            state = None
        if state is None:
            # 未开始或已结束：要求显式 game__init，避免模型误以为可续局/静默重开
            return {
                "summary": f"{game_name} 游戏未开始或已结束，请先调用 game__init(game_name=\"{game_name}\") 开启新对局",
                "success": False,
                "ended": True,
            }
        state["last_active"] = time.time()
        # 预检：会话已被外部取消（用户点×/stop/换脑线程）但 play 尚未退出。
        # 直接短路结束并弹会话，避免模型下一轮取到僵尸 session 重新弹面板/继续对局。
        if state.get("_cancelled"):
            if self._sessions.get(game_name) is state:
                self._sessions.pop(game_name, None)
            try:
                TOOL_CTX.hide_game_board(game_name)
            except Exception:
                pass
            if state.get("_forfeit"):
                result = {
                    "summary": f"用户主动结束了 {game_name} 游戏，判用户输。"
                               f"（对模型）请总结本局结果并输出最终答复，"
                               f"按本局结果输出 Mood 变动范围（仅输出有变化的维度）：joy+0~1、affection+0~1。",
                    "ended": True,
                    "won": False,
                    "forfeit": True,
                }
            else:
                result = {
                    "summary": f"{game_name} 游戏已结束",
                    "ended": True,
                    "won": None,
                    "suppress_speech": True,
                }
            result.setdefault("success", True)
            result.setdefault("game_name", game_name)
            self._emit_speech(game, result)
            return result
        try:
            result = game.play(state, **params)
        except Exception as e:
            logger.exception(f"[Game] {game_name} play error: {e}")
            if self._sessions.get(game_name) is state:
                self._sessions.pop(game_name, None)
            return {
                "summary": f"游戏 {game_name} 出错了：{e}",
                "success": False,
                "ended": True,
            }
        if result.get("ended") and self._sessions.get(game_name) is state:
            self._sessions.pop(game_name, None)
            # 明确告知游戏已结束，避免模型继续调用 game__play/game__stop
            if result.get("summary"):
                result["summary"] = (
                    f"{result['summary']}（对模型）游戏已结束（ended=True），"
                    f"请总结本局结果并输出最终答复，不要继续调用游戏工具；"
                    f"按本局胜负输出 Mood 变动范围（仅输出有变化的维度，输赢都增加，大小有别）："
                    f"获胜 joy+2~5、affection+2~5；落败 joy+0~2、affection+0~1。"
                )
        elif result.get("summary"):
            # 游戏未结束：必须在 summary 里明确"继续推进"，否则模型可能提前输出最终答复
            result["summary"] = (
                f"{result['summary']}（对模型）本回合尚未结束（ended=False），"
                f"请立即继续调用 game__play 推进游戏并等待玩家动作，不要输出最终答复；"
                f"只有某次返回 ended=True 才结束并总结。"
            )
        result.setdefault("success", True)
        result.setdefault("game_name", game_name)
        self._emit_speech(game, result)
        return result

    def stop(self, game_name: str) -> dict:
        """主动结束（模型 game__stop / 面板闲置超时）：普通收场，不算胜负。"""
        if game_name not in self._sessions:
            return {
                "summary": f"{game_name} 没有进行中的对局（已结束或未开始）。"
                           f"若刚结束，请直接输出最终回复，无需调用 game__stop；"
                           f"想再玩一局请调用 game__init",
                "success": False,
                "ended": True,
            }
        self._teardown(game_name)
        if TOOL_CTX.is_model_aside_pending():
            return {
                "summary": f"已结束 {game_name} 游戏",
                "success": True,
                "ended": True,
            }
        game = self._games.get(game_name)
        speech = game.stop_speech() if game else None
        if not speech:
            speech = f"不玩了，{game_name} 先到这吧"
        TOOL_CTX.speech(speech)
        return {
            "summary": f"已结束 {game_name} 游戏",
            "success": True,
            "ended": True,
        }

    def forfeit(self, game_name: str) -> dict:
        """用户主动结束（棋盘面板点击关闭/结束）：判用户输。

        游戏进行中（会话存在）：只打判负标志并唤醒等待中的 play，
        由 play 以工具结果形式返回给模型，模型下一轮收到"用户主动结束、判输"。
        游戏已结束（会话已清）：面板只是残留展示，静默关闭，不回应模型。
        """
        state = self._sessions.get(game_name)
        if state is None:
            # 游戏已提前结束，用户只是关闭残留面板：不播台词、不回应 LLM
            return {
                "summary": f"已结束 {game_name} 游戏",
                "success": True,
                "ended": True,
            }
        state["_forfeit"] = True
        state["_cancelled"] = True
        ev = state.get("wait_event")
        if ev is not None:
            ev.set()
        try:
            TOOL_CTX.hide_game_board(game_name)
        except Exception:
            pass
        return {
            "summary": f"你结束了 {game_name} 游戏，判你输",
            "success": True,
            "ended": True,
            "won": False,
        }

    def _teardown(self, game_name: str):
        """清理会话并唤醒等待中的 play（如有），让脑线程尽快返回。"""
        state = self._sessions.pop(game_name)
        state["_cancelled"] = True
        ev = state.get("wait_event")
        if ev is not None:
            ev.set()
        try:
            TOOL_CTX.hide_game_board(game_name)
        except Exception:
            pass

    def _emit_speech(self, game: Game, result: dict):
        """按优先级输出桌宠台词：结果自带 speech → 游戏钩子 → summary 兜底。

        模型已在 tool_call 里带 speech（由 _exec_tool 播出）时跳过，避免播两句。
        外部取消导致 play 中断返回的（suppress_speech）同样跳过，收场词由调用方负责。
        """
        if result.pop("suppress_speech", False):
            return
        speech = result.pop("speech", None)
        if TOOL_CTX.is_model_aside_pending():
            return
        if not speech:
            ended = result.get("ended")
            if result.get("forfeit"):
                # 用户主动结束：判输但播"你认输"台词，而非普通败北台词
                speech = game.forfeit_speech()
            elif ended and result.get("won"):
                speech = game.win_speech()
            elif ended and result.get("won") is False:
                speech = game.lose_speech()
        if speech:
            TOOL_CTX.speech(speech)
        elif result.get("summary"):
            # 剥离（对模型）指令段，只把自然内容播给用户
            TOOL_CTX.speech(str(result["summary"]).split("（对模型）", 1)[0].strip())


GAME = GameBase()
