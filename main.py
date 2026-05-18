import sys
from PySide6.QtWidgets import QApplication
from pet.ui.pet_window import PetWindow
from pet.ui.system_tray import SystemTrayManager


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = PetWindow()
    window.show()

    tray = SystemTrayManager(app, window)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
