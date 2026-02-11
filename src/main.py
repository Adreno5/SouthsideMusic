
import sys
import time
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPaintEvent
from PySide6.QtWidgets import *  # type: ignore
from PySide6.QtCore import *  # type: ignore
from PySide6.QtGui import *  # type: ignore
from qfluentwidgets import *  # type: ignore
import requests

import darkdetect
import math

import pygame

pygame.init()

from utils.lyrics.base_util import FolderInfo, SongInfo, SongStorable
from functools import cache, lru_cache
from utils.lyrics.base_util import SongDetail
from utils.lyric_util import LRCLyricManager
from utils.time_util import float2time
from utils.favorite_util import loadFavorites, saveFavorites


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

        self.favbtn = TransparentToolButton(
            QIcon(f'icons/fav_{'dark' if theme() == Theme.DARK else 'light'}.svg')
        )
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
            from utils.favorite_util import loadFavorites
            from utils.dialog_util import get_values_bylist, get_text_lineedit
            from PySide6.QtWidgets import QListWidget

            favs = loadFavorites()
            
            # Prepare folder list for selection
            folder_names = [folder['folder_name'] for folder in favs]
            folder_names.append('Create new folder...')
            
            # Let user select folder
            selected = get_values_bylist(
                mwindow,
                'Select folder',
                f'which folder do you want to add {self.info['name']} to?',
                folder_names,
                QListWidget.SelectionMode.SingleSelection
            )
            
            if not selected:
                # User cancelled
                return
            
            selected_folder = selected[0]
            
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
                from utils.lyrics.base_util import FolderInfo
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
            from utils.lyrics.base_util import SongStorable
            
            song_storable = SongStorable(self.info, image_bytes, music_bytes, lyric, translated_lyric)
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
                print(f'loading {card.info['name']}')
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
                print(info['name'], info['privilege'])

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
    songFinished = Signal()

    playLastSignal = Signal()
    playNextSignal = Signal()

    def __init__(self):
        super().__init__()
        self.playing = False
        self.first = True

        self.playing_time: float = 0
        self.start_time: float = 0

        self.expanded = False

        self.dragging = False

        global_layout = QHBoxLayout()

        self.time_label = QLabel()
        global_layout.addWidget(
            self.time_label,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )

        self.last_btn = TransparentToolButton(
            QIcon(f'icons/last_{'dark' if theme() == Theme.DARK else 'light'}.svg')
        )
        self.next_btn = TransparentToolButton(
            QIcon(f'icons/next_{'dark' if theme() == Theme.DARK else 'light'}.svg')
        )
        self.last_btn.clicked.connect(self.playLastSignal.emit)
        self.next_btn.clicked.connect(self.playNextSignal.emit)

        self.play_pausebtn = TransparentToolButton(
            QIcon(f'icons/playa_{'dark' if theme() == Theme.DARK else 'light'}.svg')
        )
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
        self.expand_btn = PushButton(QIcon(f'icons/pl_expand_{'dark' if theme() == Theme.DARK else 'light'}.svg'), 'Playlist')
        right_layout.addWidget(
            self.expand_btn,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
        )
        self.expand_btn.clicked.connect(self.toggle_expand)

        global_layout.addLayout(right_layout)

        self.setLayout(global_layout)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.repaint)
        self.timer.start(20)

        self.playing_time_updater = QTimer(self)
        self.playing_time_updater.timeout.connect(self.updatePlayingtime)
        self.playing_time_updater.start(50)
        self.playingtime_lastupdate = time.perf_counter()

    def toggle_expand(self):
        self.expanded = not self.expanded

        if self.expanded:
            mwindow.resize(mwindow.width() + 205, mwindow.height())
            mwindow.move(mwindow.x() - 102, mwindow.y())
            dp.lst.show()

            self.expand_btn.setText('Collapse')
            self.expand_btn.setIcon(QIcon(f'icons/pl_collapse_{'dark' if theme() == Theme.DARK else 'light'}.svg'))
        else:
            dp.lst.hide()
            mwindow.resize(mwindow.width() - 205, mwindow.height())
            mwindow.move(mwindow.x() + 103, mwindow.y())

            self.expand_btn.setText('Playlist')
            self.expand_btn.setIcon(QIcon(f'icons/pl_expand_{'dark' if theme() == Theme.DARK else 'light'}.svg'))

    def updatePlayingtime(self):
        if self.playing:
            self.playing_time = time.perf_counter() - self.start_time
            if self.playing_time > dp.total_length:
                self.playing_time = dp.total_length
                self.songFinished.emit()

        if dp.cur:
            # Highlight the currently playing song in the playlist
            for i, song in enumerate(dp.playlist):
                if dp.cur and hasattr(dp.cur, 'storable') and song.name == dp.cur.storable.name:
                    dp.lst.setCurrentRow(i)
                    break

    def updateVol(self):
        value = self.vol_slider.value()
        if value == 0:
            volume = 0
        else:
            volume = math.log10(value / 100 * 9 + 1)
        print(volume)
        pygame.mixer.music.set_volume(volume)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.position().y() < 8:
            self.dragging = True
            self.playing_time = min(
                dp.total_length,
                max(0, (event.position().x() / self.width()) * dp.total_length),
            )
            if self.playing:
                pygame.mixer.music.set_pos(self.playing_time)
                self.start_time = time.perf_counter() - self.playing_time
        return super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.dragging:
            self.playing_time = min(
                dp.total_length,
                max(0, (event.position().x() / self.width()) * dp.total_length),
            )
            if self.playing:
                pygame.mixer.music.set_pos(self.playing_time)
                self.start_time = time.perf_counter() - self.playing_time
            self.dragging = False
        return super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.dragging:
            self.playing_time = min(
                dp.total_length,
                max(0, (event.position().x() / self.width()) * dp.total_length),
            )
            if self.playing:
                pygame.mixer.music.set_pos(self.playing_time)
                self.start_time = time.perf_counter() - self.playing_time
        return super().mouseMoveEvent(event)

    def toggle(self):
        print('toggle')

        self.playing = not self.playing

        if self.playing_time >= dp.total_length:
            self.playing_time = 0

        if self.playing:
            self.play_pausebtn.setIcon(
                QIcon(f'icons/pause_{'dark' if theme() == Theme.DARK else 'light'}.svg')
            )
            if self.first and dp.cur:
                pygame.mixer.music.play()
                self.first = False
                self.start_time = time.perf_counter()
            else:
                pygame.mixer.music.unpause()
                self.start_time = time.perf_counter() - self.playing_time
        else:
            self.play_pausebtn.setIcon(
                QIcon(f'icons/playa_{'dark' if theme() == Theme.DARK else 'light'}.svg')
            )
            pygame.mixer.music.pause()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setPen(QPen(QColor(120, 120, 120), 8))
        painter.drawLine(0, 0, self.width(), 0)
        if dp.total_length > 0:
            painter.setPen(
                QPen(
                    QColor(255, 255, 255) if darkdetect.isDark() else QColor(0, 0, 0), 8
                )
            )
            painter.drawLine(
                0, 0, int(self.width() * (self.playing_time / dp.total_length)), 0
            )

        cur_time = float2time(self.playing_time)
        # self.time_label.setText(
        #     f'{str(cur_time['minutes']).zfill(2)}:{str(cur_time['seconds']).zfill(2)}.{str(cur_time['millionsecs']).zfill(3)}'
        # )

        self.time_label.setText(
            f'{str(cur_time['minutes']).zfill(2)}:{str(cur_time['seconds']).zfill(2)}'
        )

        dp.lyric_label.setText(mgr.getCurrentLyric(self.playing_time)['content'])
        dp.transl_label.setText(transmgr.getCurrentLyric(self.playing_time)['content'])

        painter.end()


