
import base64
import io
import logging
import subprocess
import sys
import threading
import time
from PySide6.QtGui import QHideEvent, QKeyEvent, QMouseEvent, QPaintEvent, QShowEvent
from PySide6.QtWidgets import *  # type: ignore
from PySide6.QtCore import *  # type: ignore
from PySide6.QtGui import *  # type: ignore
import numpy as np
from qfluentwidgets import *  # type: ignore
import requests

from colorama import Fore, Style

from pydub import AudioSegment
import darkdetect
import math

from utils.random_util import AdvancedRandom

from functools import cache, lru_cache
from utils.lyrics.base_util import FolderInfo, SongInfo, SongStorable
from utils.lyrics.base_util import SongDetail
from utils.lyric_util import LRCLyricManager
from utils.time_util import float2time
from utils.favorite_util import loadFavorites, saveFavorites
from utils.config_util import loadConfig, saveConfig, cfg
from utils.loudness_balance_util import getAdjustedGainFactor
from utils.play_util import AudioPlayer
from utils.icon_util import getQIcon
from utils.dialog_util import get_value_bylist, get_text_lineedit
from utils.websocket_util import ws_server, ws_handler

ws_handler.onConnected.connect(lambda: InfoBar.success(
    'SouthsideClient connection',
    'SouthsideMusic was connected to SouthsidClient',
    duration=5000,
    parent=mwindow
))
ws_handler.onDisconnected.connect(lambda: InfoBar.warning(
    'SouthsideClient connection',
    'SouthsideMusic was been disconnected from SouthsidClient',
    duration=5000,
    parent=mwindow
))

original_popen = subprocess.Popen

def patched_popen(*args, **kwargs):
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    kwargs['startupinfo'] = startupinfo
    kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    
    return original_popen(*args, **kwargs)

subprocess.Popen = patched_popen
subprocess.call = patched_popen

class DummyCard:
    def __init__(self, storable: SongStorable):
        self.info: SongInfo = SongInfo(
            name=storable.name,
            artists=storable.artists,
            id=storable.id,
            privilege=-1,
        )
        self.detail: SongDetail = SongDetail(image_url='')
        self.storable: SongStorable = storable

class MusicCard(QWidget):
    imageLoaded = Signal(bytes)

    def __init__(self, info: SongInfo) -> None:
        super().__init__()
        self.info = info
        self.setFixedHeight(105)

        self.detail = SongDetail(image_url='')

        global_layout = FlowLayout()

        ali = Qt.AlignmentFlag
        self.img_label = QLabel()
        self.img_label.setFixedSize(100, 100)
        global_layout.addWidget(self.img_label)
        self.img_label.hide()
        self.ring = IndeterminateProgressRing()
        self.ring.setFixedSize(100, 100)
        global_layout.addWidget(self.ring)
        title_label = SubtitleLabel(info['name'])
        global_layout.addWidget(title_label)
        artists_label = QLabel(info['artists'])
        artists_label.setWordWrap(True)
        global_layout.addWidget(artists_label)
        self.vip_label = SubtitleLabel('Needs VIP!')
        self.vip_label.setStyleSheet('color: red;')
        if info['privilege'] != 1:
            self.vip_label.hide()
        global_layout.addWidget(self.vip_label)

        self.playbtn = PrimaryToolButton(FluentIcon.SEND)
        self.playbtn.setEnabled(False)
        global_layout.addWidget(self.playbtn)
        self.playbtn.clicked.connect(self.play)

        self.favbtn = TransparentToolButton(getQIcon('fav'))
        self.favbtn.setEnabled(True)
        global_layout.addWidget(self.favbtn)
        self.favbtn.clicked.connect(self.addToFavorites)

        self.setLayout(global_layout)

        self.load = False
        self.imageLoaded.connect(self.onImageLoaded)

    def play(self):
        mwindow.play(self)

    def addToFavorites(self):
        if self.info['privilege'] == 1:
            InfoBar.warning(
                'Cannot add to favorites',
                'VIP songs cannot be added to favorites',
                parent=mwindow,
            )
            return

        result_container = []

        def _download():
            # Get image URL
            response = requests.get(
                f'https://apis.netstart.cn/music/song/detail?ids={self.info['id']}',
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                },
            ).json()
            image_url = response['songs'][0]['al']['picUrl']

            # Download image
            image_bytes = requests.get(
                image_url,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                },
            ).content

            # Download music
            music_bytes = requests.get(
                f'https://music.163.com/song/media/outer/url?id={self.info['id']}.mp3',
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                },
            ).content

            # Download lyrics
            lyric_data = requests.get(
                f'https://apis.netstart.cn/music/lyric?id={self.info['id']}',
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                },
            ).json()
            
            lyric = lyric_data['lrc']['lyric']
            translated_lyric = lyric_data.get('tlyric', {}).get('lyric', '[00:00.000]')

            result_container.append((image_bytes, music_bytes, lyric, translated_lyric))

        def _on_finished():
            if not result_container:
                InfoBar.error(
                    'Failed to add to favorites', 'Download failed', parent=mwindow
                )
                return

            image_bytes, music_bytes, lyric, translated_lyric = result_container[0]

            # Load favorites

            favs = loadFavorites()
            
            # Prepare folder list for selection
            folder_names = [folder['folder_name'] for folder in favs]
            folder_names.append('Create new folder...')
            
            # Let user select folder
            selected = get_value_bylist(
                mwindow,
                'Select folder',
                f'which folder do you want to add {self.info['name']} to?',
                folder_names
            )
            
            if not selected:
                # User cancelled
                return
            
            selected_folder = selected
            
            # Handle new folder creation
            if selected_folder == 'Create new folder...':
                new_folder_name = get_text_lineedit(
                    'Create New Folder',
                    'Enter folder name',
                    'My folder',
                    mwindow
                )

                if not new_folder_name:
                    # User cancelled
                    return
                
                # Create new folder
                new_folder = FolderInfo(folder_name=new_folder_name, songs=[])
                favs.append(new_folder)
                selected_folder = new_folder_name
            
            # Find the target folder
            target_folder = None
            for folder in favs:
                if folder['folder_name'] == selected_folder:
                    target_folder = folder
                    break
            
            # Add song to target folder
            
            with lock:
                song_storable = SongStorable(self.info, image_bytes, music_bytes, lyric, translated_lyric, getAdjustedGainFactor(-16, AudioSegment.from_file(io.BytesIO(music_bytes), format='mp3')), cfg.target_lufs)
                target_folder['songs'].append(song_storable) # type: ignore

            # Save favorites
            from utils.favorite_util import saveFavorites

            saveFavorites(favs)

            InfoBar.success(
                'Added to favorites',
                f'Added {self.info['name']} To {selected_folder}',
                parent=mwindow,
                duration=2000,
            )

            fp.refresh()

        doWithMultiThreading(_download, (), mwindow, 'Downloading...', _on_finished)

    def loadDetailAndImage(self):
        self.load = True

        @lru_cache(maxsize=128)
        def _load():
            response = requests.get(
                f'https://apis.netstart.cn/music/song/detail?ids={self.info['id']}',
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                },
            ).json()

            self.detail['image_url'] = response['songs'][0]['al']['picUrl']

            image: bytes = requests.get(
                self.detail['image_url'],
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                },
            ).content

            self.imageLoaded.emit(image)

        doWithMultiThreading(_load, (), mwindow, f'Loading...', dialog=False)

    @Slot(bytes)
    def onImageLoaded(self, byte_data: bytes):
        pixmap = QPixmap()
        pixmap.loadFromData(byte_data)
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(
                self.img_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.img_label.setPixmap(scaled_pixmap)

            self.img_label.show()
            self.ring.hide()

            if self.info['privilege'] != 1:
                self.playbtn.setEnabled(True)


class SearchPage(QWidget):
    resultGot = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName('search_page')
        self.img_card_map: dict[str, MusicCard] = {}

        global_layout = QVBoxLayout()

        top_layout = QHBoxLayout()
        self.inputer = LineEdit()
        self.search_btn = PrimaryPushButton(FluentIcon.SEARCH, 'Search')
        self.search_btn.clicked.connect(self.search)
        self.inputer.returnPressed.connect(self.search)
        top_layout.addWidget(self.inputer)
        top_layout.addWidget(self.search_btn)
        global_layout.addLayout(top_layout)

        self.lst = ListWidget()
        self.lst.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.lst.verticalScrollBar().setSingleStep(14)
        self.lst.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.lst.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        global_layout.addWidget(self.lst)

        self.setLayout(global_layout)

        self.resultGot.connect(self.addSongs)

        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self.checkRect)
        self.check_timer.start(50)

        self.cards: list[MusicCard] = []

    def checkRect(self) -> None:
        for i, card in enumerate(self.cards):
            item = self.lst.item(i)
            if item is None:
                continue

            item_rect = self.lst.visualItemRect(item)
            viewport_rect = self.lst.viewport().rect()

            if viewport_rect.intersects(item_rect) and not card.load:
                logging.debug(f'loading {card.info['name']}')
                card.loadDetailAndImage()

    def setImage_(self, byte: bytes, ca: MusicCard):
        ca.img_label.setPixmap(QPixmap(byte))

    def search(self) -> None:
        if not self.inputer.text().strip():
            InfoBar.warning('Search failed', 'the keyword is empty!', parent=mwindow)
            return

        self.search_btn.setEnabled(False)
        self.lst.clear()
        self.cards.clear()
        self.img_card_map.clear()

        result: list[SongInfo] = []

        @cache
        def _do():
            nonlocal result
            result = wy.search(self.inputer.text())

        def _finish():
            nonlocal result

            for info in result:
                logging.debug(f'{info['name']} -> {info['privilege']}')

            self.search_btn.setEnabled(True)

            self.resultGot.emit(result)

        doWithMultiThreading(_do, (), mwindow, 'Searching...', _finish)

    def addSongs(self, result: list[SongInfo]) -> None:
        for i, song in enumerate(result):
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 150))
            self.lst.addItem(item)
            content_widget = MusicCard(song)
            self.lst.setItemWidget(item, content_widget)
            self.cards.append(content_widget)
            content_widget.load = False


