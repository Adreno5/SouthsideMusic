from core.app_context import AppContext
from core.backend import getBackend
from core.models import CloudFolderInfo, SongStorable
from core.qt_utils import removeWidgets
from imports import (
    PLAYLIST_CHANGED,
    PLAY_STORABLE,
    VIEW_FOLDER,
    QLabel,
    QHBoxLayout,
    QMouseEvent,
    QSizePolicy,
    QSpacerItem,
    QTimer,
    Qt,
    QVBoxLayout,
    QWidget,
    SubtitleLabel,
    TitleLabel,
    bindText,
    event_bus,
)
from views.folder_card import CloudFolderCard
from views.list_widget import SScrollArea
from views.account_widget import AccountWidget
from views.animated_layout import SFlowLayout
from views.number_viewer import NumberViewer
from core.downloader import asyncTask
from views.song_card import CloudFavoriteSongCard

class HomePage(SScrollArea):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx

        contents_widget = QWidget()
        contents_layout = QVBoxLayout()
        contents_widget.setLayout(contents_layout)

        title_label = TitleLabel('')
        bindText(title_label, 'home_page.title')
        contents_layout.addWidget(title_label)

        welcome_layout = QHBoxLayout()
        welcome_layout.setSpacing(0)
        welcome_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        welcome_label = SubtitleLabel('')
        bindText(welcome_label, 'home_page.welcome_back')
        welcome_layout.addWidget(welcome_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.accounter = AccountWidget(self, self.ctx)
        def _empty(event: QMouseEvent): return None
        self.accounter.mousePressEvent = _empty
        self.accounter.setCursor(Qt.CursorShape.ArrowCursor)
        self.accounter.setFixedHeight(60)
        self.accounter.avatar_widget.setRadius(29)
        f = self.accounter.nickname_label.font()
        f.setPointSize(16)
        self.accounter.nickname_label.setFont(f)
        welcome_layout.addWidget(welcome_label, alignment=Qt.AlignmentFlag.AlignBottom)
        welcome_layout.addWidget(self.accounter)
        welcome_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        if getBackend().loggedIn():
            contents_layout.addLayout(welcome_layout)

        bottom_layout = QHBoxLayout()

        right_layout = QVBoxLayout()

        hbox = QHBoxLayout()
        hbox.setSpacing(12)
        title_label = SubtitleLabel('')
        bindText(title_label, 'home_page.recommend_songs')
        hbox.addWidget(title_label)
        self.songs_counter = NumberViewer(self.ctx.harmony_font_family, self.ctx, 15, 1.3)
        hbox.addWidget(self.songs_counter)
        self.recommend_songs_layout = SFlowLayout(yAnimations=True)
        self.recommend_songs_layout.setAnimation(300)
        right_layout.addLayout(hbox)
        right_layout.addLayout(self.recommend_songs_layout)
        bottom_layout.addLayout(right_layout)

        left_layout = QVBoxLayout()

        hbox = QHBoxLayout()
        hbox.setSpacing(12)
        title_label = SubtitleLabel('')
        bindText(title_label, 'home_page.recommend_folders')
        hbox.addWidget(title_label)
        self.folders_counter = NumberViewer(self.ctx.harmony_font_family, self.ctx, 15, 1.3)
        hbox.addWidget(self.folders_counter)
        self.recommend_folders_layout = SFlowLayout(yAnimations=True)
        self.recommend_folders_layout.setAnimation(1000)
        left_layout.addLayout(hbox)
        left_layout.addLayout(self.recommend_folders_layout)
        bottom_layout.addLayout(left_layout)

        left_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding))
        right_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding))

        if getBackend().loggedIn():
            contents_layout.addLayout(bottom_layout)

        self.setWidgetResizable(True)
        self.setWidget(contents_widget)

    def fetchDailyRecommend(self):
        removeWidgets(self.recommend_folders_layout)
        removeWidgets(self.recommend_songs_layout)

        self.folders_counter.setText('0')
        self.songs_counter.setText('0')
        self.folders_counter.y_map.clear()
        self.songs_counter.y_map.clear()

        def _fetchFolders():
            folders: list[CloudFolderInfo] = []
            idx = -1

            def add():
                nonlocal folders, idx
                idx += 1
                if idx >= len(folders):
                    return
                inf = folders[idx]
                card = CloudFolderCard(inf, self.width() / 4 - 2, self.ctx)
                card.clicked.connect(
                    lambda f=inf: event_bus.emit(VIEW_FOLDER, f)
                )
                self.recommend_folders_layout.insertWidget(0, card)

                QTimer.singleShot(90, add)
            
            folders = getBackend().getDailyRecommendFolders()
            self.ctx.addScheduledTask(lambda: self.folders_counter.setText(str(len(folders))))
            self.ctx.addScheduledTask(add)

        def _fetchSongs():
            songs = getBackend().getDailyRecommendSongs()
            idx = -1

            def add():
                nonlocal songs, idx
                idx += 1
                if idx >= len(songs):
                    return
                song = songs[idx]
                card = CloudFavoriteSongCard(song, self.ctx.playing_page, self.ctx.main_window, self.ctx.playlist_page)
                card.clicked.connect(self._playSong)
                card.queued.connect(self._queueSong)
                self.recommend_songs_layout.insertWidget(0, card)

                QTimer.singleShot(50, add)

            self.ctx.addScheduledTask(lambda: self.songs_counter.setText(str(len(songs))))
            self.ctx.addScheduledTask(add)

        asyncTask(_fetchFolders, (), self)
        asyncTask(_fetchSongs, (), self)

    def _playSong(self, song: SongStorable) -> None:
        event_bus.emit(PLAY_STORABLE, song)

    def _queueSong(self, song: SongStorable) -> None:
        playlist = self.ctx.playing_manager.playlist
        insert_index = self.ctx.playing_manager.current_index + 2
        playlist.insert(insert_index, song)
        event_bus.emit(PLAYLIST_CHANGED)
