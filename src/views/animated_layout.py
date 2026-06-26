from __future__ import annotations

import shiboken6

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtWidgets import (
    QLayout,
    QLayoutItem,
    QSizePolicy,
    QSpacerItem,
    QWidget,
    QWidgetItem,
)
from imports import (
    QAbstractAnimation,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QTimer,
    QVBoxLayout,
)


def _slideWidgetIn(
    layout: QObject,
    widget: QWidget,
    duration: int,
    easing: QEasingCurve.Type,
) -> None:
    if not shiboken6.isValid(widget):
        return
    target_pos = widget.pos()
    widget_width = max(widget.width(), 1)
    widget.move(-widget_width, target_pos.y())
    widget.show()

    anim = QPropertyAnimation(widget, b'pos', layout)
    anim.setDuration(duration)
    anim.setEasingCurve(easing)
    anim.setStartValue(QPoint(-widget_width, target_pos.y()))
    anim.setEndValue(target_pos)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


class SVAnimatedLayout(QVBoxLayout):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._slide_duration: int = 300
        self._slide_easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic

    def setAnimation(self, duration: int, curve: QEasingCurve.Type):
        self._slide_duration = duration
        self._slide_easing = curve

    def addWidget(  # type: ignore[override]
        self,
        widget: QWidget,
        stretch: int = 0,
        alignment: Qt.AlignmentFlag | None = None,
    ) -> None:
        if alignment is None:
            super().addWidget(widget, stretch)
        else:
            super().addWidget(widget, stretch, alignment)
        QTimer.singleShot(
            0,
            lambda w=widget: _slideWidgetIn(
                self, w, self._slide_duration, self._slide_easing
            ),
        )

    def insertWidget(  # type: ignore[override]
        self,
        index: int,
        widget: QWidget,
        stretch: int = 0,
        alignment: Qt.AlignmentFlag | None = None,
    ) -> None:
        if alignment is None:
            super().insertWidget(index, widget, stretch)
        else:
            super().insertWidget(index, widget, stretch, alignment)
        QTimer.singleShot(
            0,
            lambda w=widget: _slideWidgetIn(
                self, w, self._slide_duration, self._slide_easing
            ),
        )


