"""Game 基类"""

import logging

logger = logging.getLogger(__name__)


class Game:
    """回合制游戏接口。"""

    def name(self) -> str:
        raise NotImplementedError

    def description(self) -> str:
        """规则说明，供 game__list 展示。"""
        return ""

    def new_state(self) -> dict:
        """初始化会话状态（首次 play 时创建）。"""
        return {}

    def play(self, state: dict, **params) -> dict:
        """推进一回合，返回结果 dict（含 ended 标记）。"""
        raise NotImplementedError


class GameBase:
    """回合制游戏容器：按需创建会话，play 推进，ended 自动清理。"""

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

    def play(self, game_name: str, **params) -> dict:
        game = self._games.get(game_name)
        if game is None:
            return {
                "summary": f"没有叫 {game_name} 的游戏，可玩：{', '.join(self._games) or '无'}",
                "success": False,
                "ended": True,
            }
        state = self._sessions.get(game_name)
        if state is None:
            state = game.new_state()
            self._sessions[game_name] = state
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
        return result

    def stop(self, game_name: str) -> dict:
        if game_name not in self._sessions:
            return {
                "summary": f"没有进行中的 {game_name} 游戏",
                "success": False,
                "ended": True,
            }
        self._sessions.pop(game_name)
        return {
            "summary": f"已结束 {game_name} 游戏",
            "success": True,
            "ended": True,
        }


GAME = GameBase()
