"""桌宠对话气泡：同一时刻只讲一句，多出来的排队"""

from pathlib import Path

from PySide6.QtWidgets import QLabel, QWidget
from PySide6.QtCore import (Qt, QTimer, QPoint, QPointF, QRectF, QPropertyAnimation,
                            QParallelAnimationGroup, QEasingCurve)
from PySide6.QtGui import (QPainter, QColor, QPainterPath, QPen, QLinearGradient,
                           QBrush, QPixmap)
from pet.config import config
from pet import theme
from pet.ui.edge_dock import edge_mask_rect

TAIL_HEIGHT = 13

_TAIL_HALF_W = 9    # 尾巴的根部半宽
_TAIL_ROOT = 4      # 尾根压进本体的深度
_SHADOW = (2, 3)    # 零模糊墨影
_BADGE_LIFT = 9     # 顶部为第三只眼徽章让出的高度
_BADGE_R = 7.5      # 徽章半径

_ROSE_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "icon" / "rose.webp"
_ROSE_SIZE = 20     # 蔷薇显示边长
_ROSE_INSET = 4     # 花心从气泡右下角内收
_ROSE_DROP = (2, 2)  # 蔷薇的零模糊墨影
_ROSE_LIP = _ROSE_SIZE // 2 - _ROSE_INSET + _ROSE_DROP[0]  # 蔷薇探出边框的宽度
_ROSE_GAP = 6       # 文字与蔷薇的间距
_PAD_RIGHT = 1 + _SHADOW[0] + _ROSE_LIP + _ROSE_GAP
_PAD_LEFT = 10
_PAD_TOP = 10 + _BADGE_LIFT           # 顶部留徽章位
_PAD_BOTTOM = 10 + TAIL_HEIGHT + _SHADOW[1]

_GAP_MS = 300  # 两句之间的间隔

_QUEUE_MAX = 4  # 待播上限：多轮工具链的 aside 堆积时丢最旧的


