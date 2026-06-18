"""Vitals — 生理数值引擎：饱食度、精力。

衰减由 Scheduler 的 slow_tick 驱动（每 5 分钟一次），
每次衰减 0~3，查表模拟正态分布（均值 ≈ 1.5）。
数值持久化到 pet.db（与 MemoryStore 共用同一数据库，不同表）。
"""

import logging
import random
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

# ── 数据库路径（与 MemoryStore 共用 pet.db） ──────────
_DB_PATH = str(Path(__file__).resolve().parent.parent.parent / "pet.db")


# ── 阈值定义 ──────────────────────────────────────────────

@dataclass(frozen=True)
class Thresholds:
    """各状态的触发阈值，方便集中调整。"""
    satiety_hungry: float = 30.0    # 饱食度 < 此值 → 饿了
    satiety_starving: float = 10.0  # 饱食度 < 此值 → 饿坏了
    energy_tired: float = 30.0     # 精力 < 此值 → 累了
    energy_exhausted: float = 10.0  # 精力 < 此值 → 筋疲力尽


# ── 正态衰减查表 ──────────────────────────────────────────
# 模拟 μ=1.5 σ≈0.8 的截断正态分布，值域 [0, 3]
# 权重合计 100，便于 random.choices 直接用

_DECAY_TABLE = [
    (0.0,  5),   # P ≈ 5%
    (0.5, 12),   # P ≈ 12%
    (1.0, 25),   # P ≈ 25%  ← 峰值
    (1.5, 28),   # P ≈ 28%  ← 峰值
    (2.0, 18),   # P ≈ 18%
    (2.5,  8),   # P ≈ 8%
    (3.0,  2),   # P ≈ 2%
]

_DECAY_VALUES = [v for v, _ in _DECAY_TABLE]
_DECAY_WEIGHTS = [w for _, w in _DECAY_TABLE]


def _sample_decay() -> float:
    """从查表中随机抽取一次衰减量，近似正态分布。"""
    return random.choices(_DECAY_VALUES, weights=_DECAY_WEIGHTS, k=1)[0]


# ── Vitals ────────────────────────────────────────────────

