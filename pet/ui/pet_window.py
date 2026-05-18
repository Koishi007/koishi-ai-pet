from PySide6.QtWidgets import QLabel, QVBoxLayout
from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from pet.ui.base_window import TransparentWindow


class PetWindow(TransparentWindow):
    def __init__(self):
        super().__init__()
        self._setup_ui()
        self._old_pos = None

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.pet_label = QLabel("\U0001f436")
        self.pet_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.pet_label.font()
        font.setPointSize(48)
        self.pet_label.setFont(font)
        layout.addWidget(self.pet_label)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._old_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._old_pos is not None:
            delta = event.globalPosition().toPoint() - self._old_pos
            self.move(self.pos() + delta)
            self._old_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._old_pos = None
