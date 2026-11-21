"""UI 层确认对话框：自绘标题栏，按钮用 InkButton。"""

from enum import Enum, auto

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QGuiApplication, QIcon
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from pet import theme
from pet.ui.frame import paint_ink_frame
from pet.ui.ink_button import InkButton
from pet.ui.qss import (BUTTON_DANGER_QSS, BUTTON_PRIMARY_QSS, BUTTON_QSS,
                        app_stylesheet)
from pet.ui.styles import ICON_PATH, make_close_button


class Answer(Enum):
    """按钮角色；调用方按角色比较结果，不绑定按钮文案。"""
    ACCEPT = auto()
    DESTRUCTIVE = auto()
    REJECT = auto()


class InkDialog(QDialog):
    """模态自绘对话框：标题栏可拖动，回车、Esc 与标题栏关闭都落在默认角色。"""

    def __init__(self, parent=None, *, title: str, text: str,
                 buttons: list[tuple[Answer, str, str]],
                 default: Answer = Answer.REJECT,
                 warning: bool = False,
                 center_on_screen: bool = False):
        super().__init__(parent)
        self._default = default
        self._answer = default
        self._center_on_screen = center_on_screen
        self._buttons: dict[Answer, InkButton] = {}
        self._button_row: QHBoxLayout = QHBoxLayout()
        self._drag_pos = None

        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(app_stylesheet())
        try:
            self.setWindowIcon(QIcon(ICON_PATH))
        except Exception:
            pass

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14 + theme.SHADOW_WINDOW,
                                12 + theme.SHADOW_WINDOW)
        root.setSpacing(10)

        title_bar = QWidget()
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_label = QLabel(title)
        title_label.setStyleSheet(
            "font-size:14px; font-weight:800; background:transparent;"
            f" color:{theme.DANGER if warning else theme.INK};")
        title_row.addWidget(title_label)
        title_row.addStretch()
        title_row.addWidget(make_close_button(None, on_close=self._close, size=26))
        title_bar.mousePressEvent = self._header_press
        title_bar.mouseMoveEvent = self._header_move
        root.addWidget(title_bar)

        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setMaximumWidth(520)
        body.setStyleSheet(f"font-size:12px; color:{theme.INK}; background:transparent;")
        root.addWidget(body)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addStretch()
        for role, label, style in buttons:
            btn = InkButton(label)
            btn.setStyleSheet(style)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, r=role: self._finish(r))
            self._buttons[role] = btn
            button_row.addWidget(btn)
        self._button_row = button_row
        root.addLayout(button_row)

    def button(self, role: Answer) -> InkButton:
        """按角色取按钮。"""
        return self._buttons[role]

    def answer(self) -> Answer:
        """被点按钮的角色；Esc、回车与标题栏关闭返回 default。"""
        return self._answer

    def showEvent(self, event):
        super().showEvent(event)
        self._center()
        default_btn = self._buttons.get(self._default)
        if default_btn is not None:
            default_btn.setFocus()

    def reject(self):
        self._finish(self._default)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish(self._default)
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        paint_ink_frame(self)

    def _close(self) -> None:
        self._finish(self._default)

    def _finish(self, role: Answer) -> None:
        self._answer = role
        self.done(0)

    def _center(self) -> None:
        """center_on_screen 或无可见父窗时居中于光标所在屏幕，否则居中于父窗。"""
        self.adjustSize()
        parent = self.parent()
        if not self._center_on_screen and parent is not None and parent.isVisible():
            area = parent.frameGeometry()
        else:
            screen = (QGuiApplication.screenAt(QCursor.pos())
                      or QGuiApplication.primaryScreen())
            area = screen.availableGeometry() if screen is not None else None
        if area is not None:
            self.move(area.center() - self.rect().center())

    def _header_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def _header_move(self, event):
        if self._drag_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()


def ask(parent=None, *, title: str, text: str, accept: str,
        destructive: str = "", reject: str = "",
        default: Answer = Answer.REJECT, warning: bool = False,
        center_on_screen: bool = False) -> Answer:
    """确认框：返回被点按钮的角色；不提供 reject/destructive 时只显示 accept。"""
    buttons = [(Answer.ACCEPT, accept, BUTTON_PRIMARY_QSS)]
    if destructive:
        buttons.append((Answer.DESTRUCTIVE, destructive, BUTTON_DANGER_QSS))
    if reject:
        buttons.append((Answer.REJECT, reject, BUTTON_QSS))
    dialog = InkDialog(parent, title=title, text=text, buttons=buttons,
                       default=default, warning=warning,
                       center_on_screen=center_on_screen)
    dialog.exec()
    return dialog.answer()


def notify(parent=None, *, title: str, text: str, warning: bool = False) -> Answer:
    """单按钮提示框；按钮文案固定为「确定」。"""
    return ask(parent, title=title, text=text, accept="确定",
               default=Answer.ACCEPT, warning=warning)
