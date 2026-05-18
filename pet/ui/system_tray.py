from PySide6.QtWidgets import QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QAction
from PySide6.QtCore import Qt, QObject

from pet.ui.debug_window import DebugWindow

class SystemTrayManager(QObject):
    """管理系统托盘图标和菜单"""
    
    def __init__(self, app, pet_window):
        super().__init__()
        self.app = app
        self.pet = pet_window
        self._debug_window = None
        
        # 1. 创建托盘图标 (暂时画一个简易图标)
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self._create_temp_icon())
        self.tray_icon.setToolTip("我的智能桌宠")
        
        # 2. 构建右键菜单
        self._build_menu()
        
        # 3. 信号连接
        self.tray_icon.activated.connect(self._on_tray_activated)
        
        # 4. 显示托盘图标
        self.tray_icon.show()

    def _create_temp_icon(self):
        """创建一个临时图标，之后可替换为你的宠物头像"""
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(255, 150, 100))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(2, 2, 28, 28)
        painter.end()
        return QIcon(pixmap)

    def _build_menu(self):
        """构建托盘右键菜单"""
        self.menu = QMenu()
        
        # 显示/隐藏桌宠
        self.toggle_action = QAction("显示/隐藏桌宠")
        self.toggle_action.triggered.connect(self._toggle_pet_visibility)
        self.menu.addAction(self.toggle_action)
        
        self.menu.addSeparator()

        # 调试窗口
        self.debug_action = QAction("调试")
        self.debug_action.triggered.connect(self._show_debug_window)
        self.menu.addAction(self.debug_action)

        self.menu.addSeparator()
        
        # 退出程序
        self.quit_action = QAction("退出")
        self.quit_action.triggered.connect(self._quit_app)
        self.menu.addAction(self.quit_action)
        
        self.tray_icon.setContextMenu(self.menu)

    def _toggle_pet_visibility(self):
        """切换桌宠的可见性"""
        self.pet.setVisible(not self.pet.isVisible())

    def _show_debug_window(self):
        """显示或激活调试窗口"""
        if self._debug_window is None:
            self._debug_window = DebugWindow(self.pet)
        self._debug_window.show()
        self._debug_window.activateWindow()
        self._debug_window.raise_()

    def _quit_app(self):
        """完全退出应用程序"""
        self.pet.close()
        self.tray_icon.hide()
        self.app.quit()  

    def _on_tray_activated(self, reason):
        """处理托盘图标的激活事件"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # 左键单击切换显示
            self._toggle_pet_visibility()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            # 左键双击显示宠物
            if not self.pet.isVisible():
                self.pet.show()