class PlayingController(QWidget):
    onSongFinish = Signal()
    playLastSignal = Signal()
    playNextSignal = Signal()

    def __init__(self):
        super().__init__()
        self.expanded = False
        self.dragging = False

        global_layout = QHBoxLayout()

        self.cur_freqs: np.ndarray | None = None
        self.cur_magnitudes: np.ndarray | None = None
        self.smoothed_magnitudes: np.ndarray = np.zeros(513, dtype=np.float32)
        self.last_lyric: str = ''

        self.time_label = QLabel()
        global_layout.addWidget(
            self.time_label,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )

        self.last_btn = TransparentToolButton(getQIcon('last'))
        self.next_btn = TransparentToolButton(getQIcon('next'))
        self.last_btn.clicked.connect(self.playLastSignal.emit)
        self.next_btn.clicked.connect(self.playNextSignal.emit)

        self.play_pausebtn = TransparentToolButton(getQIcon('playa'))
        self.play_pausebtn.setIconSize(QSize(30, 30))
        self.last_btn.setIconSize(QSize(30, 30))
        self.next_btn.setIconSize(QSize(30, 30))
        self.play_pausebtn.clicked.connect(self.toggle)
        global_layout.addWidget(
            self.last_btn,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
        )
        global_layout.addWidget(
            self.play_pausebtn,
            alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
        )
        global_layout.addWidget(
            self.next_btn,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )

        right_layout = QVBoxLayout()

        self.vol_slider = Slider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(100)
        self.vol_slider.valueChanged.connect(self.updateVol)
        right_layout.addWidget(
            self.vol_slider,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
        )
        self.expand_btn = PushButton(getQIcon('pl_expand'), 'Menu')
        right_layout.addWidget(
            self.expand_btn,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
        )
        self.expand_btn.clicked.connect(self.toggleExpand)

        global_layout.addLayout(right_layout)

        self.setLayout(global_layout)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.repaint)
        self.timer.start(20)

        self.timelabel_updater = QTimer(self)
        self.timelabel_updater.timeout.connect(self.updateWidgets)
        self.timelabel_updater.start(50)
        self.playingtime_lastupdate = time.perf_counter()

        player.fftDataReady.connect(self.updateFFTData)

    def updateFFTData(self, freqs: np.ndarray, magnitudes: np.ndarray) -> None:
        self.cur_freqs = freqs
        self.cur_magnitudes = magnitudes

    def toggleExpand(self):
        self.expanded = not self.expanded

        if self.expanded:
            if not mwindow.isMaximized():
                mwindow_anim = QPropertyAnimation(mwindow, b'geometry', self)
                mwindow_anim.setDuration(200)
                mwindow_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                mwindow_anim.setStartValue(mwindow.geometry())
                mwindow_anim.setEndValue(QRect(mwindow.x() - 252, mwindow.y(), mwindow.width() + 505, mwindow.height()))
                mwindow_anim.start()

            dp.expanded_widget.show()

            self.expand_btn.setText('Collapse')
            self.expand_btn.setIcon(getQIcon('pl_collapse'))
        else:
            dp.expanded_widget.hide()
            if not mwindow.isMaximized():
                mwindow_anim = QPropertyAnimation(mwindow, b'geometry', self)
                mwindow_anim.setDuration(200)
                mwindow_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                mwindow_anim.setStartValue(mwindow.geometry())
                mwindow_anim.setEndValue(QRect(mwindow.x() + 253, mwindow.y(), mwindow.width() - 505, mwindow.height()))
                mwindow_anim.start()

            self.expand_btn.setText('Menu')
            self.expand_btn.setIcon(getQIcon('pl_expand'))

    def updateWidgets(self):
        if dp.cur and dp.lst_shoud_set:
            # Highlight the currently playing song in the playlist
            for i, song in enumerate(dp.playlist):
                if dp.cur and hasattr(dp.cur, 'storable') and song.name == dp.cur.storable.name:
                    dp.lst.setCurrentRow(i)
                    break

        if player.isPlaying():
            if not dp._preload_triggered and dp.current_index < len(dp.playlist) - 1:
                dp._preload_triggered = True
                dp.preloadNextSong()

        cl = mgr.getCurrentLyric(player.getPosition())
        nxt = mgr.getOffsetedLyric(player.getPosition(), 1)
        trd = mgr.getOffsetedLyric(player.getPosition(), 2)
        lat = mgr.getOffsetedLyric(player.getPosition(), -1)
        if cl['content'] != self.last_lyric:
            ws_handler.send(json.dumps({
                'option': 'update_lyric',
                'current': cl['content'],
                'next': nxt['content'],
                'third': trd['content'],
                'last': lat['content']
            }))
            self.last_lyric = cl['content']

    def updateVol(self):
        value = self.vol_slider.value()
        if value == 0:
            volume = 0
        else:
            volume = math.log(value / 100 * (math.e - 1) + 1)
        logging.debug(volume)
        player.setVolume(volume)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.position().y() < 8:
            self.dragging = True
            playing_time = min(
                dp.total_length,
                max(0, (event.position().x() / self.width()) * dp.total_length),
            )
            player.setPosition(playing_time)
        return super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.dragging:
            playing_time = min(
                dp.total_length,
                max(0, (event.position().x() / self.width()) * dp.total_length),
            )
            player.setPosition(playing_time)
            self.dragging = False
        return super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.dragging:
            playing_time = min(
                dp.total_length,
                max(0, (event.position().x() / self.width()) * dp.total_length),
            )
            player.setPosition(playing_time)
        return super().mouseMoveEvent(event)

    def toggle(self):
        logging.debug('toggle')

        if player.isPlaying():
            player.pause()
            self.play_pausebtn.setIcon(getQIcon('playa'))
        else:
            player.resume()
            self.play_pausebtn.setIcon(getQIcon('pause'))
    
    def setPlaytime(self, time_value: float) -> None:
        playing_time = time_value
        player.setPosition(playing_time)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)

        isDark = darkdetect.isDark()

        if dp.enableFFT_box.isChecked() and self.cur_freqs is not None and self.cur_magnitudes is not None:
            if not player.isPlaying():
                self.cur_magnitudes = np.zeros(513, dtype=np.float32)
            window_size = cfg.fft_filtering_windowsize

            self.smoothed_magnitudes += (self.cur_magnitudes - self.smoothed_magnitudes) * (cfg.fft_factor if player.isPlaying() else 0.07)
            final_magnitudes = np.convolve(
                self.smoothed_magnitudes,
                np.ones(window_size) / window_size,
                mode='same'
            )
            if isinstance(dp.cur, DummyCard):
                final_magnitudes *= (2 / dp.cur.storable.loudness_gain) * 0.75

            path = QPainterPath(QPointF(0, 0))
            total = int(self.cur_magnitudes.size / 1.5)
            for i in range(total):
                x = ((i + 1) / total) * self.width()
                path.lineTo(QPointF(x, final_magnitudes[i] * ((1 + (i * 0.01)) - 0.1) + 3.5))
            path.lineTo(QPointF(self.width(), 0))

            painter.setPen(QPen(QColor(120, 120, 120), 1))
            painter.setClipPath(path)
            painter.drawPath(path)
            gradient = QLinearGradient(0, self.height(), 0, 0)
            gradient.setColorAt(1, QColor(QColor(255, 255, 255, 150) if isDark else QColor(0, 0, 0, 150)))
            gradient.setColorAt(0.5, QColor(0, 0, 0, 0))
            painter.fillRect(0, 0, self.width(), self.height(), gradient)

        painter.setPen(QPen(QColor(120, 120, 120), 8))
        painter.drawLine(0, 0, self.width(), 0)
        if dp.total_length > 0:
            painter.setPen(
                QPen(
                    QColor(255, 255, 255) if isDark else QColor(0, 0, 0), 8
                )
            )
            painter.drawLine(
                0, 0, int(self.width() * (player.getPosition() / dp.total_length)), 0
            )

        cur_time = float2time(player.getPosition())
        # self.time_label.setText(
        #     f'{str(cur_time['minutes']).zfill(2)}:{str(cur_time['seconds']).zfill(2)}.{str(cur_time['millionsecs']).zfill(3)}'
        # )
        self.time_label.setText(
            f'{str(cur_time['minutes']).zfill(2)}:{str(cur_time['seconds']).zfill(2)}'
        )
        
        yw = mgr.getCurrentLyric(player.getPosition())['content']
        dp.lyric_label.setText(yw)
        if not yw.strip():
            dp.transl_label.setText('')
        else:
            dp.transl_label.setText(transmgr.getCurrentLyric(player.getPosition())['content'])

        painter.end()

