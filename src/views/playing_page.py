from __future__ import annotations

import base64
import hashlib
import io
import json
import logging

import math
import os
import threading
import time
from typing import Callable, TYPE_CHECKING, cast as _cast

if TYPE_CHECKING:
    from views.sidebar import Sidebar
    from views.main_window import MainWindow
    from views.favorites_page import FavoritesPage
    from utils.play_util import AudioPlayer
    from utils.lyric_util import LRCLyricParser, YRCLyricParser

import numpy as np
from imports import (
    IMAGE_ASSET_PERSISTED,
    SONG_CHANGED,
    SWITCH_PAGE,
    UPDATE_FM,
    QBuffer,
    QIODevice,
    QSize,
    Qt,
    QTimer,
    Signal,
    event_bus,
)
from imports import QColor, QImage, QPixmap
from imports import (
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CardWidget,
    FlowLayout,
    IndeterminateProgressRing,
    InfoBar,
    SubtitleLabel,
)

from utils.base.base_util import (
    IMAGE_DATA_DIR,
    LYRIC_DATA_DIR,
    MUSIC_DATA_DIR,
    SongStorable,
)
from utils.color_util import mixColor
from utils.image_util import getAverageColorFromBytes
from utils.config_util import cfg
from utils.favorite_util import saveFavorites
from utils.icon_util import bindIcon
from utils.image_util import getAverageColor
from utils.loading_util import doWithMultiThreading, downloadWithMultiThreading
from utils.loudness_balance_util import getAdjustedGainFactor
from utils.play_util import (
    PatchedAudioSegment as AudioSegment_,
    get_cached_audio,
    cache_decoded_audio,
)
from utils.random_util import AdvancedRandom
from utils import requests_util as requests
from views.song_card import DummyCard
from views.lyrics_viewer import LyricsViewer
from views.playing_controller import PlayingController

from pyncm import apis
import pyncm as ncm


