"""觅食本能 — 需求驱动的自主觅食行为（satiety 低时触发）。"""

import logging
import random
import threading
import time
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from pet.config import config
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

# 生成时与宠物的最小水平间隔（px）
_SPAWN_MARGIN = 200
# 判定轮询间隔（ms）
_TICK_MS = 200
# 近距离判定（px）：食物在上方且水平距离小于该值时，一个 bounce 即可同时完成移动与起跳
_NEAR_JUMP_PX = 400

# 食物种类真源：emoji 池与名称映射（food_window 反向引用）
FOOD_EMOJIS = ["🍰", "🍙", "🍎", "🍜", "🍗", "🍩", "🍕", "🍓", "🥟", "🍣"]
FOOD_NAMES = {
    "🍰": "蛋糕", "🍙": "饭团", "🍎": "苹果", "🍜": "拉面", "🍗": "鸡腿",
    "🍩": "甜甜圈", "🍕": "披萨", "🍓": "草莓", "🥟": "饺子", "🍣": "寿司",
}

# 食物窗口尺寸（px）：生成范围与碰撞判定共用（food_window 反向引用）
FOOD_SIZE = 64


def pick_emoji(food_type: str | None = None) -> str:
    """按模型指定的食物类型选 emoji；未指定或未知则随机。"""
    if food_type:
        for emoji, name in FOOD_NAMES.items():
            if name == food_type:
                return emoji
    return random.choice(list(FOOD_EMOJIS))


def name_of(emoji: str) -> str:
    """emoji 对应的中文食物名，未知时返回「食物」。"""
    return FOOD_NAMES.get(emoji, "食物")


def _side_text(direction: str) -> str:
    """方向 → 中文侧词；Action 参数仍用 left/right。"""
    return "右侧" if direction == "right" else "左侧"


