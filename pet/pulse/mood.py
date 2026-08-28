"""情绪 — 心理数值引擎：好感度、愉悦度、理智值"""

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, Signal

from pet.config import config
from pet.db import get_conn, get_db_path

logger = logging.getLogger(__name__)

_DB_PATH = get_db_path()


@dataclass(frozen=True)
class MoodThresholds:
    affection_low: float = 30.0
    affection_estranged: float = 10.0
    joy_low: float = 30.0
    joy_depressed: float = 10.0
    sanity_low: float = 30.0
    sanity_mad: float = 10.0


@dataclass(frozen=True)
class MoodDecayConfig:
    """心理数值自然衰减配置（slow tick 周期性调用）。

    joy/affection 采用"回归基线"机制：高于基线向下移、低于基线向上移，
    步长为对应 per_tick 速率。sanity 不参与自然衰减（完全由事件/行为/LLM 驱动）。
    """
    joy_baseline: float = 50.0
    joy_per_tick: float = 2.5
    affection_baseline: float = 60.0
    affection_per_tick: float = 0.8
    grace_seconds: float = 60.0         # 互动后免衰减秒数
    enabled: bool = True



class Mood(QObject):
    """心理数值系统"""

    affection_low       = Signal()
    affection_estranged = Signal()
    affection_increased = Signal()
    joy_low             = Signal()
    joy_depressed       = Signal()
    sanity_low          = Signal()
    sanity_mad          = Signal()
    mood_recovered      = Signal()

    def __init__(self, db_path: Optional[str] = None,
                 thresholds: Optional[MoodThresholds] = None,
                 decay: Optional[MoodDecayConfig] = None, parent=None):
        super().__init__(parent)
        self._thresholds = thresholds or MoodThresholds()
        self._decay = decay or MoodDecayConfig(
            enabled=config.MOOD_DECAY_ENABLED,
            joy_baseline=config.MOOD_JOY_BASELINE,
            joy_per_tick=config.MOOD_JOY_DECAY_PER_TICK,
            affection_baseline=config.MOOD_AFFECTION_BASELINE,
            affection_per_tick=config.MOOD_AFFECTION_DECAY_PER_TICK,
            grace_seconds=config.MOOD_GRACE_SECONDS,
        )
        self._last_activity_ts: float = 0.0  # 最近一次互动时间（防抖）

        # SQLite 持久化
        # 构造期用充足 timeout 保证建表/加载不被并发写打断；
        # 完成后收短 busy_timeout，save 在主线程 slow_tick 执行时最长只等 0.5s
        self._db_path = db_path or _DB_PATH
        self._conn = get_conn(self._db_path, timeout=5.0)
        self._lock = threading.Lock()
        self._create_table()
        self._load()
        self._conn.execute("PRAGMA busy_timeout=500")

        # 信号防抖
        self._was_aff_low       = False
        self._was_aff_estranged = False
        self._was_joy_low       = False
        self._was_joy_depressed = False
        self._was_sanity_low    = False
        self._was_sanity_mad    = False
        self._init_threshold_flags()

        logger.info(f"[Mood] 初始化完成 好感={self._affection:.1f} "
                    f"愉悦={self._joy:.1f} 理智={self._sanity:.1f}")


    def _create_table(self):
        """创建 mood 表（单行存储当前心理状态）。"""
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS mood (
                    id        INTEGER PRIMARY KEY CHECK(id = 1),
                    affection REAL NOT NULL DEFAULT 60.0,
                    joy       REAL NOT NULL DEFAULT 70.0,
                    sanity    REAL NOT NULL DEFAULT 80.0
                )
            """)
            row = self._conn.execute("SELECT COUNT(*) FROM mood").fetchone()
            if row[0] == 0:
                self._conn.execute(
                    "INSERT INTO mood (id, affection, joy, sanity) VALUES (1, 60.0, 70.0, 80.0)"
                )
            self._conn.commit()

    def _load(self):
        with self._lock:
            row = self._conn.execute(
                "SELECT affection, joy, sanity FROM mood WHERE id = 1"
            ).fetchone()
            self._affection: float = row[0]
            self._joy: float       = row[1]
            self._sanity: float    = row[2]

    def save(self):
        try:
            with self._lock:
                self._conn.execute(
                    "UPDATE mood SET affection=?, joy=?, sanity=? WHERE id = 1",
                    (self._affection, self._joy, self._sanity)
                )
                self._conn.commit()
        except sqlite3.OperationalError as e:
            # 锁竞争（或磁盘/表等 DB 错误）时快速跳过，
            # 数值保留在内存，下个 slow_tick 重试
            logger.warning(f"[Mood] save failed, will retry next tick: {e}")


    @property
    def affection(self) -> float:
        return self._affection

    @property
    def joy(self) -> float:
        return self._joy

    @property
    def sanity(self) -> float:
        return self._sanity

    @property
    def thresholds(self) -> MoodThresholds:
        return self._thresholds

    def is_affection_low(self) -> bool:
        return self._affection < self._thresholds.affection_low

    def is_joy_low(self) -> bool:
        return self._joy < self._thresholds.joy_low

    def is_sanity_low(self) -> bool:
        return self._sanity < self._thresholds.sanity_low


    def summary(self) -> str:
        """返回当前心理状态的人可读摘要。"""
        parts = []

        # 好感度
        if self._affection >= 70:
            parts.append("很亲近")
        elif self._affection >= 50:
            parts.append("还算友好")
        elif self._affection >= self._thresholds.affection_low:
            parts.append("变得冷淡了")
        elif self._affection >= self._thresholds.affection_estranged:
            parts.append("变得生疏了")
        else:
            parts.append("完全不信任")

        # 愉悦度
        if self._joy >= 70:
            parts.append("很开心")
        elif self._joy >= 50:
            parts.append("心情还行")
        elif self._joy >= self._thresholds.joy_low:
            parts.append("不太开心")
        elif self._joy >= self._thresholds.joy_depressed:
            parts.append("很郁闷")
        else:
            parts.append("郁郁寡欢")

        # 理智值
        if self._sanity >= 70:
            parts.append("神志清醒")
        elif self._sanity >= 50:
            parts.append("还算理智")
        elif self._sanity >= self._thresholds.sanity_low:
            parts.append("神志恍惚")
        elif self._sanity >= self._thresholds.sanity_mad:
            parts.append("几近癫狂")
        else:
            parts.append("已经疯了")

        return "、".join(parts)

    def numeric_summary(self) -> dict:
        """返回数值摘要，供规则系统使用。"""
        return {
            "affection": round(self._affection, 1),
            "joy":       round(self._joy, 1),
            "sanity":    round(self._sanity, 1),
            "aff_low":   self.is_affection_low(),
            "joy_low":   self.is_joy_low(),
            "sanity_low": self.is_sanity_low(),
        }


    _DELTA_MAX = 5.0

    def modify_affection(self, delta: float):
        delta = max(-self._DELTA_MAX, min(self._DELTA_MAX, delta))
        old = self._affection
        self._affection = max(0.0, min(100.0, self._affection + delta))
        self._touch_activity()
        logger.info(f"[Mood] 好感度 {delta:+.1f} ({old:.1f}→{self._affection:.1f})")
        if delta > 0:
            self.affection_increased.emit()

    def modify_joy(self, delta: float):
        delta = max(-self._DELTA_MAX, min(self._DELTA_MAX, delta))
        old = self._joy
        self._joy = max(0.0, min(100.0, self._joy + delta))
        self._touch_activity()
        logger.info(f"[Mood] 愉悦度 {delta:+.1f} ({old:.1f}→{self._joy:.1f})")

    def modify_sanity(self, delta: float):
        old = self._sanity
        self._sanity = max(0.0, min(100.0, self._sanity + delta))
        self._touch_activity()
        logger.info(f"[Mood] 理智值 {delta:+.1f} ({old:.1f}→{self._sanity:.1f})")

    def set_affection(self, value: float):
        self._affection = max(0.0, min(100.0, value))
        self._touch_activity()
        logger.info(f"[Mood] 好感度 直接设置 → {self._affection:.1f}")

    def set_joy(self, value: float):
        self._joy = max(0.0, min(100.0, value))
        self._touch_activity()
        logger.info(f"[Mood] 愉悦度 直接设置 → {self._joy:.1f}")

    def set_sanity(self, value: float):
        self._sanity = max(0.0, min(100.0, value))
        self._touch_activity()
        logger.info(f"[Mood] 理智值 直接设置 → {self._sanity:.1f}")

    def _touch_activity(self):
        """标记互动时间，重置衰减防抖窗口。"""
        self._last_activity_ts = time.monotonic()

    def apply_decay(self):
        """按衰减配置向基线回归（愉悦/好感），理智不随时间衰减"""
        if not self._decay.enabled:
            return
        now = time.monotonic()
        if now - self._last_activity_ts < self._decay.grace_seconds:
            return

        changed = False
        # 愉悦度：向基线回归
        joy_new = self._decay_to(self._joy, self._decay.joy_baseline,
                                 self._decay.joy_per_tick)
        if abs(joy_new - self._joy) > 1e-9:
            self._joy = joy_new
            changed = True
            logger.info(f"[Mood] 愉悦度自然回归 → {self._joy:.1f}")

        # 好感度：向基线回归
        aff_new = self._decay_to(self._affection, self._decay.affection_baseline,
                                 self._decay.affection_per_tick)
        if abs(aff_new - self._affection) > 1e-9:
            self._affection = aff_new
            changed = True
            logger.info(f"[Mood] 好感度自然回归 → {self._affection:.1f}")

        if changed:
            self.save()
            self.check_thresholds()

    @staticmethod
    def _decay_to(value: float, baseline: float, per_tick: float) -> float:
        """向基线移动一步：高于基线减 per_tick，低于基线加 per_tick。"""
        if value > baseline:
            return max(baseline, value - per_tick)
        if value < baseline:
            return min(baseline, value + per_tick)
        return value

    def _init_threshold_flags(self):
        """启动时根据当前数值设置防抖标记。"""
        t = self._thresholds
        self._was_aff_low       = self._affection < t.affection_low
        self._was_aff_estranged = self._affection < t.affection_estranged
        self._was_joy_low       = self._joy < t.joy_low
        self._was_joy_depressed = self._joy < t.joy_depressed
        self._was_sanity_low    = self._sanity < t.sanity_low
        self._was_sanity_mad    = self._sanity < t.sanity_mad

    def check_thresholds(self):
        t = self._thresholds

        # 好感度
        if self._affection < t.affection_estranged:
            if not self._was_aff_estranged:
                self._was_aff_estranged = True
                self.affection_estranged.emit()
                logger.warning("[Mood] 好感度变得生疏！< estranged 阈值")
        elif self._affection < t.affection_low:
            if not self._was_aff_low:
                self._was_aff_low = True
                self.affection_low.emit()
                logger.info("[Mood] 好感度下降，变得冷淡")
            self._was_aff_estranged = False
        else:
            self._was_aff_low = False
            self._was_aff_estranged = False

        # 愉悦度
        if self._joy < t.joy_depressed:
            if not self._was_joy_depressed:
                self._was_joy_depressed = True
                self.joy_depressed.emit()
                logger.warning("[Mood] 郁闷！愉悦度 < depressed 阈值")
        elif self._joy < t.joy_low:
            if not self._was_joy_low:
                self._was_joy_low = True
                self.joy_low.emit()
                logger.info("[Mood] 不太开心")
            self._was_joy_depressed = False
        else:
            self._was_joy_low = False
            self._was_joy_depressed = False

        # 理智值
        if self._sanity < t.sanity_mad:
            if not self._was_sanity_mad:
                self._was_sanity_mad = True
                self.sanity_mad.emit()
                logger.warning("[Mood] 癫狂！理智值 < mad 阈值")
        elif self._sanity < t.sanity_low:
            if not self._was_sanity_low:
                self._was_sanity_low = True
                self.sanity_low.emit()
                logger.info("[Mood] 神志恍惚")
            self._was_sanity_mad = False
        else:
            self._was_sanity_low = False
            self._was_sanity_mad = False

        # 恢复信号
        if (self._affection > 50 and self._joy > 50 and self._sanity > 50
                and (self._was_aff_low or self._was_aff_estranged
                     or self._was_joy_low or self._was_joy_depressed
                     or self._was_sanity_low or self._was_sanity_mad)):
            self._was_aff_low = False
            self._was_aff_estranged = False
            self._was_joy_low = False
            self._was_joy_depressed = False
            self._was_sanity_low = False
            self._was_sanity_mad = False
            self.mood_recovered.emit()
            logger.info("[Mood] 心理状态恢复正常！")


    def close(self):
        self.save()
        with self._lock:
            self._conn.close()
