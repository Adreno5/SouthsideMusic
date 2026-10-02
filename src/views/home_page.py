import PySide6
from PySide6.QtCore import QRect, QEvent
from PySide6.QtGui import QColor
from qfluentwidgets import FlowLayout
from core import theme
from services.events import SECOND_TICK
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.app_context import AppContext
from core.backend import getBackend
from core.models import CloudFolderInfo, SongStorable
from core.qt_utils import removeWidgets
from imports import (
    QPainter,
    QLinearGradient,
    PLAYLIST_CHANGED,
    PLAY_STORABLE,
    VIEW_FOLDER,
    CardWidget,
    IndeterminateProgressBar,
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


class HeartModeCard(CardWidget):
    def __init__(self, ctx: 'AppContext'):
        super().__init__()
        self.ctx = ctx
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = SubtitleLabel('')
        bindText(title, 'home_page.heart_mode')
        title.setStyleSheet('color: white; font-weight: 700;')
        title_row.addWidget(title)
        title_row.addStretch()
        layout.addLayout(title_row)

        subtitle = QLabel('')
        bindText(subtitle, 'home_page.heart_mode_subtitle')
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet('color: rgba(255,255,255,210); font-size: 13px;')
        layout.addWidget(subtitle)
        layout.addStretch()

        hint = QLabel('')
        bindText(hint, 'home_page.heart_mode_hint')
        hint.setStyleSheet('color: rgba(255,255,255,170); font-size: 12px;')
        layout.addWidget(hint)

        self.inde_bar = IndeterminateProgressBar()
        self.inde_bar.hide()
        layout.addWidget(self.inde_bar)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setEnabled(False)
            self.inde_bar.show()
            QTimer.singleShot(1800, self.restoreStatus)
            self.ctx.playing_manager.startHeartMode()
        return super().mousePressEvent(event)

    def restoreStatus(self):
        self.setEnabled(True)
        self.inde_bar.hide()


class PrivateRoamCard(CardWidget):
    def __init__(self, ctx: 'AppContext'):
        super().__init__()
        self.ctx = ctx
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = SubtitleLabel('')
        bindText(title, 'home_page.private_roam')
        title.setStyleSheet('color: white; font-weight: 700;')
        title_row.addWidget(title)
        title_row.addStretch()
        layout.addLayout(title_row)

        subtitle = QLabel('')
        bindText(subtitle, 'home_page.private_roam_subtitle')
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet('color: rgba(255,255,255,210); font-size: 13px;')
        layout.addWidget(subtitle)
        layout.addStretch()

        hint = QLabel('')
        bindText(hint, 'home_page.private_roam_hint')
        hint.setStyleSheet('color: rgba(255,255,255,170); font-size: 12px;')
        layout.addWidget(hint)

        self.inde_bar = IndeterminateProgressBar()
        self.inde_bar.hide()
        layout.addWidget(self.inde_bar)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setEnabled(False)
            self.inde_bar.show()
            QTimer.singleShot(1800, self.restoreStatus)
            self.ctx.playing_manager.startPersonalFM()
        return super().mousePressEvent(event)

    def restoreStatus(self) -> None:
        self.setEnabled(True)
        self.inde_bar.hide()


class PrivateRadarCard(CardWidget):
    def __init__(self, ctx: 'AppContext'):
        super().__init__()
        self.ctx = ctx
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = SubtitleLabel('')
        bindText(title, 'home_page.private_radar')
        title.setStyleSheet('color: white; font-weight: 700;')
        title_row.addWidget(title)
        title_row.addStretch()
        layout.addLayout(title_row)

        subtitle = QLabel('')
        bindText(subtitle, 'home_page.private_radar_subtitle')
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet('color: rgba(255,255,255,210); font-size: 13px;')
        layout.addWidget(subtitle)
        layout.addStretch()

        hint = QLabel('')
        bindText(hint, 'home_page.private_radar_hint')
        hint.setStyleSheet('color: rgba(255,255,255,170); font-size: 12px;')
        layout.addWidget(hint)

        self.inde_bar = IndeterminateProgressBar()
        self.inde_bar.hide()
        layout.addWidget(self.inde_bar)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setEnabled(False)
            self.inde_bar.show()
            QTimer.singleShot(1800, self.restoreStatus)
            self.ctx.playing_manager.startPrivateRadar()
        return super().mousePressEvent(event)

    def restoreStatus(self) -> None:
        self.setEnabled(True)
        self.inde_bar.hide()


class SimilarSongsCard(CardWidget):
    def __init__(self, ctx: 'AppContext'):
        super().__init__()
        self.ctx = ctx
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = SubtitleLabel('')
        bindText(title, 'home_page.similar_songs')
        title.setStyleSheet('color: white; font-weight: 700;')
        title_row.addWidget(title)
        title_row.addStretch()
        layout.addLayout(title_row)

        subtitle = QLabel('')
        bindText(subtitle, 'home_page.similar_songs_subtitle')
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet('color: rgba(255,255,255,210); font-size: 13px;')
        layout.addWidget(subtitle)
        layout.addStretch()

        hint = QLabel('')
        bindText(hint, 'home_page.similar_songs_hint')
        hint.setStyleSheet('color: rgba(255,255,255,170); font-size: 12px;')
        layout.addWidget(hint)

        self.inde_bar = IndeterminateProgressBar()
        self.inde_bar.hide()
        layout.addWidget(self.inde_bar)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setEnabled(False)
            self.inde_bar.show()
            QTimer.singleShot(1800, self.restoreStatus)
            self.ctx.playing_manager.startSimilarSongs()
        return super().mousePressEvent(event)

    def restoreStatus(self) -> None:
        self.setEnabled(True)
        self.inde_bar.hide()


class HomePage(SScrollArea):
    def __init__(self, ctx: 'AppContext'):
        super().__init__()
        self.ctx = ctx

        contents_widget = QWidget()
        contents_layout = QVBoxLayout()
        contents_widget.setLayout(contents_layout)

        welcome_layout = QHBoxLayout()
        welcome_layout.setSpacing(0)
        welcome_layout.addSpacerItem(
            QSpacerItem(15, 0, QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        )
        welcome_label = SubtitleLabel('')
        bindText(welcome_label, 'home_page.welcome_back')
        welcome_layout.addWidget(welcome_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.accounter = AccountWidget(self, self.ctx)

        def _empty(event: QMouseEvent):
            return None

        self.accounter.mousePressEvent = _empty
        self.accounter.setCursor(Qt.CursorShape.ArrowCursor)
        self.accounter.setFixedHeight(60)
        self.accounter.avatar_widget.setRadius(29)
        f = self.accounter.nickname_label.font()
        f.setPointSize(16)
        self.accounter.nickname_label.setFont(f)
        welcome_layout.addWidget(self.accounter)
        welcome_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        )

        if getBackend().loggedIn():
            contents_layout.addLayout(welcome_layout)

        mode_cards_layout = QHBoxLayout()
        mode_cards_layout.setSpacing(12)
        self.heart_mode_card = HeartModeCard(self.ctx)
        self.private_roam_card = PrivateRoamCard(self.ctx)
        self.private_radar_card = PrivateRadarCard(self.ctx)
        self.similar_songs_card = SimilarSongsCard(self.ctx)
        mode_cards_layout.addWidget(self.heart_mode_card)
        mode_cards_layout.addWidget(self.private_roam_card)
        mode_cards_layout.addWidget(self.private_radar_card)
        mode_cards_layout.addWidget(self.similar_songs_card)
        contents_layout.addLayout(mode_cards_layout)

        self.setWidgetResizable(True)
        self.setWidget(contents_widget)

        self.setAutoFillBackground(False)

        self.viewport().setAutoFillBackground(False)
        contents_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        event_bus.subscribe(SECOND_TICK, self.update)

    def _playSong(self, song: SongStorable) -> None:
        event_bus.emit(PLAY_STORABLE, song)

    def _queueSong(self, song: SongStorable) -> None:
        playlist = self.ctx.playing_manager.playlist
        insert_index = self.ctx.playing_manager.current_index + 2
        playlist.insert(insert_index, song)
        event_bus.emit(PLAYLIST_CHANGED)

    def viewportEvent(self, event):
        ret = super().viewportEvent(event)

        if event.type() == QEvent.Type.Paint:
            vp = self.viewport()
            h, w = vp.height(), vp.width()

            painter = QPainter(vp)
            gradient = QLinearGradient(w * 0.1, h * 0.8, w * 0.11, h)
            if theme.isDark():
                gradient.setColorAt(0, QColor(0, 0, 0, 0))
            else:
                gradient.setColorAt(0, QColor(255, 255, 255, 0))
            s = theme.getSystemThemeColor()
            s.setAlpha(12)
            gradient.setColorAt(1, s)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawRect(QRect(0, 0, w, h))
            painter.end()

        return ret
