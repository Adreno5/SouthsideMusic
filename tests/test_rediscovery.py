from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast, override
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QStackedWidget, QWidget

from core.app_context import AppContext
from core.favorites import favorites_manager
from core.i18n import tr
from core.models import (
    ArtistInfo,
    CloudFolderInfo,
    LocalFolderInfo,
    SongInfo,
    SongStorable,
)
from core.playing_manager import PlayingManager
from core.rediscovery import getListeningHistory, getRediscoverySongs, recordListening
from services.events import event_bus
from views.favorites_page import FavoritesPage
from views.home_page import HomePage
from views.main_window import MainWindow
from views.rediscovery_card import RediscoveryCard
from views.rediscovery_page import RediscoveryPage
from views.song_card import _SongCardItem


class RediscoveryTests(unittest.TestCase):
    app: QApplication

    @classmethod
    @override
    def setUpClass(cls) -> None:
        cls.app = cast(QApplication, QApplication.instance()) or QApplication([])

    @override
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.history_path = Path(temporary.name) / 'listening_history.json'
        self.count_path = Path(temporary.name) / 'count.json'
        self.now = 2_000_000_000.0
        for patcher in (
            patch('core.models.COUNT_FILE', str(self.count_path)),
            patch('core.rediscovery.LISTENING_HISTORY_FILE', str(self.history_path)),
            patch('core.rediscovery.time.time', return_value=self.now),
            patch('core.rediscovery.random.random', random.Random(42).random),
            patch.object(
                _SongCardItem,
                'loadDetailAndImage',
                lambda card: setattr(card, 'load', True),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def song(self, song_id: int, count: int = 0) -> SongStorable:
        song = SongStorable(
            SongInfo(
                name=f'Song {song_id}',
                artists=[ArtistInfo(id=song_id, name=f'Artist {song_id}')],
                id=str(song_id),
                privilege=0,
                duration=180_000,
            )
        )
        song.count = count
        return song

    def history(self, days: dict[str, int]) -> None:
        self.history_path.write_text(
            json.dumps({
                song_id: self.now - day * 86400 for song_id, day in days.items()
            }),
            encoding='utf-8',
        )

    def deleteWidget(self, widget: QWidget) -> None:
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def page(self, songs: list[SongStorable]) -> RediscoveryPage:
        folder = LocalFolderInfo(folder_name='Favorites', songs=songs)
        patcher = patch.object(favorites_manager, 'folders', [folder])
        patcher.start()
        self.addCleanup(patcher.stop)
        ctx = AppContext()
        ctx.playing_manager = cast(PlayingManager, Mock(current_song=None))
        stack = QStackedWidget()
        stack.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.addCleanup(self.deleteWidget, stack)
        ctx.main_window = cast(MainWindow, Mock(ctx=ctx, contents_widget=stack))
        ctx.home_page = cast(HomePage, QWidget())
        ctx.favorites_page = cast(FavoritesPage, QWidget())
        page = RediscoveryPage(ctx)
        ctx.rediscovery_page = page
        for widget in (ctx.home_page, ctx.favorites_page, page):
            stack.addWidget(widget)
        stack.setCurrentWidget(page)
        page.setSource()
        return page

    def test_rare_mix_starts_with_overlooked_tail(self) -> None:
        songs = [self.song(i, 120 if i < 6 else 0) for i in range(36)]
        result = getRediscoverySongs(songs, 'rare')
        self.assertEqual(len(result), 20)
        self.assertTrue(all(song.count == 0 for song in result))
        self.assertEqual([song.id for song in songs], [str(i) for i in range(36)])

    def test_old_counts_override_stale_song_instances(self) -> None:
        self.count_path.write_text('{"1": 180, "2": 0}', encoding='utf-8')
        result = getRediscoverySongs([self.song(1), self.song(2, 99)], 'rare')
        self.assertEqual([song.id for song in result], ['2', '1'])

    def test_cold_start_shuffles_instead_of_repeating_folder_order(self) -> None:
        songs = [self.song(i) for i in range(30)]
        result = getRediscoverySongs(songs)
        self.assertNotEqual(result[:6], songs[:6])
        self.assertEqual(len({song.id for song in result}), 20)

    def test_balanced_mix_favors_the_overlooked_tail(self) -> None:
        songs = [self.song(i, 180 if i < 6 else 0) for i in range(36)]
        familiar = 0
        for _ in range(20):
            familiar += sum(song.count > 0 for song in getRediscoverySongs(songs))
        self.assertLess(familiar, 40)

    def test_duplicate_ids_are_played_once(self) -> None:
        first = self.song(1)
        result = getRediscoverySongs([first, self.song(1), self.song(2)])
        self.assertEqual(len(result), 2)
        self.assertIs(next(song for song in result if song.id == '1'), first)

    def test_recent_and_current_songs_are_behind_other_candidates(self) -> None:
        self.history({'1': 0})
        songs = [self.song(i) for i in range(1, 7)]
        result = getRediscoverySongs(songs, 'rare', current_id='2')
        self.assertEqual([song.id for song in result[-2:]], ['1', '2'])

    def test_forgotten_uses_old_listens_and_excludes_unplayed_songs(self) -> None:
        self.history({'1': 90, '2': 7, '3': 0})
        result = getRediscoverySongs([self.song(i) for i in range(1, 5)], 'forgotten')
        self.assertEqual([song.id for song in result], ['1', '2', '3'])

    def test_forgotten_supports_legacy_counts_without_inventing_dates(self) -> None:
        result = getRediscoverySongs([self.song(1, 20), self.song(2)], 'forgotten')
        self.assertEqual([song.id for song in result], ['1'])
        self.assertEqual(getListeningHistory(), {})

    def test_another_mix_rotates_the_previous_preview(self) -> None:
        songs = [self.song(i) for i in range(30)]
        first = getRediscoverySongs(songs, 'rare')
        previous = {song.id for song in first[:6]}
        second = getRediscoverySongs(songs, 'rare', previous_ids=previous)
        self.assertTrue(previous.isdisjoint(song.id for song in second[:6]))

    def test_empty_and_small_folders_remain_usable(self) -> None:
        self.assertEqual(getRediscoverySongs([]), [])
        song = self.song(1)
        self.assertEqual(getRediscoverySongs([song], current_id='1'), [song])
        self.assertEqual(getRediscoverySongs([song], limit=0), [])

    def test_history_persists_without_changing_legacy_counts(self) -> None:
        self.count_path.write_text('{"1": 180}', encoding='utf-8')
        self.history({'2': 40})
        recordListening(self.song(1))
        self.assertEqual(
            getListeningHistory(), {'1': self.now, '2': self.now - 40 * 86400}
        )
        self.assertEqual(self.count_path.read_text(encoding='utf-8'), '{"1": 180}')
        self.assertFalse(Path(str(self.history_path) + '.tmp').exists())

    def test_invalid_history_entries_are_ignored(self) -> None:
        self.history_path.write_text(
            '{"1": null, "2": "yesterday", "3": -1, "4": 2000000000}',
            encoding='utf-8',
        )
        self.assertEqual(getListeningHistory(), {'4': self.now})
        self.history_path.write_text('{broken', encoding='utf-8')
        with self.assertLogs('core.rediscovery', level='ERROR'):
            self.assertEqual(getListeningHistory(), {})

    def test_page_plays_all_ranked_songs_including_unloaded_rows(self) -> None:
        page = self.page([self.song(i) for i in range(95)])
        self.assertEqual(page.song_viewer.count(), 40)
        self.assertEqual(len(page._songs), 95)
        selected = list(page._songs)
        page.play_button.click()
        page.ctx.playing_manager.setPlaylist.assert_called_once_with(selected)
        page.ctx.playing_manager.playSongAtIndex.assert_called_once_with(0)
        page.ctx.playing_manager.playSongAtIndex.reset_mock()
        page._song_cards[3].clicked.emit(selected[3])
        page.ctx.playing_manager.playSongAtIndex.assert_called_once_with(3)

    def test_page_updates_after_source_changes(self) -> None:
        songs = [self.song(1), self.song(2)]
        page = self.page(songs)
        songs.clear()
        page.play()
        page.ctx.playing_manager.setPlaylist.assert_not_called()
        self.assertFalse(page.play_button.isEnabled())
        self.assertEqual(page.song_viewer.count(), 0)

    def test_page_modes_show_honest_empty_state_and_legacy_dates(self) -> None:
        songs = [self.song(1)]
        page = self.page(songs)
        page.mode_box.setCurrentIndex(page.mode_box.findData('forgotten'))
        self.assertEqual(page.summary.text(), tr('rediscovery.no_history'))
        self.assertFalse(page.play_button.isEnabled())
        songs[0].count = 100
        page.refresh()
        self.assertTrue(page.play_button.isEnabled())
        self.assertEqual(page.song_viewer.count(), 1)
        self.assertEqual(page._date_labels[0][0].text(), tr('rediscovery.unknown_date'))

    def test_scroll_loads_all_favorites_without_reordering_or_duplicates(self) -> None:
        page = self.page([self.song(i) for i in range(95)])
        ranked_ids = [song.id for song in page._songs]
        window = page.window()
        window.resize(900, 600)
        window.show()
        self.app.processEvents()
        scrollbar = page.song_viewer.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        QTest.qWait(100)
        self.assertEqual(page.song_viewer.count(), 80)
        first_batch = [card.storable.id for card in page._song_cards[:40]]
        self.assertEqual(first_batch, ranked_ids[:40])
        scrollbar.setValue(scrollbar.maximum())
        QTest.qWait(100)
        self.assertEqual(page.song_viewer.count(), 95)
        self.assertEqual([card.storable.id for card in page._song_cards], ranked_ids)
        self.assertEqual(len(set(ranked_ids)), 95)
        self.assertEqual(page.summary.text(), tr('rediscovery.loaded_all', count=95))
        self.assertFalse(page._load_timer.isActive())

    def test_scrolling_and_mode_switch_reset_batches_cleanly(self) -> None:
        songs = [self.song(i, 120 if i < 6 else 0) for i in range(95)]
        page = self.page(songs)
        page._appendSongBatch()
        self.assertEqual(page.song_viewer.count(), 80)
        page.mode_box.setCurrentIndex(page.mode_box.findData('rare'))
        self.assertEqual(page.song_viewer.count(), 40)
        self.assertEqual(len(page._songs), 95)
        self.assertTrue(all(song.count == 0 for song in page._songs[:40]))

    def test_hidden_page_stops_incremental_loading(self) -> None:
        page = self.page([self.song(i) for i in range(100)])
        page.window().show()
        self.app.processEvents()
        page._load_timer.start(16)
        page.ctx.main_window.contents_widget.setCurrentWidget(page.ctx.home_page)
        page.hide()
        QTest.qWait(40)
        self.assertEqual(page.song_viewer.count(), 40)
        self.assertFalse(page._load_timer.isActive())

    def test_favorites_only_has_a_compact_button_for_the_open_folder(self) -> None:
        songs = [self.song(i) for i in range(3)]
        ctx = cast(
            AppContext,
            SimpleNamespace(
                playing_manager=Mock(current_song=None),
                launch_window=None,
                playing_page=None,
                main_window=Mock(),
                playlist_page=None,
            ),
        )
        page = FavoritesPage(ctx)
        self.addCleanup(self.deleteWidget, page)
        folder = LocalFolderInfo(folder_name='Selected folder', songs=songs)
        page.setDisplayFolder(folder)
        self.assertFalse(hasattr(page, 'rediscovery_card'))
        self.assertTrue(page.rediscovery_button.isEnabled())
        page.rediscovery_button.click()
        ctx.main_window.openRediscovery.assert_called_once_with(folder)
        page.displayEmpty()
        self.assertFalse(page.rediscovery_button.isEnabled())

    def test_home_top_card_opens_the_page_without_playing_or_scanning_favorites(
        self,
    ) -> None:
        ctx = cast(AppContext, SimpleNamespace(main_window=Mock()))
        for logged_in in (True, False):
            with patch('views.home_page.getBackend') as backend:
                backend.return_value.loggedIn.return_value = logged_in
                home = HomePage(ctx)
                self.addCleanup(self.deleteWidget, home)
                self.assertEqual(home.rediscovery_card.findChildren(QComboBox), [])
                self.assertFalse(hasattr(home.rediscovery_card, 'play_button'))
                self.assertIs(
                    home.widget().layout().itemAt(0).widget(), home.rediscovery_card
                )
                self.assertEqual(home.rediscovery_card.maximumHeight(), 84)
                if logged_in:
                    modes = home._contents_widget.layout().itemAt(2).layout()
                    self.assertEqual(modes.count(), 4)
                    self.assertIs(modes.itemAt(0).widget(), home.heart_mode_card)
                    self.assertIs(modes.itemAt(3).widget(), home.similar_songs_card)
                QTest.mouseClick(home.rediscovery_card, Qt.MouseButton.LeftButton)
                ctx.main_window.openRediscovery.assert_called_with()
                self.assertIsInstance(home.rediscovery_card, RediscoveryCard)

    def test_navigation_opens_the_scoped_page_and_returns_to_its_entry(self) -> None:
        page = self.page([self.song(i) for i in range(3)])
        window = page.ctx.main_window
        folder = favorites_manager.folders[0]
        MainWindow.openRediscovery(window, folder)
        self.assertIs(window.contents_widget.currentWidget(), page)
        self.assertIs(page._folder, folder)
        self.assertEqual(
            page.scope_label.text(), tr('rediscovery.folder_scope', name='Favorites')
        )
        page.back_button.click()
        self.assertIs(window.contents_widget.currentWidget(), page.ctx.favorites_page)
        MainWindow.openRediscovery(window)
        self.assertIsNone(page._folder)
        page.back_button.click()
        self.assertIs(window.contents_widget.currentWidget(), page.ctx.home_page)

    def test_cloud_entry_uses_the_loaded_cloud_playlist_only(self) -> None:
        page = self.page([self.song(1)])
        cloud_songs = [self.song(100), self.song(101)]
        folder = CloudFolderInfo(folder_name='Cloud', image_url='', id='cloud-id')
        page.ctx.main_window._fp = SimpleNamespace(curr_cloud_songs=cloud_songs)
        MainWindow.openRediscovery(page.ctx.main_window, folder)
        self.assertEqual(set(page._songs), set(cloud_songs))
        self.assertIs(page._folder, folder)

    def test_controls_wrap_in_a_narrow_window(self) -> None:
        page = self.page([self.song(i) for i in range(10)])
        window = page.window()
        window.resize(370, 500)
        window.show()
        self.app.processEvents()
        buttons = (page.mode_box, page.play_button, page.refresh_button)
        self.assertGreater(len({button.y() for button in buttons}), 1)
        self.assertTrue(
            all(button.geometry().right() < page.width() - 10 for button in buttons)
        )

    def test_paused_and_skipped_playback_do_not_create_listening_dates(self) -> None:
        manager = PlayingManager(None)
        player = Mock(is_paused=False)
        player.isPlaying.return_value = True
        manager.ctx = cast(AppContext, SimpleNamespace(player=player))
        manager.current_song = self.song(1)
        manager.total_length = 180
        clock = 0.0

        def tick(seconds: int) -> None:
            nonlocal clock
            for _ in range(seconds):
                clock += 1
                with patch(
                    'core.playing_manager.timeLib.monotonic', return_value=clock
                ):
                    manager._recordListening()

        with patch('core.playing_manager.recordListening') as record:
            tick(10)
            player.is_paused = True
            tick(60)
            record.assert_not_called()
            player.is_paused = False
            manager._play_seq += 1
            manager.current_song = self.song(2)
            tick(30)
            record.assert_not_called()
            tick(1)
            record.assert_called_once_with(manager.current_song)
            tick(60)
            record.assert_called_once()
        manager.deleteLater()

    def test_short_song_records_after_half_its_duration(self) -> None:
        manager = PlayingManager(None)
        manager.ctx = cast(AppContext, SimpleNamespace(player=Mock(is_paused=False)))
        manager.current_song = self.song(1)
        manager.total_length = 10
        with patch('core.playing_manager.recordListening') as record:
            for clock in range(6):
                with patch(
                    'core.playing_manager.timeLib.monotonic', return_value=clock
                ):
                    manager._recordListening()
            record.assert_called_once_with(manager.current_song)
        manager.deleteLater()

    @override
    def tearDown(self) -> None:
        self.app.processEvents()
        self.assertTrue(event_bus.enabled)


if __name__ == '__main__':
    unittest.main()