class PlayingPage(QWidget):
    imageLoaded = Signal(bytes)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName('studio_page')
        self.cur: DummyCard | None = None

        self.total_length = 0

        self.shoud_expand_when_show: bool = False

        self._preload_triggered = False

        # Playlist management
        self.playlist: list[SongStorable] = []
        self.current_index = -1
        self.next_song_audio: AudioSegment | None = None
        self.next_song_gain: float | None = None

        # Caches
        self._gain_cache: dict[str, float] = {}

        self.controller = PlayingController()
        player.onFinished.connect(self.controller.onSongFinish.emit)
        self.controller.onSongFinish.connect(lambda: self.playNext(False))
        # Connect play button to start playlist if no song is loaded
        self.controller.play_pausebtn.clicked.connect(self.onPlayButtonClicked)

        self.lst_shoud_set: bool = True

        global_layout = QHBoxLayout()

        contents_layout = QVBoxLayout()

        ali = Qt.AlignmentFlag

        top_layout = FlowLayout(needAni=False)
        # top_layout.setAnimation(500, QEasingCurve.Type.OutCubic)
        topright_layout = QVBoxLayout()
        topright_widget = QWidget()
        topright_widget.setLayout(topright_layout)
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
        topright_layout.addWidget(
            self.title_label, alignment=ali.AlignLeft | ali.AlignTop
        )
        topright_layout.addWidget(
            self.artists_label, alignment=ali.AlignLeft | ali.AlignTop
        )
        top_layout.addWidget(topright_widget)

        contents_layout.addLayout(top_layout)

        middle_layout = QVBoxLayout()
        self.lyric_label = SubtitleLabel('null')
        self.transl_label = QLabel('null')
        middle_layout.addWidget(
            self.lyric_label, alignment=ali.AlignHCenter | ali.AlignBottom
        )
        middle_layout.addWidget(
            self.transl_label, alignment=ali.AlignHCenter | ali.AlignTop
        )
        contents_layout.addLayout(middle_layout)

        self.controller.setFixedWidth(self.width())
        contents_layout.addWidget(
            self.controller, alignment=ali.AlignBottom | ali.AlignHCenter
        )

        global_layout.addLayout(contents_layout)

        self.expanded_widget = QWidget()
        expanded_layout = QVBoxLayout()
        self.pivot = Pivot(self)
        self.stacked_widget = QStackedWidget(self)

        self.expanded_widget.setFixedWidth(500)

        expanded_layout.addWidget(self.pivot)
        expanded_layout.addWidget(self.stacked_widget)
        expanded_layout.setContentsMargins(30, 0, 30, 30)

        self.lst_interface = QWidget()
        self.lst_layout = QVBoxLayout()
        self.lst = ListWidget()
        self.lst.setFixedWidth(500)
        self.lst.entered.connect(lambda: self.__setattr__('lst_shoud_set', False))
        self.lst.leaveEvent = lambda e: self.__setattr__('lst_shoud_set', True)
        self.lst.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.lst.itemClicked.connect(self.onPlaylistItemClicked)
        self.lst_layout.addWidget(self.lst)
        
        btn_layout = QHBoxLayout()
        self.delete_btn = TransparentPushButton(FluentIcon.DELETE, 'Remove')
        self.delete_btn.clicked.connect(self.removeSong)
        self.add_btn = TransparentPushButton(getQIcon('pl'), 'Add')
        self.add_btn.clicked.connect(self.addSong)
        self.insert_btn = TransparentPushButton(getQIcon('insert'), 'Insert')
        self.insert_btn.clicked.connect(self.insertSong)
        self.removeall_btn = TransparentPushButton(getQIcon('clearall'), 'Remove All')
        self.removeall_btn.clicked.connect(self.removeAllSongs)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.insert_btn)
        btn_layout.addWidget(self.removeall_btn)
        self.lst_layout.addLayout(btn_layout)

        self.lst_interface.setLayout(self.lst_layout)

        self.playing_scrollarea = SmoothScrollArea()

        self.playing_interface = QWidget()
        self.playing_interface.setStyleSheet(f'background: #{'000000' if darkdetect.isDark() else 'FFFFFF'}')
        self.playing_layout = QGridLayout()

        self.addSeparateWidget(TitleLabel('Playing'))

        self.play_method_box = ComboBox()
        self.play_method_box.addItems(['Repeat one', 'Repeat list', 'Shuffle', 'Play in order'])
        self.play_method_box.setCurrentText('Repeat list')
        self.addSetting('Play order', self.play_method_box)

        self.play_speed = DoubleSpinBox()
        self.play_speed.setRange(0.1, 5)
        self.play_speed.setSingleStep(0.1)
        self.play_speed.valueChanged.connect(self.onPlaySpeedChanged)
        self.play_speed.setValue(1)
        self.addSetting('Play Speed(poor effect)', self.play_speed)

        self.addSeparateWidget(TitleLabel('FFT'))

        self.enableFFT_box = CheckBox('Enable Frequency Graphics')
        self.enableFFT_box.checkStateChanged.connect(self.onFFTEnabledStateChanged)
        self.addSeparateWidget(self.enableFFT_box)
        self.enableFFT_box.setChecked(cfg.enable_fft)

        self.FFT_filtering_windowsize = SpinBox()
        self.FFT_filtering_windowsize.setRange(1, 200)
        self.FFT_filtering_windowsize.setSingleStep(1)
        self.FFT_filtering_windowsize.valueChanged.connect(self.onFFTWindowsizeChanged)
        self.FFT_filtering_windowsize.setValue(cfg.fft_filtering_windowsize)
        self.FFT_filtering_windowsize.setEnabled(cfg.enable_fft)
        self.addSetting('FFT Filtering Window size', self.FFT_filtering_windowsize)

        self.FFT_factor = DoubleSpinBox()
        self.FFT_factor.setRange(0.01, 1.0)
        self.FFT_factor.setSingleStep(0.05)
        self.FFT_factor.valueChanged.connect(self.onFFTFactorChanged)
        self.FFT_factor.setValue(cfg.fft_factor)
        self.FFT_factor.setEnabled(cfg.enable_fft)
        self.addSetting('FFT Smoothing Factor', self.FFT_factor)

        self.addSeparateWidget(TitleLabel('Loudness Balance'))
        
        self.target_lufs = Slider(Qt.Orientation.Horizontal)
        self.target_lufs.setRange(-60, 0)
        self.target_lufs.setSingleStep(1)
        self.target_lufs.valueChanged.connect(self.onTargetLUFSChanged)
        self.target_lufs.setValue(cfg.target_lufs)
        self.addSeparateWidget(self.target_lufs)
        self.target_lufs_label = SubtitleLabel(f'Target LUFS: {cfg.target_lufs}')
        self.addSeparateWidget(self.target_lufs_label)
        self.addSeparateWidget(QLabel(
            'Target LUFS Help:\nRange: -60(quietest)~0(loudest)\nRecommend: -16~-18'
            '\nReference:\nYoutube > -14LUFS\nNetflix > -27LUFS\nTikTok / Instagram Reels > -13LUFS\nApple Music (Video) > -16LUFS'
            '\nSpotify (Video): -14LUFS / -16LUFS'
        ))

        self.song_randomer = AdvancedRandom()
        self.song_randomer.init(self.playlist)

        self.playing_interface.setLayout(self.playing_layout)
        self.playing_scrollarea.setWidget(self.playing_interface)
        self.playing_scrollarea.setWidgetResizable(True)

        self.addSubInterface(self.lst_interface, 'playlist_listwidget', 'Playlist')
        self.addSubInterface(self.playing_scrollarea, 'playing_interface', 'Options')

        self.stacked_widget.setCurrentWidget(self.lst)
        self.pivot.setCurrentItem('playlist_listwidget')
        self.pivot.currentItemChanged.connect(lambda k: self.stacked_widget.setCurrentWidget(self.findChild(QWidget, k))) # type: ignore

        self.expanded_widget.setLayout(expanded_layout)
        global_layout.addWidget(self.expanded_widget)

        self.expanded_widget.hide()

        self.setLayout(global_layout)

        self.imageLoaded.connect(self.onImageLoaded)

        self.controller.playLastSignal.connect(self.playLast)
        self.controller.playNextSignal.connect(lambda: self.playNext(True))

        self.onFFTEnabledStateChanged(self.enableFFT_box.checkState())

        self.lufs_changed_timer = QTimer(self)
        self.lufs_changed_timer.timeout.connect(self.applyNewLUFS)
    
    def removeSong(self) -> None:
        selected = get_value_bylist(mwindow, 'Remove Song', 'select a song to remove', [song.name for song in self.playlist])

        if not selected:
            return
        
        for i, storable in enumerate(self.playlist):
            if storable.name == selected:
                self.playlist.remove(self.playlist[i])

                InfoBar.success(
                    'Removed',
                    f'{selected} has been removed',
                    duration=1500,
                    parent=mwindow
                )
                break

        self.refreshPlaylistWidget()

        if self.cur:
            if selected == self.cur.storable.name:
                self.playSongAtIndex(self.current_index)
    def addSong(self) -> None:
        selected = getFavoriteSong()

        if not selected:
            return
        
        self.playlist.append(selected)
        InfoBar.success(
            'Added',
            f'Added {selected.name} to playlist',
            duration=1500,
            parent=mwindow
        )

        self.refreshPlaylistWidget()

        self.preloadNextSong()
    def insertSong(self) -> None:
        selected = getFavoriteSong()

        if not selected:
            return
        
        self.playlist.insert(self.current_index + 1, selected)
        InfoBar.success(
            'Inserted',
            f'Inserted {selected.name} to next song',
            duration=1500,
            parent=mwindow
        )

        self.refreshPlaylistWidget()

        self.preloadNextSong()
    def removeAllSongs(self) -> None:
        self.playlist.clear()
        if isinstance(self.cur, DummyCard) and isinstance(self.cur.storable, SongStorable):
            self.playlist.append(self.cur.storable)

        self.refreshPlaylistWidget()

        InfoBar.success(
            'Inserted',
            'Removed all songs',
            duration=1500,
            parent=mwindow
        )

    def refreshPlaylistWidget(self):
        self.lst.clear()

        for song in self.playlist:
            self.lst.addItem(song.name)

    def applyNewLUFS(self):
        self.lufs_changed_timer.stop()

        self.target_lufs_label.setText('Reapplying')
        def _apply():
            if not isinstance(self.cur, DummyCard):
                return
            if not hasattr(self.cur, 'storable'):
                return
        
            audio: AudioSegment = AudioSegment.from_file(io.BytesIO(base64.b64decode(self.cur.storable.content_base64)), format='mp3')
            logging.debug('new lufs -> applying gain')
            gain = getAdjustedGainFactor(cfg.target_lufs, audio)
            self.cur.storable.loudness_gain = gain
            self.cur.storable.target_lufs = cfg.target_lufs

            p = player.getPosition()
            playingnow = player.isPlaying()
            self.playStorable(self.cur.storable)
            if not playingnow:
                self.controller.toggle()

            self.target_lufs_label.setText(f'Target LUFS: {cfg.target_lufs}')
            player.setPosition(p)

            QTimer.singleShot(250, self.preloadNextSong)

        threading.Thread(target=_apply).start()

    def onPlaySpeedChanged(self, value: float):
        player.play_speed = value

    def onTargetLUFSChanged(self, value: int):
        cfg.target_lufs = value
        if hasattr(self, 'target_lufs_label'):
            self.target_lufs_label.setText(f'Target LUFS: {value}')
            self.lufs_changed_timer.start(1000)

    def addSetting(self, name: str, widget: QWidget) -> None:
        self.playing_layout.addWidget(QLabel(name), self.playing_layout.rowCount(), 0, Qt.AlignmentFlag.AlignVCenter)
        self.playing_layout.addWidget(widget, self.playing_layout.rowCount() - 1, 1, Qt.AlignmentFlag.AlignVCenter)

    def addSeparateWidget(self, widget: QWidget) -> None:
        self.playing_layout.addWidget(widget, self.playing_layout.rowCount(), 0, 1, 2)

    def onFFTWindowsizeChanged(self, value: int):
        if value < 1 or value > 200:
            self.FFT_filtering_windowsize.setValue(max(1, min(self.FFT_filtering_windowsize.value(), 200)))

        cfg.fft_filtering_windowsize = self.FFT_filtering_windowsize.value()

    def onFFTFactorChanged(self, value: float):
        if value < 0.01 or value > 1:
            self.FFT_factor.setValue(max(0.01, min(self.FFT_filtering_windowsize.value(), 1.0)))

        cfg.fft_factor = self.FFT_factor.value()

    def addSubInterface(self, widget: QWidget, objectName, text):
        widget.setObjectName(objectName)
        self.stacked_widget.addWidget(widget)
        self.pivot.addItem(routeKey=objectName, text=text)

    def onFFTEnabledStateChanged(self, check_state: Qt.CheckState) -> None:
        checked = check_state == Qt.CheckState.Checked
        player.fft_enabled = checked
        cfg.enable_fft = checked

        if hasattr(self, 'FFT_filtering_windowsize'):
            self.FFT_filtering_windowsize.setEnabled(checked)
            self.FFT_factor.setEnabled(checked)

    def onPlaylistItemClicked(self, item: QListWidgetItem):
        for i, song in enumerate(self.playlist):
            if song.name == item.text():
                self.current_index = i
                self.playSongAtIndex(i)

    def init(self):
        if self.cur is None:
            return

        for label in self.findChildren(QLabel):
            label.setWordWrap(True)

        self.title_label.setText(self.cur.info['name'])
        self.artists_label.setText(self.cur.info['artists'])

        # Check if cur has storable attribute (DummyCard from playlist)
        if hasattr(self.cur, 'storable'):
            # Use local data from storable
            import base64

            image_bytes = base64.b64decode(self.cur.storable.image_base64)
            self.onImageLoaded(image_bytes)
            if self.cur.storable.target_lufs == cfg.target_lufs:
                self.loadMusicFromBase64(self.cur.storable.content_base64, self.cur.storable.loudness_gain)
            else:
                self.applyNewLUFS()
            
            # Use local lyrics if available
            if hasattr(self.cur.storable, 'lyric') and self.cur.storable.lyric:
                mgr.cur = self.cur.storable.lyric
                
                if hasattr(self.cur.storable, 'translated_lyric') and self.cur.storable.translated_lyric:
                    transmgr.cur = self.cur.storable.translated_lyric
                else:
                    transmgr.cur = '[00:00.000]'
                
                def _parse_local():
                    mgr.parse()
                    transmgr.parse()
                
                doWithMultiThreading(_parse_local, (), mwindow, 'Parsing...')
            else:
                # Fallback to network download
                self.downloadLyric()
            
            mwindow.switchTo(dp)
        else:
            # Original network download
            if player.isPlaying():
                player.stop()
            
            def _do():
                img_bytes = requests.get(
                    self.cur.detail['image_url'], # type: ignore
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                    },
                ).content
                self.imageLoaded.emit(img_bytes)

            self.img_label.hide()
            self.ring.show()
            doWithMultiThreading(_do, (), mwindow, 'Loading...')

    def preloadNextSong(self):
        if len(dp.playlist) <= 1:
            return
        if dp.current_index >= len(dp.playlist) - 1:
            return

        try:
            logging.info('preloading')

            next_song = self.playlist[self.current_index + 1]

            logging.debug(next_song)

            if self.play_method_box.currentText() == 'Play in order':
                if self.current_index + 1 >= len(self.playlist):
                    return
            elif self.play_method_box.currentText() == 'Repeat list':
                if self.current_index + 1 >= len(self.playlist):
                    next_song = self.playlist[0]
                else:
                    next_song = self.playlist[self.current_index + 1]
            else:
                next_song = self.playlist[self.current_index + 1]
            if not (self.play_method_box.currentText() in ['Play in order', 'Repeat list']):
                return

            def _preload():
                with lock:
                    song_bytes = base64.b64decode(next_song.content_base64)
                    self.next_song_audio = AudioSegment.from_file(io.BytesIO(song_bytes), format='mp3')

                if (next_song.loudness_gain == 1.0 or next_song.target_lufs != cfg.target_lufs) and isinstance(self.next_song_audio, AudioSegment):
                    next_song.loudness_gain = getAdjustedGainFactor(cfg.target_lufs, self.next_song_audio)
                    next_song.target_lufs = cfg.target_lufs
                self.next_song_gain = next_song.loudness_gain

                if isinstance(self.next_song_audio, AudioSegment):
                    logging.debug(f'preload -> applying gain {self.next_song_gain} {cfg.target_lufs=}')
                    self.next_song_audio = self.next_song_audio.apply_gain(20 * np.log10(self.next_song_gain))

                logging.info('preloaded')
                logging.debug(f'(Types) {type(self.next_song_audio)=} {type(self.next_song_gain)=}')

            threading.Thread(target=_preload, daemon=True).start()
        finally:
            logging.debug('started preload thread')

    def downloadLyric(self):
        assert self.cur is not None

        def _parse():
            data = requests.get(
                f'https://apis.netstart.cn/music/lyric?id={self.cur.info['id']}', # type: ignore
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                },
            ).json()
            mgr.cur = data['lrc']['lyric']
            if data.get('tlyric'):
                transmgr.cur = '\n'.join(data['tlyric']['lyric'].splitlines()[1:])
            else:
                transmgr.cur = '[00:00.000]'

            def _real():
                mgr.parse()
                transmgr.parse()

            def _fini():
                self.controller.toggle()

            doWithMultiThreading(_real, (), mwindow, 'Parsing...', finished=_fini)

        doWithMultiThreading(_parse, (), mwindow, 'Loading...')

    def downloadMusic(self):
        assert self.cur is not None

        def _downloaded(bytes: bytes):
            if player.isPlaying():
                player.stop()

            with lock:
                audio = AudioSegment.from_file(io.BytesIO(bytes), format='mp3')

            player.load(audio)
            self.total_length = player.getLength()
            self.controller.toggle()

            def computeGain():
                try:
                    gain = getAdjustedGainFactor(cfg.target_lufs, audio)
                    if self.cur:
                        self._gain_cache[self.cur.info['id']] = gain
                    player.setGain(gain)
                except Exception as e:
                    pass

            threading.Thread(target=computeGain, daemon=True).start()

            self.downloadLyric()

        downloadWithMultiThreading(
            f'https://music.163.com/song/media/outer/url?id={self.cur.info['id']}.mp3',
            {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            },
            None,
            mwindow,
            'Loading...',
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

        if not hasattr(self.cur, 'storable'):
            self.downloadMusic()

    def onPlayButtonClicked(self):
        # If no song is currently loaded, start playlist
        if self.cur is None:
            self.startPlaylist()

    def playNext(self, byuser: bool):
        logging.debug(f'(Types) {type(self.next_song_audio)=} {type(self.next_song_gain)=}')
        if isinstance(self.next_song_audio, AudioSegment) and isinstance(self.next_song_gain, float):
            self.playPreloadedSong()
            self.current_index += 1
            return

        if self.current_index < 0 or self.current_index >= len(self.playlist) - 1:
            if dp.play_method_box.currentText() == 'Play in order':
                # No next song, reset and pause
                InfoBar.warning('Warning', 'This song is the last song in the playlist.', parent=mwindow)
                self.controller.setPlaytime(0)
                return
            elif dp.play_method_box.currentText() == 'Repeat list':
                self.current_index = 0
                self.playSongAtIndex(self.current_index)
                return

        if dp.play_method_box.currentText() == 'Repeat one' and not byuser:
            self.playSongAtIndex(self.current_index)
            return
        elif dp.play_method_box.currentText() == 'Shuffle':
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
        if (not isinstance(self.next_song_audio, AudioSegment)) or (not isinstance(self.next_song_gain, float)):
            logging.error(f'cant play preloaded song: (Types) {type(self.next_song_audio)=} {type(self.next_song_gain)=}')
            return
        
        logging.info('using preloaded song')

        song_storable = self.playlist[self.current_index + 1]
        self.cur = DummyCard(song_storable)

        # Update UI
        self.title_label.setText(song_storable.name)
        self.artists_label.setText(song_storable.artists)
        self.lyric_label.setText('Loading...')
        self.transl_label.setText('')
        QApplication.processEvents()

        image_bytes = base64.b64decode(song_storable.image_base64)
        self.onImageLoaded(image_bytes)
        
        audio = self.next_song_audio

        player.load(audio)
        self.total_length = player.getLength()

        mgr.cur = song_storable.lyric
        if song_storable.translated_lyric:
            transmgr.cur = song_storable.translated_lyric
        else:
            transmgr.cur = '[00:00.000]'
        try:
            mgr.parse()
            transmgr.parse()
        finally:
            if not player.isPlaying():
                self.controller.toggle()

            self._preload_triggered = False
            self.next_song_audio = None
            self.next_song_gain = None

    def playLast(self):
        if self.current_index < 1 or self.current_index >= len(self.playlist):
            # No last song, reset and pause
            InfoBar.warning('Warning', 'This song is the first song in the playlist.', parent=mwindow)
            self.controller.setPlaytime(0)
            return
        
        self._preload_triggered = False
        self.next_song_audio = None
        self.next_song_gain = None

        self.current_index -= 1
        self.playSongAtIndex(self.current_index)

    def playSongAtIndex(self, index: int):
        if index < 0 or index >= len(self.playlist):
            return

        self.current_index = index
        song = self.playlist[index]
        self.playStorable(song)

    def playStorable(self, song_storable: SongStorable):
        self.cur = DummyCard(song_storable)

        # Update UI
        self.title_label.setText(song_storable.name)
        self.artists_label.setText(song_storable.artists)
        self.lyric_label.setText('Loading...')
        self.transl_label.setText('')
        QApplication.processEvents()

        image_bytes = base64.b64decode(song_storable.image_base64)
        self.onImageLoaded(image_bytes)

        # Load from base64
        if song_storable.target_lufs == cfg.target_lufs:
            self.loadMusicFromBase64(song_storable.content_base64, song_storable.loudness_gain)
        else:
            music_bytes = base64.b64decode(song_storable.content_base64)
            logging.debug(f'loading data {len(music_bytes)}')
            with lock:
                audio = AudioSegment.from_file(io.BytesIO(music_bytes), format='mp3')

            song_storable.target_lufs = cfg.target_lufs
            song_storable.loudness_gain = getAdjustedGainFactor(cfg.target_lufs, audio)
            gain = song_storable.loudness_gain

            logging.debug(f'applying gain {gain} {cfg.target_lufs=}')
            audio = audio.apply_gain(20 * np.log10(gain))

            player.load(audio)
            self.total_length = player.getLength()

        mgr.cur = song_storable.lyric
        if song_storable.translated_lyric:
            transmgr.cur = song_storable.translated_lyric
        else:
            transmgr.cur = '[00:00.000]'
        try:
            mgr.parse()
            transmgr.parse()
        finally:
            if not player.isPlaying():
                self.controller.toggle()

            self._preload_triggered = False
            self.next_song_audio = None
            self.next_song_gain = None

    def loadMusicFromBase64(self, content_base64: str, gain: float):
        music_bytes = base64.b64decode(content_base64)
        logging.debug(f'loading data {len(music_bytes)}')
        with lock:
            audio = AudioSegment.from_file(io.BytesIO(music_bytes), format='mp3')

        logging.debug(f'applying gain {gain} {cfg.target_lufs=}')
        audio = audio.apply_gain(20 * np.log10(gain))

        player.load(audio)
        self.total_length = player.getLength()

    def startPlaylist(self):
        fp.folder_selector.setCurrentRow(0)
        fp.addFolderToPlaylist()

        # Start playing first song
        self.current_index = 0
        self.playSongAtIndex(0)

        # Ensure playing state
        if not player.isPlaying():
            self.controller.toggle()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.controller.toggle()
        return super().keyPressEvent(event)
    
    def showEvent(self, event: QShowEvent) -> None:
        if self.enableFFT_box.isChecked():
            player.fft_enabled = True
            logging.debug('enabled FFT')

        if self.shoud_expand_when_show:
            self.controller.toggleExpand()
        return super().showEvent(event)
    
    def hideEvent(self, event: QHideEvent) -> None:
        player.fft_enabled = False
        logging.debug('disabled FFT')

        if not globals().get('mwindow'):
            return
        if mwindow.closing:
            return

        if self.controller.expanded:
            self.controller.toggleExpand()
            self.shoud_expand_when_show = True
        else:
            self.shoud_expand_when_show = False
        return super().hideEvent(event)

class FavoritesPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName('favorites_page')

        global_layout = QVBoxLayout(self)

        top_layout = FlowLayout()
        top_layout.addWidget(TitleLabel('Favorites'))
        self.refresh_btn = PrimaryPushButton(FluentIcon.SYNC, 'Refresh')
        self.refresh_btn.clicked.connect(self.refresh)
        top_layout.addWidget(self.refresh_btn)
        self.newfolder_btn = PushButton(FluentIcon.ADD, 'New Folder')
        self.newfolder_btn.clicked.connect(self.newFolder)
        top_layout.addWidget(self.newfolder_btn)
        self.deletefolder_btn = PushButton(FluentIcon.DELETE, 'Delete Folder')
        self.deletefolder_btn.clicked.connect(self.deleteFolder)
        top_layout.addWidget(self.deletefolder_btn)
        self.renamefolder_btn = PushButton(FluentIcon.EDIT, 'Rename Folder')
        self.renamefolder_btn.clicked.connect(self.renameFolder)
        top_layout.addWidget(self.renamefolder_btn)
        global_layout.addLayout(top_layout)
        bottom_layout = QHBoxLayout()

        # Left side: folder selector and add to playlist button
        left_layout = QVBoxLayout()
        self.folder_selector = ListWidget()
        self.folder_selector.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.folder_selector.itemClicked.connect(self.viewSongs)
        left_layout.addWidget(self.folder_selector, 1)
        self.addplaylist_btn = PushButton(getQIcon('pl'), 'Add selected folder to playlist')
        self.addplaylist_btn.clicked.connect(self.addFolderToPlaylist)
        left_layout.addWidget(self.addplaylist_btn)
        self.addall_btn = PrimaryPushButton(getQIcon('pl', 'light'), 'Add all folder to playlist')
        self.addall_btn.clicked.connect(self.addAllToPlaylist)
        left_layout.addWidget(self.addall_btn)
        bottom_layout.addLayout(left_layout, 3)

        # Separator
        bottom_layout.addWidget(QLabel('>'), alignment=Qt.AlignmentFlag.AlignVCenter)

        # Right side: song viewer and delete song button
        right_layout = QVBoxLayout()
        self.song_viewer = ListWidget()
        self.song_viewer.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        right_layout.addWidget(self.song_viewer, 1)
        self.deletesong_btn = PushButton(FluentIcon.DELETE, 'Delete Song')
        self.deletesong_btn.clicked.connect(self.deleteSong)
        right_layout.addWidget(self.deletesong_btn)
        bottom_layout.addLayout(right_layout, 7)

        global_layout.addLayout(bottom_layout)

        self.setLayout(global_layout)

    def renameFolder(self):
        got = get_text_lineedit('Rename Folder', 'Enter new folder name:', self.folder_selector.selectedItems()[0].text(), mwindow)

        if got:
            global favs

            for i, folder in enumerate(favs):
                if folder['folder_name'] == self.folder_selector.selectedItems()[0].text():
                    favs[i]['folder_name'] = got
                    break
            saveFavorites(favs)

            self.refresh()

    def viewSongs(self, i: QListWidgetItem):
        def _view():
            global favs
            favs = loadFavorites()
            self.song_viewer.clear()
            for f in favs:
                if i.text() == f['folder_name']:
                    for song in f['songs']:
                        self.song_viewer.addItem(song.name)

        doWithMultiThreading(_view, (), mwindow, 'Loading...')

    def newFolder(self):
        from utils.lyrics.base_util import FolderInfo

        global favs

        name, ok = QInputDialog.getText(mwindow, 'New Folder', 'Enter folder name:')
        if ok and name:
            if not name.strip():
                InfoBar.warning(
                    'Invalid name', 'Folder name cannot be empty', parent=mwindow
                )
                return
            # Check duplicate
            for folder in favs:
                if folder['folder_name'] == name:
                    InfoBar.warning(
                        'Duplicate', 'Folder already exists', parent=mwindow
                    )
                    return
            favs.append(FolderInfo(folder_name=name, songs=[]))
            saveFavorites(favs)
            self.refresh()
            InfoBar.success(
                'Folder created', f'Folder {name} created', parent=mwindow
            )

    def deleteFolder(self):
        global favs
        selected = self.folder_selector.currentItem()
        if not selected:
            InfoBar.warning(
                'No selection', 'Please select a folder to delete', parent=mwindow
            )
            return

        folder_name = selected.text()
        reply = QMessageBox.question(
            mwindow,
            'Confirm Delete',
            f'Are you sure you want to delete folder {folder_name}?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            # Remove folder
            favs = [f for f in favs if f['folder_name'] != folder_name]
            saveFavorites(favs)
            self.refresh()
            InfoBar.success(
                'Folder deleted', f'Folder {folder_name} deleted', parent=mwindow
            )

    def deleteSong(self):
        global favs
        selected_folder = self.folder_selector.currentItem()
        if not selected_folder:
            InfoBar.warning(
                'No folder selected', 'Please select a folder first', parent=mwindow
            )
            return
        selected_song = self.song_viewer.currentItem()
        if not selected_song:
            InfoBar.warning(
                'No song selected', 'Please select a song to delete', parent=mwindow
            )
            return

        folder_name = selected_folder.text()
        song_name = selected_song.text()

        reply = QMessageBox.question(
            mwindow,
            'Confirm Delete',
            f'Are you sure you want to delete song {song_name} from folder {folder_name}?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            # Find folder and remove song
            for folder in favs:
                if folder['folder_name'] == folder_name:
                    folder['songs'] = [
                        s for s in folder['songs'] if s.name != song_name
                    ]
                    break
            saveFavorites(favs)
            self.viewSongs(selected_folder)  # refresh song view
            InfoBar.success(
                'Song deleted', f'Song {song_name} deleted', parent=mwindow
            )

    def addFolderToPlaylist(self):
        global favs
        selected_folder = self.folder_selector.currentItem()
        if not selected_folder:
            InfoBar.warning(
                'No folder selected', 'Please select a folder first', parent=mwindow
            )
            return

        folder_name = selected_folder.text()

        # Find folder
        target_folder = None
        for folder in favs:
            if folder['folder_name'] == folder_name:
                target_folder = folder
                break

        if not target_folder or not target_folder['songs']:
            InfoBar.warning(
                'Empty folder', f'Folder {folder_name} is empty', parent=mwindow
            )
            return

        if not dp:
            InfoBar.error(
                'Playlist not available',
                'Playlist page not initialized',
                parent=mwindow,
            )
            return

        added_count = 0
        for song in target_folder['songs']:
            # Check for duplicates
            if not any(s.name == song.name for s in dp.playlist):
                dp.playlist.append(song)
                dp.lst.addItem(song.name)
                added_count += 1

        if added_count > 0:
            InfoBar.success(
                'Songs added',
                f'Added {added_count} songs from folder {folder_name} to playlist',
                parent=mwindow,
            )
        else:
            InfoBar.info(
                'No new songs',
                f'All songs from folder {folder_name} already in playlist',
                parent=mwindow,
            )

        dp.song_randomer.init(dp.playlist)

    def addAllToPlaylist(self):
        global favs

        for folder in favs:
            for song in folder['songs']:
                if not any(s.name == song.name for s in dp.playlist):
                    dp.playlist.append(song)
                    dp.lst.addItem(song.name)
        InfoBar.success(
            'Songs added',
            'Added all songs from favorites to playlist',
            parent=mwindow,
        )

        dp.song_randomer.init(dp.playlist)

    def refresh(self):
        def _load():
            global favs
            favs = loadFavorites()

            self.folder_selector.clear()
            self.song_viewer.clear()

            for folder in favs:
                self.folder_selector.addItem(folder['folder_name'])

        doWithMultiThreading(_load, (), mwindow, 'Loading...')

class FavoriteSelectionDialog(MessageBoxBase):
    def __init__(self, parent):
        super().__init__(parent)
        # Title
        self.title_label = SubtitleLabel('Add Songs from Favorites')
        self.viewLayout.addWidget(self.title_label)

        # Horizontal layout for folder list and song list
        content_layout = QHBoxLayout()

        # Left: folder list
        folder_layout = QVBoxLayout()
        folder_layout.addWidget(QLabel('Folders:'))
        self.folder_list = ListWidget()
        self.folder_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        folder_layout.addWidget(self.folder_list)

        # Right: song list
        song_layout = QVBoxLayout()
        song_layout.addWidget(QLabel('Songs:'))
        self.song_list = ListWidget()
        self.song_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        song_layout.addWidget(self.song_list)

        content_layout.addLayout(folder_layout)
        content_layout.addLayout(song_layout)

        self.viewLayout.addLayout(content_layout)

        # Load folders
        self.loadFolders()

        # Connect signals
        self.folder_list.itemClicked.connect(self.onFolderSelected)

    def loadFolders(self):
        global favs
        self.folder_list.clear()
        self.song_list.clear()

        for folder in favs:
            self.folder_list.addItem(folder['folder_name'])

    def onFolderSelected(self, item):
        global favs
        self.song_list.clear()

        folder_name = item.text()
        for folder in favs:
            if folder['folder_name'] == folder_name:
                for song in folder['songs']:
                    self.song_list.addItem(song.name)
                break

    def getSelectedSong(self):
        '''Return list of selected SongStorable objects'''
        global favs

        folder_item = self.folder_list.currentItem()
        song_item = self.song_list.currentItem()

        if not folder_item or not song_item:
            return None

        folder_name = folder_item.text()
        song_name = song_item.text()

        for folder in favs:
            if folder['folder_name'] == folder_name:
                for song in folder['songs']:
                    if song.name == song_name:
                        return song

        return None

def getFavoriteSong() -> SongStorable | None:
    box = FavoriteSelectionDialog(mwindow)
    reply = box.exec()
    selected = box.getSelectedSong()

    if reply and selected:
        return selected
    else:
        return None

class DynamicIslandPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName('dynamic_island_page')

        self.delta = 1 / 60

        self.island_x = cfg.island_x
        self.island_y = cfg.island_y

        self.show_island = cfg.island_checked

        global_layout = QVBoxLayout()
        global_layout.addWidget(TitleLabel('Lyric Island'))
        
        self.island_check = CheckBox('Enable Lyric Island')
        self.island_check.checkStateChanged.connect(lambda state: self.__setattr__('show_island', state == Qt.CheckState.Checked))
        global_layout.addWidget(self.island_check)

        self.edit_btn = PrimaryPushButton(FluentIcon.EDIT, 'Edit island position')
        self.edit_btn.clicked.connect(self.editIslandPosition)
        global_layout.addWidget(self.edit_btn)

        self.setLayout(global_layout)

    def editIslandPosition(self):
        if not self.show_island:
            InfoBar.warning('Lyric Island', 'enable the lyric island first!', parent=mwindow, duration=2000)
            return
        
        mwindow.hide()
        island_editing_overlay.show()

class EditingIslandOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self.setFixedSize(QApplication.primaryScreen().size())
        self.move(0, 0)

        self.setMouseTracking(True)

        self.dragging = False
        self.dragging_pos = QPointF(0, 0)

        self.mouse_pos = QPointF(0, 0)

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.X11BypassWindowManagerHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.repaint)
        self.timer.start(int(ip.delta * 1000))

        InfoBar.info(
            'Help',
            'press ESC to quit',
            isClosable=False,
            duration=-1,
            parent=self
        )

        self.hide()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.mouse_pos = event.position()
        if self.dragging:
            ip.island_x = int(self.mouse_pos.x() - self.dragging_pos.x() + (island.island_width / 2))
            if abs((self.width() / 2) - ip.island_x) < 30:
                ip.island_x = int(self.width() / 2)
            ip.island_y = int(self.mouse_pos.y() - self.dragging_pos.y())
            if abs((self.height() / 2) - ip.island_y) < 30:
                ip.island_y = int(self.height() / 2)
        return super().mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.dragging:
            self.dragging = False
        return super().mouseReleaseEvent(event)

    def showEvent(self, event: QShowEvent) -> None:
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        return super().showEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            mwindow.show()
            return
        return super().keyPressEvent(event)
    
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if QRectF(ip.island_x - (island.island_width / 2), ip.island_y, island.island_width, island.island_height).contains(event.position()):
                self.dragging = True
                self.dragging_pos = event.position() - QPointF(ip.island_x - (island.island_width / 2), ip.island_y)
        return super().mousePressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        if not self.isVisible():
            return

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))
        painter.setPen(QPen(QColor(255, 255, 255, 100), 1))
        painter.drawLines([
            QLineF(self.mouse_pos.x(), 0, self.mouse_pos.x(), self.height()),
            QLineF(0, self.mouse_pos.y(), self.width(), self.mouse_pos.y())
        ])
        painter.drawRoundedRect(ip.island_x - (island.island_width / 2), ip.island_y, island.island_width, island.island_height,  # type: ignore
                                (island.island_height / 2), (island.island_height / 2))
        
        if ip.island_x == int(self.width() / 2) and self.dragging:
            painter.setPen(QPen(QColor(255, 255, 255, 50), 1))
            painter.drawLine(int(self.width() / 2), 0, int(self.width() / 2), self.height())
        if ip.island_y == int(self.height() / 2) and self.dragging:
            painter.setPen(QPen(QColor(255, 255, 255, 50), 1))
            painter.drawLine(0, int(self.height() / 2), self.width(), int(self.height() / 2))
        painter.end()

class LyricIslandOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.X11BypassWindowManagerHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.last_draw = time.perf_counter()
        
        self.ft = QFont(harmony_font_family, 14)
        self.me = QFontMetricsF(self.ft)
        self.island_height = self.me.height() + 5

        self.island_width = 0
        self.target_width = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.repaint)
        self.timer.start(int(ip.delta * 1000))

        self.resize(QApplication.primaryScreen().size())
        self.move(0, 0)

        self.show()

    def paintEvent(self, event: QPaintEvent) -> None:
        lyric = mgr.getCurrentLyric(player.getPosition())['content']
        txt = (lyric if not island_editing_overlay.isVisible() or lyric else 'Example Lyric') if ip.island_check.isChecked() else ''

        self.target_width = (self.me.horizontalAdvance(txt) + (10 if txt else 0)) if ip.island_check.isChecked() else 0
        self.island_width += (self.target_width - self.island_width) * min(1, (time.perf_counter() - self.last_draw) * 5)

        if not ip.island_check.isChecked() and self.island_width < 1:
            return

        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.last_draw = time.perf_counter()

        path = QPainterPath()
        path.addRoundedRect(ip.island_x - (self.island_width / 2), ip.island_y, self.island_width, self.island_height, self.island_height / 2, self.island_height / 2)
        painter.setClipPath(path)
        painter.fillPath(path, QColor(0, 0, 0, 175))
        painter.setFont(self.ft)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText((ip.island_x - (self.island_width / 2)) + 5, ip.island_y + self.me.height() - 2.5, txt) # type: ignore # 实际上这个函数可以提供浮点数数据，虽然类型标注为int...

        self.resize(ip.island_x + (self.island_width / 2), ip.island_y + self.island_height) # type: ignore

        painter.end()

