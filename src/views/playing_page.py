from __future__ import annotations

import base64
import json
import logging

from core import theme
from core.app_context import AppContext
from core.color import mixColor
from core.config import cfg
from core.icons import bindIcon
from core.models import SearchSongInfo, SongStorable
from core.theme import isDark
from core.playing_manager import PlayMode
from imports import (
    BACKGROUND_RATIO_CHANGED,
    PLAY_START_PLAYLIST,
    PLAY_SEARCH_SONG,
    PLAYBACK_ERROR,
    PLAYBACK_IMAGE_LOADED,
    PLAYBACK_LYRICS_UPDATED,
    PLAYBACK_SONG_LOADING,
    POST_THEME_CHANGED,
    SONG_CHANGED,
    UPDATE_FM,
    QBuffer,
    QHBoxLayout,
    QIODevice,
    QLabel,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QRect,
    QResizeEvent,
    QSizePolicy,
    QSpacerItem,
    Qt,
    QVBoxLayout,
    QWidget,
    event_bus,
)
from services.events.events import COLLECT_DEBUG_INFO, EMIT_DEBUG_INFO
from imports import QColor, QImage, QPixmap
from qfluentwidgets import (
    CardWidget,
    IndeterminateProgressRing,
    InfoBar,
    SubtitleLabel,
    PillToolButton,
)
from views.lyrics_viewer import LyricsViewer
from views.song_card import DummyCard


