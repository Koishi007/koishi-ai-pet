"""启动时版本检查"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import urllib.error
import urllib.request
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py>=3.11 自带
    tomllib = None

from packaging.version import InvalidVersion, parse as _parse_ver

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

logger = logging.getLogger(__name__)

REPO = "Koishi007/koishi-ai-pet"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
_PKG_NAME = "koishi-ai-pet"
_TIMEOUT = 8  # 网络请求超时（秒）
_ssl_ctx = ssl.create_default_context()

# 文本解析 pyproject.toml 中的 version 字段（match 不适用，文件不以 version 开头）
_RE_VERSION = re.compile(r'^\s*version\s*=\s*["\']([^"\']+)', re.IGNORECASE | re.MULTILINE)


def _project_root() -> str:
    """项目根目录（pet 的上级目录）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_local_version_cache: str | None = None


def get_local_version() -> str:
    """获取本地版本号"""
    global _local_version_cache
    if _local_version_cache is not None:
        return _local_version_cache

    path = os.path.join(_project_root(), "pyproject.toml")
    try:
        if tomllib:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            v = data.get("project", {}).get("version", "")
            if v:
                logger.debug(f"[VersionCheck] source=pyproject.toml version={v}")
                _local_version_cache = v
                return v
        # py<3.11 无 tomllib 时用正则解析
        with open(path, encoding="utf-8") as f:
            m = _RE_VERSION.search(f.read())
            if m:
                v = m.group(1)
                logger.debug(f"[VersionCheck] source=pyproject.txt version={v}")
                _local_version_cache = v
                return v
    except Exception:
        pass
    try:
        v = _pkg_version(_PKG_NAME)
        if v:
            logger.debug(f"[VersionCheck] source=metadata version={v}")
            _local_version_cache = v
            return v
    except PackageNotFoundError:
        pass
    _local_version_cache = ""
    return ""


def _strip_v(tag: str) -> str:
    """去掉 tag 开头的单个 v/V 前缀（精确剥离，避免 lstrip 的字符集陷阱）。"""
    return tag[1:] if tag[:1] in ("v", "V") else tag


def _ver_newer(remote: str, local: str) -> bool:
    """判断 remote 是否比 local 新（PEP 440 规范比较）。"""
    try:
        return _parse_ver(remote) > _parse_ver(local)
    except InvalidVersion:
        logger.debug(f"[VersionCheck] 版本号无法解析，跳过比较: remote={remote} local={local}")
        return False


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


class _CheckWorker(QObject):
    """后台执行 GitHub API 请求的工作对象"""

    update_available = Signal(str, str)
    finished = Signal()

    @Slot()
    def run(self) -> None:
        """在线程中执行版本检查（同步阻塞，由 QThread.started 触发）。"""
        try:
            local = get_local_version()
            if not local:
                logger.debug("[VersionCheck] 本地版本未知，跳过检查")
                return
            try:
                req = urllib.request.Request(API_URL, headers=_build_headers())
                with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_ssl_ctx) as resp:
                    # urlopen 对 4xx/5xx 直接抛 HTTPError，此处仅防御 3xx 等极端场景
                    if resp.status != 200:
                        logger.debug(f"[VersionCheck] HTTP {resp.status}")
                        return
                    data = json.load(resp)

                tag_name = data.get("tag_name", "")
                # releases/latest 仅返回非草稿、非预发布版本（防御 API 策略变更）
                if not tag_name or data.get("prerelease"):
                    return
                latest = _strip_v(tag_name)
                logger.debug(f"[VersionCheck] local={local} latest={latest}")
                if _ver_newer(latest, local):
                    self.update_available.emit(latest, local)
            except urllib.error.HTTPError as e:
                logger.debug(f"[VersionCheck] HTTPError {e.code}: {e.reason}")
            except urllib.error.URLError as e:
                logger.debug(f"[VersionCheck] URLError: {e.reason}")
            except Exception as e:
                logger.debug(f"[VersionCheck] 检查失败: {e}")
        finally:
            self.finished.emit()


class UpdateChecker(QObject):
    """异步版本检查器 — QThread + moveToThread 模式，信号安全跨线程"""

    update_available = Signal(str, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: _CheckWorker | None = None

    def check(self) -> None:
        """启动后台检查（非阻塞）。前次未清理完前不接受新请求。"""
        if self._thread is not None:
            return
        self._thread = QThread()
        self._worker = _CheckWorker()
        self._worker.moveToThread(self._thread)
        self._worker.update_available.connect(
            self.update_available, type=Qt.ConnectionType.QueuedConnection
        )
        # worker 在自身线程内析构，必须在 quit 连接之前
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.finished.connect(self._thread.quit, type=Qt.ConnectionType.DirectConnection)
        self._thread.finished.connect(self._cleanup)
        self._thread.started.connect(self._worker.run)
        self._thread.start()

    @Slot()
    def _cleanup(self) -> None:
        """线程结束后清理资源（在主线程执行）。"""
        if self._thread:
            self._thread.deleteLater()
            self._thread = None
        # worker 已由 deleteLater 在自身线程析构，仅需解除引用
        self._worker = None
