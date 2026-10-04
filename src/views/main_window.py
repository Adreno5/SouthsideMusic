from __future__ import annotations

import logging
import time

from PySide6.QtGui import QMouseEvent

from core import theme
from core.app_context import AppContext

from core.backend import getBackend
from core.dialogs import getTextLineedit
from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    QSize,
    QTimer,
)
from PySide6.QtGui import QFont, QIcon, Qt
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
)
from qfluentwidgets import (
    FluentIcon,
    Path,
    TransparentPushButton,
    TransparentToolButton,
)
from services.events import (
    BACKGROUND_RATIO_CHANGED,
    ENDING_NO_SOUND,
    LANGUAGE_CHANGED,
    MWINDOW_REFRESH_FOLDERS,
    PLAY_CONTINUE_LAST_SONG,
    PLAY_STORABLE,
    REFRESH_RATE_CHANGED,
    REPAINT,
    REPAINT_EVENT_INTERVAL,
    SONG_FINISH,
    START_INTER_LOADING,
    START_PROGRESS_LOADING,
    STOP_INTER_LOADING,
    STOP_PROGRESS_LOADING,
    UPDATE_LOADING_PROGRESS,
    VIEW_FOLDER,
    WEBSOCKET_CONNECTED,
    WEBSOCKET_DISCONNECTED,
    event_bus,
)
from PySide6.QtGui import QCloseEvent, QColor, QKeyEvent, QPainter
from services.events.events import POST_THEME_CHANGED
from views.list_widget import SListWidget, setTransparentBackground
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import InfoBar
from qfluentwidgets.window.fluent_window import FluentWindowBase

from core.models import CloudFolderInfo, LocalFolderInfo, SongInfo, SongStorable
from core.color import mixColor
from core.config import cfg
from core.favorites import favorites_manager, saveFavorites
from core.icons import bindIcon
from core.downloader import asyncTask
from core.i18n import tr, bindText
from views.account_widget import AccountWidget
from views.folder_card import CloudFolderCard, LocalFolderCard
from views.line_edit import SearchLineEdit
from views.llm_viewer_panel import LLMViewerPanel, LLM_WINDOW_WIDTH_DELTA
from views.lyric_editor_page import LyricEditorPage
from views.playing_controller import PlayingController
from views.song_card import SearchSongCard
from views.title_bar import SouthsideMusicTitleBar
from views.debug_overlay import DebugOverlay


