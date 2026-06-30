"""开机自启管理 — 跨平台支持 Windows / macOS / Linux。

- Windows: 写入注册表 HKCU\\...\\Run
- macOS:   创建 ~/Library/LaunchAgents/ai.koishi.deskpet.plist
- Linux:   创建 ~/.config/autostart/deskpet.desktop (XDG Autostart)
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 平台标识
_WIN_LABEL = "KoishiAI"
_MAC_LABEL = "ai.koishi.deskpet"
_LINUX_FILE = "deskpet.desktop"


# ── 命令构建 ──

def _python_exe() -> str:
    """当前 Python 解释器路径（frozen 模式下为打包后的可执行文件）。"""
    return sys.executable


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


# ── Windows ──

_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _build_windows_command() -> str:
    """构建 Windows 启动命令，确保工作目录正确且无控制台窗口。"""
    if _is_frozen():
        return f'"{sys.executable}"'

    arg0 = os.path.abspath(sys.argv[0])
    # pip install -e 创建的入口 exe → 直接用它
    if arg0.lower().endswith(".exe"):
        return f'"{arg0}"'

    # 开发模式：pythonw.exe -m pet（无控制台），start 让 cmd 立即退出
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'cmd /c start "" /d "{_PROJECT_ROOT}" "{pythonw}" -m pet'


def _set_auto_start_windows(enabled: bool):
    try:
        import winreg
        if enabled:
            cmd = _build_windows_command()
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, _WIN_LABEL, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            logger.info(f"[AutoStart] Windows enabled: {cmd}")
        else:
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE)
                winreg.DeleteValue(key, _WIN_LABEL)
                winreg.CloseKey(key)
                logger.info("[AutoStart] Windows disabled")
            except FileNotFoundError:
                pass
    except Exception as e:
        logger.exception(f"[AutoStart] Windows failed: {e}")


# ── macOS ──

def _mac_plist_path() -> str:
    return os.path.expanduser(f"~/Library/LaunchAgents/{_MAC_LABEL}.plist")


def _build_mac_plist() -> str:
    """生成 LaunchAgent plist 内容。"""
    exe = _python_exe()
    # frozen 模式下 sys.executable 即 .app 内的可执行文件
    program_args = (
        f'    <string>{exe}</string>\n    <string>-m</string>\n    <string>pet</string>'
        if not _is_frozen()
        else f'    <string>{exe}</string>'
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_MAC_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{program_args}
    </array>
    <key>WorkingDirectory</key>
    <string>{_PROJECT_ROOT}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardErrorPath</key>
    <string>/tmp/deskpet.err.log</string>
</dict>
</plist>
"""


def _set_auto_start_macos(enabled: bool):
    plist_path = _mac_plist_path()
    try:
        if enabled:
            os.makedirs(os.path.dirname(plist_path), exist_ok=True)
            with open(plist_path, "w", encoding="utf-8") as f:
                f.write(_build_mac_plist())
            logger.info(f"[AutoStart] macOS enabled: {plist_path}")
        else:
            if os.path.exists(plist_path):
                os.remove(plist_path)
                logger.info("[AutoStart] macOS disabled")
    except Exception as e:
        logger.exception(f"[AutoStart] macOS failed: {e}")


# ── Linux ──

def _linux_desktop_path() -> str:
    return os.path.expanduser(f"~/.config/autostart/{_LINUX_FILE}")


def _build_linux_desktop() -> str:
    """生成 XDG Autostart .desktop 文件内容。"""
    exe = _python_exe()
    if _is_frozen():
        exec_line = f'"{exe}"'
    else:
        exec_line = f'"{exe}" -m pet'
    return f"""[Desktop Entry]
Type=Application
Name=DeskPet
Comment=Desktop Pet
Exec={exec_line}
Path={_PROJECT_ROOT}
Terminal=false
X-GNOME-Autostart-enabled=true
NoDisplay=false
"""


def _set_auto_start_linux(enabled: bool):
    desktop_path = _linux_desktop_path()
    try:
        if enabled:
            os.makedirs(os.path.dirname(desktop_path), exist_ok=True)
            with open(desktop_path, "w", encoding="utf-8") as f:
                f.write(_build_linux_desktop())
            logger.info(f"[AutoStart] Linux enabled: {desktop_path}")
        else:
            if os.path.exists(desktop_path):
                os.remove(desktop_path)
                logger.info("[AutoStart] Linux disabled")
    except Exception as e:
        logger.exception(f"[AutoStart] Linux failed: {e}")


# ── 统一入口 ──

def set_auto_start(enabled: bool):
    """启用或禁用开机自启（自动适配 Windows / macOS / Linux）。"""
    platform = sys.platform
    if platform == "win32":
        _set_auto_start_windows(enabled)
    elif platform == "darwin":
        _set_auto_start_macos(enabled)
    elif platform.startswith("linux"):
        _set_auto_start_linux(enabled)
    else:
        logger.info(f"[AutoStart] unsupported platform: {platform}")
