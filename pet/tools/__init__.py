"""工具加载器 — 自动发现 + 自动安装依赖 + 配置选择性加载。

工具组织方式：每个工具是一个独立目录，位于 tools/ 下。
  tools/<tool_name>/__init__.py          — 必须定义 TOOL_NAME、TOOL_DESCRIPTION、register()
  tools/<tool_name>/requirements.txt     — 工具私有依赖（可选，首次加载时自动安装）
  tools/<tool_name>/config.json          — 工具私有配置（可选，gitignored）
  tools/<tool_name>/config.example.json  — 配置模板（tracked，首次加载时自动复制为 config.json）
"""

import importlib
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from pet.tools.registry import TOOL_REGISTRY

logger = logging.getLogger(__name__)


def _get_pip_cmd() -> list[str]:
    """获取当前 Python 环境中的 pip 命令（自动适配虚拟环境）。"""
    python_exe = sys.executable
    return [python_exe, "-m", "pip", "install", "--quiet"]


def _ensure_tool_config(tool_dir: Path):
    """若工具目录下有 config.example.json 但无 config.json，自动复制一份。"""
    example = tool_dir / "config.example.json"
    target = tool_dir / "config.json"
    if example.is_file() and not target.is_file():
        try:
            shutil.copy2(example, target)
            logger.info(f"[ToolLoader] Created config.json from example for {tool_dir.name}")
        except OSError as e:
            logger.warning(f"[ToolLoader] Failed to copy config.example.json: {e}")


def _ensure_tool_deps(tool_dir: Path):
    """自动安装工具的 requirements.txt 依赖。

    使用 .deps_installed 标记文件 + requirements.txt 的 mtime 来判断是否需要安装，
    避免每次启动都跑 pip install。
    """
    req_file = tool_dir / "requirements.txt"
    if not req_file.is_file():
        return

    stamp_file = tool_dir / ".deps_installed"

    if stamp_file.is_file():
        req_mtime = req_file.stat().st_mtime
        stamp_mtime = stamp_file.stat().st_mtime
        if req_mtime <= stamp_mtime:
            logger.debug(
                f"[ToolLoader] {tool_dir.name} deps up-to-date "
                f"(req_mtime={req_mtime:.0f} <= stamp_mtime={stamp_mtime:.0f})"
            )
            return
        logger.info(
            f"[ToolLoader] {tool_dir.name} requirements.txt changed "
            f"(req_mtime={req_mtime:.0f} > stamp_mtime={stamp_mtime:.0f}), reinstalling..."
        )

    pip_cmd = _get_pip_cmd()
    in_venv = sys.prefix != sys.base_prefix
    venv_tag = " (venv)" if in_venv else ""

    logger.info(
        f"[ToolLoader] Installing deps for {tool_dir.name}: "
        f"pip {' '.join(pip_cmd[2:])} -r {req_file}{venv_tag}"
    )
    try:
        subprocess.run(
            pip_cmd + ["-r", str(req_file)],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        stamp_file.write_text("ok", encoding="utf-8")
    except subprocess.CalledProcessError as e:
        logger.warning(
            f"[ToolLoader] Failed to install deps for {tool_dir.name}: "
            f"{e.stderr.strip()}"
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            f"[ToolLoader] Deps install timeout for {tool_dir.name}"
        )
    except OSError as e:
        logger.warning(f"[ToolLoader] Deps install OS error for {tool_dir.name}: {e}")


def _load_tools_sync(enabled: list[str]):
    """同步加载所有工具（在后台线程中执行）。"""
    if not enabled:
        logger.info("[ToolLoader] No tools enabled")
        return

    tools_dir = Path(__file__).parent
    if not tools_dir.is_dir():
        logger.warning("[ToolLoader] tools directory not found")
        return

    loaded = []

    for sub_dir in sorted(tools_dir.iterdir()):
        if not sub_dir.is_dir() or sub_dir.name.startswith("_"):
            continue

        init_file = sub_dir / "__init__.py"
        if not init_file.is_file():
            continue

        _ensure_tool_config(sub_dir)
        _ensure_tool_deps(sub_dir)

        module_path = f"pet.tools.{sub_dir.name}"
        try:
            module = importlib.import_module(module_path)
        except Exception as e:
            logger.warning(f"[ToolLoader] Failed to import {sub_dir.name}: {e}")
            continue

        tool_name = getattr(module, "TOOL_NAME", None)
        if not tool_name:
            continue

        if "*" not in enabled and tool_name not in enabled:
            logger.debug(f"[ToolLoader] Skip disabled tool: {tool_name}")
            continue

        register_fn = getattr(module, "register", None)
        if not callable(register_fn):
            logger.warning(f"[ToolLoader] {sub_dir.name} has no register() function")
            continue

        try:
            register_fn(TOOL_REGISTRY)
            tool_group = getattr(module, "TOOL_GROUP", None)
            if tool_group:
                tdef = TOOL_REGISTRY._tools.get(tool_name)
                if tdef:
                    tdef.group = tool_group
                    logger.debug(f"[ToolLoader] {tool_name} → group: {tool_group}")
            loaded.append(tool_name)
            logger.info(f"[ToolLoader] Loaded tool: {tool_name}")
        except Exception as e:
            logger.error(f"[ToolLoader] Failed to register {tool_name}: {e}")

    logger.info(f"[ToolLoader] {len(loaded)} tools loaded: {loaded}")


def load_tools(enabled: list[str]):
    """异步加载工具 — 在后台线程执行，立即返回不阻塞启动。

    Args:
        enabled: 启用列表。["*"] 表示全部启用，[] 表示全部禁用。
    """
    if not enabled:
        logger.info("[ToolLoader] No tools enabled")
        return

    threading.Thread(
        target=_load_tools_sync,
        args=(enabled,),
        daemon=True,
        name="tool-loader",
    ).start()
