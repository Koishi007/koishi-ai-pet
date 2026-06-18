"""Mood — 心理数值引擎：好感度、愉悦度、理智值。

三项参数仅通过 modify 方法增减，无自动衰减。
数值持久化到 pet.db（与 MemoryStore / Vitals 共用同一数据库，不同表）。
"""

import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

# ── 数据库路径 ──────────────────────────────────────────
_DB_PATH = str(Path(__file__).resolve().parent.parent.parent / "pet.db")


# ── 阈值定义 ──────────────────────────────────────────

@dataclass(frozen=True)
class MoodThresholds:
    """心理状态的触发阈值。"""
    affection_low: float = 30.0        # 好感度 < 此值 → 冷淡
    affection_estranged: float = 10.0  # 好感度 < 此值 → 生疏
    joy_low: float = 30.0             # 愉悦度 < 此值 → 不开心
    joy_depressed: float = 10.0       # 愉悦度 < 此值 → 郁闷
    sanity_low: float = 30.0          # 理智值 < 此值 → 神志恍惚
    sanity_mad: float = 10.0          # 理智值 < 此值 → 癫狂


# ── Mood ──────────────────────────────────────────────

class Mood(QObject):
    """心理数值系统，三项参数仅通过 modify 方法增减，无自动衰减。

    三个参数：
        affection（好感度）— 对主人的亲近程度
        joy（愉悦度）      — 当前的开心程度
        sanity（理智值）   — 清醒/理智程度，越低越疯癫

    信号：
        affection_low       — 好感度 < 30，变得冷淡
        affection_estranged — 好感度 < 10，变得生疏
        joy_low             — 愉悦度 < 30，不开心
        joy_depressed       — 愉悦度 < 10，郁闷
        sanity_low          — 理智值 < 30，神志恍惚
        sanity_mad          — 理智值 < 10，癫狂
        mood_recovered      — 三项都 > 50，恢复正常
    """

    affection_low       = Signal()
    affection_estranged = Signal()
    joy_low             = Signal()
    joy_depressed       = Signal()
    sanity_low          = Signal()
    sanity_mad          = Signal()
    mood_recovered      = Signal()

    def __init__(self, db_path: Optional[str] = None,
                 thresholds: Optional[MoodThresholds] = None, parent=None):
        super().__init__(parent)
        self._thresholds = thresholds or MoodThresholds()

        # SQLite 持久化
        self._db_path = db_path or _DB_PATH
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._create_table()
        self._load()

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

    # ── SQLite ──────────────────────────────────────────

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

    def _save(self):
        with self._lock:
            self._conn.execute(
                "UPDATE mood SET affection=?, joy=?, sanity=? WHERE id = 1",
                (self._affection, self._joy, self._sanity)
            )
            self._conn.commit()

    # ── 属性 ──────────────────────────────────────────

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

    # ── 状态摘要（供 prompt 注入） ────────────────────

    def summary(self) -> str:
        """返回当前心理状态的人可读摘要。"""
        parts = []

        # 好感度
        if self._affection >= 70:
            parts.append("很亲近主人")
        elif self._affection >= 50:
            parts.append("对主人还算友好")
        elif self._affection >= self._thresholds.affection_low:
            parts.append("变得冷淡了")
        elif self._affection >= self._thresholds.affection_estranged:
            parts.append("与主人生疏了")
        else:
            parts.append("完全不信任主人")

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

    # ── 参数增减（对外暴露） ──────────────────────────

    _DELTA_MAX = 5.0

    def modify_affection(self, delta: float):
        """增减好感度，delta 会被 clamp 到 ±5，参数范围 0~100。持久化由 tick 统一处理。"""
        delta = max(-self._DELTA_MAX, min(self._DELTA_MAX, delta))
        old = self._affection
        self._affection = max(0.0, min(100.0, self._affection + delta))
        logger.info(f"[Mood] 好感度 {delta:+.1f} ({old:.1f}→{self._affection:.1f})")

    def modify_joy(self, delta: float):
        """增减愉悦度，delta 会被 clamp 到 ±5，参数范围 0~100。持久化由 tick 统一处理。"""
        delta = max(-self._DELTA_MAX, min(self._DELTA_MAX, delta))
        old = self._joy
        self._joy = max(0.0, min(100.0, self._joy + delta))
        logger.info(f"[Mood] 愉悦度 {delta:+.1f} ({old:.1f}→{self._joy:.1f})")

    def modify_sanity(self, delta: float):
        """增减理智值，delta 会被 clamp 到 ±5，参数范围 0~100。持久化由 tick 统一处理。"""
        delta = max(-self._DELTA_MAX, min(self._DELTA_MAX, delta))
        old = self._sanity
        self._sanity = max(0.0, min(100.0, self._sanity + delta))
        logger.info(f"[Mood] 理智值 {delta:+.1f} ({old:.1f}→{self._sanity:.1f})")

    # ── tick：由 Scheduler.slow_tick 驱动 ────────────────

    def tick(self):
        """每次 slow_tick 调用，持久化当前数值并检查阈值信号。"""
        self._save()
        self._check_thresholds()

    # ── 信号触发 ──────────────────────────────────────

    def _init_threshold_flags(self):
        """启动时根据当前数值设置防抖标记。"""
        t = self._thresholds
        self._was_aff_low       = self._affection < t.affection_low
        self._was_aff_estranged = self._affection < t.affection_estranged
        self._was_joy_low       = self._joy < t.joy_low
        self._was_joy_depressed = self._joy < t.joy_depressed
        self._was_sanity_low    = self._sanity < t.sanity_low
        self._was_sanity_mad    = self._sanity < t.sanity_mad

    def _check_thresholds(self):
        t = self._thresholds

        # 好感度
        if self._affection < t.affection_estranged:
            if not self._was_aff_estranged:
                self._was_aff_estranged = True
                self.affection_estranged.emit()
                logger.warning("[Mood] 与主人生疏！好感度 < estranged 阈值")
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

    # ── 生命周期 ──────────────────────────────────────

    def close(self):
        self._save()
        with self._lock:
            self._conn.close()
