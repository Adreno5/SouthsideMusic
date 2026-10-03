from __future__ import annotations

from typing import override

from imports import (
    QFrame,
    QIcon,
    QLabel,
    QOpenGLWidget,
    QPainter,
    QPaintEvent,
    QPoint,
    QRect,
    QRegion,
    QStyle,
    QStyleOption,
    Qt,
    QTimer,
    QWidget,
)


class _ImageCanvas(QOpenGLWidget):
    def __init__(self, label: SImageLabel) -> None:
        super().__init__(label)
        self._label = label
        self._draw_rect = QRect()
        self._paint_state: tuple[object, ...] = ()
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.PartialUpdate)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        surface_format = self.format()
        surface_format.setSwapInterval(0)
        surface_format.setAlphaBufferSize(8)
        self.setFormat(surface_format)

    @override
    def paintGL(self) -> None:
        label = self._label
        pixmap = label.pixmap()
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )
            if pixmap is None or pixmap.isNull():
                return
            if pixmap.hasAlphaChannel():
                ancestors: list[QWidget] = []
                ancestor: QWidget | None = label
                while ancestor is not None:
                    ancestors.append(ancestor)
                    ancestor = ancestor.parentWidget()
                label._rendering_background = True
                try:
                    for ancestor in reversed(ancestors):
                        origin = self.mapTo(ancestor, QPoint())
                        ancestor.render(
                            painter,
                            QPoint(),
                            QRegion(QRect(origin, self.size())),
                            QWidget.RenderFlag.DrawWindowBackground
                            if ancestor.isWindow()
                            else QWidget.RenderFlag(0),
                        )
                finally:
                    label._rendering_background = False
            if not label.isEnabled():
                option = QStyleOption()
                option.initFrom(label)
                pixmap = label.style().generatedIconPixmap(
                    QIcon.Mode.Disabled, pixmap, option
                )
            if label.hasScaledContents():
                painter.drawPixmap(self._draw_rect, pixmap)
            else:
                alignment = QStyle.visualAlignment(
                    label.layoutDirection(), label.alignment()
                )
                label.style().drawItemPixmap(
                    painter, self._draw_rect, int(alignment), pixmap
                )
        finally:
            painter.end()


class SImageLabel(QLabel):
    def __init__(
        self,
        parent: QWidget | None = None,
        flags: Qt.WindowType = Qt.WindowType.Widget,
    ) -> None:
        super().__init__(parent, flags)
        self._canvas: _ImageCanvas | None = None
        self._canvas_pending = False
        self._rendering_background = False

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        QFrame.paintEvent(self, event)
        if self._rendering_background:
            return
        if not self._canvas_pending:
            self._canvas_pending = True
            QTimer.singleShot(0, self, self._syncCanvas)

    def _syncCanvas(self) -> None:
        self._canvas_pending = False
        if not self.isVisible():
            return
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            if self._canvas is not None:
                self._canvas.hide()
            return
        rect = self.contentsRect()
        margin = self.margin()
        rect.adjust(margin, margin, -margin, -margin)
        alignment = QStyle.visualAlignment(self.layoutDirection(), self.alignment())
        image_rect = (
            rect
            if self.hasScaledContents()
            else self.style().itemPixmapRect(rect, int(alignment), pixmap)
        )
        visible_rect = image_rect.intersected(rect).intersected(self.rect())
        if visible_rect.isEmpty():
            if self._canvas is not None:
                self._canvas.hide()
            return
        if self._canvas is None:
            self._canvas = _ImageCanvas(self)
        draw_rect = rect.translated(-visible_rect.topLeft())
        paint_state = (
            pixmap.cacheKey(),
            draw_rect,
            alignment,
            self.hasScaledContents(),
            self.isEnabled(),
            self.palette().cacheKey(),
            self.style(),
            self.devicePixelRatioF(),
        )
        self._canvas._draw_rect = draw_rect
        self._canvas.setGeometry(visible_rect)
        self._canvas.show()
        if self._canvas._paint_state != paint_state:
            self._canvas._paint_state = paint_state
            self._canvas.update()
