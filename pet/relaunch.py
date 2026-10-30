"""进程重启：按当前运行方式拉起一个新实例。"""

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _python_exe() -> str:
    """开发模式优先 pythonw，避免控制台闪烁。"""
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return pythonw if os.path.isfile(pythonw) else sys.executable


def relaunch_command() -> tuple[list[str], str]:
    """返回 (启动命令, 工作目录)，还原当前实例的真实入口。"""
    if getattr(sys, "frozen", False):
        # 冻结版只能重跑自身
        return [sys.executable, *sys.argv[1:]], os.path.dirname(sys.executable) or os.getcwd()
    arg0 = os.path.abspath(sys.argv[0])
    if arg0.lower().endswith(".exe"):
        # 入口 exe：复用同一份安装
        return [arg0, *sys.argv[1:]], os.path.dirname(arg0)
    exe = _python_exe()
    parent = os.path.dirname(arg0)
    # python -m pet：用 -m 保住包内相对导入
    if os.path.basename(arg0) == "__main__.py" and os.path.basename(parent) == "pet":
        return [exe, "-m", "pet"], os.path.dirname(parent)
    if arg0.lower().endswith(".py") and os.path.isfile(arg0):
        return [exe, arg0, *sys.argv[1:]], parent
    return [exe, "-m", "pet"], _PROJECT_ROOT


def spawn_relaunch() -> bool:
    """拉起新实例，失败返回 False。"""
    cmd, cwd = relaunch_command()
    kwargs = {"cwd": cwd, "close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)
    except Exception as e:
        logger.error(f"[Relaunch] 拉起新实例失败: {e}")
        return False
    logger.info(f"[Relaunch] 已拉起新实例: {cmd} (cwd={cwd})")
    return True