class SFlowLayout(QLayout):
    def __init__(
        self,
        parent: QWidget | None = None,
        needAni: bool = True,
        isTight: bool = False,
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._anis: list[QPropertyAnimation] = []
        self._vSpacing = 10
        self._hSpacing = 10
        self._slideDuration = 300
        self._slideEasing: QEasingCurve.Type = QEasingCurve.Type.OutCubic
        self._reflowDuration = 300
        self._reflowEasing: QEasingCurve.Type = QEasingCurve.Type.Linear
        self._needAni = needAni
        self._isTight = isTight
        self._deBounceTimer = QTimer(self)
        self._deBounceTimer.setSingleShot(True)
        self._deBounceTimer.timeout.connect(
            lambda: self._doLayout(self.geometry(), True)
        )
        self._wParent: QWidget | None = None
        self._eventFilterInstalled = False

    def setAnimation(
        self, duration: int, ease: QEasingCurve.Type = QEasingCurve.Type.Linear
    ) -> None:
        if not self._needAni:
            return
        self._reflowDuration = duration
        self._reflowEasing = ease
        for ani in self._anis:
            ani.setDuration(duration)
            ani.setEasingCurve(ease)

    def setSlideAnimation(
        self, duration: int, ease: QEasingCurve.Type = QEasingCurve.Type.OutCubic
    ) -> None:
        self._slideDuration = duration
        self._slideEasing = ease

    def setVerticalSpacing(self, spacing: int) -> None:
        self._vSpacing = spacing

    def verticalSpacing(self) -> int:
        return self._vSpacing

    def setHorizontalSpacing(self, spacing: int) -> None:
        self._hSpacing = spacing

    def horizontalSpacing(self) -> int:
        return self._hSpacing

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def addWidget(  # type: ignore[override]
        self,
        widget: QWidget,
        stretch: int = 0,
        alignment: Qt.AlignmentFlag | None = None,
    ) -> None:
        self.addItem(QWidgetItem(widget))
        self.addChildWidget(widget)
        if alignment is not None:
            self._items[-1].setAlignment(alignment)
        self._onWidgetAdded(widget)

    def insertWidget(  # type: ignore[override]
        self,
        index: int,
        widget: QWidget,
        stretch: int = 0,
        alignment: Qt.AlignmentFlag | None = None,
    ) -> None:
        item = QWidgetItem(widget)
        self._items.insert(index, item)
        self.addChildWidget(widget)
        if alignment is not None:
            item.setAlignment(alignment)
        self._onWidgetAdded(widget, index)

    def addStretch(self, stretch: int = 0) -> None:
        item = QSpacerItem(
            0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self.addItem(item)

    def addSpacing(self, size: int) -> None:
        item = QSpacerItem(
            size, size, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.addItem(item)

    def _onWidgetAdded(self, widget: QWidget, index: int = -1) -> None:
        if not self._eventFilterInstalled:
            p = widget.parent()
            if p is not None:
                self._wParent = p  # type: ignore[assignment]
                p.installEventFilter(self)
            else:
                widget.installEventFilter(self)

        if not self._needAni:
            return

        ani = QPropertyAnimation(widget, b'geometry')
        ani.setStartValue(QRect(QPoint(0, 0), QSize(0, 0)))
        ani.setEndValue(QRect(QPoint(0, 0), widget.sizeHint()))
        ani.setDuration(self._reflowDuration)
        ani.setEasingCurve(self._reflowEasing)
        widget.setProperty('flowAni', ani)
        widget.setProperty('flowAniPending', True)

        if index == -1:
            self._anis.append(ani)
        else:
            self._anis.insert(index, ani)

        widget.setGeometry(QRect(QPoint(0, 0), QSize(0, 0)))
        widget.show()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if index < 0 or index >= len(self._items):
            return None
        item = self._items.pop(index)
        w = item.widget()
        if w is not None:
            ani: QPropertyAnimation | None = w.property('flowAni')
            if ani is not None:
                self._anis.remove(ani)
                ani.deleteLater()
        return item

    def removeWidget(self, widget: QWidget) -> None:
        for i, item in enumerate(self._items):
            if item.widget() is widget:
                self.takeAt(i)
                return

    def removeAllWidgets(self) -> None:
        while self._items:
            self.takeAt(0)

    def takeAllWidgets(self) -> None:
        while self._items:
            item = self.takeAt(0)
            if item is not None:
                w = item.widget()
                if w is not None:
                    w.deleteLater()

    def expandingDirections(self) -> Qt.Orientations:  # type: ignore[name]
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._doLayout(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        if self._needAni:
            self._deBounceTimer.start(80)
        else:
            self._doLayout(rect, True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if obj in [w.widget() for w in self._items] and (
            event.type() == QEvent.Type.ParentChange
        ):
            p = obj.parent()
            if p is not None:
                self._wParent = p  # type: ignore[assignment]
                p.installEventFilter(self)
                self._eventFilterInstalled = True

        if obj == self._wParent and event.type() == QEvent.Type.Show:
            self._doLayout(self.geometry(), True)
            self._eventFilterInstalled = True

        return super().eventFilter(obj, event)

    def _doLayout(self, rect: QRect, move: bool) -> int:
        changedAnis: list[QPropertyAnimation] = []
        margin = self.contentsMargins()
        x = rect.x() + margin.left()
        y = rect.y() + margin.top()
        rowHeight = 0
        spaceX = self.horizontalSpacing()
        spaceY = self.verticalSpacing()
        aniIdx = 0

        for i, item in enumerate(self._items):
            w = item.widget()
            if w is not None and not w.isVisible() and self._isTight:
                continue

            itemWidth = item.sizeHint().width()
            nextX = x + itemWidth + spaceX

            if nextX - spaceX > rect.right() - margin.right() and rowHeight > 0:
                x = rect.x() + margin.left()
                y = y + rowHeight + spaceY
                nextX = x + itemWidth + spaceX
                rowHeight = 0

            if move:
                target = QRect(QPoint(x, y), item.sizeHint())
                if not self._needAni or w is None:
                    item.setGeometry(target)
                elif aniIdx < len(self._anis):
                    ani = self._anis[aniIdx]
                    pending = bool(w.property('flowAniPending'))
                    if pending or target != ani.endValue():
                        ani.stop()
                        current = w.geometry()
                        start = QRect(
                            QPoint(target.x(), current.y()),
                            current.size(),
                        )
                        if current != start:
                            w.setGeometry(start)
                        ani.setStartValue(start)
                        ani.setEndValue(target)
                        w.setProperty('flowAniPending', False)
                        changedAnis.append(ani)

            if w is not None:
                aniIdx += 1

            x = nextX
            rowHeight = max(rowHeight, item.sizeHint().height())

        for ani in changedAnis:
            ani.start()

        return y + rowHeight + margin.bottom() - rect.y()
