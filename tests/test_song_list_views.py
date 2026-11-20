from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast, override
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QImage, QPixmap, QPixmapCache
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from core.app_context import AppContext
from core.favorites import favorites_manager
from core.image import encodePng
from core.models import LocalFolderInfo, SongInfo, SongStorable
from views.favorites_page import FavoritesPage
from views.playlist_page import PlaylistPage
from views.song_card import _SongCardItem
from views.list_widget import SListWidget


class SongListViewTests(unittest.TestCase):
    @classmethod
    @override
    def setUpClass(cls) -> None:
        cls.app = cast(QApplication, QApplication.instance()) or QApplication([])

    @override
    def setUp(self) -> None:
        self.ctx = AppContext()
        self.songs = [
            SongStorable(SongInfo(name=f'Song {i}', artists=[], id=str(i), privilege=0))
            for i in range(300)
        ]
        patcher = patch.object(_SongCardItem, 'loadDetailAndImage', autospec=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def preparePage(self, page: QWidget) -> None:
        page.resize(680, 450)
        page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)

        def close() -> None:
            page.close()
            page.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

        self.addCleanup(close)

    def waitForCard(self, view: SListWidget, row: int) -> _SongCardItem:
        for _ in range(100):
            card = view.itemWidget(view.item(row))
            if isinstance(card, _SongCardItem):
                return card
            QTest.qWait(10)
        self.fail(f'No song card for row {row}')

    def testHiddenPlaylistDoesNotBuildCards(self) -> None:
        self.ctx.playing_manager = SimpleNamespace(
            playlist=self.songs, clearPreload=lambda: None
        )
        self.ctx.playing_page = SimpleNamespace(cur=None)
        page = PlaylistPage(self.ctx)
        self.preparePage(page)

        page.refreshPlaylistWidget()
        QTest.qWait(30)
        self.assertEqual(len(page.findChildren(_SongCardItem)), 0)

        page.show()
        self.assertEqual(page.lst.count(), len(self.songs))
        self.assertIs(self.waitForCard(page.lst, 0).storable, self.songs[0])
        self.assertLessEqual(len(page.findChildren(_SongCardItem)), 12)

        page.lst.scrollToBottom()
        bottom_card = self.waitForCard(page.lst, 299)
        self.assertIs(bottom_card.storable, self.songs[-1])
        self.assertIsNone(page.lst.itemWidget(page.lst.item(0)))
        self.assertLessEqual(len(page.findChildren(_SongCardItem)), 12)

        page.hide()
        page.show()
        self.assertIs(self.waitForCard(page.lst, 299), bottom_card)

        page.hide()
        page.refreshPlaylistWidget()
        QTest.qWait(30)
        self.assertEqual(len(page.findChildren(_SongCardItem)), 0)

    def testFolderCanJumpToLastSongWithBatchSelection(self) -> None:
        page = FavoritesPage(self.ctx)
        self.preparePage(page)
        page.show()
        folder = LocalFolderInfo(folder_name='Large folder', songs=self.songs)
        with patch.object(favorites_manager, 'folders', [folder]):
            page.setDisplayFolder(folder)
            self.assertEqual(page.song_viewer.count(), len(self.songs))
            page.setBatchMode(True)
            page.selectAllSongs()
            page.song_viewer.scrollToBottom()
            card = self.waitForCard(page.song_viewer, 299)
            self.assertIs(card.storable, self.songs[-1])
            self.assertTrue(card.isSelected())
            self.assertLessEqual(len(page.findChildren(_SongCardItem)), 12)

            page.search_input.setText('Song 29')
            self.assertEqual(page.song_viewer.count(), 11)
            self.assertIs(
                self.waitForCard(page.song_viewer, 0).storable,
                self.songs[29],
            )

    def testSongCoverDoesNotUseAnOpenGLWidget(self) -> None:
        card = _SongCardItem(self.songs[0], lazy=True)
        self.preparePage(card)
        card.show()
        pixmap = QPixmap(50, 50)
        pixmap.fill(Qt.GlobalColor.red)
        card.img_label.setPixmap(pixmap)
        QTest.qWait(30)
        self.assertEqual(len(card.img_label.findChildren(QOpenGLWidget)), 0)

    def testRemountedCardReusesDecodedThumbnail(self) -> None:
        window = QWidget()
        self.preparePage(window)
        setattr(window, 'ctx', self.ctx)
        self.addCleanup(QPixmapCache.clear)
        song = self.songs[0]
        song.image_cache_hash = 'song-list-cover-test'
        image = QImage(150, 100, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.red)

        with patch.object(song, 'getImageBytes', return_value=encodePng(image)) as read:
            for _ in range(2):
                card = _SongCardItem(song, mwindow=window, parent=window, lazy=True)
                card.load = True
                card.loadImage()
                for _ in range(100):
                    if not card.img_label.pixmap().isNull():
                        break
                    QTest.qWait(10)
                self.assertEqual(card.img_label.pixmap().width(), 50)
                self.assertEqual(card.img_label.pixmap().height(), 33)
                card.releaseResources()
            read.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
