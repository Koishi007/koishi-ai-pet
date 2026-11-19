"""画布持久化：pixel_canvases 表的读写。"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime

import numpy as np

from pet.db import get_conn
from pet.tools.context import TOOL_CTX
from pet.tools.pixel_art.canvas import (Canvas, decode_rle, encode_rle,
                                        hex_of, parse_hex)

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pixel_canvases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    size       INTEGER NOT NULL,
    preset     TEXT    NOT NULL,
    palette    TEXT    NOT NULL,
    pixels     TEXT    NOT NULL,
    revision   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
)
"""


class PixelArtStorage:
    """画布数据的 SQLite 持久化层。"""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or TOOL_CTX.db_path()
        self._conn = get_conn(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_table()

    def _create_table(self) -> None:
        with self._lock:
            self._conn.execute(_TABLE_SQL)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pixel_canvases_updated "
                "ON pixel_canvases(updated_at DESC)")
            self._conn.commit()

    def create(self, name: str, size: int, preset: str, palette,
               index: np.ndarray) -> Canvas:
        now = datetime.now().isoformat()
        palette_json = json.dumps([hex_of(rgb) for rgb in palette])
        pixels = encode_rle(index)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO pixel_canvases "
                "(name, size, preset, palette, pixels, revision, created_at, updated_at) "
                "VALUES (?,?,?,?,?,0,?,?)",
                (name, size, preset, palette_json, pixels, now, now))
            self._conn.commit()
            canvas_id = str(cur.lastrowid)
        return Canvas(canvas_id, name, size, preset, list(palette), index, 0)

    def save_pixels(self, canvas: Canvas, index: np.ndarray, revision: int) -> None:
        """像素与 revision 同一条 UPDATE，成功后调用方再换内存。"""
        now = datetime.now().isoformat()
        palette_json = json.dumps([hex_of(rgb) for rgb in canvas.palette])
        with self._lock:
            self._conn.execute(
                "UPDATE pixel_canvases SET pixels=?, palette=?, preset=?, revision=?, "
                "updated_at=? WHERE id=?",
                (encode_rle(index), palette_json, canvas.preset, revision, now, canvas.id))
            self._conn.commit()

    def rename(self, canvas_id: str, name: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pixel_canvases SET name=? WHERE id=?", (name, canvas_id))
            self._conn.commit()

    def get(self, canvas_id: str) -> Canvas | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pixel_canvases WHERE id=?", (canvas_id,)).fetchone()
        return self._to_canvas(row) if row else None

    def latest(self) -> Canvas | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pixel_canvases ORDER BY updated_at DESC LIMIT 1").fetchone()
        return self._to_canvas(row) if row else None

    def list(self, page: int, page_size: int) -> tuple[list[dict], int]:
        offset = (page - 1) * page_size
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) AS n FROM pixel_canvases").fetchone()["n"]
            rows = self._conn.execute(
                "SELECT id, name, size, preset, revision, updated_at FROM pixel_canvases "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (page_size, offset)).fetchall()
        return [dict(row) for row in rows], int(total)

    def delete(self, canvas_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM pixel_canvases WHERE id=?", (canvas_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _to_canvas(row) -> Canvas:
        palette = [parse_hex(item) for item in json.loads(row["palette"])]
        index = decode_rle(row["pixels"], int(row["size"]))
        return Canvas(str(row["id"]), row["name"], int(row["size"]), row["preset"],
                      palette, index, int(row["revision"]))