class Vitals(QObject):
    """生理数值系统，由 Scheduler.slow_tick 驱动衰减，外部通过 modify 方法增减参数。

    两个参数：
        satiety（饱食度）— 自然衰减，投喂恢复
        energy（精力）   — 自然衰减，休息恢复

    信号：
        hungry      — 饿了（饱食度 < 30）
        starving    — 饿坏了（饱食度 < 10）
        tired       — 累了（精力 < 30）
        exhausted   — 筋疲力尽（精力 < 10）
        recovered   — 恢复正常（饱食度 & 精力都 > 50）
    """

    hungry     = Signal()
    starving   = Signal()
    tired      = Signal()
    exhausted  = Signal()
    recovered  = Signal()

    def __init__(self, db_path: Optional[str] = None,
                 thresholds: Optional[Thresholds] = None, parent=None):
        super().__init__(parent)
        self._thresholds = thresholds or Thresholds()

        # SQLite 持久化（与 MemoryStore 共用 pet.db，不同表）
        self._db_path = db_path or _DB_PATH
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._create_table()
        self._load()

        # 信号防抖
        self._was_hungry     = False
        self._was_starving   = False
        self._was_tired      = False
        self._was_exhausted  = False
        self._init_threshold_flags()

        logger.info(f"[Vitals] 初始化完成 饱食度={self._satiety:.1f} 精力={self._energy:.1f}")

    # ── SQLite ────────────────────────────────────────────

    def _create_table(self):
        """创建 vitals 表（单行 key-value 结构，只存一行当前状态）。"""
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS vitals (
                    id      INTEGER PRIMARY KEY CHECK(id = 1),
                    satiety REAL NOT NULL DEFAULT 100.0,
                    energy  REAL NOT NULL DEFAULT 100.0
                )
            """)
            row = self._conn.execute("SELECT COUNT(*) FROM vitals").fetchone()
            if row[0] == 0:
                self._conn.execute("INSERT INTO vitals (id, satiety, energy) VALUES (1, 100.0, 100.0)")
            self._conn.commit()

    def _load(self):
        """从数据库加载当前数值。"""
        with self._lock:
            row = self._conn.execute("SELECT satiety, energy FROM vitals WHERE id = 1").fetchone()
            self._satiety: float = row[0]
            self._energy: float  = row[1]

    def _save(self):
        """将当前数值写入数据库。"""
        with self._lock:
            self._conn.execute(
                "UPDATE vitals SET satiety=?, energy=? WHERE id = 1",
                (self._satiety, self._energy)
            )
            self._conn.commit()

    # ── 属性 ──────────────────────────────────────────────

    @property
    def satiety(self) -> float:
        return self._satiety

    @property
    def energy(self) -> float:
        return self._energy

    @property
    def thresholds(self) -> Thresholds:
        return self._thresholds

    def is_hungry(self) -> bool:
        return self._satiety < self._thresholds.satiety_hungry

    def is_tired(self) -> bool:
        return self._energy < self._thresholds.energy_tired

    # ── 状态摘要（供 prompt 注入） ────────────────────────

    def summary(self) -> str:
        """返回当前生理状态的人可读摘要，供 LLM prompt 使用。"""
        parts = []
        if self._satiety >= 70:
            parts.append("饱食度良好")
        elif self._satiety >= self._thresholds.satiety_hungry:
            parts.append("有点饿了")
        elif self._satiety >= self._thresholds.satiety_starving:
            parts.append("很饿了")
        else:
            parts.append("饿坏了")

        if self._energy >= 70:
            parts.append("精力充沛")
        elif self._energy >= self._thresholds.energy_tired:
            parts.append("有点累了")
        elif self._energy >= self._thresholds.energy_exhausted:
            parts.append("很累了")
        else:
            parts.append("筋疲力尽")

        return "、".join(parts)

    def numeric_summary(self) -> dict:
        """返回数值摘要，供规则系统使用。"""
        return {
            "satiety": round(self._satiety, 1),
            "energy":  round(self._energy, 1),
            "hungry":  self.is_hungry(),
            "tired":   self.is_tired(),
        }

    # ── 参数增减（对外暴露） ──────────────────────────────

    def modify_satiety(self, delta: float):
        """增减饱食度，delta 正值增加、负值减少，范围 0~100。持久化由 tick 统一处理。"""
        old = self._satiety
        self._satiety = max(0.0, min(100.0, self._satiety + delta))
        logger.info(f"[Vitals] 饱食度 {delta:+.1f} ({old:.1f}→{self._satiety:.1f})")

    def modify_energy(self, delta: float):
        """增减精力，delta 正值增加、负值减少，范围 0~100。持久化由 tick 统一处理。"""
        old = self._energy
        self._energy = max(0.0, min(100.0, self._energy + delta))
        logger.info(f"[Vitals] 精力 {delta:+.1f} ({old:.1f}→{self._energy:.1f})")

    # ── tick：由 Scheduler.slow_tick 驱动 ──────────────────

    def tick(self):
        """每次 slow_tick 调用一次，饱食度和精力各自独立衰减 0~3，然后持久化。"""
        satiety_decay = _sample_decay()
        energy_decay  = _sample_decay()

        old_s = self._satiety
        old_e = self._energy
        self._satiety = max(0.0, self._satiety - satiety_decay)
        self._energy  = max(0.0, self._energy  - energy_decay)

        logger.debug(f"[Vitals] tick 衰减 饱食度 -{satiety_decay:.1f}({old_s:.1f}→{self._satiety:.1f}) "
                     f"精力 -{energy_decay:.1f}({old_e:.1f}→{self._energy:.1f})")

        self._save()
        self._check_thresholds()

    # ── 信号触发 ──────────────────────────────────────────

    def _init_threshold_flags(self):
        """启动时根据当前数值设置防抖标记，避免重复触发。"""
        t = self._thresholds
        self._was_hungry   = self._satiety < t.satiety_hungry
        self._was_starving = self._satiety < t.satiety_starving
        self._was_tired    = self._energy < t.energy_tired
        self._was_exhausted = self._energy < t.energy_exhausted

    def _check_thresholds(self):
        t = self._thresholds

        # 饱食度
        if self._satiety < t.satiety_starving:
            if not self._was_starving:
                self._was_starving = True
                self.starving.emit()
                logger.warning("[Vitals] 饿坏了！饱食度 < starving 阈值")
        elif self._satiety < t.satiety_hungry:
            if not self._was_hungry:
                self._was_hungry = True
                self.hungry.emit()
                logger.info("[Vitals] 有点饿了")
            self._was_starving = False
        else:
            self._was_hungry = False
            self._was_starving = False

        # 精力
        if self._energy < t.energy_exhausted:
            if not self._was_exhausted:
                self._was_exhausted = True
                self.exhausted.emit()
                logger.warning("[Vitals] 筋疲力尽！精力 < exhausted 阈值")
        elif self._energy < t.energy_tired:
            if not self._was_tired:
                self._was_tired = True
                self.tired.emit()
                logger.info("[Vitals] 有点累了")
            self._was_exhausted = False
        else:
            self._was_tired = False
            self._was_exhausted = False

        # 恢复信号
        if (self._satiety > 50 and self._energy > 50
                and (self._was_hungry or self._was_starving
                     or self._was_tired or self._was_exhausted)):
            self._was_hungry = False
            self._was_starving = False
            self._was_tired = False
            self._was_exhausted = False
            self.recovered.emit()
            logger.info("[Vitals] 恢复正常！")

    # ── 生命周期 ──────────────────────────────────────────

    def close(self):
        self._save()
        with self._lock:
            self._conn.close()
