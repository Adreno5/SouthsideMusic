import logging
import time
from typing import cast, override

from qfluentwidgets import ListWidget, ScrollBar, SmoothScrollArea, TextEdit
from qfluentwidgets.components.widgets.list_view import ListItemDelegate

from core import config
from core.icons import CachedFluentIcon
from core.models import AnimatingObject
from core.smooth import EaseOutTimer
from imports import (
    REFRESH_RATE_CHANGED,
    Property,
    QAbstractItemView,
    QAbstractScrollArea,
    QColor,
    QEasingCurve,
    QEvent,
    QFont,
    QLinearGradient,
    QListView,
    QListWidgetItem,
    QModelIndex,
    QMouseEvent,
    QObject,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QPoint,
    QPropertyAnimation,
    QResizeEvent,
    QSize,
    QStyleOptionViewItem,
    Qt,
    QTimer,
    QWheelEvent,
    QWidget,
    event_bus,
)
from services.events import LIST_SCROLLING_DURATION_CHANGED, REPAINT


def setTransparentBackground(widget: QWidget | None) -> None:
    if widget is None:
        return
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
    widget.setAutoFillBackground(False)

    palette = widget.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 0))
    palette.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
    widget.setPalette(palette)


def _debugging_enabled(widget: QWidget) -> bool:
    obj = widget
    while obj is not None:
        ctx = getattr(obj, 'ctx', None)
        if ctx is not None:
            return bool(getattr(ctx, 'debugging', False))
        parent = obj.parent()
        obj = parent if isinstance(parent, QWidget) else None
    return False