class PlayingPage(QWidget):
    imageLoaded = Signal(bytes)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName('studio_page')
        self.cur = None  # Can be MusicCard or dummy object

        self.total_length = 0

        # Playlist management
        self.playlist: list[SongStorable] = []
        self.current_index = -1

        self.controller = PlayingController()
        self.controller.songFinished.connect(self.playNext)
        # Connect play button to start playlist if no song is loaded
        self.controller.play_pausebtn.clicked.connect(self.onPlayButtonClicked)

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

        self.lst = ListWidget()
        self.lst.setFixedWidth(200)
        self.lst.hide()
        global_layout.addWidget(self.lst)

        self.setLayout(global_layout)

        self.imageLoaded.connect(self.onImageLoaded)

        self.controller.play_pausebtn.click()
        self.controller.playLastSignal.connect(self.playLast)
        self.controller.playNextSignal.connect(self.playNext)

    def init(self):
        if self.cur is None:
            return

        for label in self.findChildren(QLabel):
            label.setWordWrap(True)

        if self.controller.playing:
            self.controller.toggle()

        self.title_label.setText(self.cur.info['name'])
        self.artists_label.setText(self.cur.info['artists'])

        self.controller.playing_time = 0

        # Check if cur has storable attribute (DummyCard from playlist)
        if hasattr(self.cur, 'storable'):
            # Use local data from storable
            import base64

            image_bytes = base64.b64decode(self.cur.storable.image_base64)
            self.onImageLoaded(image_bytes)
            self.loadMusicFromBase64(self.cur.storable.content_base64)
            
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

            doWithMultiThreading(_real, (), mwindow, 'Parsing...', finished=lambda: self.controller.toggle())

        doWithMultiThreading(_parse, (), mwindow, 'Loading...')

    def downloadMusic(self):
        assert self.cur is not None

        def _write_file(bytes: bytes):
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()

            with open('./temp', 'wb') as f:
                f.write(bytes)

            pygame.mixer.music.load('./temp')

            sound = pygame.mixer.Sound('./temp')
            self.total_length = sound.get_length()

            self.controller.first = True

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
            _write_file,
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

        if self.cur is None:
            self.downloadMusic()

    def onPlayButtonClicked(self):
        # If no song is currently loaded, start playlist
        if self.cur is None:
            self.startPlaylist()

    def playNext(self):
        if self.current_index < 0 or self.current_index >= len(self.playlist) - 1:
            # No next song, reset and pause
            self.controller.playing_time = 0
            if self.controller.playing:
                self.controller.toggle()
            return
        
        if self.controller.playing:
            self.controller.toggle()

        self.current_index += 1
        self.playSongAtIndex(self.current_index)

    def playLast(self):
        if self.current_index < 1 or self.current_index >= len(self.playlist):
            # No next song, reset and pause
            self.controller.playing_time = 0
            if self.controller.playing:
                self.controller.toggle()
            return

        if self.controller.playing:
            self.controller.toggle()

        self.current_index -= 1
        self.playSongAtIndex(self.current_index)

    def playSongAtIndex(self, index: int):
        if index < 0 or index >= len(self.playlist):
            return

        self.current_index = index
        song = self.playlist[index]
        self.playStorable(song)

    def playStorable(self, song_storable: SongStorable):
        # Create dummy MusicCard with song info
        from utils.lyrics.base_util import SongInfo, SongDetail

        class DummyCard:
            def __init__(self, storable):
                self.info = SongInfo(
                    name=storable.name,
                    artists=storable.artists,
                    id=storable.id,
                    privilege=-1,
                )
                self.detail = SongDetail(image_url='')
                self.storable = storable

        self.cur = DummyCard(song_storable)

        # Update UI
        self.title_label.setText(song_storable.name)
        self.artists_label.setText(song_storable.artists)
        self.lyric_label.setText('')
        self.transl_label.setText('')

        # Load image from base64
        import base64

        image_bytes = base64.b64decode(song_storable.image_base64)
        self.onImageLoaded(image_bytes)

        # Load from base64
        self.loadMusicFromBase64(song_storable.content_base64)

        mgr.cur = song_storable.lyric
        transmgr.cur = song_storable.translated_lyric
        try:
            mgr.parse()
            transmgr.parse()
        finally:
            self.controller.first = True
            self.controller.toggle()

    def loadMusicFromBase64(self, content_base64: str):
        import base64

        music_bytes = base64.b64decode(content_base64)

        pygame.mixer.music.stop()
        pygame.mixer.music.unload()

        with open('./temp', 'wb') as f:
            f.write(music_bytes)

        pygame.mixer.music.load('./temp')
        sound = pygame.mixer.Sound('./temp')
        self.total_length = sound.get_length()

        self.controller.first = True

    def startPlaylist(self):
        fp.folder_selector.setCurrentRow(0)
        fp.addFolderToPlaylist()

        # Start playing first song
        self.current_index = 0
        self.playSongAtIndex(0)

        # Ensure playing state
        if not self.controller.playing:
            self.controller.toggle()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.controller.toggle()
        return super().keyPressEvent(event)


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
        global_layout.addLayout(top_layout)
        bottom_layout = QHBoxLayout()

        # Left side: folder selector and add to playlist button
        left_layout = QVBoxLayout()
        self.folder_selector = ListWidget()
        self.folder_selector.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.folder_selector.itemClicked.connect(self.viewSongs)
        left_layout.addWidget(self.folder_selector, 1)
        self.addplaylist_btn = PushButton(QIcon(f'icons/pl_{'dark' if theme() == Theme.DARK else 'light'}.svg'), 'Add to playlist')
        self.addplaylist_btn.clicked.connect(self.addFolderToPlaylist)
        left_layout.addWidget(self.addplaylist_btn)
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

        # Do NOT hide cancel button (keep default)

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

    def getSelectedSongs(self):
        '''Return list of selected SongStorable objects'''
        global favs

        folder_item = self.folder_list.currentItem()
        song_item = self.song_list.currentItem()

        if not folder_item or not song_item:
            return []

        folder_name = folder_item.text()
        song_name = song_item.text()

        for folder in favs:
            if folder['folder_name'] == folder_name:
                for song in folder['songs']:
                    if song.name == song_name:
                        return [song]

        return []


class PlayListPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName('playlist_page')

        global_layout = QVBoxLayout(self)
        self.playlist: list[SongStorable] = []

        # Top FlowLayout with title and buttons
        top_layout = FlowLayout()
        top_layout.addWidget(TitleLabel('Playlist'))
        self.remove_song_btn = PushButton(FluentIcon.DELETE, 'Remove Song')
        self.remove_song_btn.clicked.connect(self.removeSong)
        top_layout.addWidget(self.remove_song_btn)
        self.add_songs_btn = PrimaryPushButton(QIcon(f'icons/pl_{'dark' if theme() == Theme.DARK else 'light'}.svg'), 'Add songs')
        self.add_songs_btn.clicked.connect(self.addSongs)
        top_layout.addWidget(self.add_songs_btn)
        global_layout.addLayout(top_layout)

        # Song list
        self.lst = ListWidget()
        self.lst.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        global_layout.addWidget(self.lst)

        self.setLayout(global_layout)

    def removeSong(self):
        selected = self.lst.currentItem()
        if not selected:
            InfoBar.warning(
                'No selection', 'Please select a song to remove', parent=mwindow
            )
            return

        song_name = selected.text()
        # Find song in playlist
        for i, song in enumerate(self.playlist):
            if song.name == song_name:
                del self.playlist[i]
                self.lst.takeItem(self.lst.row(selected))
                InfoBar.success(
                    'Song removed',
                    f'Song {song_name} removed from playlist',
                    parent=mwindow,
                )
                return

    def addSongs(self):
        dialog = FavoriteSelectionDialog(mwindow)
        result = dialog.exec()

        if result == 1:  # Accepted
            selected_songs = dialog.getSelectedSongs()
            if not selected_songs:
                InfoBar.warning(
                    'No selection', 'Please select a song to add', parent=mwindow
                )
                return

            for song in selected_songs:
                # Check for duplicates
                if not any(s.name == song.name for s in self.playlist):
                    self.playlist.append(song)
                    self.lst.addItem(song.name)
                    InfoBar.success(
                        'Song added',
                        f'Song {song.name} added to playlist',
                        parent=mwindow,
                    )
                else:
                    InfoBar.warning(
                        'Duplicate',
                        f'Song {song.name} already in playlist',
                        parent=mwindow,
                    )


