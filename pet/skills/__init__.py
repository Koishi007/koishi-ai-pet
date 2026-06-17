"""技能插件加载器 — 自动发现 + 自动安装依赖 + 配置选择性加载。

插件组织方式：每个插件是一个独立目录，位于 skills/plugins/ 下。
  plugins/<plugin_name>/__init__.py          — 必须定义 SKILL_NAME、SKILL_DESCRIPTION、register()
  plugins/<plugin_name>/requirements.txt     — 插件私有依赖（可选，首次加载时自动安装）
  plugins/<plugin_name>/config.json          — 插件私有配置（可选，gitignored）
  plugins/<plugin_name>/config.example.json  — 配置模板（tracked，首次加载时自动复制为 config.json）
"""

import importlib
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from pet.skills.registry import SKILL_REGISTRY

logger = logging.getLogger(__name__)


def _get_pip_cmd() -> list[str]:
    """获取当前 Python 环境中的 pip 命令（自动适配虚拟环境）。"""
    # sys.executable 在 venv 中指向 venv 内的 python.exe
    python_exe = sys.executable
    return [python_exe, "-m", "pip", "install", "--quiet"]


def _ensure_plugin_config(plugin_dir: Path):
    """若插件目录下有 config.example.json 但无 config.json，自动复制一份。"""
    example = plugin_dir / "config.example.json"
    target = plugin_dir / "config.json"
    if example.is_file() and not target.is_file():
        try:
            shutil.copy2(example, target)
            logger.info(f"[SkillLoader] Created config.json from example for {plugin_dir.name}")
        except OSError as e:
            logger.warning(f"[SkillLoader] Failed to copy config.example.json: {e}")


def _ensure_plugin_deps(plugin_dir: Path):
    """自动安装插件的 requirements.txt 依赖。

    使用 .deps_installed 标记文件 + requirements.txt 的 mtime 来判断是否需要安装，
    避免每次启动都跑 pip install。
    """
    req_file = plugin_dir / "requirements.txt"
    if not req_file.is_file():
        return

    stamp_file = plugin_dir / ".deps_installed"

    # requirements.txt 自上次安装后未更新，跳过
    if stamp_file.is_file():
        req_mtime = req_file.stat().st_mtime
        stamp_mtime = stamp_file.stat().st_mtime
        if req_mtime <= stamp_mtime:
            return

    pip_cmd = _get_pip_cmd()
    # 检测是否在虚拟环境中
    in_venv = sys.prefix != sys.base_prefix
    venv_tag = " (venv)" if in_venv else ""

    logger.info(
        f"[SkillLoader] Installing deps for {plugin_dir.name}: "
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
        # 写入安装标记
        stamp_file.write_text("ok", encoding="utf-8")
    except subprocess.CalledProcessError as e:
        logger.warning(
            f"[SkillLoader] Failed to install deps for {plugin_dir.name}: "
            f"{e.stderr.strip()}"
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            f"[SkillLoader] Deps install timeout for {plugin_dir.name}"
        )
    except OSError as e:
        logger.warning(f"[SkillLoader] Deps install OS error for {plugin_dir.name}: {e}")


def load_skills(enabled: list[str]):
    """扫描 skills/plugins/ 下的子目录，按配置加载启用的插件。

    Args:
        enabled: 启用列表。["*"] 表示全部启用，[] 表示全部禁用。
    """
    if not enabled:
        logger.info("[SkillLoader] No skills enabled")
        return

    plugins_dir = Path(__file__).parent / "plugins"
    if not plugins_dir.is_dir():
        logger.warning("[SkillLoader] plugins directory not found")
        return

    loaded = []

    for sub_dir in sorted(plugins_dir.iterdir()):
        if not sub_dir.is_dir() or sub_dir.name.startswith("_"):
            continue

        init_file = sub_dir / "__init__.py"
        if not init_file.is_file():
            continue

        # 自动从 config.example.json 复制生成 config.json
        _ensure_plugin_config(sub_dir)

        # 自动安装插件依赖
        _ensure_plugin_deps(sub_dir)

        module_path = f"pet.skills.plugins.{sub_dir.name}"
        try:
            module = importlib.import_module(module_path)
        except Exception as e:
            logger.warning(f"[SkillLoader] Failed to import {sub_dir.name}: {e}")
            continue

        skill_name = getattr(module, "SKILL_NAME", None)
        if not skill_name:
            continue

        if "*" not in enabled and skill_name not in enabled:
            logger.debug(f"[SkillLoader] Skip disabled skill: {skill_name}")
            continue

        register_fn = getattr(module, "register", None)
        if not callable(register_fn):
            logger.warning(f"[SkillLoader] {sub_dir.name} has no register() function")
            continue

        try:
            register_fn(SKILL_REGISTRY)
            loaded.append(skill_name)
            logger.info(f"[SkillLoader] Loaded skill: {skill_name}")
        except Exception as e:
            logger.error(f"[SkillLoader] Failed to register {skill_name}: {e}")

    logger.info(f"[SkillLoader] {len(loaded)} skills loaded: {loaded}")
