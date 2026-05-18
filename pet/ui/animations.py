from PySide6.QtCore import QPoint, QTimer, QPropertyAnimation, QEasingCurve, QObject


class AnimationManager(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._animations = []

    def move_to(self, target, start_pos, end_pos, duration=500, callback=None):
        print("from", start_pos ," move to:", end_pos)
        anim = QPropertyAnimation(target, b"pos")
        anim.setDuration(duration)
        anim.setStartValue(start_pos)
        anim.setEndValue(end_pos)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if callback:
            anim.finished.connect(callback)
        anim.start()
        self._animations.append(anim)
        return anim

    def fade_in(self, target, duration=300):
        anim = QPropertyAnimation(target, b"windowOpacity")
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.start()
        self._animations.append(anim)
        return anim

    def fade_out(self, target, duration=300, callback=None):
        anim = QPropertyAnimation(target, b"windowOpacity")
        anim.setDuration(duration)
        anim.setStartValue(target.windowOpacity())
        anim.setEndValue(0.0)
        if callback:
            anim.finished.connect(callback)
        anim.start()
        self._animations.append(anim)
        return anim

    def bounce(self, target, duration=600):
        original_pos = target.pos()
        anim = QPropertyAnimation(target, b"pos")
        anim.setDuration(duration)
        anim.setKeyValueAt(0, original_pos)
        anim.setKeyValueAt(0.3, QPoint(original_pos.x(), original_pos.y() - 20))
        anim.setKeyValueAt(0.5, original_pos)
        anim.setKeyValueAt(0.7, QPoint(original_pos.x(), original_pos.y() - 10))
        anim.setKeyValueAt(1, original_pos)
        anim.setEasingCurve(QEasingCurve.Type.OutBounce)
        anim.start()
        self._animations.append(anim)
        return anim

    def idle_sway(self, target, amplitude=3):
        timer = QTimer(self)
        original_x = target.x()
        direction = 1

        def sway():
            nonlocal direction
            new_x = original_x + amplitude * direction
            target.move(new_x, target.y())
            direction *= -1

        timer.timeout.connect(sway)
        timer.start(1000)
        return timer
