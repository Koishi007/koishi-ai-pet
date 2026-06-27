"""开机自启管理（Windows 注册表方式）。"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REG_VALUE_NAME = "KoishiAI"


def _build_command() -> str:
    """构建启动命令。"""
    # 打包后的 exe 直接用 exe 路径
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}"'
    # 开发模式：python 解释器 + 入口脚本绝对路径
    script = os.path.abspath(sys.argv[0])
    return f'"{sys.executable}" "{script}"'


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