class MainWindow(FluentWindow):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.resize(QApplication.primaryScreen().size() * 0.65)

        self.setWindowTitle('Southside Music')

        self.addSubInterface(
            sp,
            QIcon(f'icons/music_{'dark' if theme() == Theme.DARK else 'light'}.svg'),
            'Search',
        )
        self.addSubInterface(
            dp,
            QIcon(f'icons/studio_{'dark' if theme() == Theme.DARK else 'light'}.svg'),
            'Playing',
        )
        self.addSubInterface(
            fp,
            QIcon(f'icons/fav_{'dark' if theme() == Theme.DARK else 'light'}.svg'),
            'Favorites',
            NavigationItemPosition.SCROLL,
        )

        self.show()

        self.init()

    def play(self, card: MusicCard):
        print(card.info['id'])

        dp.lyric_label.setText('')
        dp.transl_label.setText('')

        dp.cur = card # type: ignore
        self.switchTo(dp)
        dp.init()

    def init(self) -> None:
        def _init():
            wy.init()

        doWithMultiThreading(_init, (), self, 'Loading...')

        InfoBar.info(
            'Initialization', f'Loaded {len(favs)} folders', parent=self, duration=2000
        )

    def closeEvent(self, e):
        pygame.mixer.music.unload()
        pygame.quit()
        sys.exit(0)


if __name__ == '__main__':
    app = QApplication([])

    from utils.loading_util import doWithMultiThreading, downloadWithMultiThreading

    from utils.lyrics.w163_util import CloudMusicUtil

    wy = CloudMusicUtil()  # type: ignore

    mgr = LRCLyricManager()
    transmgr = LRCLyricManager()
    favs: list[FolderInfo] = loadFavorites()

    loadFavorites()

    setTheme(Theme.AUTO)
    dp = PlayingPage()
    sp = SearchPage()
    fp = FavoritesPage()
    mwindow = MainWindow()

    fp.refresh()

    app.exec()
