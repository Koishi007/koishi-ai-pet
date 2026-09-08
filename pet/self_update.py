"""启动时应用遗留的 update 脚本更新"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def apply_pending_update_scripts(root: Path | None = None) -> None:
    """用上次更新遗留的 update.*.new 替换对应脚本。"""
    if root is None:
        root = Path(__file__).resolve().parent.parent
    for name in ("update.bat.new", "update.sh.new"):
        src = root / name
        if not src.is_file():
            continue
        dst = root / name.removesuffix(".new")
        try:
            os.replace(src, dst)
        except OSError as e:
            logger.warning(f"[SelfUpdate] 替换 {name} 失败（下次启动重试）: {e}")
        else:
            logger.info(f"[SelfUpdate] 已应用待更新脚本: {name} -> {dst.name}")