class FoodManager(QObject):
    """生成 / 过期 / 到达判定 / 进食交互触发。"""

    spawn_ui_requested = Signal(str, str, int, int)  # food_id, emoji, x, y
    bind_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # 单例可能首次在脑线程创建
        app = QApplication.instance()
        if app is not None and self.thread() is not app.thread():
            self.moveToThread(app.thread())

        self._lock = threading.RLock()
        self._agent = None
        self._bound = False

        # 宠物位置快照
        self._pet_x = 0
        self._pet_y = 0
        self._screen_geo = (0, 0, 0, 0)  # 就绪前占位；_dy_info 只读 top，按屏幕原点处理
        self._snapshot_ready = False

        self._food: Optional[dict] = None
        self._food_window = None
        self._window_factory = None  # 装配期由 app 注入（见 set_window_factory）

        self._tick_timer = None  # 主线程绑定后创建

        self.spawn_ui_requested.connect(self._spawn_ui)
        self.bind_requested.connect(self._bind_agent)

        if getattr(TOOL_CTX, "_agent", None) is not None:
            self.bind_requested.emit()
        else:
            TOOL_CTX.on_bind(lambda: self.bind_requested.emit())

    def _bind_agent(self, agent=None):
        """主线程执行：绑定 agent 并启动 tick 定时器。"""
        if self._bound:
            return
        self._bound = True
        self._agent = agent if agent is not None else getattr(TOOL_CTX, "_agent", None)
        # QTimer 须在主线程创建
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(_TICK_MS)
        self._tick_timer.timeout.connect(self.tick)
        self._tick_timer.start()
        logger.info("[Food] bound to agent, tick started")

    def _update_position_snapshot(self):
        """主线程调用：读取宠物窗口位置与屏幕几何，更新快照。"""
        win = getattr(self._agent, "_pet_window", None)
        if win is None:
            return
        try:
            screen = win.screen() or QApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            with self._lock:
                self._pet_x = win.x()
                self._pet_y = win.y()
                self._screen_geo = (geo.left(), geo.top(), geo.right(), geo.bottom())
                self._snapshot_ready = True
        except RuntimeError:
            pass

    def _pet_center_x(self) -> int:
        with self._lock:
            return self._pet_x + config.PET_WIDTH // 2

    def _dx_to(self, target_center_x: int) -> tuple[int, str]:
        """目标中心与宠物中心的水平偏移：(距离, 方向)。"""
        pet_cx = self._pet_center_x()
        dx = abs(target_center_x - pet_cx)
        direction = "right" if target_center_x >= pet_cx else "left"
        return dx, direction

    def _dy_info(self, food_ground_y: int) -> tuple[int, int]:
        """食物底边相对宠物底边的垂直信息：(dy, 建议跳高)。

        dy = 宠物底边 − 食物底边：正值=食物在上方（需 bounce），负值=在下方，≈0=同平面。
        建议跳高 = dy，钳制到屏幕上边缘（bounce 内部同样钳制）。
        """
        pet_ground = self._pet_y + config.PET_HEIGHT
        dy = pet_ground - food_ground_y
        if dy <= 0:
            return dy, 0
        max_bounce = max(0, self._pet_y - self._screen_geo[1])
        return dy, min(dy, max_bounce)

    @staticmethod
    def _height_hint(dy: int) -> str:
        """高度差提示，阈值随宠物窗口高度自适应。"""
        tolerance = max(1, config.PET_HEIGHT)
        if dy > tolerance:
            return f"在你上方约{dy}px，需要 bounce 跳起来吃"
        if dy < -tolerance:
            return f"在你下方约{-dy}px，可以走到窗口边缘掉下去再吃"
        return "和你同一平面，直接走过去就行"

    @staticmethod
    def _arrived(pet_x: int, pet_y: int, food_x: int, food_y: int) -> bool:
        """宠物窗口与食物窗口是否矩形相交。"""
        return (
            pet_x < food_x + FOOD_SIZE
            and pet_x + config.PET_WIDTH > food_x
            and pet_y < food_y + FOOD_SIZE
            and pet_y + config.PET_HEIGHT > food_y
        )

    def spawn(self, food_type: Optional[str] = None) -> dict:
        """food__spawn：生成一份食物，返回坐标与相对宠物的偏移。"""
        if not config.FOOD_ENABLED:
            return {
                "summary": "觅食已关闭（FOOD_ENABLED=False）",
                "success": False,
                "error": "觅食已关闭（FOOD_ENABLED=False）",
            }
        with self._lock:
            if not self._snapshot_ready:
                return {
                    "summary": "宠物位置尚未就绪，请稍后重试",
                    "success": False,
                    "error": "宠物位置快照尚未就绪（首个心跳未完成），请稍后重试",
                }
            if self._food is not None:
                f = self._food
                dx, direction = self._dx_to(f["center_x"])
                dy, bounce_height = self._dy_info(f["ground_y"])
                remaining = max(0, int(f["ttl"] - (time.monotonic() - f["spawned_at"])))
                return {
                    "summary": f"桌面上已经有{f['name']}了，直接去吃吧",
                    "success": True,
                    "food_id": f["id"],
                    "food_type": f["name"],
                    "position": {"x": f["x"], "y": f["y"]},
                    "dx": dx, "direction": direction,
                    "dy": dy, "bounce_height": bounce_height,
                    "ttl_seconds": remaining,
                }

            emoji = pick_emoji(food_type)
            name = name_of(emoji)

            # 生成范围：屏幕可用区内，左右留 20px
            left, top, right, bottom = self._screen_geo
            lo = left + 20
            hi = right - 20 - FOOD_SIZE
            pet_cx = self._pet_x + config.PET_WIDTH // 2
            if hi <= lo:
                x = lo
            else:
                excluded_lo = pet_cx - _SPAWN_MARGIN
                excluded_hi = pet_cx + _SPAWN_MARGIN
                candidates = [
                    px for px in range(lo, hi + 1, 20)
                    if not (excluded_lo <= px + FOOD_SIZE // 2 <= excluded_hi)
                ]
                x = random.choice(candidates) if candidates else random.randint(lo, hi)

            y_lo = top + 10
            y_hi = max(y_lo, bottom - FOOD_SIZE - 10)
            y = random.randint(y_lo, y_hi)

            food_id = f"food-{int(time.time() * 1000)}"
            self._food = {
                "id": food_id,
                "emoji": emoji,
                "name": name,
                "x": x,
                "y": y,
                "center_x": x + FOOD_SIZE // 2,
                "ground_y": y + FOOD_SIZE,
                "spawned_at": time.monotonic(),
                "ttl": float(config.FOOD_TTL_SECONDS),
            }
            dx, direction = self._dx_to(self._food["center_x"])
            dy, bounce_height = self._dy_info(self._food["ground_y"])
            height_hint = self._height_hint(dy)
            logger.info(f"[Food] spawned {name}({food_id}) at ({x},{y}), dx={dx} {direction}, dy={dy}")

            # 主线程创建食物窗口
            self.spawn_ui_requested.emit(food_id, emoji, x, y)

            return {
                "summary": f"已生成{name}，在你{_side_text(direction)} {dx}px，{height_hint}",
                "success": True,
                "food_id": food_id,
                "food_type": name,
                "position": {"x": x, "y": y},
                "dx": dx,
                "direction": direction,
                "dy": dy,
                "bounce_height": bounce_height,
                "height_hint": height_hint,
                "ttl_seconds": config.FOOD_TTL_SECONDS,
            }

    def status(self) -> dict:
        """food__status：查询当前食物状态与实时偏移。"""
        with self._lock:
            pet_pos = {"x": self._pet_x, "y": self._pet_y}
            if self._food is None:
                return {
                    "summary": "桌面上没有食物，可以调用 food__spawn 生成一份",
                    "success": True,
                    "has_food": False,
                    "pet_position": pet_pos,
                }
            f = self._food
            dx, direction = self._dx_to(f["center_x"])
            dy, bounce_height = self._dy_info(f["ground_y"])
            height_hint = self._height_hint(dy)
            elapsed = time.monotonic() - f["spawned_at"]
            expired = elapsed > f["ttl"]
            remaining = max(0, int(f["ttl"] - elapsed))
            arrived = self._arrived(self._pet_x, self._pet_y, f["x"], f["y"])
            if expired:
                summary = (f"{f['name']}已经过期，即将自动消失。"
                           f"不用再过去吃；想吃就调用 food__spawn 重新生成，否则直接输出最终回复即可。")
            elif arrived:
                summary = (f"{f['name']}已经在你身边（到达进食范围）。"
                           f"无需再移动，直接输出最终回复即可，会自动开吃。")
            else:
                # 需要跳时：近距离一个 bounce 同时走完水平距离并起跳；
                # 远距离先 walk 完水平距离，再用横向 0 的 bounce 原地跳上食物
                if bounce_height > 0 and dx < _NEAR_JUMP_PX:
                    action_hint = f"Action: bounce {direction} {dx} {bounce_height}"
                elif bounce_height > 0:
                    action_hint = (f"Action: walk {direction} {dx}，"
                                   f"然后 Action: bounce {direction} 0 {bounce_height}")
                else:
                    action_hint = f"Action: walk {direction} {dx}"
                summary = (f"{f['name']}在你{_side_text(direction)} {dx}px，{height_hint}，{remaining}秒后过期。"
                           f"下一步输出最终回复即可，其中带上移动 Action 靠近它：{action_hint}；"
                           f"到达后自动开吃，不必再调用本工具。")
            return {
                "summary": summary,
                "success": True,
                "has_food": True,
                "food_id": f["id"],
                "food_type": f["name"],
                "position": {"x": f["x"], "y": f["y"]},
                "pet_position": pet_pos,
                "dx": dx,
                "direction": direction,
                "dy": dy,
                "bounce_height": bounce_height,
                "height_hint": height_hint,
                "arrived": arrived,
                "expired": expired,
                "ttl_seconds": remaining,
            }

    def set_window_factory(self, factory):
        """装配期注入窗口工厂（emoji, x, y → 窗口对象）；窗口须在主线程构造。"""
        self._window_factory = factory

    def _spawn_ui(self, food_id: str, emoji: str, x: int, y: int):
        """主线程：调用注入的窗口工厂创建食物窗口。"""
        if self._window_factory is None:
            logger.warning(f"[Food] window factory not injected; skip food window ({food_id})")
            return
        try:
            self._food_window = self._window_factory(emoji, x, y)
        except Exception as e:
            logger.warning(f"[Food] FoodWindow create failed ({food_id}): {e}")

    def tick(self):
        """主线程每秒：更新快照 → 过期检查 → 到达判定。"""
        self._update_position_snapshot()
        with self._lock:
            if not config.FOOD_ENABLED:
                if self._food is not None:
                    self._clear_food("觅食已关闭，食物消失了")
                return
            food = self._food
            if food is None:
                return

            # 过期检查
            elapsed = time.monotonic() - food["spawned_at"]
            if elapsed > food["ttl"]:
                self._clear_food(f"你生成的食物（{food['name']}）放太久变质消失了")
                return

            # 刷新食物实时位置（浮动动画会偏移快照坐标）
            fw = self._food_window
            if fw is not None:
                try:
                    food["x"] = fw.x()
                    food["y"] = fw.y()
                    food["center_x"] = food["x"] + FOOD_SIZE // 2
                    food["ground_y"] = food["y"] + FOOD_SIZE
                except RuntimeError:
                    pass

            if self._arrived(self._pet_x, self._pet_y, food["x"], food["y"]):
                name = food["name"]
                self._clear_food(f"你找到了{name}并吃掉了（自己觅食）")
                logger.info(f"[Food] arrived, food eaten: {food['id']}")
                self._trigger_self_fed(name)

    def _clear_food(self, event_text: Optional[str] = None):
        """清空食物状态；event_text 非空时作为事件上报（吃到/变质）"""
        if event_text and self._agent is not None:
            TOOL_CTX.note_event("food", event_text)
        win = self._food_window
        self._food_window = None
        self._food = None
        if win is not None:
            try:
                win.disappear()
            except RuntimeError:
                pass

    def _trigger_self_fed(self, name: str):
        """触发进食交互"""
        agent = self._agent
        if agent is None:
            return
        try:
            from pet.brain.prompts import interact_self_fed_prompt
            agent.trigger(
                "interact",
                hint=interact_self_fed_prompt(name),
                delay_ms=150,
                cooldown_ms=0,  # 不做冷却：同一种食物重复吃到各触发一次
                record_context=False,  # 事件由 describe() 的 system 路径落库，避免与 user 消息重复
                is_play_loading=False,
                thinking=False,
                enable_tools=False,
            )
        except Exception as e:
            logger.warning(f"[Food] trigger interact failed: {e}")

    def describe(self) -> str:
        """供 context_builder 注入 [觅食] 实时行：进行中的食物位置/距离/剩余时间"""
        with self._lock:
            food = self._food
            if food is None:
                return ""
            dx, direction = self._dx_to(food["center_x"])
            dy, _ = self._dy_info(food["ground_y"])
            remaining = max(0, int(food["ttl"] - (time.monotonic() - food["spawned_at"])))
            return (
                f"[觅食] 桌面上有一份{food['name']}：位置 x={food['x']} y={food['y']}，"
                f"在你{direction}侧 {dx}px，{self._height_hint(dy)}，{remaining}秒后过期"
            )


FOOD = FoodManager()