class MainWindow(FluentWindow):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.closing = False

        self.setWindowTitle('Southside Music')

        self.addSubInterface(
            sp,
            getQIcon('music'),
            'Search',
        )
        self.addSubInterface(
            dp,
            getQIcon('studio'),
            'Playing',
        )
        self.addSubInterface(
            fp,
            getQIcon('fav'),
            'Favorites',
            NavigationItemPosition.BOTTOM,
        )
        self.addSubInterface(
            ip,
            getQIcon('island'),
            'Lyric Island',
            NavigationItemPosition.SCROLL,
        )

        self.show()

        if cfg.window_width == 0 and cfg.window_height == 0:
            self.resize(QApplication.primaryScreen().size() * 0.65)

            cfg.window_x = self.x()
            cfg.window_y = self.y()
            cfg.window_width = self.width()
            cfg.window_height = self.height()
        else:
            if cfg.wiondow_maximized:
                QTimer.singleShot(500, self.showMaximized)
            else:
                self.move(cfg.window_x, cfg.window_y)
                self.resize(cfg.window_width, cfg.window_height)

        self.init()

        QTimer.singleShot(1750, ws_server.start)

    def play(self, card: MusicCard):
        logging.debug(card.info['id'])

        dp.lyric_label.setText('')
        dp.transl_label.setText('')

        dp.cur = None

        dp.cur = card # type: ignore
        self.switchTo(dp)
        dp.init()

    def init(self) -> None:
        def _init():
            wy.init()

            dp.play_method_box.setCurrentText(cfg.play_method)
            ip.island_check.setChecked(cfg.island_checked)

            if cfg.last_playing_song:
                storable = cfg.last_playing_song
                
                dp.playlist.append(storable)
                dp.lst.addItem(storable.name)

                dp.playSongAtIndex(0)
                dp.controller.setPlaytime(cfg.last_playing_time)
                dp.controller.toggle()

        doWithMultiThreading(_init, (), self, 'Loading...')

        InfoBar.info(
            'Initialization', f'Loaded {len(favs)} folders', parent=self, duration=2000
        )

    def closeEvent(self, e):
        self.closing = True

        self.hide()
        island.hide()
        player.stop()

        ws_server.stop()
        ws_server.join()

        cfg.last_playing_song = dp.cur.storable if isinstance(dp.cur, DummyCard) else None
        cfg.last_playing_time = player.getPosition()
        cfg.island_checked = ip.island_check.isChecked()
        cfg.play_method = dp.play_method_box.currentText() # type: ignore
        cfg.island_x = ip.island_x
        cfg.island_y = ip.island_y
        cfg.window_x = self.x() + (253 if dp.controller.expand_btn.text() == 'Collapse' else 0)
        cfg.window_y = self.y()
        cfg.window_width = self.width() - (505 if dp.controller.expand_btn.text() == 'Collapse' else 0)
        cfg.window_height = self.height()
        cfg.wiondow_maximized = self.isMaximized()

        saveConfig()
        saveFavorites(favs)

        island_editing_overlay.timer.stop()
        island_editing_overlay.timer.deleteLater()
        island_editing_overlay.deleteLater()
        island.timer.stop()
        island.timer.deleteLater()
        island.deleteLater()

        sys.exit(0)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG,
        format=f'[%(asctime)s/{Style.BRIGHT}%(levelname)s{Style.RESET_ALL}] {Fore.LIGHTBLACK_EX}-{Style.RESET_ALL} %(message)s',
        datefmt='%H:%M:%S',
        handlers=[
            logging.StreamHandler()
        ]
    )
    
    app = QApplication([])
    app.setStyleSheet(f'QLabel {{ color: {'white' if darkdetect.isDark() else 'black'}; }}')

    harmony_font_family = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont('fonts/HARMONYOS_SANS_SC_REGULAR.ttf'))[0]

    from utils.loading_util import doWithMultiThreading, downloadWithMultiThreading

    from utils.lyrics.w163_util import CloudMusicUtil

    wy = CloudMusicUtil()  # type: ignore

    mgr = LRCLyricManager()
    transmgr = LRCLyricManager()
    favs: list[FolderInfo] = loadFavorites()

    loadConfig()

    setTheme(Theme.AUTO)

    player = AudioPlayer()

    lock = threading.Lock()

    dp = PlayingPage()
    sp = SearchPage()
    fp = FavoritesPage()
    ip = DynamicIslandPage()
    island_editing_overlay = EditingIslandOverlay()
    island = LyricIslandOverlay()
    mwindow = MainWindow()

    fp.refresh()

    app.exec()
