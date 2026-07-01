"""启动时版本检查"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py>=3.11 自带
    tomllib = None

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

REPO = "Koishi007/koishi-ai-pet"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
_PKG_NAME = "koishi-ai-pet"
_TIMEOUT = 15  # 网络请求超时（秒）

# packaging 可选，用于 PEP 440 规范的版本比较；不可用时回退到手写比较
try:
    from packaging.version import InvalidVersion, parse as _parse_ver
    _HAS_PACKAGING = True
except ModuleNotFoundError:
    _HAS_PACKAGING = False


def _project_root() -> str:
    """项目根目录（pet 的上级目录）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_local_version() -> str:
    """获取本地版本号。"""
    # 1) 安装元数据
    try:
        v = _pkg_version(_PKG_NAME)
        if v:
            return v
    except PackageNotFoundError:
        pass
    # 2) 回退到 pyproject.toml
    path = os.path.join(_project_root(), "pyproject.toml")
    try:
        if tomllib:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            return data.get("project", {}).get("version", "")
        # py<3.11 无 tomllib 时简单文本解析
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith("version") and "=" in s and '"' in s:
                    return s.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception as e:
        logger.warning(f"[VersionCheck] 读取本地版本失败: {e}")
    return ""


def _strip_v(tag: str) -> str:
    """去掉 tag 开头的单个 v/V 前缀（精确剥离，避免 lstrip 的字符集陷阱）。"""
    return tag[1:] if tag[:1] in ("v", "V") else tag


def _ver_newer(remote: str, local: str) -> bool:
    """判断 remote 是否比 local 新"""
    if _HAS_PACKAGING:
        try:
            return _parse_ver(remote) > _parse_ver(local)
        except InvalidVersion:
            return remote != local
    try:
        ra = [int(x) for x in remote.split(".")]
        la = [int(x) for x in local.split(".")]
        for r, l in zip(ra, la):
            if r != l:
                return r > l
        return len(ra) > len(la)
    except (ValueError, AttributeError):
        return remote != local


def _build_headers() -> dict:
    """构建 GitHub API 请求头，可选支持 GITHUB_TOKEN 环境变量。"""
    headers = {
        "User-Agent": "koishi-ai-pet",
        "Accept": "application/vnd.github+json",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class UpdateChecker(QObject):
    """异步版本检查器"""

    #: 发现新版本时发出 (latest_tag, local_version)
    update_available = Signal(str, str)

    def check(self) -> None:
        """启动后台线程检查更新（非阻塞）。"""
        t = threading.Thread(target=self._check_worker, daemon=True)
        t.start()

    def _check_worker(self) -> None:
        local = get_local_version()
        if not local:
            logger.debug("[VersionCheck] 本地版本未知，跳过检查")
            return
        try:
            req = urllib.request.Request(API_URL, headers=_build_headers())
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                # 限流检查：匿名 60 次/小时，NAT 环境易触发
                remaining = resp.headers.get("X-RateLimit-Remaining")
                if remaining is not None:
                    try:
                        if int(remaining) <= 0:
                            logger.debug("[VersionCheck] 触发限流，跳过本次")
                            return
                    except ValueError:
                        pass
                if resp.status != 200:
                    logger.debug(f"[VersionCheck] HTTP {resp.status}")
                    return
                data = json.loads(resp.read().decode("utf-8"))
            tag_name = data.get("tag_name", "")
            # releases/latest 只返回非草稿/非预发布，显式判断以防策略变更
            if not tag_name or data.get("prerelease"):
                return
            latest = _strip_v(tag_name)
            logger.debug(f"[VersionCheck] local={local} latest={latest}")
            if _ver_newer(latest, local):
                self.update_available.emit(tag_name, local)
        except urllib.error.HTTPError as e:
            # 404 = 仓库尚无 release，静默跳过
            logger.debug(f"[VersionCheck] HTTPError {e.code}: {e.reason}")
        except Exception as e:
            logger.debug(f"[VersionCheck] 检查失败: {e}")
