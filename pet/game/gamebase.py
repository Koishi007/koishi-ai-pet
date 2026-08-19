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

    def submit_move(self, game_name: str, row: int, col: int) -> bool:
        """UI 线程调用：提交玩家落子并唤醒等待中的 play。

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
            state["pending_move"] = {"row": row, "col": col}
        ev.set()
        return True

    def cancel_all(self):
        """程序退出/丢弃旧脑线程时取消所有进行中的会话，唤醒等待中的 play。

        遍历用 list() 副本，避免 UI 线程并发 pop 导致迭代时字典变更异常。
        """
        for name, state in list(self._sessions.items()):
            state["_cancelled"] = True
            ev = state.get("wait_event")
            if ev is not None:
                ev.set()
        self._sessions.clear()

    def play_args_schema(self) -> dict:
        """合并所有游戏的 play 参数 schema（供 game__play 工具动态生成）。"""
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
                schema[arg_name] = spec
        return schema

    def play(self, game_name: str, **params) -> dict:
        game = self._games.get(game_name)
        if game is None:
            return {
                "summary": f"没有叫 {game_name} 的游戏，可玩：{', '.join(self._games) or '无'}",
                "success": False,
                "ended": True,
            }
        state = self._sessions.get(game_name)
        if state is not None and time.time() - state.get("last_active", 0) > self.SESSION_TTL:
            # 会话闲置超时（如模型提前退出未结束），丢弃旧状态重新开局
            logger.info(f"[Game] {game_name} session expired after {self.SESSION_TTL}s, restart")
            self._sessions.pop(game_name, None)
            state = None
        if state is None:
            state = game.new_state()
            self._sessions[game_name] = state
        state["last_active"] = time.time()
        # 预检：会话已被外部取消（用户点×/stop/换脑线程）但 play 尚未退出。
        # 直接短路结束并弹会话，避免模型下一轮取到僵尸 session 重新弹面板/继续对局。
        if state.get("_cancelled"):
            self._sessions.pop(game_name, None)
            try:
                TOOL_CTX.hide_game_board(game_name)
            except Exception:
                pass
            if state.get("_forfeit"):
                result = {
                    "summary": f"用户主动结束了 {game_name} 游戏，判用户输",
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
            self._sessions.pop(game_name, None)
            return {
                "summary": f"游戏 {game_name} 出错了：{e}",
                "success": False,
                "ended": True,
            }
        if result.get("ended"):
            self._sessions.pop(game_name, None)
        result.setdefault("success", True)
        result.setdefault("game_name", game_name)
        self._emit_speech(game, result)
        return result

    def stop(self, game_name: str) -> dict:
        """主动结束（模型 game__stop / 面板闲置超时）：普通收场，不算胜负。"""
        if game_name not in self._sessions:
            return {
                "summary": f"没有进行中的 {game_name} 游戏",
                "success": False,
                "ended": True,
            }
        self._teardown(game_name)
        if TOOL_CTX.is_model_speech_pending():
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
        if TOOL_CTX.is_model_speech_pending():
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
            TOOL_CTX.speech(result["summary"])


GAME = GameBase()
