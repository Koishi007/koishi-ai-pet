from PySide6.QtWidgets import QLabel, QWidget
from PySide6.QtCore import Qt, QTimer, QPoint, QPropertyAnimation, QParallelAnimationGroup, QEasingCurve
from PySide6.QtGui import QPainter, QColor, QPen, QPolygon
from config import config

TAIL_HEIGHT = 10


class SpeechBubble(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(
            "padding: 10px;"
            f"font-size: {config.BUBBLE_FONT_SIZE}px;"
            "color: #333;"
        )
        self.setWordWrap(True)
        self.setMaximumWidth(config.BUBBLE_MAX_WIDTH)
        self.setMinimumWidth(80)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_hide_timer)
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._follow_pet)
        self._anim_group: QParallelAnimationGroup | None = None

        # 打字机效果相关
        self._stream_buffer = ""
        self._char_queue: list[str] = []
        self._stream_ending = False
        self._type_timer = QTimer(self)
        self._type_timer.timeout.connect(self._type_next_char)
        self._type_interval = 35

        # ── 气泡输出队列 ──
        # 每个元素：{"text": str, "duration": int}
        self._speech_queue: list[dict] = []
        # 当前正在缓冲（气泡显示中有新流进来时）
        self._buffering = False
        self._incoming_chunks: list[str] = []
        self._incoming_duration: int = 5000

        self.hide()

    # ══════════════════════════════════════════════════════
    # paintEvent / resize / position helpers
    # ══════════════════════════════════════════════════════

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        body_h = h - TAIL_HEIGHT

        body_rect = self.rect()
        body_rect.setHeight(body_h)
        painter.setBrush(QColor(255, 255, 255, 220))
        painter.setPen(QPen(QColor(200, 200, 200), 1))
        painter.drawRoundedRect(body_rect.adjusted(0, 0, -1, -1), 12, 12)

        cx = w // 2
        tail_top = body_h - 1
        tail = QPolygon([
            QPoint(cx - 6, tail_top),
            QPoint(cx + 6, tail_top),
            QPoint(cx,     h - 1),
        ])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 220))
        painter.drawPolygon(tail)

        painter.end()
        super().paintEvent(event)

    def _resize_to_text(self):
        text = self.text()
        self.setMinimumWidth(80)
        self.setMaximumWidth(config.BUBBLE_MAX_WIDTH)
        metrics = self.fontMetrics()
        avg_w = metrics.averageCharWidth()
        target_w = int(avg_w * 28)
        text_w = metrics.horizontalAdvance(text) + 20
        w = text_w if text_w <= target_w else target_w
        w = max(w, self.minimumWidth())
        w = min(w, self.maximumWidth())
        self.setFixedWidth(w)
        self.adjustSize()
        self.resize(self.width(), self.height() + TAIL_HEIGHT)

    def _reposition(self):
        pet = self.parent()
        if isinstance(pet, QWidget) and self.isVisible():
            target = pet.geometry().center()
            final = self._final_position(target)
            self.move(final)

    def _start_tracking(self):
        self._follow_timer.start(50)

    # ══════════════════════════════════════════════════════
    # 公共显示接口（show_text / start_stream / append_stream / end_stream）
    # ══════════════════════════════════════════════════════

    def show_text(self, text: str, duration: int = 5000, parent_pos=None):
        """显示静态文本。若气泡正在使用中则入队。"""
        if self._is_active():
            self._enqueue(text, duration)
            return
        self._play_text(text, duration, parent_pos)

    def start_stream(self, parent_pos=None):
        """开始流式。若气泡正在使用中则进入缓冲模式。"""
        if self._is_active():
            # 进入缓冲模式：收集 chunks，等当前结束后再播
            self._buffering = True
            self._incoming_chunks.clear()
            self._incoming_duration = 5000
            return

        # 气泡空闲，立即流式显示
        self._buffering = False
        self._stream_buffer = ""
        self._char_queue.clear()
        self._stream_ending = False
        self._type_timer.stop()
        self.setText("")
        self._resize_to_text()

        if parent_pos is None and isinstance(self.parent(), QWidget):
            parent_pos = self.parent().geometry().center()
        if parent_pos is not None:
            pos = self._final_position(parent_pos)
            self.move(pos)

        self.show()
        self._start_tracking()

    def append_stream(self, chunk: str):
        """追加流式文本片段。"""
        if self._buffering:
            self._incoming_chunks.append(chunk)
        else:
            self._char_queue.extend(chunk)
            if not self._type_timer.isActive():
                self._type_timer.start(self._type_interval)

    def end_stream(self, duration: int = 5000):
        """流式结束。缓冲模式下将完整文本入队。"""
        if self._buffering:
            text = "".join(self._incoming_chunks)
            self._incoming_chunks.clear()
            self._buffering = False
            if text:
                self._enqueue(text, duration)
            return

        self._end_stream_duration = duration
        if self._char_queue:
            self._stream_ending = True
            if not self._type_timer.isActive():
                self._type_timer.start(self._type_interval)
        else:
            self._type_timer.stop()
            self._finish_stream()

    # ══════════════════════════════════════════════════════
    # 队列管理
    # ══════════════════════════════════════════════════════

    def _is_active(self) -> bool:
        """气泡当前是否正在使用（显示中或打字中）。"""
        return self.isVisible() and (
            self._type_timer.isActive() or self._stream_ending or self._hide_timer.isActive()
        )

    def _enqueue(self, text: str, duration: int):
        """将文本加入待播队列。"""
        self._speech_queue.append({"text": text, "duration": duration})

    def _play_next_queued(self):
        """播放队列中下一条，若无则隐藏。"""
        if self._speech_queue:
            item = self._speech_queue.pop(0)
            # 短暂停顿后显示下一条
            QTimer.singleShot(300, lambda: self._play_text(item["text"], item["duration"]))
        else:
            self._hide_bubble()

    def _play_text(self, text: str, duration: int, parent_pos=None):
        """直接播放一条静态文本（带打字机效果）。"""
        self._stream_buffer = ""
        self._char_queue.clear()
        self._stream_ending = False
        self._type_timer.stop()

        if parent_pos is None and isinstance(self.parent(), QWidget):
            parent_pos = self.parent().geometry().center()

        if parent_pos is not None:
            start_pos = self._head_position(parent_pos)
            self.move(start_pos)
            self.setWindowOpacity(0.0)

        self.setText("")
        self._resize_to_text()
        self.show()

        if parent_pos is not None:
            final_pos = self._final_position(parent_pos)
            self._anim_group = QParallelAnimationGroup(self)

            pos_anim = QPropertyAnimation(self, b"pos")
            pos_anim.setDuration(300)
            pos_anim.setStartValue(self.pos())
            pos_anim.setEndValue(final_pos)
            pos_anim.setEasingCurve(QEasingCurve.Type.OutBack)

            opacity_anim = QPropertyAnimation(self, b"windowOpacity")
            opacity_anim.setDuration(250)
            opacity_anim.setStartValue(0.0)
            opacity_anim.setEndValue(0.95)
            opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            self._anim_group.addAnimation(pos_anim)
            self._anim_group.addAnimation(opacity_anim)
            self._anim_group.finished.connect(self._on_anim_finished)
            self._anim_group.start()
        else:
            self._start_tracking()

        # 把文本塞入打字机
        self._end_stream_duration = duration
        self._char_queue.extend(text)
        self._stream_ending = True
        self._type_timer.start(self._type_interval)

    # ══════════════════════════════════════════════════════
    # 打字机内部
    # ══════════════════════════════════════════════════════

    def _type_next_char(self):
        if self._char_queue:
            char = self._char_queue.pop(0)
            self._stream_buffer += char
            self.setText(self._stream_buffer)
            self._resize_to_text()
            self._reposition()
        else:
            self._type_timer.stop()
            if self._stream_ending:
                self._stream_ending = False
                self._finish_stream()

    def _finish_stream(self):
        """流式/打字结束后的收尾：启动 hide 定时器或播队列下一条。"""
        if self._anim_group:
            self._anim_group.stop()
            self._anim_group = None
            self.setWindowOpacity(1.0)
        self._hide_timer.start(self._end_stream_duration)

    def _on_hide_timer(self):
        """hide 定时器到期：若队列有内容则播下一条，否则隐藏。"""
        self._play_next_queued()

    def _hide_bubble(self):
        self._follow_timer.stop()
        if self._anim_group:
            self._anim_group.stop()
            self._anim_group.deleteLater()
            self._anim_group = None
        super().hide()

    # ══════════════════════════════════════════════════════
    # 位置 helpers
    # ══════════════════════════════════════════════════════

    def _head_position(self, target_pos: QPoint) -> QPoint:
        pet_top = target_pos.y() - config.PET_HEIGHT // 2
        head_bottom = pet_top + config.PET_HEIGHT // 3
        x = target_pos.x() - self.width() // 2
        y = head_bottom - self.height()
        return QPoint(max(0, x), max(0, y))

    def _final_position(self, target_pos: QPoint) -> QPoint:
        x = target_pos.x() - self.width() // 2
        pet_top = target_pos.y() - config.PET_HEIGHT // 2
        y = pet_top - self.height() - 15
        return QPoint(max(0, x), max(0, y))

    def _on_anim_finished(self):
        self.setWindowOpacity(1.0)
        self._follow_timer.start(50)

    def hide(self):
        self._follow_timer.stop()
        if self._anim_group:
            self._anim_group.stop()
            self._anim_group.deleteLater()
            self._anim_group = None
        super().hide()

    def _follow_pet(self):
        pet = self.parent()
        if isinstance(pet, QWidget) and self.isVisible():
            target = pet.geometry().center()
            final = self._final_position(target)
            self.move(final)