class SSmoothScrollBar(ScrollBar):
    def __init__(self, orientation: Qt.Orientation, parent: QAbstractScrollArea):
        super().__init__(orientation, parent)
        for button in (self.groove.upButton, self.groove.downButton):
            button._icon = CachedFluentIcon(button._icon)
        self._logger = logging.getLogger(__name__)
        self._area = parent
        self.animating_objs: list[AnimatingObject] = []
        self.refresh_rate = max(60, parent.window().screen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')
        self.delta = 1 / self.refresh_rate
        self.last_draw: int = time.perf_counter_ns()
        self._scroll_remainder = 0.0
        self.debug_forces: list[float] = []
        self.debug_total_force = 0.0
        self.debug_offset = 0.0
        self.debug_offset_target = 0.0
        self.duration = config.cfg.scroll_duration

        self.origin_setValue = self.setValue
        self.setValue = self._patched_setValue

        event_bus.subscribe(REFRESH_RATE_CHANGED, self._onRefreshRateChanged)
        event_bus.subscribe(
            LIST_SCROLLING_DURATION_CHANGED, lambda v: setattr(self, 'duration', int(v))
        )
        event_bus.subscribe(REPAINT, self._tick)

    def _patched_setValue(self, value: int):
        self.scrollValue(value - self.value())

    def mousePressEvent(self, e: QMouseEvent):
        super().mousePressEvent(e)
        self._isPressed = True
        self._pressedPos = e.pos()

        if self.childAt(e.pos()) is self.handle or not self._isSlideResion(e.pos()):
            return

        if self.orientation() == Qt.Orientation.Vertical:
            if e.pos().y() > self.handle.geometry().bottom():
                value = e.pos().y() - self.handle.height() - self._padding
            else:
                value = e.pos().y() - self._padding
        else:
            if e.pos().x() > self.handle.geometry().right():
                value = e.pos().x() - self.handle.width() - self._padding
            else:
                value = e.pos().x() - self._padding

        self.setValue(int(value / max(self._slideLength(), 1) * self.maximum()))
        self.sliderPressed.emit()

    def mouseMoveEvent(self, e: QMouseEvent):
        if self.orientation() == Qt.Orientation.Vertical:
            dv = e.pos().y() - self._pressedPos.y()
        else:
            dv = e.pos().x() - self._pressedPos.x()

        dv = int(dv / max(self._slideLength(), 1) * (self.maximum() - self.minimum()))
        self.setValue(self.value() + dv)

        self._pressedPos = e.pos()
        self.sliderMoved.emit()

    def _onRefreshRateChanged(self) -> None:
        screen = self.window().screen()
        if screen is None:
            return
        self.refresh_rate = max(60, screen.refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')
        self.delta = 1 / self.refresh_rate

    @staticmethod
    def _smoothstep(t: float) -> float:
        t = max(0.0, min(1.0, t))
        return t * t * (3.0 - 2.0 * t)

    def _tick(self, _: float) -> None:
        now = time.perf_counter_ns()
        if not self.animating_objs and not self.debug_forces:
            self.last_draw = now
            return
        elapsed = min((now - self.last_draw) / 1_000_000_000, 0.1)
        self.last_draw = now
        multiple_factor = elapsed * self.refresh_rate

        new: list[AnimatingObject] = []
        total_delta = 0.0
        forces: list[float] = []
        for obj in self.animating_objs:
            obj.elapsed += self.delta * 1000 * multiple_factor
            progress = self._smoothstep(obj.elapsed / obj.duration)
            force = obj.total * (progress - obj.last_progress)
            forces.append(force)
            total_delta += force
            obj.last_progress = progress
            if obj.elapsed < obj.duration:
                new.append(obj)
        self.animating_objs = new
        if total_delta != 0:
            next_value = self.value() + total_delta + self._scroll_remainder
            final_value = int(next_value)
            self._scroll_remainder = next_value - final_value
            self.origin_setValue(final_value)
        self.debug_forces = forces
        self.debug_total_force = total_delta
        self.debug_offset = float(self.value())
        self.debug_offset_target = (
            self.debug_offset
            + self._scroll_remainder
            + sum(obj.total * (1.0 - obj.last_progress) for obj in self.animating_objs)
        )
        if _debugging_enabled(self._area):
            overlay = getattr(self._area, '_overlay', None)
            if overlay is not None:
                overlay.update()

    def scrollValue(self, delta: int) -> None:
        if delta == 0:
            return
        if not self.animating_objs:
            self.last_draw = time.perf_counter_ns()
        self.animating_objs.append(
            AnimatingObject(
                total=float(delta),
                elapsed=0.0,
                duration=self.duration,
                last_progress=0.0,
            )
        )


class SSmoothDelegate(QObject):
    def __init__(self, parent: 'SListWidget | SScrollArea | TextEdit'):
        super().__init__(parent)
        self.par = parent
        self.vScrollBar = SSmoothScrollBar(Qt.Orientation.Vertical, parent)
        self.hScrollBar = SSmoothScrollBar(Qt.Orientation.Horizontal, parent)

        if isinstance(parent, QAbstractItemView):
            parent.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            parent.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        if isinstance(parent, QListView):
            parent.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
            parent.horizontalScrollBar().setStyleSheet(
                'QScrollBar:horizontal{height: 0px}'
            )

        parent.viewport().installEventFilter(self)
        parent.setVerticalScrollBarPolicy = self.setVerticalScrollBarPolicy
        parent.setHorizontalScrollBarPolicy = self.setHorizontalScrollBarPolicy

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if isinstance(e, QWheelEvent):
            delta = e.angleDelta()
            if e.modifiers() & Qt.KeyboardModifier.AltModifier and not delta.x():
                delta = QPoint(delta.y(), 0)
            vdlimited = (
                delta.y() < 0
                and self.vScrollBar.value() == self.vScrollBar.maximum()
            )
            vulimited = (
                delta.y() > 0
                and self.vScrollBar.value() == self.vScrollBar.minimum()
            )

            hdlimited = (
                delta.x() < 0
                and self.hScrollBar.value() == self.hScrollBar.maximum()
            )
            hulimited = (
                delta.x() > 0
                and self.hScrollBar.value() == self.hScrollBar.minimum()
            )

            if (vdlimited or vulimited or hdlimited or hulimited) and not isinstance(
                self.par, TextEdit
            ):
                if vulimited:
                    self.par._trigger_limit_anim(self.par._top_anim)
                if vdlimited:
                    self.par._trigger_limit_anim(self.par._bot_anim)
                if hulimited:
                    self.par._trigger_limit_anim(self.par._left_anim)
                if hdlimited:
                    self.par._trigger_limit_anim(self.par._right_anim)
                e.setAccepted(True)
                return True

            if delta.y() != 0:
                self.vScrollBar.scrollValue(-delta.y())
            else:
                self.hScrollBar.scrollValue(-delta.x())

            e.setAccepted(True)
            return True

        return super().eventFilter(obj, e)

    def setVerticalScrollBarPolicy(self, policy):
        QAbstractScrollArea.setVerticalScrollBarPolicy(
            self.parent(),  # type: ignore
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self.vScrollBar.setForceHidden(policy == Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def setHorizontalScrollBarPolicy(self, policy):
        QAbstractScrollArea.setHorizontalScrollBarPolicy(
            self.parent(),  # type: ignore
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self.hScrollBar.setForceHidden(policy == Qt.ScrollBarPolicy.ScrollBarAlwaysOff)


class LimitOverlay(QWidget):
    def __init__(self, parent: 'SListWidget | SScrollArea'):
        super().__init__(parent)
        self.par = parent
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.db_ft = QFont()
        self.db_ft.setPointSize(10)
        self.show()

    def paintEvent(self, event: QPaintEvent) -> None:
        tl = self.par.tlmtimer.current_value
        bl = self.par.blmtimer.current_value
        ll = self.par.llmtimer.current_value
        rl = self.par.rlmtimer.current_value
        debugging = _debugging_enabled(self.par)

        if tl <= 0 and bl <= 0 and ll <= 0 and rl <= 0 and not debugging:
            return

        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setPen(Qt.PenStyle.NoPen)

        h = self.height()
        w = self.width()
        if h == 0 or w == 0:
            painter.end()
            return

        top_h = max(1, int(h * 0.1))
        bot_h = max(1, int(h * 0.1))
        left_w = max(1, int(w * 0.1))
        right_w = max(1, int(w * 0.1))

        if tl > 0:
            gra = QLinearGradient(0, 0, 0, top_h)
            gra.setColorAt(0.0, QColor(128, 128, 128, int(tl * 110)))
            gra.setColorAt(1.0, QColor(128, 128, 128, 0))
            painter.setBrush(gra)
            painter.drawRect(0, 0, w, top_h)

        if bl > 0:
            gra = QLinearGradient(0, h, 0, h - bot_h)
            gra.setColorAt(0.0, QColor(128, 128, 128, int(bl * 110)))
            gra.setColorAt(1.0, QColor(128, 128, 128, 0))
            painter.setBrush(gra)
            painter.drawRect(0, h - bot_h, w, bot_h)

        if ll > 0:
            gra = QLinearGradient(0, 0, left_w, 0)
            gra.setColorAt(0.0, QColor(128, 128, 128, int(ll * 110)))
            gra.setColorAt(1.0, QColor(128, 128, 128, 0))
            painter.setBrush(gra)
            painter.drawRect(0, 0, left_w, h)

        if rl > 0:
            gra = QLinearGradient(w, 0, w - right_w, 0)
            gra.setColorAt(0.0, QColor(128, 128, 128, int(rl * 110)))
            gra.setColorAt(1.0, QColor(128, 128, 128, 0))
            painter.setBrush(gra)
            painter.drawRect(w - right_w, 0, right_w, h)

        if debugging:
            self._paint_debug_overlay(painter)

        painter.end()

    def _paint_debug_overlay(self, painter: QPainter) -> None:
        delegate = getattr(self.par, 'scrollDelegate', None) or getattr(
            self.par, 'delegate', None
        )
        if delegate is None:
            return
        bar = getattr(delegate, 'vScrollBar', None)
        if bar is None:
            return

        center = self.height() // 2
        force_left = self.width() - 400
        force_right = self.width() - 200
        offset_left = force_right
        offset_right = self.width()

        painter.setFont(self.db_ft)
        painter.setPen(QPen(QColor(160, 160, 160), 1))
        painter.drawLine(force_left, center, force_right, center)
        force_colors = (
            QColor(255, 120, 120),
            QColor(255, 180, 80),
            QColor(255, 255, 80),
            QColor(120, 255, 120),
            QColor(120, 255, 255),
            QColor(120, 120, 255),
        )
        bar_gap = 0
        bar_width = 8
        x = force_left
        for i, force in enumerate(bar.debug_forces):
            delta_ = int(force) + center
            painter.setPen(QPen(force_colors[i % len(force_colors)], bar_width))
            painter.drawLine(x, center, x, delta_)
            x += bar_width + bar_gap

        x += bar_gap
        painter.setPen(QPen(QColor(255, 75, 255), bar_width + 2))
        delta_ = int(bar.debug_total_force) + center
        painter.drawLine(x, center, x, delta_)
        painter.setPen(QPen(QColor(255, 75, 255), 1))
        painter.drawText(x + 5, delta_ + 15, f'Force Sum: {bar.debug_total_force:.2f}')

        painter.setPen(QPen(QColor(255, 120, 120), 1))
        offset_delta = bar.debug_offset_target - bar.debug_offset
        delta_ = int(offset_delta) + center
        painter.drawLine(offset_left, delta_, offset_right, delta_)
        painter.drawText(offset_left, delta_ + 15, 'Offset Target')

        painter.setPen(QPen(QColor(120, 255, 255), 1))
        painter.drawLine(offset_left, center, offset_right, center)
        painter.drawText(offset_left, center + 15, 'Offset')


class SListItemDelegate(ListItemDelegate):
    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        lst = cast('SListWidget', self.parent())
        item = lst.itemFromIndex(index)
        if item is None or item.sizeHint().isValid():
            return super().sizeHint(option, index)
        widget = lst.itemWidget(item)
        if widget is None or not widget.hasHeightForWidth():
            return super().sizeHint(option, index)
        margins = lst.contentsMargins()
        width = max(
            1,
            lst.viewport().width()
            - margins.left()
            - margins.right()
            - 2 * lst.spacing(),
        )
        return QSize(0, max(widget.minimumHeight(), widget.heightForWidth(width)))


class SListWidget(ListWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setItemDelegate(SListItemDelegate(self))
        self.setResizeMode(QListView.ResizeMode.Adjust)
        setTransparentBackground(self)
        setTransparentBackground(self.viewport())

        self._top_limit = 0.0
        self._bot_limit = 0.0
        self._left_limit = 0.0
        self._right_limit = 0.0

        self.tlmtimer = EaseOutTimer(0.5, 3)
        self.blmtimer = EaseOutTimer(0.5, 3)
        self.llmtimer = EaseOutTimer(0.5, 3)
        self.rlmtimer = EaseOutTimer(0.5, 3)
        self.tlmtimer.target_value = 0.0
        self.blmtimer.target_value = 0.0
        self.llmtimer.target_value = 0.0
        self.rlmtimer.target_value = 0.0

        self._top_anim = self._create_guide_anim(b'topGuide')
        self._bot_anim = self._create_guide_anim(b'botGuide')
        self._left_anim = self._create_guide_anim(b'leftGuide')
        self._right_anim = self._create_guide_anim(b'rightGuide')

        self._overlay = LimitOverlay(self)
        self._sync_overlay()

        self.scrollDelegate = SSmoothDelegate(self)
        self.viewport().installEventFilter(self)

        self.ltimer = QTimer(self)
        self.ltimer.timeout.connect(self._tick)
        self.ltimer.start(16)

    @override
    def setItemWidget(self, item: QListWidgetItem, widget: QWidget) -> None:
        super().setItemWidget(item, widget)
        widget.installEventFilter(self)
        self.scheduleDelayedItemsLayout()

    @override
    def doItemsLayout(self) -> None:
        super().doItemsLayout()
        self.updateEditorGeometries()

    def _create_guide_anim(self, prop: bytes) -> QPropertyAnimation:
        anim = QPropertyAnimation(self, prop)
        anim.setDuration(800)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        return anim

    def _trigger_limit_anim(self, anim: QPropertyAnimation) -> None:
        anim.stop()
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.start()

    @Property(float)
    def topGuide(self) -> float:  # type: ignore
        return self._top_limit

    @topGuide.setter
    def topGuide(self, value: float) -> None:
        self._top_limit = value
        self.tlmtimer.target_value = value

    @Property(float)
    def botGuide(self) -> float:  # type: ignore
        return self._bot_limit

    @botGuide.setter
    def botGuide(self, value: float) -> None:
        self._bot_limit = value
        self.blmtimer.target_value = value

    @Property(float)
    def leftGuide(self) -> float:  # type: ignore
        return self._left_limit

    @leftGuide.setter
    def leftGuide(self, value: float) -> None:
        self._left_limit = value
        self.llmtimer.target_value = value

    @Property(float)
    def rightGuide(self) -> float:  # type: ignore
        return self._right_limit

    @rightGuide.setter
    def rightGuide(self, value: float) -> None:
        self._right_limit = value
        self.rlmtimer.target_value = value

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if obj is self.viewport() and isinstance(e, QResizeEvent):
            self._sync_overlay()
            self.scheduleDelayedItemsLayout()
        elif e.type() == QEvent.Type.LayoutRequest:
            self.scheduleDelayedItemsLayout()
        return super().eventFilter(obj, e)

    def _sync_overlay(self) -> None:
        vp = self.viewport()
        if vp:
            self._overlay.setGeometry(vp.geometry())
            self._overlay.raise_()

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        self._sync_overlay()

    def _tick(self):
        if (
            self.tlmtimer.is_animating
            or self.blmtimer.is_animating
            or self.llmtimer.is_animating
            or self.rlmtimer.is_animating
        ):
            self._overlay.update()


class SScrollArea(SmoothScrollArea):
    def __init__(self):
        super().__init__()
        setTransparentBackground(self)
        setTransparentBackground(self.viewport())
        self.delegate = SSmoothDelegate(self)

        self._top_limit = 0.0
        self._bot_limit = 0.0
        self._left_limit = 0.0
        self._right_limit = 0.0

        self.tlmtimer = EaseOutTimer(0.5, 3)
        self.blmtimer = EaseOutTimer(0.5, 3)
        self.llmtimer = EaseOutTimer(0.5, 3)
        self.rlmtimer = EaseOutTimer(0.5, 3)
        self.tlmtimer.target_value = 0.0
        self.blmtimer.target_value = 0.0
        self.llmtimer.target_value = 0.0
        self.rlmtimer.target_value = 0.0

        self._top_anim = self._create_guide_anim(b'topGuide')
        self._bot_anim = self._create_guide_anim(b'botGuide')
        self._left_anim = self._create_guide_anim(b'leftGuide')
        self._right_anim = self._create_guide_anim(b'rightGuide')

        self._overlay = LimitOverlay(self)
        self._sync_overlay()

        self.viewport().installEventFilter(self)

        self.ltimer = QTimer(self)
        self.ltimer.timeout.connect(self._tick)
        self.ltimer.start(16)

    def _create_guide_anim(self, prop: bytes) -> QPropertyAnimation:
        anim = QPropertyAnimation(self, prop)
        anim.setDuration(800)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        return anim

    def setWidget(self, widget: QWidget | None) -> None:
        setTransparentBackground(widget)
        super().setWidget(widget)  # type: ignore
        setTransparentBackground(widget)

    def _trigger_limit_anim(self, anim: QPropertyAnimation) -> None:
        anim.stop()
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.start()

    @Property(float)
    def topGuide(self) -> float:  # type: ignore
        return self._top_limit

    @topGuide.setter
    def topGuide(self, value: float) -> None:
        self._top_limit = value
        self.tlmtimer.target_value = value

    @Property(float)
    def botGuide(self) -> float:  # type: ignore
        return self._bot_limit

    @botGuide.setter
    def botGuide(self, value: float) -> None:
        self._bot_limit = value
        self.blmtimer.target_value = value

    @Property(float)
    def leftGuide(self) -> float:  # type: ignore
        return self._left_limit

    @leftGuide.setter
    def leftGuide(self, value: float) -> None:
        self._left_limit = value
        self.llmtimer.target_value = value

    @Property(float)
    def rightGuide(self) -> float:  # type: ignore
        return self._right_limit

    @rightGuide.setter
    def rightGuide(self, value: float) -> None:
        self._right_limit = value
        self.rlmtimer.target_value = value

    def eventFilter(self, obj, e: QEvent) -> bool:
        if obj is self.viewport() and isinstance(e, QResizeEvent):
            self._sync_overlay()
        return super().eventFilter(obj, e)

    def _sync_overlay(self) -> None:
        vp = self.viewport()
        if vp:
            self._overlay.setGeometry(vp.geometry())
            self._overlay.raise_()

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        self._sync_overlay()

    def _tick(self):
        if (
            self.tlmtimer.is_animating
            or self.blmtimer.is_animating
            or self.llmtimer.is_animating
            or self.rlmtimer.is_animating
        ):
            self._overlay.update()