class PlayingPage(QWidget):
    imageLoaded = Signal(bytes)
    preloadRetryRequested = Signal()

    def __init__(
        self,
        app,
        player: AudioPlayer,
        mgr: LRCLyricParser,
        transmgr: LRCLyricParser,
        ymgr: YRCLyricParser,
        mwindow: MainWindow | None,
        sidebar: Sidebar | None,
        favs_ref: list,
        lock: threading.Lock,
        ws_handler,
        harmony_font_family: str = '',
        launchwindow=None,
    ) -> None:
        super().__init__()
        self._logger = logging.getLogger(__name__)
        if launchwindow:
            launchwindow.top('Initializing playing page...')
            self._lw = launchwindow
        else:
            self._lw = None
        self._app = app
        self._player = player
        self._mgr = mgr
        self._transmgr = transmgr
        self._ymgr = ymgr
        self._mwindow_obj: MainWindow = mwindow  # type: ignore
        self._sidebar: Sidebar = sidebar  # type: ignore
        self._favs_ref = favs_ref
        self._lock = lock
        self._ws_handler = ws_handler
        self._fp: FavoritesPage = None  # type: ignore  # post-init wiring

        self.setObjectName('studio_page')
        self.cur: DummyCard | None = None

        self.total_length = 0

        self._preload_triggered = False
        self.preloaded: bool = False

        self.playlist: list[SongStorable] = []
        self.current_index = -1
        self.next_song_audio: AudioSegment_ | None = None
        self.next_song_gain: float | None = None

        self._gain_cache: dict[str, float] = {}

        lw = self._lw
        if lw:
            lw.top('  Creating playback controller...')
        self.controller = PlayingController(
            player, mgr, transmgr, ymgr, self, sidebar, mwindow, ws_handler
        )
        player.onFullFinished.connect(self.controller.onSongFinish.emit)
        player.onEndingNoSound.connect(self.onEndingNoSound)
        self.controller.onSongFinish.connect(lambda: self.playNext(False))
        self.controller.play_pausebtn.clicked.connect(self.onPlayButtonClicked)
        self.preloadRetryRequested.connect(self.preloadNextSong)

        event_bus.subscribe(SONG_CHANGED, self._onSongChangedEvent)

        self.lst_shoud_set: bool = True

        if lw:
            lw.top('  Building player UI...')
        global_layout = QHBoxLayout()

        contents_layout = QVBoxLayout()

        ali = Qt.AlignmentFlag

        top_layout = FlowLayout(needAni=False)
        topleft_layout = QVBoxLayout()
        topright_widget = QWidget()
        topright_widget.setLayout(topleft_layout)
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
        self.artists_label.setWordWrap(True)
        self.title_label.setWordWrap(True)
        top_layout.addWidget(topright_widget)

        contents_widget = QWidget()
        contents_layout.addLayout(top_layout)

        self.controller.setFixedWidth(self.width())

        contents_widget.setLayout(contents_layout)
        global_layout.addWidget(contents_widget, stretch=-1)
        if lw:
            lw.top('  Creating lyrics viewer...')
        self.viewer = LyricsViewer(
            app, mgr, transmgr, ymgr, player, mwindow, harmony_font_family, cfg, self
        )
        global_layout.addWidget(self.viewer, stretch=2)

        if lw:
            lw.top('  Initializing randomizer...')
        self.song_randomer = AdvancedRandom()
        self.song_randomer.init(self.playlist)

        self.setLayout(global_layout)

        self.imageLoaded.connect(self.onImageLoaded)

        if lw:
            lw.top('  Wiring signal connections...')
        self.controller.playLastSignal.connect(self.playLast)
        self.controller.playNextSignal.connect(lambda: self.playNext(True))

        self.lufs_changed_timer = QTimer(self)
        self.lufs_changed_timer.timeout.connect(self._sidebar.applyNewLUFS)  # type: ignore[union-attr]

        event_bus.subscribe(SWITCH_PAGE, self._onSwitchPage)

    def _onSwitchPage(self, interface: QWidget):
        if interface is not self:
            return

        event_bus.emit(
            UPDATE_FM,
            self.img_label.pixmap(),
            self.cur.info['name'] if self.cur else '',
        )

    def _onSongChangedEvent(self, _song_storable):
        if not self._player.isPlaying():
            return
        if not self._preload_triggered and self.current_index < len(self.playlist) - 1:
            self._preload_triggered = True
            self.preloadNextSong()
        if self.current_index >= len(self.playlist) - 1:
            self.preloaded = True

    def onNosoundSkipChanged(self, state: Qt.CheckState):
        checked = state == Qt.CheckState.Checked
        cfg.skip_nosound = checked

    def onEndingNoSound(self):
        if not cfg.skip_nosound:
            return
        self.controller.onSongFinish.emit()

    @staticmethod
    def patchedPaintEvent(card: CardWidget, e):
        from PySide6.QtGui import QPainter, QPainterPath, QPen
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

    def onPlaylistCardClicked(self, storable: SongStorable, item: QListWidgetItem):
        self.current_index = self.playlist.index(storable)
        self.playSongAtIndex(self.current_index)

    def init(self):
        if self.cur is None:
            return

        for label in self.findChildren(QLabel):
            label.setWordWrap(True)

        self.title_label.setText(self.cur.info['name'])
        self.artists_label.setText(self.cur.info['artists'])

        if self._player.isPlaying():
            self._player.stop()

        self.img_label.hide()
        self.ring.show()

        self._downloadAssetsAndPlay()

    def _downloadAssetsAndPlay(self):
        assert self.cur is not None
        info = self.cur.info
        image_url = self.cur.detail['image_url']
        song_id = str(info['id'])

        prepared: dict[str, object] = {}

        def _try_play():
            if prepared.get('music_error'):
                self.ring.hide()
                prepared['_playing'] = True
                raise Exception('music_error')

            image = prepared.get('image')
            lyrics_data = prepared.get('lyrics')
            music = prepared.get('music')
            if (
                not isinstance(image, bytes)
                or not isinstance(lyrics_data, dict)
                or not isinstance(music, bytes)
            ):
                return
            if len(music) == 0:
                return
            if prepared.get('_playing'):
                return
            prepared['_playing'] = True

            lyric = lyrics_data.get('lrc', {}).get('lyric', '')
            tlyric_dict = lyrics_data.get('tlyric', {})
            if isinstance(tlyric_dict, dict):
                translated_lyric = '\n'.join(
                    tlyric_dict.get('lyric', '').splitlines()[1:]
                )
            else:
                translated_lyric = ''
            yrc_lyric = lyrics_data.get('yrc', {}).get('lyric', '')

            storable = SongStorable(
                info=info,
                image=image,
                music_bin=music,
                lyric=lyric,
                translated_lyric=translated_lyric,
                yrc_lyric=yrc_lyric,
            )

            self.cur = DummyCard(storable)
            self.playStorable(storable)

        def _prepare():
            try:
                prepared['image'] = requests.get(image_url).content
                with ncm.GetCurrentSession():
                    prepared['lyrics'] = apis.track.GetTrackLyricsNew(song_id)
            except Exception as e:
                prepared['error'] = str(e)

        def _on_prepared():
            if self.cur is None or str(self.cur.info['id']) != song_id:
                return

            if prepared.get('error'):
                self._logger.error('Asset download failed: %s', prepared['error'])
                InfoBar.error(
                    'Playback failed',
                    'Failed to download song assets.',
                    parent=self._mwindow_obj,
                )
                self.ring.hide()
                return

            image = prepared.get('image')
            if isinstance(image, bytes):
                self.imageLoaded.emit(image)

            _try_play()

        def _process_audio():
            try:
                with ncm.GetCurrentSession():
                    music_url_resp = apis.track.GetTrackAudio(
                        song_id,  # type: ignore
                        bitrate=3200 * 1000,
                    )
                    prepared['music_url'] = music_url_resp['data'][0]['url']  # type: ignore
            except Exception as e:
                prepared['music_error'] = str(e)

        def _on_audio_prepare_done():
            if self.cur is None or str(self.cur.info['id']) != song_id:
                return
            if prepared.get('music_error'):
                InfoBar.error(
                    'Playback failed',
                    'Failed to get song audio URL.',
                    parent=self._mwindow_obj,
                )
                self.ring.hide()
                return

            music_url = prepared.get('music_url')
            if not isinstance(music_url, str) or not music_url:
                return

            def _on_downloaded(data: bytes):
                if self.cur is None or str(self.cur.info['id']) != song_id:
                    return
                prepared['music'] = data
                _try_play()

            downloadWithMultiThreading(
                music_url,  # type: ignore
                {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                },
                None,
                self._mwindow_obj,
                _on_downloaded,
            )

        doWithMultiThreading(_prepare, (), self._mwindow_obj, _on_prepared)
        doWithMultiThreading(
            _process_audio, (), self._mwindow_obj, _on_audio_prepare_done
        )

    def preloadNextSong(self):
        if len(self.playlist) <= 1:
            return
        if self.current_index >= len(self.playlist) - 1:
            return

        try:
            self.preloaded = False
            self._logger.info('preloading')

            next_song = self.playlist[self.current_index + 1]

            self._logger.debug(next_song)

            if self._sidebar.play_method_box.currentText() == 'Play in order':
                if self.current_index + 1 >= len(self.playlist):
                    return
            elif self._sidebar.play_method_box.currentText() == 'Repeat list':
                if self.current_index + 1 >= len(self.playlist):
                    next_song = self.playlist[0]
                else:
                    next_song = self.playlist[self.current_index + 1]
            else:
                next_song = self.playlist[self.current_index + 1]
            if not (
                self._sidebar.play_method_box.currentText()
                in ['Play in order', 'Repeat list']
            ):
                return

            preload_base_index = self.current_index

            def _is_preload_current() -> bool:
                return (
                    self.current_index == preload_base_index
                    and preload_base_index + 1 < len(self.playlist)
                    and self.playlist[preload_base_index + 1] is next_song
                )

            def _start_preload(redownload_on_failure: bool = True):
                threading.Thread(
                    target=lambda: _preload(redownload_on_failure),
                    daemon=True,
                ).start()

            def _download_then_preload(image_missing: bool, music_missing: bool):
                self._logger.info('downloading next song before preload')
                self.next_song_audio = None
                self.next_song_gain = None

                def _after_download(success: bool):
                    if not success:
                        self._logger.warning('failed to download next song for preload')
                        return
                    if not _is_preload_current():
                        self._logger.info('discarding stale preload download')
                        return
                    _start_preload(False)

                self._downloadStorableMissingAssets(
                    next_song,
                    image_missing,
                    music_missing,
                    _after_download,
                )

            def _preload(redownload_on_failure: bool):
                if not _is_preload_current():
                    self._logger.info('discarding stale preload')
                    return
                try:
                    with self._lock:
                        song_bytes = next_song.get_music_bytes()
                        cache_key = next_song.content_cache_hash
                        cached = get_cached_audio(cache_key) if cache_key else None
                        if cached is not None:
                            audio = cached
                        else:
                            audio = AudioSegment_.from_file(io.BytesIO(song_bytes))
                            if cache_key:
                                cache_decoded_audio(cache_key, audio)
                except Exception as e:
                    next_song.content_cache_hash = ''
                    saveFavorites()
                    self.next_song_audio = None
                    self.next_song_gain = None
                    self._logger.warning(
                        f'skipping preload because cached audio is invalid: {e}'
                    )
                    if redownload_on_failure:
                        self.preloadRetryRequested.emit()
                    return

                if not _is_preload_current():
                    self._logger.info('discarding stale preload')
                    return

                self.next_song_audio = audio  # type: ignore
                if (
                    next_song.loudness_gain == 1.0
                    or next_song.target_lufs != cfg.target_lufs
                ) and isinstance(self.next_song_audio, AudioSegment_):
                    next_song.loudness_gain = getAdjustedGainFactor(
                        cfg.target_lufs, self.next_song_audio
                    )
                    next_song.target_lufs = cfg.target_lufs
                self.next_song_gain = next_song.loudness_gain

                if isinstance(self.next_song_audio, AudioSegment_):
                    self._logger.debug(
                        f'preload -> applying gain {self.next_song_gain} {cfg.target_lufs=}'
                    )
                    self.next_song_audio = self.next_song_audio.apply_gain(
                        20 * np.log10(self.next_song_gain)
                    )

                self._logger.info('preloaded')
                self._logger.debug(
                    f'(Types) {type(self.next_song_audio)=} {type(self.next_song_gain)=}'
                )

                self.preloaded = True

            image_missing, music_missing = self._storable_asset_missing(next_song)
            if image_missing or music_missing:
                _download_then_preload(image_missing, music_missing)
            else:
                _start_preload()
        finally:
            self._logger.debug('started preload thread')

    def downloadLyric(self):
        assert self.cur is not None

        def _parse():
            data: dict = {}
            with ncm.GetCurrentSession():
                if self.cur:
                    data = apis.track.GetTrackLyricsNew(str(self.cur.info['id']))  # type: ignore
            self._mgr.cur = data.get('lrc', {}).get('lyric', '[00:00.000]')
            tlyric = data.get('tlyric')
            if isinstance(tlyric, dict):
                self._transmgr.cur = '\n'.join(tlyric.get('lyric', '').splitlines()[1:])
            else:
                self._transmgr.cur = '[00:00.000]'
            self._ymgr.cur = data.get('yrc', {}).get('lyric', '')

            def _real():
                self._mgr.parse()
                self._transmgr.parse()
                self._ymgr.parse()

            def _fini():
                self.viewer.prewarmFontMetrics()
                self.sendSongFMAndInfo()

            doWithMultiThreading(_real, (), self._mwindow_obj, _fini)

        doWithMultiThreading(_parse, (), self._mwindow_obj)

    def downloadMusic(self):
        assert self.cur is not None

        def _downloaded(bytes: bytes):
            if self._player.isPlaying():
                self._player.stop()

            with self._lock:
                audio = AudioSegment_.from_file(io.BytesIO(bytes))

            self._player.load(audio)
            self.total_length = self._player.getLength()
            self._player.play()

            def computeGain():
                try:
                    gain = getAdjustedGainFactor(cfg.target_lufs, audio)
                    if self.cur:
                        self._gain_cache[self.cur.info['id']] = gain
                    self._player.setGain(gain)
                except Exception as e:
                    pass

            threading.Thread(target=computeGain, daemon=True).start()

            self.downloadLyric()

        music_url = apis.track.GetTrackAudio(
            str(self.cur.info['id']),  # type: ignore
            bitrate=3200 * 1000,
        )
        self._logger.debug(f'{music_url['data'][0]['url']=}')  # type: ignore
        downloadWithMultiThreading(
            music_url['data'][0]['url'],  # type: ignore
            {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            },
            None,
            self._mwindow_obj,
            _downloaded,
        )

    def onImageLoaded(self, bytes):
        pixmap = QPixmap()
        pixmap.loadFromData(bytes)
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(
                self.img_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.img_label.setPixmap(scaled_pixmap)

            self.img_label.show()
            self.ring.hide()

        self.sendSongFMAndInfo()

    def onPlayButtonClicked(self):
        if self.cur is None:
            self.startPlaylist()

    def playNext(self, byuser: bool):
        self.sendSongFMAndInfo()
        self._logger.debug(
            f'(Types) {type(self.next_song_audio)=} {type(self.next_song_gain)=}'
        )
        if (
            isinstance(self.next_song_audio, AudioSegment_)
            and isinstance(self.next_song_gain, float)
            and (
                self._sidebar.play_method_box.currentText()
                in ['Play in order', 'Repeat list']
            )
        ):
            self.playPreloadedSong()
            self.current_index += 1
            return

        if self.current_index < 0 or self.current_index >= len(self.playlist) - 1:
            if self._sidebar.play_method_box.currentText() == 'Play in order':
                InfoBar.warning(
                    'Warning',
                    'This song is the last song in the playlist.',
                    parent=self._mwindow_obj,
                )
                self.controller.setPlaytime(0)
                return
            elif self._sidebar.play_method_box.currentText() == 'Repeat list':
                self.current_index = 0
                self.playSongAtIndex(self.current_index)
                return

        if self._sidebar.play_method_box.currentText() == 'Repeat one' and not byuser:
            self.playSongAtIndex(self.current_index)
            return
        elif self._sidebar.play_method_box.currentText() == 'Shuffle':
            start_storable: SongStorable = self.playlist[self.current_index]
            cur_storable: SongStorable = self.playlist[self.current_index]
            while self.current_index == self.playlist.index(start_storable):
                cur_storable = self.song_randomer.random()
                self.current_index = self.playlist.index(cur_storable)
            self.playSongAtIndex(self.current_index)
            return

        self.current_index += 1
        self.playSongAtIndex(self.current_index)

    def playPreloadedSong(self) -> None:
        if (not isinstance(self.next_song_audio, AudioSegment_)) or (
            not isinstance(self.next_song_gain, float)
        ):
            self._logger.error(
                f'cant play preloaded song: (Types) {type(self.next_song_audio)=} {type(self.next_song_gain)=}'
            )
            return

        self._logger.info('using preloaded song')

        song_storable = self.playlist[self.current_index + 1]

        self.playStorable(song_storable, preloaded_audio=self.next_song_audio)

    def playLast(self):
        if self.current_index < 1 or self.current_index >= len(self.playlist):
            InfoBar.warning(
                'Warning',
                'This song is the first song in the playlist.',
                parent=self._mwindow_obj,
            )
            self.controller.setPlaytime(0)
            return

        self._preload_triggered = False
        self.next_song_audio = None
        self.next_song_gain = None

        self.current_index -= 1
        self.playSongAtIndex(self.current_index)

    def continueLastSong(self, index: int):
        if index < 0 or index >= len(self.playlist):
            return

        self.current_index = index
        song_storable = self.playlist[index]

        if not self.ensureAssets(song_storable):
            return

        self._player.stop()
        self.cur = DummyCard(song_storable)

        self._mgr.cur = ''
        self._transmgr.cur = ''
        self._ymgr.cur = ''
        self._mgr.parse()
        self._transmgr.parse()
        self._ymgr.parse()
        self.viewer.prewarmFontMetrics()

        self.title_label.setText(song_storable.name)
        self.artists_label.setText(song_storable.artists)

        self._app.processEvents()

        result: dict = {}

        def _prepare():
            self._mwindow_obj._loading_song = True
            music_bytes = song_storable.get_music_bytes()
            cache_key = song_storable.content_cache_hash
            cached = get_cached_audio(cache_key) if cache_key else None
            if cached is not None:
                audio = cached
            else:
                if song_storable.target_lufs == cfg.target_lufs:
                    audio = AudioSegment_.from_file(io.BytesIO(music_bytes))
                else:
                    audio = AudioSegment_.from_file(io.BytesIO(music_bytes))
                    gain = getAdjustedGainFactor(cfg.target_lufs, audio)
                    song_storable.target_lufs = cfg.target_lufs
                    song_storable.loudness_gain = gain
                if cache_key:
                    cache_decoded_audio(cache_key, audio)
            if song_storable.target_lufs == cfg.target_lufs:
                audio = audio.apply_gain(20 * np.log10(song_storable.loudness_gain))
            else:
                gain = getAdjustedGainFactor(cfg.target_lufs, audio)
                audio = audio.apply_gain(20 * np.log10(gain))
                song_storable.target_lufs = cfg.target_lufs
                song_storable.loudness_gain = gain

            self._mwindow_obj._loading_song = False

            result['audio'] = audio
            self._player.load(audio)  # type: ignore
            self.total_length = self._player.getLength()
            self._player.play()
            self._player.setPosition(cfg.last_playing_time)
            self._player.pause()
            image_bytes = song_storable.get_image_bytes()
            from PySide6.QtGui import QImage

            qimg = QImage()
            qimg.loadFromData(image_bytes)
            if not qimg.isNull():
                result['qimg'] = qimg
                result['avg_color'] = getAverageColorFromBytes(image_bytes)

        def _finish():
            if self.cur is None or self.cur.storable is not song_storable:
                return

            self._preload_triggered = False
            self.next_song_audio = None
            self.next_song_gain = None

            self._sidebar.refreshPlaylistWidget()
            self.sendSongFMAndInfo()
            self._download_update_lyrics(song_storable)

            qimg = result.get('qimg')
            if isinstance(qimg, QImage) and not qimg.isNull():
                pixmap = QPixmap.fromImage(qimg)
                scaled = pixmap.scaled(
                    self.img_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.img_label.setPixmap(scaled)
                self.img_label.show()
                self.ring.hide()

                avg_color = result.get('avg_color', [128, 128, 128])
                self._mwindow_obj.song_theme = QColor(
                    int(avg_color[0]), int(avg_color[1]), int(avg_color[2])
                )
                self._mwindow_obj.repaint()

        doWithMultiThreading(_prepare, (), self._mwindow_obj, _finish)

    def playSongAtIndex(self, index: int):
        if index < 0 or index >= len(self.playlist):
            return

        self.current_index = index
        song = self.playlist[index]
        self.playStorable(song)

    def _storable_asset_missing(self, song_storable: SongStorable) -> tuple[bool, bool]:
        song_storable._ensure_cache_fields()
        image_missing = not song_storable.image_cache_hash or not os.path.exists(
            os.path.join(IMAGE_DATA_DIR, song_storable.image_cache_hash)
        )
        music_missing = not song_storable.content_cache_hash or not os.path.exists(
            os.path.join(MUSIC_DATA_DIR, song_storable.content_cache_hash)
        )
        return image_missing, music_missing

    def _write_storable_asset(self, cache_dir: str, data: bytes) -> str:
        os.makedirs(cache_dir, exist_ok=True)
        cache_hash = hashlib.sha256(data).hexdigest()
        cache_path = os.path.join(cache_dir, cache_hash)
        if not os.path.exists(cache_path):
            with open(cache_path, 'wb') as f:
                f.write(data)
        return cache_hash

    def _downloadStorableMissingAssets(
        self,
        song_storable: SongStorable,
        image_missing: bool,
        music_missing: bool,
        finished: Callable[[bool], None],
    ):
        prepared: dict[str, bytes | str] = {}

        def _prepare():
            try:
                with ncm.GetCurrentSession():
                    if image_missing:
                        response = apis.track.GetTrackDetail(
                            song_ids=[song_storable.id]
                        )
                        assert isinstance(response, dict), 'Invalid response'
                        image_url = response['songs'][0]['al']['picUrl']  # type: ignore
                        prepared['image'] = requests.get(image_url).content

                    if music_missing:
                        music_url = apis.track.GetTrackAudio(
                            str(song_storable.id),  # type: ignore
                            bitrate=3200 * 1000,
                        )
                        self._logger.debug(f'{music_url['data'][0]['url']=}')  # type: ignore
                        prepared['music_url'] = music_url['data'][0]['url']  # type: ignore

            except Exception as e:
                prepared['error'] = str(e)

        def _persist_assets(music_bytes: bytes | None = None) -> bool:
            try:
                image_just_persisted = False
                if image_missing:
                    image_bytes = prepared.get('image')
                    if not isinstance(image_bytes, bytes) or not image_bytes:
                        return False
                    song_storable.image_cache_hash = self._write_storable_asset(
                        IMAGE_DATA_DIR,
                        image_bytes,
                    )
                    image_just_persisted = True

                if music_missing:
                    if not music_bytes:
                        return False
                    song_storable.content_cache_hash = self._write_storable_asset(
                        MUSIC_DATA_DIR,
                        music_bytes,
                    )

                saveFavorites()
                if image_just_persisted:
                    event_bus.emit(IMAGE_ASSET_PERSISTED, song_storable)
                return True
            except Exception:
                self._logger.exception('failed to persist downloaded storable assets')
                return False

        def _play_after_persist(music_bytes: bytes | None = None):
            finished(_persist_assets(music_bytes))

        def _on_prepared():
            if prepared.get('error'):
                self._logger.warning(
                    f'failed to prepare storable asset download: {prepared["error"]}'
                )
                finished(False)
                return

            if music_missing:
                music_url = prepared.get('music_url')
                if not isinstance(music_url, str) or not music_url:
                    finished(False)
                    return
                downloadWithMultiThreading(
                    music_url,
                    {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                    },
                    None,
                    self._mwindow_obj,
                    _play_after_persist,
                )
            else:
                _play_after_persist()

        doWithMultiThreading(_prepare, (), self._mwindow_obj, _on_prepared)

    def _downloadMissingStorableAssets(
        self,
        song_storable: SongStorable,
        image_missing: bool,
        music_missing: bool,
    ):
        self._player.stop()
        self.cur = DummyCard(song_storable)
        self._mgr.cur = ''
        self._transmgr.cur = ''
        self._ymgr.cur = ''
        self._mgr.parse()
        self._transmgr.parse()
        self._ymgr.parse()
        self.viewer.prewarmFontMetrics()

        self.title_label.setText(song_storable.name)
        self.artists_label.setText(song_storable.artists)
        self._app.processEvents()

        def _play_after_download(success: bool):
            if not success:
                InfoBar.error(
                    'Playback failed',
                    'Failed to download missing cached files.',
                    parent=self._mwindow_obj,
                )
                return
            self._mwindow_obj.addScheduledTask(self.playStorable, song_storable)

        self._downloadStorableMissingAssets(
            song_storable,
            image_missing,
            music_missing,
            _play_after_download,
        )

    def ensureAssets(self, song_storable: SongStorable) -> bool:
        image_missing, music_missing = self._storable_asset_missing(song_storable)
        if image_missing or music_missing:
            self._downloadMissingStorableAssets(
                song_storable,
                image_missing,
                music_missing,
            )
            return False
        return True

    def playStorable(
        self,
        song_storable: SongStorable,
        preloaded_audio: AudioSegment_ | None = None,
    ):
        if not self.ensureAssets(song_storable):
            return

        self._player.stop()
        self.cur = DummyCard(song_storable)

        self._mgr.cur = ''
        self._transmgr.cur = ''
        self._ymgr.cur = ''
        self._mgr.parse()
        self._transmgr.parse()
        self._ymgr.parse()
        self.viewer.prewarmFontMetrics()

        self.title_label.setText(song_storable.name)
        self.artists_label.setText(song_storable.artists)

        self._app.processEvents()

        result: dict = {}

        def _prepare():
            if preloaded_audio is not None:
                audio = preloaded_audio
            else:
                self._mwindow_obj._loading_song = True
                music_bytes = song_storable.get_music_bytes()
                cache_key = song_storable.content_cache_hash
                cached = get_cached_audio(cache_key) if cache_key else None
                if cached is not None:
                    audio = cached
                else:
                    audio = AudioSegment_.from_file(io.BytesIO(music_bytes))
                    if cache_key:
                        cache_decoded_audio(cache_key, audio)
                gain_db = 20 * np.log10(
                    getAdjustedGainFactor(cfg.target_lufs, audio)
                    if song_storable.target_lufs != cfg.target_lufs
                    else song_storable.loudness_gain
                )
                audio = audio.apply_gain(gain_db)
                if song_storable.target_lufs != cfg.target_lufs:
                    song_storable.target_lufs = cfg.target_lufs
                    gain = getAdjustedGainFactor(cfg.target_lufs, audio)
                    song_storable.loudness_gain = gain

            self._mwindow_obj._loading_song = False

            result['audio'] = audio
            self._player.load(audio)  # type: ignore
            self.total_length = self._player.getLength()
            if not self._player.isPlaying():
                self._player.play()

            image_bytes = song_storable.get_image_bytes()
            from PySide6.QtGui import QImage

            qimg = QImage()
            qimg.loadFromData(image_bytes)
            if not qimg.isNull():
                result['qimg'] = qimg
                result['avg_color'] = getAverageColorFromBytes(image_bytes)

        def _finish():
            if self.cur is None or self.cur.storable is not song_storable:
                return

            self._preload_triggered = False
            self.next_song_audio = None
            self.next_song_gain = None

            self._sidebar.refreshPlaylistWidget()
            self.sendSongFMAndInfo()
            self._download_update_lyrics(song_storable)

            qimg = result.get('qimg')
            if isinstance(qimg, QImage) and not qimg.isNull():
                pixmap = QPixmap.fromImage(qimg)
                scaled = pixmap.scaled(
                    self.img_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.img_label.setPixmap(scaled)
                self.img_label.show()
                self.ring.hide()

                avg_color = result.get('avg_color', [128, 128, 128])
                self._mwindow_obj.song_theme = QColor(
                    int(avg_color[0]), int(avg_color[1]), int(avg_color[2])
                )
                self._mwindow_obj.repaint()

            event_bus.emit(SONG_CHANGED, song_storable)

        doWithMultiThreading(_prepare, (), self._mwindow_obj, _finish)

    def _download_update_lyrics(self, song_storable: SongStorable) -> None:
        lyric_target = song_storable
        lyric_result: dict | None = None

        def _download():
            nonlocal lyric_result
            if not song_storable.yrc_lyrics_missing():
                return
            try:
                data = apis.track.GetTrackLyricsNew(song_storable.id)
                assert isinstance(data, dict), 'Invalid response'
                lyric_result = data
            except Exception:
                self._logger.exception(
                    'failed to download lyrics for storable playback'
                )
                lyric_result = None

        def _apply():
            if self.cur is None or self.cur.storable is not lyric_target:
                return

            if lyric_result is None:
                lyrics = lyric_target.get_lyrics()
                self._mgr.cur = lyrics['lyric'] or '[00:00.000]'
                if lyrics['translated_lyric']:
                    self._transmgr.cur = lyrics['translated_lyric']
                else:
                    self._transmgr.cur = '[00:00.000]'
                self._ymgr.cur = lyrics['yrc_lyric']
            else:
                self._mgr.cur = lyric_result.get('lrc', {}).get('lyric', '[00:00.000]')
                tlyric = lyric_result.get('tlyric')
                if isinstance(tlyric, dict):
                    self._transmgr.cur = '\n'.join(
                        tlyric.get('lyric', '').splitlines()[1:]
                    )
                else:
                    self._transmgr.cur = '[00:00.000]'
                self._ymgr.cur = lyric_result.get('yrc', {}).get('lyric', '')
                lyric_target.write_lyrics(
                    self._mgr.cur,
                    self._transmgr.cur if self._transmgr.cur != '[00:00.000]' else '',
                    self._ymgr.cur,
                )
                saveFavorites()

            self._mgr.parse()
            self._transmgr.parse()
            self._ymgr.parse()
            self.viewer.prewarmFontMetrics()

        doWithMultiThreading(_download, (), self._mwindow_obj, _apply)

    def loadMusicFromBase64(self, content_base64: str, gain: float):
        music_bytes = base64.b64decode(content_base64)
        self.loadMusicFromBytes(music_bytes, gain)

    def loadMusicFromBytes(self, music_bytes: bytes, gain: float):
        self._logger.debug(f'loading data {len(music_bytes)}')
        with self._lock:
            audio = AudioSegment_.from_file(io.BytesIO(music_bytes))

        self._logger.debug(f'applying gain {gain} {cfg.target_lufs=}')
        audio = audio.apply_gain(20 * np.log10(gain))

        self._player.load(audio)
        self.total_length = self._player.getLength()

    def startPlaylist(self):
        self._fp.folder_selector.setCurrentRow(0)
        self._fp.addFolderToPlaylist()

        self.current_index = 0
        self.playSongAtIndex(0)

        if not self._player.isPlaying():
            self._player.play()

    def sendSongFMAndInfo(self):
        if self.cur is None:
            return
        if not isinstance(self.cur, DummyCard):
            return

        pixmap = self.img_label.pixmap().scaled(
            self.img_label.pixmap().size(), Qt.AspectRatioMode.KeepAspectRatio
        )

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