class PlayingPage(QWidget):
    def __init__(
        self,
        ctx: AppContext,
    ) -> None:
        super().__init__()
        self._logger = logging.getLogger(__name__)
        if ctx.launch_window:
            ctx.launch_window.top('Initializing playing page...')
            self._lw = ctx.launch_window
        else:
            self._lw = None
        self.ctx = ctx
        self._app = ctx.app
        self._mgr = ctx.mgr
        self._transmgr = ctx.transmgr
        self._ymgr = ctx.ymgr
        self._ws_handler = ctx.ws_handler

        self.playing_manager = ctx.playing_manager

        self.setObjectName('studio_page')
        self.cur: DummyCard | None = None

        lw = self._lw
        if lw:
            lw.top('  Building player UI...')
        global_layout = QHBoxLayout()

        contents_layout = QVBoxLayout()

        ali = Qt.AlignmentFlag

        top_layout = QVBoxLayout()
        topleft_layout = QVBoxLayout()
        topleft_widget = QWidget()
        topleft_widget.setLayout(topleft_layout)
        self.img_label = QLabel()
        self.img_label.hide()
        self.img_label.setFixedSize(200, 200)
        self.ring = IndeterminateProgressRing()
        self.ring.setFixedSize(195, 195)
        self.ring.hide()
        top_layout.addWidget(self.ring)
        top_layout.addWidget(self.img_label)
        self.title_label = SubtitleLabel()
        self.artists_label = QLabel()
        topleft_layout.addWidget(
            self.title_label, alignment=ali.AlignLeft | ali.AlignTop
        )
        topleft_layout.addWidget(
            self.artists_label, alignment=ali.AlignLeft | ali.AlignTop
        )
        topleft_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        )
        self.artists_label.setWordWrap(True)
        self.title_label.setWordWrap(True)
        top_layout.addWidget(topleft_widget)

        contents_widget = QWidget()
        contents_layout.addLayout(top_layout)

        contents_widget.setLayout(contents_layout)
        global_layout.addWidget(contents_widget, stretch=-1)
        if lw:
            lw.top('  Creating lyrics viewer...')
        self.viewer = LyricsViewer(ctx)
        global_layout.addWidget(self.viewer, stretch=2)

        self.setLayout(global_layout)

        self.bg_color = QColor(0, 0, 0) if isDark() else QColor(255, 255, 255)

        self.translation_button = PillToolButton(self)
        self.translation_button.hide()
        self.translation_button.toggled.connect(self.translationToggled)
        self.translation_button.setChecked(cfg.show_translation)
        self.translation_button.setFixedSize(32, 32)
        bindIcon(self.translation_button, 'translation')

        event_bus.subscribe(PLAYBACK_SONG_LOADING, self._onPlaybackSongLoading)
        event_bus.subscribe(PLAYBACK_IMAGE_LOADED, self._onPlaybackImageLoaded)
        event_bus.subscribe(PLAYBACK_LYRICS_UPDATED, self._onPlaybackLyricsUpdated)
        event_bus.subscribe(PLAYBACK_ERROR, self._onPlaybackError)
        event_bus.subscribe(SONG_CHANGED, self._updateDatas)
        event_bus.subscribe(POST_THEME_CHANGED, self._updateDatas)
        event_bus.subscribe(BACKGROUND_RATIO_CHANGED, self._updateDatas)
        event_bus.subscribe(COLLECT_DEBUG_INFO, self.emitDebugInfo)

    def emitDebugInfo(self):
        event_bus.emit(
            EMIT_DEBUG_INFO,
            'Playing Page',
            [
                f'cur={self.cur.storable.name if self.cur else None}',
                f'playlist_size={len(self.playlist)}',
                f'current_index={self.playing_manager.current_index}',
                f'play_mode={self.playing_manager.play_mode}',
                f'total_length={self.playing_manager.total_length:.1f}',
                f'preloaded={self.playing_manager.preloaded}',
            ],
        )

    @property
    def _mwindow_obj(self):
        return self.ctx.main_window

    @property
    def playlist(self) -> list[SongStorable]:
        return self.playing_manager.playlist

    @playlist.setter
    def playlist(self, value: list[SongStorable]) -> None:
        self.playing_manager.setPlaylist(value)

    @property
    def current_index(self) -> int:
        return self.playing_manager.current_index

    @current_index.setter
    def current_index(self, value: int) -> None:
        self.playing_manager.setCurrentIndex(value)

    @property
    def play_mode(self) -> PlayMode:
        return self.playing_manager.play_mode

    @property
    def total_length(self) -> float:
        return self.playing_manager.total_length

    @property
    def preloaded(self) -> bool:
        return self.playing_manager.preloaded

    @property
    def _preload_triggered(self) -> bool:
        return self.playing_manager._preload_triggered

    @_preload_triggered.setter
    def _preload_triggered(self, value: bool) -> None:
        self.playing_manager._preload_triggered = value

    def translationToggled(self, state: bool):
        self.ctx.cfg.show_translation = state

    def _updateDatas(self, song: SongStorable | None = None) -> None:
        self.bg_color = mixColor(
            QColor(40, 40, 40) if theme.isDark() else QColor(230, 230, 230),
            self._mwindow_obj.song_theme
            if self._mwindow_obj.song_theme
            else QColor(0, 0, 0),
            1 - cfg.background_ratio * 0.5,
        )
        self.translation_button.setVisible(bool(song and song.translated_lyric))

        self.update()

    def _onSwitchPage(self, interface: QWidget) -> None:
        if interface is not self:
            return

        event_bus.emit(
            UPDATE_FM,
            self.img_label.pixmap(),
            self.cur.info.name if self.cur else '',
        )

    def onNosoundSkipChanged(self, state: Qt.CheckState) -> None:
        cfg.skip_nosound = state == Qt.CheckState.Checked

    def init(self) -> None:
        if self.cur is None:
            return
        detail = getattr(self.cur, 'detail', None)
        info = getattr(self.cur, 'info', None)
        if detail is None or info is None:
            return
        event_bus.emit(PLAY_SEARCH_SONG, info, detail.get('image_url', ''))

    def onPlayButtonClicked(self) -> None:
        if self.cur is None:
            event_bus.emit(PLAY_START_PLAYLIST)

    @staticmethod
    def patchedPaintEvent(card: CardWidget, e) -> None:
        from PySide6.QtGui import QPainter, QPainterPath
        from qfluentwidgets import isDarkTheme

        painter = QPainter(card)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)

        w, h = card.width(), card.height()
        r = card.getBorderRadius()
        d = 2 * r

        isDark = isDarkTheme()

        path = QPainterPath()
        path.arcMoveTo(1, h - d - 1, d, d, 240)
        path.arcTo(1, h - d - 1, d, d, 225, -60)
        path.lineTo(1, r)
        path.arcTo(1, 1, d, d, -180, -90)
        path.lineTo(w - r, 1)
        path.arcTo(w - d - 1, 1, d, d, 90, -90)
        path.lineTo(w - 1, h - r)
        path.arcTo(w - d - 1, h - d - 1, d, d, 0, -60)

        topBorderColor = QColor(0, 0, 0, 0)
        if isDark:
            topBorderColor = QColor(255, 255, 255, 11)
            if card.isPressed:
                topBorderColor = QColor(255, 255, 255, 34)
            elif card.isHover:
                topBorderColor = QColor(255, 255, 255, 30)
        else:
            topBorderColor = QColor(0, 0, 0, 28)

        painter.strokePath(path, topBorderColor)

        path = QPainterPath()
        path.arcMoveTo(1, h - d - 1, d, d, 240)
        path.arcTo(1, h - d - 1, d, d, 240, 30)
        path.lineTo(w - r - 1, h - 1)
        path.arcTo(w - d - 1, h - d - 1, d, d, 270, 30)

        bottomBorderColor = topBorderColor
        if not isDark and card.isHover and not card.isPressed:
            bottomBorderColor = QColor(0, 0, 0, 27)

        painter.strokePath(path, bottomBorderColor)

        painter.setPen(Qt.PenStyle.NoPen)
        rect = card.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(card.backgroundColor)
        painter.drawRoundedRect(rect, r, r)

    def _onPlaybackSongLoading(self, song: SongStorable | SearchSongInfo) -> None:
        for label in self.findChildren(QLabel):
            label.setWordWrap(True)

        if isinstance(song, SongStorable):
            self.cur = DummyCard(song)
            self.title_label.setText(song.name)
            self.artists_label.setText(song.artists)
        else:
            self.cur = None
            self.title_label.setText(song.name)
            self.artists_label.setText('、'.join(a.name for a in song.artists))

        self._mgr.cur = ''
        self._transmgr.cur = ''
        self._ymgr.cur = ''
        self._mgr.parse()
        self._transmgr.parse()
        self._ymgr.parse()
        self.viewer.prewarmFontMetrics()

        self.img_label.hide()
        self.ring.show()
        self._app.processEvents()

    def _onPlaybackImageLoaded(
        self,
        song: SongStorable | SearchSongInfo,
        image_bytes: bytes,
        avg_color: list[int] | tuple[int, int, int] | None = None,
    ) -> None:
        qimg = QImage()
        qimg.loadFromData(image_bytes)
        if qimg.isNull():
            return

        pixmap = QPixmap.fromImage(qimg)
        scaled = pixmap.scaled(
            self.img_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.img_label.setPixmap(scaled)
        self.img_label.show()
        self.ring.hide()

        if avg_color is None:
            avg_color = [128, 128, 128]
        self._mwindow_obj.song_theme = QColor(
            int(avg_color[0]), int(avg_color[1]), int(avg_color[2])
        )
        self._mwindow_obj.update()
        event_bus.emit(POST_THEME_CHANGED)
        self.sendSongFMAndInfo()

    def _onPlaybackLyricsUpdated(self, song: SongStorable) -> None:
        if self.cur is not None and self.cur.storable.id != song.id:
            return
        self.viewer.prewarmFontMetrics()

    def _onPlaybackError(self, title: str, message: str) -> None:
        self.ring.hide()
        if title == 'Warning':
            InfoBar.warning(title, message, parent=self._mwindow_obj)
        else:
            InfoBar.error(title, message, parent=self._mwindow_obj)

    def sendSongFMAndInfo(self) -> None:
        if self.cur is None:
            return
        if not isinstance(self.cur, DummyCard):
            return

        pixmap = self.img_label.pixmap()
        if pixmap is None or pixmap.isNull():
            return

        pixmap = pixmap.scaled(pixmap.size(), Qt.AspectRatioMode.KeepAspectRatio)

        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, 'PNG')
        img_bytes = buffer.data().data()
        buffer.close()

        img_base64 = base64.b64encode(img_bytes).decode()

        self._ws_handler.send(
            json.dumps(
                {
                    'option': 'fm',
                    'image': img_base64,
                    'song_name': self.cur.storable.name,
                    'artists': self.cur.storable.artists,
                }
            )
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        self.update()
        self.translation_button.move(
            15, self.height() - 15 - self.translation_button.height()
        )
        return super().resizeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.bg_color)
        r = self.rect()
        painter.drawRoundedRect(r, 10, 10)
        painter.drawRect(QRect(r.x() + 10, r.y(), r.width() - 10, r.height()))
        painter.drawRect(QRect(r.x(), r.y() + 10, r.width(), r.height() - 10))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._mwindow_obj.dp_animating and not self.viewer.hovering:
            self._mwindow_obj.togglePlayingPageExpand()
        return super().mousePressEvent(event)
