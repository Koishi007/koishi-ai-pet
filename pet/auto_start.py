"""开机自启管理（Windows 注册表方式）。"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REG_VALUE_NAME = "KoishiAI"

# 项目根目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _build_command() -> str:
    """构建启动命令，确保工作目录正确且无控制台窗口。"""
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}"'

    arg0 = os.path.abspath(sys.argv[0])
    # pip install -e 创建的入口 exe → 直接用它
    if arg0.lower().endswith('.exe'):
        return f'"{arg0}"'

    # 开发模式：pythonw.exe -m pet（无控制台），start 让 cmd 立即退出
    pythonw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    return f'cmd /c start "" /d "{_PROJECT_ROOT}" "{pythonw}" -m pet'


def set_auto_start(enabled: bool):
    """启用或禁用开机自启（写入/删除注册表 Run 键）。"""
    if sys.platform != "win32":
        logger.info(f"[AutoStart] unsupported platform: {sys.platform}")
        return
    try:
        import winreg
        if enabled:
            cmd = _build_command()
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, _REG_VALUE_NAME, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            logger.info(f"[AutoStart] enabled: {cmd}")
        else:
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE)
                winreg.DeleteValue(key, _REG_VALUE_NAME)
                winreg.CloseKey(key)
                logger.info("[AutoStart] disabled")
            except FileNotFoundError:
                pass
    except Exception as e:
        logger.exception(f"[AutoStart] failed: {e}")
