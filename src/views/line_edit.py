import darkdetect

from core.color import mixColor
from core.config import cfg
from core.models import SongStorable
from imports import (
    BACKGROUND_RATIO_CHANGED,
    POST_THEME_CHANGED,
    SONG_CHANGED,
    QColor,
    QFont,
    QLineEdit,
    QPaintEvent,
    QPainter,
    Qt,
    event_bus,
)


class SearchLineEdit(QLineEdit):
    def __init__(self, mwindow, font_family: str, point_size: int | None = None):
        super().__init__()
        self._mwindow = mwindow

        ft = QFont(font_family, point_size or 14)
        self.setFont(ft)

        self.bg_color = QColor(0, 0, 0)
        self._updateDatas()
        self._applyTextColor()

        event_bus.subscribe(SONG_CHANGED, self._updateDatas)
        event_bus.subscribe(POST_THEME_CHANGED, self._onThemeChanged)
        event_bus.subscribe(BACKGROUND_RATIO_CHANGED, self._updateDatas)

    def _onThemeChanged(self, song=None):
        self._updateDatas(song)
        self._applyTextColor()

    def _applyTextColor(self):
        color = '#ffffff' if darkdetect.isDark() else '#000000'
        self.setStyleSheet(
            f'SearchLineEdit {{ color: {color}; background: transparent; border: none; padding: 4px 6px; }}'
        )

    def _updateDatas(self, song: SongStorable | None = None):
        self.bg_color = mixColor(
            QColor(85, 85, 85) if darkdetect.isDark() else QColor(195, 195, 195),
            self._mwindow.song_theme if self._mwindow.song_theme else QColor(0, 0, 0),
            1 - cfg.background_ratio * 0.5,
        )
        self.bg_color.setAlpha(215)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.bg_color)
        radius = int(self.height() * 0.5)
        painter.drawRoundedRect(self.rect(), radius, radius)
        painter.end()

        super().paintEvent(event)
