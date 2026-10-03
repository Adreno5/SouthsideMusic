from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent
from PySide6.QtGui import QEnterEvent

from core import theme
from core.color import mixColor
from core.config import cfg
from core.icons import SouthsideIcon, bindIcon
from core.smooth import EaseOutTimer
from imports import (
    BACKGROUND_RATIO_CHANGED,
    POST_THEME_CHANGED,
    REPAINT,
    QColor,
    QCursor,
    QFocusEvent,
    QFont,
    QIcon,
    QLineEdit,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPoint,
    Qt,
    event_bus,
)

if TYPE_CHECKING:
    from views.main_window import MainWindow


class SearchLineEdit(QLineEdit):
    class IconHandler:
        def __init__(self) -> None:
            self.icon: QIcon | None = None

        def setIcon(self, icon: SouthsideIcon):
            self.icon = icon.icon()

    def __init__(
        self,
        mwindow: 'MainWindow | None',
        font_family: str,
        point_size: int | None = None,
    ) -> None:
        super().__init__()
        self._mwindow = mwindow
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setFrame(False)

        self.handler = self.IconHandler()
        self.hovering = False
        self.draw_pixmap = None
        self._text_padding = 12
        self._icon_padding = 3
        self._icon_gap = 6
        bindIcon(self.handler, 'search')

        self._hovering = False

        self.expand_timer = EaseOutTimer(0.3, 3)

        self.ft = QFont(font_family, point_size or 14)
        self.setFont(self.ft)

        self.bg_color = QColor(0, 0, 0)
        self._repaintTick()
        self._applyTextColor()
        self._updateIconLayout()
        self._onThemeChanged()

        event_bus.subscribe(POST_THEME_CHANGED, self._onThemeChanged)
        event_bus.subscribe(BACKGROUND_RATIO_CHANGED, self._onThemeChanged)
        event_bus.subscribe(REPAINT, self._repaintTick)

    def enterEvent(self, event: QEnterEvent) -> None:
        self._hovering = True
        return super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self._hovering = False
            self.clearFocus()
            self.update()
        return super().leaveEvent(event)

    def _onThemeChanged(self, song=None):
        song_theme = self._mwindow.song_theme if self._mwindow else None
        self.bg_color = mixColor(
            QColor(85, 85, 85) if theme.isDark() else QColor(195, 195, 195),
            song_theme if song_theme else QColor(0, 0, 0),
            1 - cfg.background_ratio * 0.5,
        )
        self.bg_color.setAlpha(215)
        self._repaintTick()
        self._applyTextColor()

        bindIcon(self.handler, 'search')
        self._updateIconLayout()

    def _applyTextColor(self):
        color = '#ffffff' if theme.isDark() else '#000000'
        self.setStyleSheet(
            f'QLineEdit {{ color: {color}; background: transparent; border: none; padding: 0px; }}'
        )

    def _updateIconLayout(self) -> None:
        icon_size = max(1, self.height() - self._icon_padding * 2)
        if self.handler.icon:
            self.draw_pixmap = self.handler.icon.pixmap(icon_size, icon_size)
        else:
            self.draw_pixmap = None

        self.update()

    def _repaintTick(self, _multiple_factor: float = 1.0) -> None:
        if self.expand_timer.is_animating:
            self.update()

    def shouldExpand(self) -> bool:
        return bool(self.text().strip()) or self.hasFocus()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.update()
        return super().mousePressEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:
        self.update()
        return super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self.update()
        return super().focusOutEvent(event)

    def resizeEvent(self, event) -> None:
        self._updateIconLayout()
        return super().resizeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.bg_color)
        should = self.shouldExpand()
        if hasattr(self, 'draw_pixmap') and self.draw_pixmap:
            icon_size = self.draw_pixmap.width()
            radius = int(min(self.width() / 2, self.height() * 0.5))
            self.expand_timer.target_value = 1.0 if should else 0.0
            expansion = self.expand_timer.current_value
            collapsed_width = self.height() * 1.32
            draw_rect = self.rect()
            draw_width = int(
                collapsed_width + (self.width() - collapsed_width) * expansion
            )
            draw_rect.setX(int((self.width() - draw_width) * 0.5))
            draw_rect.setWidth(draw_width)
            self.setTextMargins(
                draw_rect.x() + self._text_padding,
                0,
                self.width()
                - draw_rect.x()
                - draw_width
                + self._text_padding
                + icon_size
                + self._icon_gap,
                0,
            )
            painter.drawRoundedRect(draw_rect, radius, radius)

            icon_x = (self.width() - icon_size) * (0.5 + expansion * 0.5)
            painter.drawPixmap(
                QPoint(
                    int(icon_x) + self._icon_gap,
                    3,
                ),
                self.draw_pixmap,
            )

        painter.end()

        margins = self.textMargins()
        if self.width() > margins.left() + margins.right():
            super().paintEvent(event)
