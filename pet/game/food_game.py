"""觅食游戏 — 桌宠自主觅食的状态机与判定"""

import logging
import random
import threading
import time
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from pet.config import config
from pet.tools.context import TOOL_CTX
from pet.ui.food_window import FOOD_SIZE, FoodWindow

logger = logging.getLogger(__name__)

# 生成时与宠物的最小水平间隔（px）
_SPAWN_MARGIN = 200
# 判定轮询间隔（ms）
_TICK_MS = 200


class FoodGameManager(QObject):
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
        self._screen_geo = (0, 0, 1920, 1080)
        self._snapshot_ready = False

        self._food: Optional[dict] = None
        self._food_window = None
        self._last_event: Optional[str] = None

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
        logger.info("[FoodGame] bound to agent, tick started")

    def _update_position_snapshot(self):
        """主线程调用：读取宠物窗口位置与屏幕几何，更新快照。"""
        win = getattr(self._agent, "_pet_window", None)
        if win is None:
            return
        try:
            screen = QApplication.primaryScreen()
            with self._lock:
                self._pet_x = win.x()
                self._pet_y = win.y()
                if screen is not None:
                    geo = screen.availableGeometry()
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

            emoji = FoodWindow.pick_emoji(food_type)
            name = FoodWindow.name_of(emoji)

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
            logger.info(f"[FoodGame] spawned {name}({food_id}) at ({x},{y}), dx={dx} {direction}, dy={dy}")

            # 主线程创建食物窗口
            self.spawn_ui_requested.emit(food_id, emoji, x, y)

            return {
                "summary": f"已生成{name}，在你{direction}侧 {dx}px，{height_hint}",
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
            return {
                "summary": f"{f['name']}在你{direction}侧 {dx}px，{height_hint}，"
                           f"{'已经到达可以吃了' if arrived else '还没到，继续走'}，{remaining}秒后过期",
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

    def _spawn_ui(self, food_id: str, emoji: str, x: int, y: int):
        """主线程：创建食物窗口。"""
        try:
            self._food_window = FoodWindow(emoji, x, y)
        except Exception as e:
            logger.warning(f"[FoodGame] FoodWindow create failed ({food_id}): {e}")

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
                logger.info(f"[FoodGame] arrived, food eaten: {food['id']}")
                self._trigger_self_fed(name)

    def _clear_food(self, event_text: Optional[str] = None):
        """清空食物状态"""
        if event_text:
            self._last_event = event_text
        win = self._food_window
        self._food_window = None
        self._food = None
        if win is not None:
            try:
                win.disappear()
            except RuntimeError:
                pass

    def _note_event(self, text: str):
        """把事件写入上下文"""
        if text and self._agent is not None:
            try:
                TOOL_CTX.add_context(f"[觅食] {text}")
            except Exception:
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
                record_context=True,
                context_hint=f"你找到了{name}并吃掉了（自己觅食）",
                wait_anim="shake_arms",
            )
        except Exception as e:
            logger.warning(f"[FoodGame] trigger interact failed: {e}")

    def describe(self) -> str:
        """供 context_builder 注入 [觅食] 行；事件文本输出一次后清空并落库"""
        lines = []
        event_text = None
        with self._lock:
            if self._last_event:
                lines.append(f"[觅食] {self._last_event}")
                event_text = self._last_event
                self._last_event = None
            food = self._food
            if food is not None:
                dx, direction = self._dx_to(food["center_x"])
                dy, _ = self._dy_info(food["ground_y"])
                remaining = max(0, int(food["ttl"] - (time.monotonic() - food["spawned_at"])))
                lines.append(
                    f"[觅食] 桌面上有一份{food['name']}：位置 x={food['x']} y={food['y']}，"
                    f"在你{direction}侧 {dx}px，{self._height_hint(dy)}，{remaining}秒后过期"
                )
        # 锁外落库，避免持锁期间执行上下文写入
        if event_text:
            self._note_event(event_text)
        return "\n".join(lines)


FOOD_GAME = FoodGameManager()
