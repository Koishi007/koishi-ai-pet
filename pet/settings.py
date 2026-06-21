"""用户设置持久化 — JSON 文件读写（覆盖 .env 默认值）。"""

import json
import logging
import os
from PySide6.QtCore import QStandardPaths

logger = logging.getLogger(__name__)


def settings_path() -> str:
    """返回跨平台 settings.json 路径，确保目录存在。

    Windows: %APPDATA%/DeskPet/settings.json
    macOS:   ~/Library/Application Support/DeskPet/settings.json
    """
    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "settings.json")


def load_user_settings() -> dict:
    """从 settings.json 读取用户覆盖值。文件不存在或解析失败返回空 dict。"""
    path = settings_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        logger.warning(f"[Settings] {path}: expected dict, got {type(data).__name__}")
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[Settings] Failed to load {path}: {e}")
        return {}


def save_user_setting(key: str, value) -> None:
    """写入单个设置到 settings.json。value 必须可 JSON 序列化。"""
    current = load_user_settings()
    current[key] = value
    _write_settings(current)


def delete_user_settings(keys: list[str]) -> None:
    """从 settings.json 中删除指定 key。key 不存在则忽略。"""
    current = load_user_settings()
    changed = False
    for k in keys:
        if k in current:
            del current[k]
            changed = True
    if changed:
        _write_settings(current)


def _write_settings(data: dict) -> None:
    """原子写入 settings.json（写临时文件 → 重命名）。"""
    path = settings_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        logger.error(f"[Settings] Failed to write {path}: {e}")
        if os.path.exists(tmp):
            os.remove(tmp)