class SpeechBubble(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # 顶部留徽章位、右下留蔷薇位：正文不进边角
        self.setStyleSheet(
            f"padding: {_PAD_TOP}px {_PAD_RIGHT}px {_PAD_BOTTOM}px {_PAD_LEFT}px;"
            f"font-size: {config.BUBBLE_FONT_SIZE}px;"
            f"color: {theme.INK};"
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

        self._stream_buffer = ""
        self._char_queue: list[str] = []
        self._stream_ending = False
        self._type_timer = QTimer(self)
        self._type_timer.timeout.connect(self._type_next_char)
        self._type_interval = 35
        self._end_stream_duration = 5000

        self._gap_timer = QTimer(self)  # 过渡期也算占用，防止新句子误判气泡空闲
        self._gap_timer.setSingleShot(True)
        self._gap_timer.timeout.connect(self._on_gap_timeout)

        self._speech_queue: list[dict] = []
        self._buffering = False
        self._incoming_chunks: list[str] = []
        self._stream_open = False  # 已开句、尚未收到 end_stream
        self._tail_x_cache = -1    # 上一次画尾巴的横坐标，贴边变化时触发重绘
        self._rose_cache: tuple[QPixmap, QPixmap] | None = None

        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bubble = self._bubble_path()
        painter.fillPath(bubble.translated(_SHADOW[0], _SHADOW[1]), QColor(theme.INK))

        paper = QLinearGradient(0, _BADGE_LIFT, 0, self.height() - TAIL_HEIGHT - _SHADOW[1])
        paper.setColorAt(0.0, QColor(theme.SURFACE_WARM))
        paper.setColorAt(1.0, QColor(theme.BUBBLE_SPEECH))
        painter.fillPath(bubble, QBrush(paper))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(theme.INK), theme.BORDER))
        painter.drawPath(bubble)

        self._paint_paper(painter, bubble)
        self._paint_eye_badge(painter)
        self._paint_rose(painter)

        painter.end()
        super().paintEvent(event)

    def _body_rect(self) -> QRectF:
        """气泡本体矩形：右下角留出蔷薇探出边框的宽度。"""
        w, h = self.width(), self.height()
        dx, dy = _SHADOW
        return QRectF(1, _BADGE_LIFT, w - 2 - dx - _ROSE_LIP,
                      h - TAIL_HEIGHT - dy - _BADGE_LIFT)

    def _bubble_path(self) -> QPainterPath:
        """气泡轮廓：圆角本体 + 尾巴，尾根压进本体后取并集。"""
        h = self.height()
        dy = _SHADOW[1]
        body = self._body_rect()
        path = QPainterPath()
        path.addRoundedRect(body, theme.RADIUS_LG, theme.RADIUS_LG)

        cx = self._tail_x()
        root_y = body.bottom() - _TAIL_ROOT
        tip_y = h - dy
        tail = QPainterPath()
        tail.moveTo(cx - _TAIL_HALF_W, root_y)
        tail.cubicTo(cx - _TAIL_HALF_W - 6, root_y + 4,
                     cx - 4, tip_y - 4, cx, tip_y)
        tail.cubicTo(cx + 4, tip_y - 4,
                     cx + _TAIL_HALF_W + 6, root_y + 4, cx + _TAIL_HALF_W, root_y)
        tail.closeSubpath()
        return path.united(tail)

    def _paint_paper(self, painter: QPainter, bubble: QPainterPath) -> None:
        """纸感：顶部一层淡淡的黄绿受光，加极浅的纸纹颗粒。"""
        w, h = self.width(), self.height()
        painter.save()
        painter.setClipPath(bubble)
        painter.setPen(Qt.PenStyle.NoPen)

        light = QLinearGradient(0, _BADGE_LIFT, 0, _BADGE_LIFT + 13)
        top = QColor(theme.ACCENT)
        top.setAlpha(78)
        clear = QColor(theme.ACCENT)
        clear.setAlpha(0)
        light.setColorAt(0.0, top)
        light.setColorAt(1.0, clear)
        painter.setBrush(QBrush(light))
        painter.drawRect(QRectF(0, _BADGE_LIFT, w, 13))

        grain = QColor(theme.INK)
        grain.setAlpha(12)
        painter.setBrush(grain)
        for gy in range(_BADGE_LIFT + 7, h - TAIL_HEIGHT - 4, 15):
            for gx in range(8, w - 10, 15):
                if (gx // 15 * 3 + gy // 15 * 5) % 4:
                    continue
                painter.drawEllipse(QPointF(gx, gy), 0.6, 0.6)
        painter.restore()

    def _paint_eye_badge(self, painter: QPainter) -> None:
        """左上角骑边的第三只眼：淡粉圆徽章加一道闭着的眼睑。"""
        cx, cy = 17, _BADGE_LIFT
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(theme.THIRD_EYE))
        painter.setPen(QPen(QColor(theme.INK), 1.6))
        painter.drawEllipse(QPointF(cx, cy), _BADGE_R, _BADGE_R)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        line = QPen(QColor(theme.THIRD_EYE_LINE), 1.4)
        line.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(line)
        lid = QPainterPath()
        lid.moveTo(cx - 4.4, cy - 0.6)
        lid.quadTo(cx, cy + 3.4, cx + 4.4, cy - 0.6)
        painter.drawPath(lid)
        painter.drawLine(QPointF(cx - 4.6, cy - 0.4), QPointF(cx - 5.8, cy + 1.4))
        painter.drawLine(QPointF(cx + 4.6, cy - 0.4), QPointF(cx + 5.8, cy + 1.4))
        painter.restore()

    def _paint_rose(self, painter: QPainter) -> None:
        """右下角骑边的绿黑蔷薇：墨色剪影垫底，贴图压在上面。"""
        images = self._rose_images()
        if images is None:
            return
        rose, shadow = images
        body = self._body_rect()
        x = round(body.right() - _ROSE_INSET - _ROSE_SIZE / 2)
        y = round(body.bottom() - _ROSE_INSET - _ROSE_SIZE / 2)
        painter.drawPixmap(QPoint(x + _ROSE_DROP[0], y + _ROSE_DROP[1]), shadow)
        painter.drawPixmap(QPoint(x, y), rose)

    def _rose_images(self) -> tuple[QPixmap, QPixmap] | None:
        """按当前 DPR 缩放蔷薇与墨色剪影；换屏导致 DPR 变化时重建。"""
        ratio = self.devicePixelRatioF()
        pixel = max(1, round(_ROSE_SIZE * ratio))
        if self._rose_cache is not None and self._rose_cache[0].width() == pixel:
            return self._rose_cache
        source = QPixmap(str(_ROSE_PATH))
        if source.isNull():
            self._rose_cache = None
            return None
        source = source.scaled(pixel, pixel, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        shadow = QPixmap(source.size())
        shadow.fill(Qt.GlobalColor.transparent)
        painter = QPainter(shadow)
        painter.drawPixmap(0, 0, source)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(shadow.rect(), QColor(theme.INK))
        painter.end()
        source.setDevicePixelRatio(ratio)
        shadow.setDevicePixelRatio(ratio)
        self._rose_cache = (source, shadow)
        return self._rose_cache

    def _tail_x(self) -> int:
        """尾巴的横坐标：对准宠物可见主体，贴边时偏向贴边的那一侧。"""
        w = self.width()
        cx = w // 2
        pet = self.parent()
        if isinstance(pet, QWidget):
            side = getattr(pet, "dock_side", "") or ""
            if side in ("left", "right"):
                x0, _, visible, _ = edge_mask_rect(side, pet.width(), pet.height())
                cx = pet.x() + x0 + visible // 2 - self.x()
        margin = _TAIL_HALF_W + 7   # 心形尾巴鼓出的部分也不能出界
        return max(margin, min(cx, w - margin))

    def _sync_tail(self):
        """贴边侧变化后尾巴位置会变，位置没变时也要重绘。"""
        tail = self._tail_x()
        if tail != self._tail_x_cache:
            self._tail_x_cache = tail
            self.update()

    def _resize_to_text(self):
        text = self.text()
        self.setMinimumWidth(80)
        self.setMaximumWidth(config.BUBBLE_MAX_WIDTH)
        metrics = self.fontMetrics()
        # CSS 左右内边距 + 12px 缓冲：QLabel 换行判定比字形宽多要几像素
        text_w = metrics.horizontalAdvance(text) + _PAD_LEFT + _PAD_RIGHT + 12
        w = text_w if text_w <= config.BUBBLE_MAX_WIDTH else config.BUBBLE_MAX_WIDTH
        w = max(w, self.minimumWidth())
        self.setFixedWidth(w)
        # 尾巴高度已经在 padding-bottom 里，adjustSize 给出的就是气泡总高
        self.adjustSize()

    def _reposition(self):
        pet = self.parent()
        if isinstance(pet, QWidget) and self.isVisible():
            target = pet.geometry().center()
            final = self._final_position(target)
            self.move(final)
            self._sync_tail()

    def _start_tracking(self):
        self._follow_timer.start(50)

    def show_text(self, text: str, duration: int = 5000, parent_pos=None):
        """显示静态文本。气泡正在使用（含队列与过渡）时入队，保证先来先播。"""
        text = (text or "").strip()
        if not text:
            return
        self._flush_buffered()  # 缓冲中的句子比本句更早，且不能被 _play_text 抹掉
        if self._is_active() or self._speech_queue:
            self._enqueue(text, duration)
            return
        self._play_text(text, duration, parent_pos)

    def start_stream(self, parent_pos=None):
        """开始一句流式台词；气泡忙则收完 chunk 后以静态文本入队播放。"""
        self._close_sentence()
        self._stream_open = True
        if self._is_active() or self._speech_queue:
            self._buffering = True
            self._incoming_chunks.clear()
            return
        self._begin_display(parent_pos)

    def append_stream(self, chunk: str):
        """追加流式文本片段。"""
        chunk = chunk.replace("\r", "").replace("\n", "")  # 去掉换行符（某些 API 会发送 \r\n）
        if not chunk:
            return
        if not self._stream_open:
            # 没收到新的 start（工具轮次之间的空档）也自行开句，否则会拼进上一句
            self.start_stream()
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
            self._stream_open = False
            if text.strip():
                self._enqueue(text, duration)
            else:
                self._dispatch()
            return

        self._stream_open = False
        self._end_stream_duration = duration
        if self._char_queue:
            self._stream_ending = True
            if not self._type_timer.isActive():
                self._type_timer.start(self._type_interval)
        else:
            self._type_timer.stop()
            self._finish_stream()

    def finish_pending(self):
        """停止接收新内容：已收到的句子与队列继续播完，不清空。"""
        self._close_sentence()

    def discard_stream(self):
        """作废正在流式输出的这句：已流出的字与未接上的 chunk 都不再显示"""
        self._stream_open = False
        if self._buffering:
            self._buffering = False
            self._incoming_chunks.clear()
            return
        self._hide_bubble()
        self._dispatch()  # 队列里还有更早的句子就接着播

    def flush_pending(self):
        """清空待播队列与缓冲：窗口隐藏时调用，旧台词不该再冒出来。"""
        self._speech_queue.clear()
        self._incoming_chunks.clear()
        self._buffering = False
        self._stream_open = False
        self._stream_ending = False
        self._hide_bubble()

    def is_busy(self) -> bool:
        """气泡是否被占用：显示、打字中、过渡中或有排队文本。"""
        return self._is_active() or bool(self._speech_queue)

    def hide(self):
        """隐藏并清空待播内容：宠物窗口隐去后不该再弹出旧台词。"""
        self.flush_pending()

    def _busy(self) -> bool:
        """当前是否有正在进行的播放动作（含句子之间的过渡）。"""
        return (
            self._type_timer.isActive()
            or self._stream_ending
            or self._hide_timer.isActive()
            or self._gap_timer.isActive()
        )

    def _is_active(self) -> bool:
        """气泡当前是否正在使用（显示中、打字中或过渡中）。"""
        return self.isVisible() and self._busy()

    def _enqueue(self, text: str, duration: int):
        """将文本加入待播队列，并尝试启动播放。"""
        if not text:
            return
        self._speech_queue.append({"text": text, "duration": duration})
        while len(self._speech_queue) > _QUEUE_MAX:
            self._speech_queue.pop(0)
        self._dispatch()

    def _dispatch(self):
        """播下一条的唯一入口；空闲时才动，避免多处各自调度导致重叠。"""
        if self._busy():
            return
        if not self._speech_queue:
            self._hide_bubble()
            return
        self._gap_timer.start(_GAP_MS)

    def _flush_buffered(self):
        """把缓冲中的整句补进待播队列：清 `_incoming_chunks` 之前都得先走这里。"""
        if not self._buffering:
            return
        text = "".join(self._incoming_chunks)
        self._incoming_chunks.clear()
        self._buffering = False
        self._stream_open = False
        if text.strip():
            self._enqueue(text, self._end_stream_duration)

    def _on_gap_timeout(self):
        """过渡结束：先收掉还在缓冲的句子，再取队首播放，保证先来先播。"""
        self._flush_buffered()
        if self._busy() or not self._speech_queue:
            self._dispatch()
            return
        item = self._speech_queue.pop(0)
        self._play_text(item["text"], item["duration"])

    def _on_hide_timer(self):
        """hide 定时器到期：交给统一调度决定播下一条还是隐藏。"""
        self._dispatch()

    def _close_sentence(self):
        """收掉一句尚未收尾的流式台词，缓冲中的文本补进队列，不丢句。"""
        self._flush_buffered()
        if self._stream_open:
            self._stream_ending = True  # 剩下的字打完再收尾，别被下一句抹掉
            if not self._type_timer.isActive():
                self._type_timer.start(self._type_interval)
        self._stream_open = False

    def _play_text(self, text: str, duration: int, parent_pos=None):
        """播放一条静态文本（带打字机效果与浮出动画）。"""
        self._flush_buffered()
        self._finish_anim()
        self._type_timer.stop()
        self._hide_timer.stop()
        self._gap_timer.stop()

        self._stream_buffer = ""
        self._char_queue.clear()
        self._stream_ending = False

        if parent_pos is None and isinstance(self.parent(), QWidget):
            parent_pos = self.parent().geometry().center()

        self.setText("")
        self._resize_to_text()
        self.show()

        if parent_pos is not None:
            self.move(self._head_position(parent_pos))
            self._start_enter_anim(self._final_position(parent_pos))
        else:
            self._start_tracking()

        self._end_stream_duration = duration
        self._char_queue.extend(text)
        self._stream_ending = True
        self._type_timer.start(self._type_interval)

    def _begin_display(self, parent_pos=None):
        """开始实时显示一句流式台词（不走浮出动画，紧跟宠物）。"""
        self._type_timer.stop()

        self._stream_buffer = ""
        self._char_queue.clear()
        self._stream_ending = False
        self.setText("")
        self._resize_to_text()

        if parent_pos is None and isinstance(self.parent(), QWidget):
            parent_pos = self.parent().geometry().center()
        if parent_pos is not None:
            self.move(self._final_position(parent_pos))

        self.show()
        self._start_tracking()

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
        """打字结束后的收尾：启动 hide 定时器，到期由统一调度接手。"""
        self._finish_anim()
        self._hide_timer.start(self._end_stream_duration)

    def _start_enter_anim(self, final_pos: QPoint):
        """从宠物头顶浮到最终位置；动画结束后接管跟随。"""
        self.setWindowOpacity(0.0)
        group = QParallelAnimationGroup(self)

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

        group.addAnimation(pos_anim)
        group.addAnimation(opacity_anim)
        group.finished.connect(self._on_anim_finished)
        self._anim_group = group
        group.start()

    def _on_anim_finished(self):
        self._finish_anim()

    def _finish_anim(self):
        """动画收尾：自然走完与被提前打断（短句打字更快）都走这里。"""
        group = self._anim_group
        self._anim_group = None
        if group is None:
            return
        group.stop()
        self.setWindowOpacity(1.0)
        self._reposition()
        self._follow_timer.start(50)
        group.deleteLater()

    def _hide_bubble(self):
        """彻底停下：定时器全停、残留文本清掉，隐藏后不再被回调唤醒。"""
        self._follow_timer.stop()
        self._type_timer.stop()
        self._hide_timer.stop()
        self._gap_timer.stop()
        self._char_queue.clear()
        self._stream_buffer = ""
        self._stream_ending = False
        self._finish_anim()
        self.setWindowOpacity(1.0)
        self.setText("")
        super().hide()

    def _clamp_to_pet_screen(self, pos: QPoint) -> QPoint:
        """夹到宠物所在屏幕：副屏原点不是 (0,0)，且可能为负坐标。"""
        pet = self.parent()
        screen = pet.screen() if isinstance(pet, QWidget) else None
        if screen is None:
            return pos
        geo = screen.availableGeometry()
        x = max(geo.left(), min(pos.x(), geo.right() - self.width()))
        y = max(geo.top(), min(pos.y(), geo.bottom() - self.height()))
        return QPoint(x, y)

    def _head_position(self, target_pos: QPoint) -> QPoint:
        pet_top = target_pos.y() - config.PET_HEIGHT // 2
        head_bottom = pet_top + config.PET_HEIGHT // 3
        x = target_pos.x() - self.width() // 2
        y = head_bottom - self.height()
        return self._clamp_to_pet_screen(QPoint(x, y))

    def _final_position(self, target_pos: QPoint) -> QPoint:
        x = target_pos.x() - self.width() // 2
        pet_top = target_pos.y() - config.PET_HEIGHT // 2
        y = pet_top - self.height() - 15
        return self._clamp_to_pet_screen(QPoint(x, y))

    def _follow_pet(self):
        pet = self.parent()
        if isinstance(pet, QWidget) and self.isVisible():
            target = pet.geometry().center()
            final = self._final_position(target)
            self.move(final)
            self._sync_tail()