class MainWindow(FluentWindowBase):
    def __init__(
        self,
        ctx: AppContext,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setMicaEffectEnabled(False)
        self._logger = logging.getLogger(__name__)
        self.ctx = ctx
        ctx.main_window = self  # type: ignore
        self._app = ctx.app
        self._dp = ctx.playing_page
        self._sp = ctx.search_page
        self._dsp = ctx.desktop_lyrics_page
        self._fp = ctx.favorites_page
        self._player = ctx.player
        self._ws_server = ctx.ws_server
        self._ws_handler = ctx.ws_handler
        self._launchwindow = ctx.launch_window
        self._loading_song: bool = False
        self._stp = ctx.setting_page
        self._plp = ctx.playlist_page
        self.song_theme: QColor | None = None
        ctx.lyric_editor_page = LyricEditorPage(ctx)

        self.setWindowIcon(
            QIcon(str(Path(__file__).resolve().parent.parent.parent / 'icon.png'))
        )

        self.contents_widget = QStackedWidget()
        setTransparentBackground(self.contents_widget)
        for w in [
            self._fp,
            self._sp,
            self._stp,
            self.ctx.home_page,
            self.ctx.library_page,
            self.ctx.lyric_editor_page,
            self.ctx.comments_page,
        ]:
            if ctx.launch_window:
                ctx.launch_window.subtitle(f'Adding {w} to stacked widget...')
            setTransparentBackground(w)
            self.contents_widget.addWidget(w)

        self.contents_widget.currentChanged.connect(self.onStackedWidgetChanged)
        self.contents_widget.setCurrentWidget(self.ctx.home_page)

        self.setTitleBar(SouthsideMusicTitleBar(self))
        self.llm_viewer_btn = TransparentToolButton(FluentIcon.CHAT)
        self.llm_viewer_btn.setFixedSize(32, 32)
        self.llm_viewer_btn.setToolTip('Onerad')
        self.llm_viewer_btn.clicked.connect(self.toggleLLMViewerExpand)
        self.titleBar.buttonLayout.insertWidget(0, self.llm_viewer_btn)  # type: ignore

        contents_layout = QHBoxLayout()
        contents_widget = QWidget(self)
        setTransparentBackground(contents_widget)
        contents_widget.setLayout(contents_layout)
        contents_layout.addWidget(self.contents_widget)

        contents_widget.setContentsMargins(0, 0, 0, 52)

        self.loading_tasks: int = 0
        self.loading_inter: bool = False
        self.loading_progressing: bool = False
        self.loading_progress: float = 0
        self.loading_ft = QFont(ctx.harmony_font_family)

        contents_layout.setContentsMargins(0, 48, 0, 0)

        self.controller = PlayingController(ctx)
        ctx.player.onFullFinished.connect(lambda: event_bus.emit(SONG_FINISH))
        ctx.player.onEndingNoSound.connect(ctx.playing_manager.onEndingNoSound)
        ctx.player.positionChanged.connect(ctx.playing_manager.onPlayerPositionChanged)

        if ctx.launch_window:
            ctx.launch_window.subtitle('  Wiring signal connections...')

        self.folders_list = SListWidget()
        self.folders_list.itemClicked.connect(self._onFolderItemClicked)
        self.folders_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._folder_header_items: list[tuple[QListWidgetItem, str]] = []
        self._folder_refresh_seq = 0

        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 48, 0, 52)

        self.home_button = TransparentPushButton(FluentIcon.HOME, '')
        bindText(self.home_button, 'main_window.home')
        self.home_button.clicked.connect(self._onHomeClicked)
        left_layout.addWidget(self.home_button)

        self.settings_btn = TransparentPushButton('')
        bindText(self.settings_btn, 'main_window.settings')
        bindIcon(self.settings_btn, 'settings')
        self.settings_btn.clicked.connect(self._onSettingsClicked)

        button_layout = QHBoxLayout()

        self.refresh_button = TransparentPushButton(FluentIcon.SYNC, '')
        bindText(self.refresh_button, 'main_window.refresh')
        self.refresh_button.clicked.connect(lambda: self.refreshFolders())
        button_layout.addWidget(self.refresh_button)

        self.library_button = TransparentPushButton('')
        bindText(self.library_button, 'main_window.library')
        bindIcon(self.library_button, 'library')
        self.library_button.clicked.connect(self._onLibraryClicked)
        button_layout.addWidget(self.library_button)

        left_layout.addLayout(button_layout)
        left_layout.addWidget(self.folders_list, 1)
        left_layout.addWidget(self.settings_btn)

        self.account_widget = AccountWidget(self, self.ctx)
        self.account_widget.setFixedHeight(40)
        self.account_widget.avatar_widget.setRadius(18)
        self.account_widget.loginChanged.connect(self._onLoginChanged)
        left_layout.addWidget(self.account_widget)

        left_layout.addSpacerItem(
            QSpacerItem(0, 5, QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        )

        self.hBoxLayout.addLayout(left_layout, 1)
        self.hBoxLayout.addWidget(contents_widget, 5)
        self.llm_viewer_panel = LLMViewerPanel(ctx, self)
        self.hBoxLayout.addWidget(self.llm_viewer_panel, 0)

        self.search_input = SearchLineEdit(self, ctx.harmony_font_family)
        self.search_input.returnPressed.connect(self.search)
        self.search_input.setParent(self)
        self.search_input.setFixedHeight(self.titleBar.height() - 15)
        self.search_input.move(
            self.minimumWidth() // 2,
            int((self.titleBar.height() - self.search_input.height()) * 0.5),
        )
        self.search_input.setFixedWidth(self.width() - self.minimumWidth())

        self.titleBar.raise_()
        self.search_input.raise_()
        self.controller.setParent(self)

        self.connected = False

        self.setWindowTitle('Southside Music')

        QTimer.singleShot(1750, ctx.ws_server.start)

        if self.ctx.dependences_available:
            self.show()

        self.setMinimumSize(ctx.app.primaryScreen().size() * 0.4)

        if cfg.window_width == 0 and cfg.window_height == 0:
            self.resize(ctx.app.primaryScreen().size() * 0.65)

            cfg.window_x = self.x()
            cfg.window_y = self.y()
            cfg.window_width = self.width()
            cfg.window_height = self.height()
        else:
            self.move(cfg.window_x, cfg.window_y)
            self.resize(
                cfg.window_width
                + (LLM_WINDOW_WIDTH_DELTA if self.llm_viewer_panel.expanded else 0),
                cfg.window_height,
            )

            if cfg.window_maximized:
                self.showMaximized()

        self.controller.setFixedSize(max(1, self.width()), 52)
        self.controller.move(0, self.height() - self.controller.height())

        self.last_action = time.time()

        self.dp_expanded = False
        self.dp_animating = False
        self._dp.setParent(self)
        self._dp.hide()
        self._dp.setFixedSize(self.size() - QSize(0, 100))
        self._dp.move(0, 48)
        self.controller.raise_()
        self.controller.show()

        self.pl_expanded = False
        self.pl_animating = False
        self._plp.setParent(self)
        self._plp.hide()
        self._plp.setFixedSize(int(self.width() * 0.45), self.height() - 110)
        self._plp.move(self.width() - 5 - self._plp.width(), 53)
        self.controller.raise_()
        self.controller.show()

        self.debug_overlay = DebugOverlay(ctx, self)
        geo = self.rect()
        geo.setWidth(int(self.width() * 0.4))
        geo.setHeight(int(self.height() * 0.8))
        self.debug_overlay.setGeometry(geo)
        self.debug_overlay.raise_()

        self.setMouseTracking(True)

        event_bus.subscribe(START_INTER_LOADING, self.onStartInterLoading)
        event_bus.subscribe(STOP_INTER_LOADING, self.onStopInterLoading)
        event_bus.subscribe(STOP_PROGRESS_LOADING, self.onStopProgressLoading)
        event_bus.subscribe(START_PROGRESS_LOADING, self.onStartProgressLoading)
        event_bus.subscribe(UPDATE_LOADING_PROGRESS, self.onUpdateLoadingProgress)
        event_bus.subscribe(ENDING_NO_SOUND, lambda: event_bus.emit(SONG_FINISH))
        event_bus.subscribe(BACKGROUND_RATIO_CHANGED, self.updateDatas)
        event_bus.subscribe(VIEW_FOLDER, self.onViewFolder)
        event_bus.subscribe(MWINDOW_REFRESH_FOLDERS, self.refreshFolders)
        event_bus.subscribe(LANGUAGE_CHANGED, self.updateLanguage)
        event_bus.subscribe(POST_THEME_CHANGED, self.onPostThemeChanged)
        event_bus.subscribe(REPAINT, self.checkAFK)
        self.refreshLoginInformations()

    def checkAFK(self, _):
        if (
            time.time() - self.last_action > self.ctx.config.afk_secs
            and not self.isActiveWindow()
        ):
            event_bus.emit(REPAINT_EVENT_INTERVAL, 200)
        else:
            event_bus.emit(
                REPAINT_EVENT_INTERVAL, 1000 / self.ctx.events_service.refresh_rate
            )

    def mousePressEvent(self, e):
        self.last_action = time.time()
        return super().mousePressEvent(e)

    def enterEvent(self, e):
        self.last_action = time.time()
        return super().enterEvent(e)

    def leaveEvent(self, e):
        self.last_action = time.time()
        return super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        self.last_action = time.time()
        return super().mouseReleaseEvent(e)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.last_action = time.time()
        return super().mouseMoveEvent(event)

    def onPostThemeChanged(self) -> None:
        self.llm_viewer_panel.onPostThemeChanged()

    def updateLanguage(self) -> None:
        for item, key in self._folder_header_items:
            item.setText(tr(key))

        self.refreshFolders()

    def refreshLLMModelBox(self) -> None:
        self.llm_viewer_panel.refreshLLMModelBox()

    def onStackedWidgetChanged(self):
        if getattr(self, 'dp_expanded', False) and not getattr(
            self, 'dp_animating', False
        ):
            self.togglePlayingPageExpand()

        setTransparentBackground(self.contents_widget.currentWidget())

    def _onSettingsClicked(self):
        self.contents_widget.setCurrentWidget(self._stp)

    def _onHomeClicked(self):
        self.contents_widget.setCurrentWidget(self.ctx.home_page)

    def _onLibraryClicked(self):
        self.contents_widget.setCurrentWidget(self.ctx.library_page)
        self.ctx.library_page.fetchSongs()

    def search(self):
        if not self.search_input.text().strip():
            InfoBar.warning(
                tr('main_window.search_failed'),
                tr('main_window.the_keyword_is_empty'),
                parent=self,
            )
            return
        else:
            self.contents_widget.setCurrentWidget(self._sp)

        if self._sp.searching:
            return

        self._sp.search(self.search_input.text())

    def onViewFolder(self, folder: LocalFolderInfo | CloudFolderInfo):
        self.contents_widget.setCurrentWidget(self._fp)
        self._fp.setDisplayFolder(folder)

    def togglePlaylistExpand(self):
        self.pl_expanded = not self.pl_expanded
        self.pl_animating = True

        anim = QPropertyAnimation(self._plp, b'geometry', self)
        anim.setDuration(200)
        anim.setEasingCurve(
            QEasingCurve.Type.OutCirc if self.pl_expanded else QEasingCurve.Type.InCirc
        )

        r = self._plp.rect()
        if self.pl_expanded:
            self._plp.show()
            anim.setStartValue(QRect(self.width() + 5, 53, r.width(), r.height()))
            anim.setEndValue(
                QRect(self.width() - 5 - r.width(), 53, r.width(), r.height())
            )
        else:
            QTimer.singleShot(200, self._plp.hide)
            anim.setStartValue(
                QRect(self.width() - 5 - r.width(), 53, r.width(), r.height())
            )
            anim.setEndValue(QRect(self.width() + 5, 53, r.width(), r.height()))

        def fini():
            self.pl_animating = False

        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

        QTimer.singleShot(225, fini)

    def togglePlayingPageExpand(self):
        self.dp_expanded = not self.dp_expanded
        self.dp_animating = True

        anim = QPropertyAnimation(self._dp, b'geometry', self)
        anim.setDuration(200)
        anim.setEasingCurve(
            QEasingCurve.Type.OutCirc if self.dp_expanded else QEasingCurve.Type.InCirc
        )

        if self.dp_expanded:
            self._dp.show()
            anim.setStartValue(QRect(0, self.height(), self.width(), self.height()))
            anim.setEndValue(QRect(0, 48, self.width(), self.height()))
        else:
            QTimer.singleShot(200, self._dp.hide)
            anim.setStartValue(QRect(0, 48, self.width(), self.height()))
            anim.setEndValue(QRect(0, self.height(), self.width(), self.height()))

        def fini():
            self.dp_animating = False

        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

        if self.dp_expanded:
            self.controller.hideLyrics()
        else:
            self.controller.showLyrics()

        QTimer.singleShot(225, fini)

    def toggleLLMViewerExpand(self) -> None:
        self.llm_viewer_panel.toggleExpand()

    def clearLLMChat(self) -> None:
        self.llm_viewer_panel.clearLLMChat()

    def onStartInterLoading(self):
        self.loading_tasks += 1
        if not self.loading_progressing:
            self.loading_inter = True

    def onStopInterLoading(self):
        self.loading_tasks -= 1
        if self.loading_tasks <= 0:
            self.loading_tasks = 0
            self.loading_inter = False

    def onStopProgressLoading(self):
        self.loading_progressing = False
        if self.loading_tasks > 0:
            self.loading_inter = True
        else:
            self.loading_inter = False

    def onStartProgressLoading(self):
        self.loading_progressing = True
        if self.loading_tasks > 0:
            self.loading_inter = False

    def onUpdateLoadingProgress(self, progress: float):
        self.loading_progress = progress

    def updateDatas(self) -> None:
        self.update()

    def addScheduledTask(self, task, *args, **kwargs) -> None:
        self.ctx.addScheduledTask(task, *args, **kwargs)

    def play(self, card: SearchSongCard) -> None:
        self._logger.debug(card.info.id)
        storable = SongStorable(
            info=SongInfo(
                name=card.info.name,
                artists=card.info.artists,
                id=str(card.info.id),
                privilege=card.info.privilege.fee,
                duration=card.info.duration,
            )
        )
        event_bus.emit(PLAY_STORABLE, storable)

    def init(self) -> None:
        self._launchwindow.clear()
        self._launchwindow.subtitle('Initializing main window...')
        last_playlist: list[SongStorable] = []
        last_playing_index = -1

        def _init():
            nonlocal last_playlist, last_playing_index

            if cfg.last_playlist:
                last_playlist = cfg.last_playlist
                last_playing_index = cfg.last_playing_index

        def _finish_init():
            if last_playlist:
                self._launchwindow.subtitle('restore playlist...')
                self._dp.playlist = list(last_playlist)
                if 0 <= last_playing_index < len(last_playlist):
                    self._launchwindow.subtitle('continue last song...')

                    def _continue():
                        event_bus.emit(PLAY_CONTINUE_LAST_SONG, cfg.last_playing_index)

                    self.ctx.addScheduledTask(_continue)

            self._launchwindow.subtitle('refreshing login information')
            asyncTask(self.refreshLoginInformations, (), self)

            event_bus._lw = None
            self._launchwindow.close()
            self._launchwindow.deleteLater()

            def _show():
                self.show()
                self.raise_()

                if self.llm_viewer_panel.expanded:
                    self.toggleLLMViewerExpand()

                self.refreshFolders()
                if favorites_manager.folders:
                    self._fp.setDisplayFolder(favorites_manager.folders[0])
                self.contents_widget.setCurrentWidget(self.ctx.home_page)

                self.controller.showLyrics()

            self.ctx.addScheduledTask(_show)

        asyncTask(_init, (), self, finished=_finish_init)

    def refreshFolders(self) -> None:
        self._folder_refresh_seq += 1
        refresh_seq = self._folder_refresh_seq
        self._fp.displayEmpty()
        open_folder = self._fp.curr_folder or self._fp.curr_cloud_folder

        self.refresh_button.setEnabled(False)
        self.folders_list.clear()
        self._folder_header_items.clear()

        local_item = QListWidgetItem(tr('main_window.local'))
        self._folder_header_items.append((local_item, 'main_window.local'))
        self.folders_list.addItem(local_item)

        for folder in favorites_manager.folders:
            card = LocalFolderCard(folder, self.folders_list.width())
            card.clicked.connect(lambda f=folder: self._openFolder(f))
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, folder)
            item.setSizeHint(card.sizeHint())
            self.folders_list.addItem(item)
            self.folders_list.setItemWidget(item, card)

        if (
            open_folder
            and isinstance(open_folder, LocalFolderInfo)
            and open_folder in favorites_manager.folders
        ):
            self._fp.setDisplayFolder(open_folder)

        item = QListWidgetItem()
        widget = TransparentPushButton(FluentIcon.ADD_TO, '')
        bindText(widget, 'main_window.add_folder')
        widget.clicked.connect(self.onAddLocalFolder)
        item.setSizeHint(widget.sizeHint())
        self.folders_list.addItem(item)
        self.folders_list.setItemWidget(item, widget)

        def _cloud() -> None:
            playlists = getBackend().getUserPlaylists()

            def add() -> None:
                nonlocal playlists
                if refresh_seq != self._folder_refresh_seq:
                    return
                self._addFolderHeader('main_window.cloud')
                for inf in playlists:
                    card = CloudFolderCard(inf, self.folders_list.width(), self.ctx)
                    card.clicked.connect(lambda f=inf: self._openFolder(f))
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, inf)
                    item.setSizeHint(card.sizeHint())
                    self.folders_list.addItem(item)
                    self.folders_list.setItemWidget(item, card)

                if (
                    open_folder
                    and isinstance(open_folder, CloudFolderInfo)
                    and open_folder in playlists
                ):
                    self._fp.setDisplayFolder(open_folder)

                item = QListWidgetItem()
                widget = TransparentPushButton(FluentIcon.ADD_TO, '')
                bindText(widget, 'main_window.add_folder')
                widget.clicked.connect(self.onAddCloudFolder)
                item.setSizeHint(widget.sizeHint())
                self.folders_list.addItem(item)
                self.folders_list.setItemWidget(item, widget)

                self.refresh_button.setEnabled(True)

            self.ctx.addScheduledTask(add)

        def _dailyRecommend() -> None:
            songs = getBackend().getDailyRecommendSongs()

            def add() -> None:
                nonlocal songs
                if refresh_seq != self._folder_refresh_seq:
                    return
                inf = LocalFolderInfo(tr('main_window.daily_recommend'), songs)
                card = LocalFolderCard(inf, self.folders_list.width())
                card.clicked.connect(lambda f=inf: self._openFolder(f))
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, inf)
                item.setSizeHint(card.sizeHint())
                self.folders_list.insertItem(0, item)
                self.folders_list.setItemWidget(item, card)

            self.ctx.addScheduledTask(add)

        if getBackend().loggedIn():
            asyncTask(_cloud, (), self)
            asyncTask(_dailyRecommend, (), self)
        else:
            self.refresh_button.setEnabled(True)

        saveFavorites()

    def refreshLoginInformations(self) -> None:
        self.account_widget.refreshLoginInformations()
        self.ctx.home_page.accounter.refreshLoginInformations()
        self.ctx.comments_page.refreshInformations()

    def _replaceHomePage(self) -> None:
        was_home_page = self.contents_widget.currentWidget() == self.ctx.home_page
        old_home_page = self.ctx.home_page
        self.contents_widget.removeWidget(old_home_page)
        old_home_page.deleteLater()

        from views.home_page import HomePage

        self.ctx.home_page = HomePage(self.ctx)
        self.contents_widget.addWidget(self.ctx.home_page)
        self.ctx.home_page.accounter.refreshLoginInformations()
        if was_home_page:
            self.contents_widget.setCurrentWidget(self.ctx.home_page)

    def _onLoginChanged(self) -> None:
        self._replaceHomePage()
        self.refreshLoginInformations()
        self.refreshFolders()

    def logout(self) -> None:
        self.account_widget.logout()

    def login(self) -> None:
        self.account_widget.login()

    def _addFolderHeader(self, key: str) -> None:
        item = QListWidgetItem(tr(key))
        self._folder_header_items.append((item, key))
        self.folders_list.addItem(item)

    def _openFolder(self, folder):
        self.contents_widget.setCurrentWidget(self._fp)
        self._fp.updateGeometry()
        self._fp.setDisplayFolder(folder)

    def onAddCloudFolder(self):
        name = getTextLineedit(
            tr('main_window.add_new_folder'),
            tr('main_window.enter_name_of_your_new_folder'),
            tr('main_window.my_folder'),
            self,
        )
        if name:
            getBackend().createPlaylist(name)
            self.refreshFolders()

    def onAddLocalFolder(self):
        name = getTextLineedit(
            tr('main_window.add_new_folder'),
            tr('main_window.enter_name_of_your_new_folder'),
            tr('main_window.my_folder'),
            self,
        )
        if name:
            new = favorites_manager.addFolder(name)
            self.refreshFolders()
            self._fp.setDisplayFolder(new)

    def _onFolderItemClicked(self, item: QListWidgetItem):
        self.contents_widget.setCurrentWidget(self._fp)
        self._fp.updateGeometry()
        folder = item.data(Qt.ItemDataRole.UserRole)
        if folder is not None:
            self._fp.setDisplayFolder(folder)

    def closeEvent(self, e: QCloseEvent):
        e.accept()
        self.hide()
        self.ctx.playing_manager.stopPlaybackForShutdown()
        self.ctx.app.quit()

    def resizeEvent(self, e):
        if not hasattr(self, 'llm_viewer_panel'):
            return
        self.titleBar.move(20, 0)
        self.titleBar.resize(self.width() - 20, self.titleBar.height())

        if hasattr(self, 'controller'):
            self.controller.setFixedSize(
                max(1, self.width() - self.llm_viewer_panel.maximumWidth()), 52
            )
            self.controller.move(0, self.height() - self.controller.height())

        if hasattr(self, '_dp'):
            self._dp.setFixedSize(
                self.size() - QSize(self.llm_viewer_panel.maximumWidth(), 100)
            )
            self._dp.move(0, 48)

        if hasattr(self, '_plp'):
            self._plp.setFixedSize(int(self.width() * 0.45), self.height() - 110)

        if hasattr(self, 'controller'):
            self.controller.raise_()

        if hasattr(self, 'search_input'):
            self.search_input.move(
                self.minimumWidth() // 2,
                int((self.titleBar.height() - self.search_input.height()) * 0.5),
            )
            self.search_input.setFixedWidth(self.width() - self.minimumWidth())
            self.search_input.raise_()

        if hasattr(self, 'debug_overlay'):
            self.debug_overlay.adjustToParent()
            self.debug_overlay.raise_()

    def onWebsocketConnected(self):
        InfoBar.success(
            tr('main_window.southside_client_connection'),
            tr('main_window.southside_music_was_connected_to_southsidclient'),
            duration=5000,
            parent=self,
        )
        QTimer.singleShot(
            500,
            lambda: self._ws_handler.sendJson({
                'option': f'{"disable" if not self._stp.enableFFT_box.isChecked() else "enable"}_fft'
            }),
        )
        QTimer.singleShot(500, self._dp.sendSongCoverAndInfo)

        self.connected = True

        self._stp.disconnect_btn.setEnabled(True)
        self._stp.connect_btn.setEnabled(False)

        event_bus.emit(WEBSOCKET_CONNECTED)

    def onWebsocketDisconnected(self):
        InfoBar.warning(
            tr('main_window.southside_client_connection'),
            tr('main_window.southside_music_was_been_disconnected_from_southsidclient'),
            duration=5000,
            parent=self,
        )

        self.connected = False

        self._stp.connect_btn.setEnabled(True)
        self._stp.disconnect_btn.setEnabled(False)

        event_bus.emit(WEBSOCKET_DISCONNECTED)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.controller.toggle()
            event.accept()
        elif event.key() == Qt.Key.Key_F3:
            self.ctx.debugging_obj.toggle()
            self.debug_overlay.refresh(raise_overlay=True)
            event.accept()
        else:
            return super().keyPressEvent(event)

    def paintEvent(self, e):
        super().paintEvent(e)

        if not self.song_theme:
            self.song_theme = QColor(self.backgroundColor)

        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setFont(self.loading_ft)
        painter.setBrush(
            mixColor(QColor(0, 0, 0) if theme.isDark() else QColor(255, 255, 255), mixColor(
                self.song_theme, QColor(self.backgroundColor), cfg.background_ratio
            ), 0.4)
        )
        painter.drawRect(self.rect())
        painter.end()
