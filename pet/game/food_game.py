"""觅食游戏 — 桌宠自主觅食的状态机与判定。

闭环：饱食度<60 提示词注入饥饿（两个选择）→ 模型调用 food__spawn 生成食物 →
模型用现有 walk 动作走向食物坐标 → 本模块在主线程 tick 判定到达 →
触发进食交互（interact_self_fed_prompt），模型输出反应与 Vitals/Mood 变化。

线程模型：
- 主线程：FoodWindow 生命周期、tick 判定、位置读取、交互触发；
- 脑线程：工具 handler（spawn/status），只读位置快照与食物状态（锁保护）。
跨线程调度：Qt 信号（自动 QueuedConnection 投递到主线程）。
"""

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

# 到达判定：水平距离阈值（px）
_ARRIVE_THRESHOLD = 60
# 到达判定：宠物底边与食物底边的高度差容差（px）
_HEIGHT_TOLERANCE = 30
# 生成时与宠物的最小水平间隔（px）
_SPAWN_MARGIN = 200


class FoodGameManager(QObject):
    """觅食游戏核心：生成 / 过期 / 到达判定 / 进食交互触发。"""

    # 跨线程请求：脑线程 spawn → 主线程创建 FoodWindow
    spawn_ui_requested = Signal(str, str, int, int)  # food_id, emoji, x, y
    # 跨线程启动：单例可能在脑线程创建，绑定动作投递到主线程执行
    bind_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        # 单例可能首次在脑线程创建（handler 懒 import），强制归属主线程
        app = QApplication.instance()
        if app is not None and self.thread() is not app.thread():
            self.moveToThread(app.thread())

        self._lock = threading.RLock()
        self._agent = None
        self._bound = False

        # 宠物位置快照（脑线程只读，tick 主线程更新）
        self._pet_x = 0
        self._pet_y = 0
        self._screen_geo = (0, 0, 1920, 1080)  # left, top, right, bottom

        # 当前食物状态（主线程修改，脑线程锁内读取）
        self._food: Optional[dict] = None
        self._food_window = None
        self._last_event: Optional[str] = None  # describe 输出一次后清空

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self.tick)

        self.spawn_ui_requested.connect(self._spawn_ui)
        self.bind_requested.connect(self._bind_agent)

        if getattr(TOOL_CTX, "_agent", None) is not None:
            # 已在 bind 之后被创建（可能在脑线程）：投递到主线程再绑定
            self.bind_requested.emit(TOOL_CTX._agent)
        else:
            TOOL_CTX.on_bind(lambda agent: self.bind_requested.emit(agent))

    # ---------- 绑定与生命周期 ----------

    def _bind_agent(self, agent):
        """主线程执行：绑定 agent 并启动 tick 定时器。"""
        if self._bound:
            return
        self._bound = True
        self._agent = agent
        self._tick_timer.start()
        logger.info("[FoodGame] bound to agent, tick started")

    # ---------- 位置快照 ----------

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

    # ---------- 工具接口（脑线程调用） ----------

    def spawn(self, food_type: Optional[str] = None) -> dict:
        """food__spawn：生成一份食物，返回坐标与相对宠物的偏移。"""
        if not config.FOOD_ENABLED:
            return {"summary": "觅食已关闭（FOOD_ENABLED=False）", "success": False}
        with self._lock:
            if self._food is not None:
                f = self._food
                dx, direction = self._dx_to(f["center_x"])
                remaining = max(0, int(f["ttl"] - (time.monotonic() - f["spawned_at"])))
                return {
                    "summary": f"桌面上已经有{f['name']}了，直接去吃吧",
                    "success": False,
                    "food_id": f["id"],
                    "food_type": f["name"],
                    "position": {"x": f["x"], "y": f["y"]},
                    "dx": dx, "direction": direction,
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
                # 避开宠物左右各 _SPAWN_MARGIN 的区域，保证需要走一段路
                excluded_lo = pet_cx - _SPAWN_MARGIN
                excluded_hi = pet_cx + _SPAWN_MARGIN
                candidates = [
                    px for px in range(lo, hi + 1, 20)
                    if not (excluded_lo <= px + FOOD_SIZE // 2 <= excluded_hi)
                ]
                x = random.choice(candidates) if candidates else random.randint(lo, hi)

            # 食物「放」在宠物当前地面线上
            ground_y = self._pet_y + config.PET_HEIGHT
            y = max(top + 10, min(ground_y - FOOD_SIZE + 4, bottom - FOOD_SIZE - 10))

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
            logger.info(f"[FoodGame] spawned {name}({food_id}) at ({x},{y}), dx={dx} {direction}")

            # 主线程创建食物窗口（跨线程信号，自动 QueuedConnection）
            self.spawn_ui_requested.emit(food_id, emoji, x, y)

            return {
                "summary": f"已生成{name}，在你{direction}侧 {dx}px 处，走过去吃掉它",
                "success": True,
                "food_id": food_id,
                "food_type": name,
                "position": {"x": x, "y": y},
                "dx": dx,
                "direction": direction,
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
            remaining = max(0, int(f["ttl"] - (time.monotonic() - f["spawned_at"])))
            pet_ground = self._pet_y + config.PET_HEIGHT
            arrived = (abs(pet_ground - f["ground_y"]) <= _HEIGHT_TOLERANCE
                       and abs(self._pet_x + config.PET_WIDTH // 2 - f["center_x"]) <= _ARRIVE_THRESHOLD)
            return {
                "summary": f"{f['name']}在你{direction}侧 {dx}px，{'已经到达可以吃了' if arrived else '还没到，继续走'}"
                           + f"，{remaining}秒后过期",
                "success": True,
                "has_food": True,
                "food_id": f["id"],
                "food_type": f["name"],
                "position": {"x": f["x"], "y": f["y"]},
                "pet_position": pet_pos,
                "dx": dx,
                "direction": direction,
                "arrived": arrived,
                "expired": False,
                "ttl_seconds": remaining,
            }

    # ---------- 主线程槽 ----------

    def _spawn_ui(self, food_id: str, emoji: str, x: int, y: int):
        """主线程：创建食物窗口。"""
        try:
            self._food_window = FoodWindow(emoji, x, y)
        except Exception as e:
            logger.warning(f"[FoodGame] FoodWindow create failed: {e}")

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

            # 到达判定：水平距离 + 底边高度差
            pet_cx = self._pet_x + config.PET_WIDTH // 2
            pet_ground = self._pet_y + config.PET_HEIGHT
            if (abs(pet_cx - food["center_x"]) <= _ARRIVE_THRESHOLD
                    and abs(pet_ground - food["ground_y"]) <= _HEIGHT_TOLERANCE):
                name = food["name"]
                self._clear_food(f"你在桌面上找到了{name}并吃掉了（自己觅食）")
                logger.info(f"[FoodGame] arrived, food eaten: {food['id']}")
                self._trigger_self_fed(name)

    def _clear_food(self, event_text: Optional[str] = None):
        """清空食物状态：销毁窗口、记录事件。调用方须已持有锁。"""
        if event_text:
            self._last_event = event_text
            self._note_event()
        win = self._food_window
        self._food_window = None
        self._food = None
        if win is not None:
            try:
                win.disappear()
            except RuntimeError:
                pass

    def _note_event(self):
        """把事件写入多轮上下文（供后续决策感知），并保留给 describe 输出一次。"""
        text = self._last_event
        if text and self._agent is not None:
            try:
                TOOL_CTX.add_context(f"[觅食] {text}")
            except Exception:
                pass

    def _trigger_self_fed(self, name: str):
        """触发进食交互：模型输出吃到食物的反应 + Vitals/Mood（与用户投喂同构）。"""
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
                context_hint=f"你在桌面上找到了{name}并吃掉了（自己觅食）",
            )
        except Exception as e:
            logger.warning(f"[FoodGame] trigger interact failed: {e}")

    # ---------- 上下文注入（脑线程调用） ----------

    def describe(self) -> str:
        """供 context_builder 注入 [觅食] 行；事件文本输出一次后清空。"""
        lines = []
        with self._lock:
            if self._last_event:
                lines.append(f"[觅食] {self._last_event}")
                self._last_event = None
            food = self._food
            if food is not None:
                dx, direction = self._dx_to(food["center_x"])
                remaining = max(0, int(food["ttl"] - (time.monotonic() - food["spawned_at"])))
                lines.append(
                    f"[觅食] 桌面上有一份{food['name']}：位置 x={food['x']} y={food['y']}，"
                    f"在你{direction}侧 {dx}px，{remaining}秒后过期"
                )
        return "\n".join(lines)


FOOD_GAME = FoodGameManager()
