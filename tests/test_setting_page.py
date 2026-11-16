from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path
from typing import cast, override
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import CardWidget

from core.app_context import AppContext
from core.config import cfg
from core.i18n import setLanguage, tr
from services.events import Events, event_bus
from views.setting_page import SettingPage


class SettingPageTests(unittest.TestCase):
    _app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = cast(QApplication, QApplication.instance()) or QApplication([])

    @override
    def setUp(self) -> None:
        self.config = deepcopy(vars(cfg))
        cfg.language = 'zh_CN'
        cfg.show_advanced_settings = True
        cfg.setting_section_expanded = {}
        cfg.llm_providers = []
        cfg.lyrics_animation_type = 'physics'
        self.addCleanup(vars(cfg).update, self.config)
        self.addCleanup(patch.stopall)
        patch('views.setting_page.saveConfig').start()
        patch('views.setting_page.getAudioDevices', return_value=[]).start()
        ctx = AppContext()
        ctx.app = self._app
        ctx.harmony_font_family = 'Segoe UI'
        ctx.player = MagicMock()
        ctx.player._device_id = 0
        ctx.playing_manager = MagicMock()
        ctx.desktop_lyrics_page = MagicMock()
        ctx.ws_handler = MagicMock()
        ctx.smtc = MagicMock()
        self.page = SettingPage(ctx)
        self.page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.page.resize(1000, 720)
        self.page.show()
        self._app.processEvents()

    @override
    def tearDown(self) -> None:
        self.page.close()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _card(self, name: str) -> CardWidget:
        return next(
            card
            for card in self.page.findChildren(CardWidget)
            if getattr(card, '_llm_setting_name', '') == name
        )

    def test_beat_is_nested_under_experimental_group(self) -> None:
        section = next(
            section
            for section in self.page._sections
            if section.title == 'setting_page.beat'
        )
        group = cast(QWidget, section.parentWidget())
        self.assertEqual(group.objectName(), 'setting_page.group_experimental')
        self.assertNotEqual(group, self.page.options_widget)

    def test_search_filters_names_and_hides_empty_groups(self) -> None:
        self.page.search_input.setText('输出设备')
        self.assertTrue(self._card('setting_page.output_device').isVisible())
        self.assertFalse(self._card('setting_page.language').isVisible())
        visible_groups = [g for g in self.page._groups if not g.isHidden()]
        self.assertEqual(len(visible_groups), 1)

    def test_search_matches_description_and_multiple_words(self) -> None:
        self.page.search_input.setText('  db   实时  ')
        self.assertTrue(self._card('setting_page.current_volume').isVisible())
        self.assertFalse(self._card('setting_page.output_device').isVisible())

    def test_clear_search_restores_collapsed_sections_and_config(self) -> None:
        expanded = cfg.setting_section_expanded.copy()
        self.page.search_input.setText('输出设备')
        self.assertTrue(self._card('setting_page.output_device').isVisible())
        self.assertEqual(cfg.setting_section_expanded, expanded)
        self.page.search_input.clear()
        self.assertTrue(all(not s.isExpanded() for s in self.page._sections))
        self.assertEqual(cfg.setting_section_expanded, expanded)

    def test_clear_search_preserves_mixed_expansion_state(self) -> None:
        section = next(
            section
            for section in self.page._sections
            if section.title == 'setting_page.app'
        )
        section.setExpanded(True)
        QTest.qWait(400)
        expanded = cfg.setting_section_expanded.copy()
        self.page.search_input.setText('输出设备')
        self.page.search_input.clear()
        self.assertTrue(section.isExpanded())
        self.assertTrue(self._card('setting_page.language').isVisible())
        self.assertEqual(cfg.setting_section_expanded, expanded)

    def test_no_results_and_clear(self) -> None:
        self.page.search_input.setText('unmatched-setting-19374')
        self.assertTrue(self.page.no_results_label.isVisible())
        self.assertTrue(all(g.isHidden() for g in self.page._groups))
        self.page.search_input.clear()
        self.assertFalse(self.page.no_results_label.isVisible())
        self.assertTrue(all(not g.isHidden() for g in self.page._groups))

    def test_advanced_visibility_combines_with_search(self) -> None:
        self.page.advanced_settings_box.setChecked(False)
        self.page.search_input.setText('节拍')
        self.assertFalse(self._card('setting_page.beat_detection').isVisible())
        self.assertTrue(self.page.no_results_label.isVisible())
        self.page.advanced_settings_box.setChecked(True)
        self.assertTrue(self._card('setting_page.beat_detection').isVisible())
        self.assertFalse(self.page.no_results_label.isVisible())

    def test_search_shows_equalizer_as_one_control(self) -> None:
        self.page.search_input.setText('均衡器')
        self.assertTrue(self.page.eq_label.isVisible())
        self.assertTrue(self.page.eq_edit.isVisible())
        self.assertTrue(self.page.eq_reset_btn.isVisible())
        self.assertFalse(self._card('setting_page.enable_reverb').isVisible())

    def test_search_keeps_loudness_value_with_slider(self) -> None:
        self.page.search_input.setText(tr('setting_page.target_lufs'))
        self.assertTrue(self.page.target_lufs.isVisible())
        self.assertTrue(self.page.target_lufs_viewer.isVisible())

    def test_search_finds_provider_api_settings(self) -> None:
        for key in (
            'setting_page.llm_api_key',
            'setting_page.model_id',
            'setting_page.display_name',
            '1m context',
        ):
            with self.subTest(key=key):
                self.page.search_input.setText(tr(key))
                self.assertTrue(self.page.llm_provider_list_widget.isVisible())
                self.assertFalse(self.page.no_results_label.isVisible())

    def test_connection_events_keep_unmatched_status_hidden(self) -> None:
        self.page.search_input.setText(tr('setting_page.ws_lyrics_interval_title'))
        event_bus.emit(Events.WEBSOCKET_CONNECTED)
        self._app.processEvents()
        self.assertTrue(self.page.status_widget.isHidden())
        card = self._card('setting_page.ws_lyrics_interval_title')
        self.assertTrue(card.parentWidget().rect().contains(card.geometry()))

    def test_disconnected_statistics_stay_hidden_during_search(self) -> None:
        event_bus.emit(Events.WEBSOCKET_DISCONNECTED)
        self.page.search_input.setText(tr('setting_page.received_size'))
        self.assertTrue(self.page.no_results_label.isVisible())
        self.assertTrue(self.page.status_widget.isHidden())
        event_bus.emit(Events.WEBSOCKET_CONNECTED)
        self.assertTrue(self.page.status_widget.isVisible())
        self.assertFalse(self.page.no_results_label.isVisible())

    def test_search_cancels_pending_smooth_scrolling(self) -> None:
        self.page.search_input.setText('节拍')
        self._app.processEvents()
        scrollbar = self.page.scroller.delegate.vScrollBar
        scrollbar.scrollValue(500)
        self.page.search_input.setText('节拍 ')
        for _ in range(5):
            QTest.qWait(20)
            scrollbar._timer._tick(0.02)
            scrollbar._tick(0.02)
        self.assertEqual(self.page.scroller.verticalScrollBar().value(), 0)

    def test_animation_mode_stays_hidden_when_searching(self) -> None:
        self.page.search_input.setText(tr('setting_page.animations'))
        self.assertTrue(all(b.isVisible() for b in self.page.lyrics_physics_boxes))
        self.assertTrue(all(b.isHidden() for b in self.page.lyrics_scrolling_boxes))
        self.page.lyrics_type_box.setCurrentIndex(1)
        self.assertTrue(all(b.isHidden() for b in self.page.lyrics_physics_boxes))
        self.assertTrue(all(b.isVisible() for b in self.page.lyrics_scrolling_boxes))

    def test_search_is_fixed_when_scrolling_and_resizing(self) -> None:
        for section in self.page._sections:
            section.setExpanded(True)
        QTest.qWait(400)
        self._app.processEvents()
        scrollbar = self.page.scroller.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        position = self.page.search_input.pos()
        scrollbar.setValue(scrollbar.maximum())
        self._app.processEvents()
        self.assertEqual(self.page.search_input.pos(), position)
        self.page.resize(600, 720)
        self._app.processEvents()
        self.assertTrue(self.page.rect().contains(self.page.search_input.geometry()))

    def test_language_change_refreshes_search_results(self) -> None:
        self.page.search_input.setText('output device')
        self.assertTrue(self.page.no_results_label.isVisible())
        setLanguage('en_US')
        self.page.updateLanguage()
        self.assertTrue(self._card('setting_page.output_device').isVisible())
        self.assertFalse(self.page.no_results_label.isVisible())
        self.assertIn('Search', self.page.search_input.placeholderText())


if __name__ == '__main__':
    unittest.main()
