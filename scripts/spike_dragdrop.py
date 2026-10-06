"""一次性验证脚本：桌宠窗口形态能否接收文件拖放。

验证目标：无边框 + 置顶 + Tool + 半透明的窗口开启 `setAcceptDrops(True)`
后能否收到 `dragEnterEvent` / `dropEvent`，以及穿透模式下是否确实收不到。
窗口形态与 `pet/ui/base_window.py:5-14` 保持一致，本脚本不导入 `pet` 包。

用法：
    python scripts/spike_dragdrop.py              # 显示探针窗口，60 秒后自动退出
    python scripts/spike_dragdrop.py --selftest   # 只打印窗口属性后退出，不显示窗口
    python scripts/spike_dragdrop.py --penetration  # 附加 WA_TransparentForMouseEvents，验证失效
    python scripts/spike_dragdrop.py --no-dragmove  # dragMoveEvent 走默认 ignore，验证是否必需

结果判读：从资源管理器拖文件到探针窗口，控制台应打印 dragEnter 与 drop 两行及本地路径。
窗口无输出即前提不成立，拖入交互设计需要改窗口形态。

探针保留 acceptProposedAction()，用于观察系统提议的动作（Windows 上实测为 CopyAction）；
产品实现改为 setDropAction(CopyAction) 后 accept()，见设计文档 §11「拖放动作语义」。

验证完成后本文件可删除，它不是产品代码。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QWidget

# 窗口 flags 与属性复制自 pet/ui/base_window.py:5-14 的 TransparentWindow。
# 不导入 pet 包：pet/__init__ 会安装崩溃钩子，探针每次退出都会在 logs/crash 留下报告。
_ACCEPT_MOVE = "--no-dragmove" not in sys.argv


class DropProbe(QWidget):
    """桌宠窗口的最小化版本：同样的 flags 与属性，加拖放处理。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFixedSize(260, 160)
        self.setAcceptDrops(True)
        label = QLabel("把文件拖到这里\n结果打印在控制台", self)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setGeometry(0, 0, 260, 160)
        label.setStyleSheet(
            "color:#333; background:rgba(255,255,255,200); border-radius:12px;"
        )

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        urls = mime.urls() if mime.hasUrls() else []
        print(f"[dragEnter] hasUrls={mime.hasUrls()} urls={len(urls)} "
              f"proposed={event.proposedAction().name}", flush=True)
        event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if _ACCEPT_MOVE:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        print(f"[drop] {len(urls)} url(s)", flush=True)
        for url in urls:
            path = Path(url.toLocalFile())
            print(f"    {path}  exists={path.exists()} dir={path.is_dir()} "
                  f"size={path.stat().st_size if path.is_file() else '-'}", flush=True)
        event.acceptProposedAction()


def main() -> int:
    penetration = "--penetration" in sys.argv
    app = QApplication(sys.argv)
    probe = DropProbe()
    if penetration:
        probe.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    flags = probe.windowFlags()
    print(f"flags: frameless={bool(flags & Qt.WindowType.FramelessWindowHint)} "
          f"stays_on_top={bool(flags & Qt.WindowType.WindowStaysOnTopHint)} "
          f"tool={bool(flags & Qt.WindowType.Tool)} "
          f"popup={bool(flags & Qt.WindowType.Popup)}", flush=True)
    print(f"dragMove={_ACCEPT_MOVE and 'accept' or 'ignore'} "
          f"acceptDrops={probe.acceptDrops()} "
          f"translucent={probe.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)} "
          f"penetration={probe.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)}",
          flush=True)

    if "--selftest" in sys.argv:
        print("selftest ok", flush=True)
        return 0

    probe.show()
    QTimer.singleShot(60000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
