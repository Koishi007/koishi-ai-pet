"""启动时应用遗留的 update 脚本更新。

update.bat / update.sh 运行中无法自我覆盖（robocopy 会报错），更新流程
把新版脚本另存为 update.bat.new / update.sh.new，历史上依赖用户手动
替换——实践中几乎无人替换，导致 update 脚本修复无法触达用户。

程序启动时更新脚本必然已退出，是安全的替换时机。
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def apply_pending_update_scripts(root: Path | None = None) -> None:
    """用上次更新遗留的 update.*.new 替换对应脚本。

    - os.replace 原子替换（同卷），目标存在时覆盖；
    - 替换失败（如 update.bat 仍被运行中的 cmd 占用）只告警不抛出，
      .new 保留，下次启动重试；
    - 无 .new 时是纯查询，开销可忽略。
    """
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
