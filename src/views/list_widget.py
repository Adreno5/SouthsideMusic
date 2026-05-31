from typing import TypedDict
from core.smooth import EaseOutTimer
from imports import (
    QAbstractItemView,
    QAbstractScrollArea,
    QColor,
    QEasingCurve,
    QEvent,
    QLinearGradient,
    QListView,
    QObject,
    QPaintEvent,
    QPainter,
    QPropertyAnimation,
    QResizeEvent,
    QTimer,
    QWidget,
    Qt,
    QWheelEvent,
    Property,
)
from qfluentwidgets import ListWidget, ScrollBar


class AnimatingObject(TypedDict):
    total: float
    elapsed: float
    duration: float
    last_progress: float


class SSmoothScrollBar(ScrollBar):
    def __init__(self, orientation: Qt.Orientation, parent: QAbstractScrollArea):
        super().__init__(orientation, parent)
        self.animating_objs: list[AnimatingObject] = []

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._tick)
        self.anim_timer.start(16)

    @staticmethod
    def _smoothstep(t: float) -> float:
        t = max(0.0, min(1.0, t))
        return t * t * (3.0 - 2.0 * t)

    def _tick(self):
        new: list[AnimatingObject] = []
        total_delta = 0.0
        for obj in self.animating_objs:
            obj['elapsed'] += 16
            progress = self._smoothstep(obj['elapsed'] / obj['duration'])
            total_delta += obj['total'] * (progress - obj['last_progress'])
            obj['last_progress'] = progress
            if obj['elapsed'] < obj['duration']:
                new.append(obj)
        self.animating_objs = new
        if total_delta != 0:
            self.setValue(int(self.value() + total_delta))

    def scrollValue(self, delta: int):
        self.animating_objs.append(
            {
                'total': float(delta),
                'elapsed': 0.0,
                'duration': 250.0,
                'last_progress': 0.0,
            }
        )


class SSmoothDelegate(QObject):
    def __init__(self, parent: 'SListWidget'):
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

    def eventFilter(self, obj, e: QEvent):
        if isinstance(e, QWheelEvent):
            vdlimited = (
                e.angleDelta().y() < 0
                and self.vScrollBar.value() == self.vScrollBar.maximum()
            )
            vulimited = (
                e.angleDelta().y() > 0
                and self.vScrollBar.value() == self.vScrollBar.minimum()
            )

            hdlimited = (
                e.angleDelta().x() < 0
                and self.hScrollBar.value() == self.hScrollBar.maximum()
            )
            hulimited = (
                e.angleDelta().x() > 0
                and self.hScrollBar.value() == self.hScrollBar.minimum()
            )

            if vdlimited or vulimited or hdlimited or hulimited:
                if vulimited:
                    self.par._trigger_limit_anim(self.par._top_anim)
                if vdlimited:
                    self.par._trigger_limit_anim(self.par._bot_anim)
                if hulimited:
                    self.par._trigger_limit_anim(self.par._left_anim)
                if hdlimited:
                    self.par._trigger_limit_anim(self.par._right_anim)
                return False

            if e.angleDelta().y() != 0:
                self.vScrollBar.scrollValue(-e.angleDelta().y())
            else:
                self.hScrollBar.scrollValue(-e.angleDelta().x())

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
    def __init__(self, parent: 'SListWidget'):
        super().__init__(parent)
        self.par = parent
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.show()

    def paintEvent(self, event: QPaintEvent) -> None:
        tl = self.par.tlmtimer.current_value
        bl = self.par.blmtimer.current_value
        ll = self.par.llmtimer.current_value
        rl = self.par.rlmtimer.current_value

        if tl <= 0 and bl <= 0 and ll <= 0 and rl <= 0:
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
            gra.setColorAt(0.0, QColor(255, 60, 60, int(tl * 200)))
            gra.setColorAt(1.0, QColor(255, 60, 60, 0))
            painter.setBrush(gra)
            painter.drawRect(0, 0, w, top_h)

        if bl > 0:
            gra = QLinearGradient(0, h, 0, h - bot_h)
            gra.setColorAt(0.0, QColor(255, 60, 60, int(bl * 200)))
            gra.setColorAt(1.0, QColor(255, 60, 60, 0))
            painter.setBrush(gra)
            painter.drawRect(0, h - bot_h, w, bot_h)

        if ll > 0:
            gra = QLinearGradient(0, 0, left_w, 0)
            gra.setColorAt(0.0, QColor(255, 60, 60, int(ll * 200)))
            gra.setColorAt(1.0, QColor(255, 60, 60, 0))
            painter.setBrush(gra)
            painter.drawRect(0, 0, left_w, h)

        if rl > 0:
            gra = QLinearGradient(w, 0, w - right_w, 0)
            gra.setColorAt(0.0, QColor(255, 60, 60, int(rl * 200)))
            gra.setColorAt(1.0, QColor(255, 60, 60, 0))
            painter.setBrush(gra)
            painter.drawRect(w - right_w, 0, right_w, h)

        painter.end()


class SListWidget(ListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

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
    def topGuide(self) -> float: # type: ignore
        return self._top_limit

    @topGuide.setter
    def topGuide(self, value: float) -> None:
        self._top_limit = value
        self.tlmtimer.target_value = value

    @Property(float)
    def botGuide(self) -> float: # type: ignore
        return self._bot_limit

    @botGuide.setter
    def botGuide(self, value: float) -> None:
        self._bot_limit = value
        self.blmtimer.target_value = value

    @Property(float)
    def leftGuide(self) -> float: # type: ignore
        return self._left_limit

    @leftGuide.setter
    def leftGuide(self, value: float) -> None:
        self._left_limit = value
        self.llmtimer.target_value = value

    @Property(float)
    def rightGuide(self) -> float: # type: ignore
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
