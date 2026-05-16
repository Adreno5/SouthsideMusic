import darkdetect

from core.color import mixColor
from core.config import cfg
from core.icons import SouthsideIcon, bindIcon
from core.models import SongStorable
from imports import (
    BACKGROUND_RATIO_CHANGED,
    POST_THEME_CHANGED,
    REPAINT,
    QColor,
    QEnterEvent,
    QEvent,
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


class SearchLineEdit(QLineEdit):
    class IconHandler:
        def __init__(self) -> None:
            self.icon: QIcon | None = None

        def setIcon(self, icon: SouthsideIcon):
            self.icon = icon.icon()

    def __init__(self, mwindow, font_family: str, point_size: int | None = None):
        super().__init__()
        self._mwindow = mwindow
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setFrame(False)

        self.status = 'idle'
        self.handler = self.IconHandler()
        self.hovering = False
        self.draw_pixmap = None
        self._text_padding = 12
        self._icon_padding = 0
        self._icon_gap = 6
        bindIcon(self.handler, 'search')

        self.ft = QFont(font_family, point_size or 14)
        self.setFont(self.ft)

        self.bg_color = QColor(0, 0, 0)
        self._repaintTick()
        self._applyTextColor()
        self._updateIconLayout()

        self.textChanged.connect(self._onTextChanged)
        event_bus.subscribe(POST_THEME_CHANGED, self._onThemeChanged)
        event_bus.subscribe(BACKGROUND_RATIO_CHANGED, self._onThemeChanged)
        event_bus.subscribe(REPAINT, self._repaintTick)

    def _onThemeChanged(self, song=None):
        song_theme = self._mwindow.song_theme if self._mwindow else None
        self.bg_color = mixColor(
            QColor(85, 85, 85) if darkdetect.isDark() else QColor(195, 195, 195),
            song_theme if song_theme else QColor(0, 0, 0),
            1 - cfg.background_ratio * 0.5,
        )
        self.bg_color.setAlpha(215)
        self._repaintTick()
        self._applyTextColor()

        bindIcon(self.handler, 'search')
        self._updateIconLayout()

    def _applyTextColor(self):
        color = '#ffffff' if darkdetect.isDark() else '#000000'
        self.setStyleSheet(
            f'QLineEdit {{ color: {color}; background: transparent; border: none; padding: 0px; }}'
        )

    def _updateIconLayout(self):
        icon_size = max(1, self.height() - self._icon_padding * 2)
        if self.handler.icon:
            self.draw_pixmap = self.handler.icon.pixmap(icon_size, icon_size)
        else:
            self.draw_pixmap = None

        right_margin = self._text_padding
        if self.draw_pixmap and not self.draw_pixmap.isNull():
            right_margin += icon_size + self._icon_gap

        self.setTextMargins(self._text_padding, 0, right_margin, 0)
        self.update()

    def _repaintTick(self, song: SongStorable | None = None):
        self.update()

    def _onTextChanged(self, text: str):
        if text or self.hasFocus():
            self.status = 'inputing'
        else:
            self.status = 'hovering' if self.hovering else 'idle'
        self.update()

    def enterEvent(self, event: QEnterEvent) -> None:
        self.hovering = True
        if self.status != 'inputing':
            self.status = 'hovering'
        return super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.hovering = False
        if self.status != 'inputing':
            self.status = 'idle'
        return super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.status = 'inputing'
        self.update()
        return super().mousePressEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:
        self.status = 'inputing'
        self.update()
        return super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        if not self.text():
            self.status = 'hovering' if self.hovering else 'idle'
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
        radius = int(min(self.width() / 2, self.height() * 0.5))
        painter.drawRoundedRect(self.rect(), radius, radius)

        if hasattr(self, 'draw_pixmap') and self.draw_pixmap:
            icon_size = self.draw_pixmap.width()
            painter.drawPixmap(
                QPoint(
                    self.width() - icon_size,
                    3,
                ),
                self.draw_pixmap,
            )

        painter.end()

        super().paintEvent(event)
