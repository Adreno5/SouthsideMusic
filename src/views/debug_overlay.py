from imports import QWidget, QFont, QFontMetricsF, Qt, QWheelEvent, QPainter, QColor
from core.app_context import AppContext
from core.smooth import EaseOutTimer
from core import theme


class DebugOverlay(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.title_ft = QFont(ctx.harmony_font_family, 10, QFont.Weight.Bold)
        self.content_ft = QFont(ctx.harmony_font_family, 7, QFont.Weight.Normal)
        self.title_height = int(QFontMetricsF(self.title_ft).height())
        self.content_height = int(QFontMetricsF(self.content_ft).height())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.hide()

        self.offset_timer = EaseOutTimer(0.2, 2)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self.offset_timer.target_value += event.angleDelta().y()
        return super().wheelEvent(event)

    def refresh(self) -> None:
        self.setVisible(self.ctx.debugging)
        if self.ctx.debugging:
            self.raise_()
            self.update()

    def paintEvent(self, event) -> None:
        if not self.ctx.debugging:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(
            QColor(255, 255, 255, 45) if theme.isLight() else QColor(0, 0, 0, 70)
        )

        painter.drawRect(self.rect())

        painter.setPen(QColor(255, 255, 255) if theme.isDark() else QColor(0, 0, 0))

        y = 50 + int(self.offset_timer.current_value)
        painter.setFont(self.title_ft)
        for info in self.ctx.debugging_obj.infos:
            name, lines = next(iter(info.items()))
            painter.setFont(self.title_ft)
            painter.drawText(10, y, name)
            y += self.title_height + 10
            painter.setFont(self.content_ft)
            for line in lines:
                painter.drawText(20, y, line)
                y += self.content_height + 1

        painter.end()
