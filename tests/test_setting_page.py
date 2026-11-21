from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from typing import cast, override
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import CardWidget

from core.app_context import AppContext
from core.config import Config, _configToJsonObject, cfg
from core.i18n import setLanguage, tr
from core.whisper_lyrics import WHISPER_MODELS, getWhisperModel
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
        cfg.whisper_enabled = False
        cfg.whisper_model = 'auto'
        self.addCleanup(vars(cfg).update, self.config)
        self.addCleanup(patch.stopall)
        self.save_config = patch('views.setting_page.scheduleConfigSave').start()
        patch('views.setting_page.getAudioDevices', return_value=[]).start()
        hardware = SimpleNamespace(
            runtime_label='ONNX Runtime CPU',
            description='Test CPU · 16 GB RAM',
            recommended_model='base',
        )
        patch('views.setting_page.getWhisperHardware', return_value=hardware).start()
        patch('core.whisper_lyrics.getWhisperHardware', return_value=hardware).start()
        self.whisper_ready = patch(
            'views.setting_page.isWhisperReady', return_value=False
        ).start()
        self.install_whisper = patch('views.setting_page.installWhisper').start()
        self.async_task = patch('views.setting_page.asyncTask').start()
        self.async_task.side_effect = self._runAsync
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
        self.async_task.reset_mock()
        self.async_task.side_effect = None

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

    def _recreatePage(self) -> None:
        ctx = self.page.ctx
        self.page.close()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.async_task.side_effect = self._runAsync
        self.page = SettingPage(ctx)
        self.page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.page.resize(1000, 720)
        self.page.show()
        self._app.processEvents()
        self.async_task.reset_mock()
        self.async_task.side_effect = None

    def _runAsync(
        self,
        task: object,
        args: tuple,
        parent: object,
        finished: object = None,
    ) -> None:
        task(*args)
        if callable(finished):
            finished()

    def _setWhisperReady(self, names: set[str]) -> None:
        self.page._whisper_ready = {
            model.name: model.name in names for model in WHISPER_MODELS
        }
        self.page._refreshWhisperModels()
        self.page._refreshWhisperState()

    def test_whisper_config_defaults_and_serialization(self) -> None:
        self.assertFalse(Config.whisper_enabled)
        self.assertEqual(Config.whisper_model, 'auto')
        cfg.whisper_model = 'small'
        cfg.whisper_enabled = True
        data = _configToJsonObject()
        self.assertTrue(data['whisper_enabled'])
        self.assertEqual(data['whisper_model'], 'small')

    def test_whisper_is_visible_without_advanced_settings(self) -> None:
        self.page.advanced_settings_box.setChecked(False)
        self.page.search_input.setText('Whisper')
        section = self.page.whisper_section
        group = cast(QWidget, section.parentWidget())
        self.assertEqual(group.objectName(), 'setting_page.group_experimental')
        self.assertEqual(section.title_l.text(), 'Whisper歌词识别')
        self.assertTrue(self.page.whisper_card.isVisible())
        self.assertTrue(self.page.whisper_enabled_box.isVisible())
        checkbox_card = self._card('setting_page.whisper_enabled')
        self.assertEqual(section.content_layout.indexOf(checkbox_card), 0)
        self.assertEqual(section.content_layout.indexOf(self.page.whisper_card), 1)

    def test_whisper_new_section_starts_expanded_with_controls(self) -> None:
        self.page.advanced_settings_box.setChecked(False)
        section = self.page.whisper_section
        self.assertTrue(section.isExpanded())
        self.assertTrue(cfg.setting_section_expanded[section.title])
        self.assertTrue(self.page.whisper_enabled_box.isVisible())
        self.assertTrue(self.page.whisper_card.isVisible())
        self.assertGreater(section.content_view.height(), 0)
        self.assertTrue(
            section.content_view.rect().contains(self.page.whisper_card.geometry())
        )
        self.assertTrue(
            all(not s.isExpanded() for s in self.page._sections if s is not section)
        )

    def test_whisper_old_collapsed_state_opens_controls_on_startup(self) -> None:
        cfg.setting_section_expanded = {
            'setting_page.whisper': False,
            'setting_page.beat': True,
        }
        self._recreatePage()
        section = self.page.whisper_section
        self.assertTrue(section.isExpanded())
        self.assertGreater(section.content_view.height(), 0)
        self.assertTrue(self.page.whisper_enabled_box.isVisible())
        self.assertTrue(
            section.content_view.rect().contains(self.page.whisper_card.geometry())
        )
        self.assertTrue(cfg.setting_section_expanded['setting_page.whisper'])
        self.assertTrue(cfg.setting_section_expanded['setting_page.beat'])
        QTest.mouseClick(section.expand_button, Qt.MouseButton.LeftButton)
        QTest.qWait(400)
        for _ in range(100):
            if section.content_view.height() == 0:
                break
            QTest.qWait(10)
        self.assertFalse(section.isExpanded())
        self.assertEqual(section.content_view.height(), 0)
        self.assertEqual(
            section.expand_button.toolTip(), tr('setting_page.expand_section')
        )
        self.page.search_input.setText('Whisper')
        self.assertGreater(section.content_view.height(), 0)
        self.assertTrue(self.page.whisper_card.isVisible())
        self.assertFalse(section.expand_button.isEnabled())
        self.page.search_input.clear()
        self.assertFalse(section.isExpanded())
        self.assertEqual(section.content_view.height(), 0)
        self.assertTrue(section.expand_button.isEnabled())
        QTest.mouseClick(section.expand_button, Qt.MouseButton.LeftButton)
        QTest.qWait(400)
        self.assertTrue(section.isExpanded())
        self.assertGreater(section.content_view.height(), 0)
        self.assertEqual(
            section.expand_button.toolTip(), tr('setting_page.collapse_section')
        )
        self._recreatePage()
        self.assertTrue(self.page.whisper_section.isExpanded())
        self.assertGreater(self.page.whisper_section.content_view.height(), 0)

    def test_whisper_requires_installed_runtime_and_model(self) -> None:
        self.assertFalse(self.page.whisper_enabled_box.isEnabled())
        self.assertFalse(self.page.whisper_enabled_box.isChecked())
        self.assertIn('先', self.page.whisper_enabled_box.toolTip())
        self.assertIn('尚未安装', self.page.whisper_status_label.text())
        self.assertIn('16 GB', self.page.whisper_hardware_label.text())
        self.assertIn('ONNX Runtime CPU', self.page.whisper_runtime_label.text())
        self.assertTrue(self.page.whisper_install_button.isEnabled())
        self.assertTrue(self.page.whisper_progress_bar.isHidden())
        self.install_whisper.assert_not_called()
        self.async_task.assert_not_called()

    def test_whisper_preserves_installed_enabled_config_on_creation(self) -> None:
        cfg.whisper_enabled = True
        self.whisper_ready.return_value = True
        self._recreatePage()
        self.assertTrue(cfg.whisper_enabled)
        self.assertTrue(self.page.whisper_enabled_box.isEnabled())
        self.assertTrue(self.page.whisper_enabled_box.isChecked())
        self.assertFalse(self.page.whisper_install_button.isEnabled())
        self.page.ctx.playing_manager.updateWhisperLyrics.assert_not_called()

    def test_whisper_clears_enabled_config_when_install_is_missing(self) -> None:
        cfg.whisper_enabled = True
        self._recreatePage()
        self.assertFalse(cfg.whisper_enabled)
        self.assertFalse(self.page.whisper_enabled_box.isChecked())
        self.assertFalse(self.page.whisper_enabled_box.isEnabled())
        self.save_config.assert_called_once()
        self.page.ctx.playing_manager.updateWhisperLyrics.assert_called_once()

    def test_whisper_initialization_handles_missing_playing_manager(self) -> None:
        cfg.whisper_enabled = True
        self.page.ctx.playing_manager = None
        self.async_task.side_effect = self._runAsync
        self.page._addWhisperSection()
        self.async_task.side_effect = None
        self.assertFalse(cfg.whisper_enabled)
        self.assertFalse(self.page.whisper_enabled_box.isChecked())
        self.assertFalse(self.page.whisper_enabled_box.isEnabled())
        self.save_config.assert_called_once()

    def test_whisper_model_labels_mark_each_installed_model(self) -> None:
        cfg.whisper_model = 'large-v3'
        installed = {'base', 'large-v3'}
        self._setWhisperReady(installed)
        for model in WHISPER_MODELS:
            with self.subTest(model=model.name):
                index = self.page.whisper_model_box.findData(model.name)
                expected = model.label
                if model.name in installed:
                    expected += ' · 已安装'
                self.assertEqual(self.page.whisper_model_box.itemText(index), expected)
                self.assertEqual(
                    self.page.whisper_model_box.itemData(index), model.name
                )
        self.assertIn('已安装', self.page.whisper_model_box.itemText(0))
        setLanguage('en_US')
        self.page.updateLanguage()
        index = self.page.whisper_model_box.findData('base')
        self.assertEqual(
            self.page.whisper_model_box.itemText(index), 'Base · Installed'
        )
        self.assertIn('Installed', self.page.whisper_model_box.itemText(0))
        self.assertEqual(cfg.whisper_model, 'large-v3')
        self.assertEqual(self.page.whisper_model_box.currentData(), 'large-v3')
        self.save_config.assert_not_called()
        self.page.ctx.playing_manager.updateWhisperLyrics.assert_not_called()

    def test_whisper_model_selection_updates_config_size_and_lyrics(self) -> None:
        self.assertEqual(
            [
                self.page.whisper_model_box.itemData(index)
                for index in range(self.page.whisper_model_box.count())
            ],
            ['auto', 'tiny', 'base', 'small', 'medium', 'large-v3', 'turbo'],
        )
        model = getWhisperModel('small')
        self.page.whisper_model_box.setCurrentIndex(
            self.page.whisper_model_box.findData('small')
        )
        self.assertEqual(cfg.whisper_model, 'small')
        self.assertIn(model.label, self.page.whisper_model_info.text())
        self.assertIn(f'{model.size_mb} MB', self.page.whisper_model_info.text())
        self.assertIn(f'{model.ram_gb} GB', self.page.whisper_model_info.text())
        self.save_config.assert_called_once()
        self.page.ctx.playing_manager.updateWhisperLyrics.assert_called_once()

    def test_whisper_toggle_saves_and_refreshes_lyrics(self) -> None:
        self._setWhisperReady({model.name for model in WHISPER_MODELS})
        self.page.whisper_enabled_box.click()
        self.assertTrue(cfg.whisper_enabled)
        self.page.whisper_enabled_box.click()
        self.assertFalse(cfg.whisper_enabled)
        self.assertEqual(self.save_config.call_count, 2)
        self.assertEqual(
            self.page.ctx.playing_manager.updateWhisperLyrics.call_count, 2
        )

    def test_whisper_missing_model_unchecks_and_disables_enabled_recognition(
        self,
    ) -> None:
        self._setWhisperReady({'base'})
        self.page.whisper_enabled_box.click()
        self.page.ctx.playing_manager.updateWhisperLyrics.reset_mock()
        self.save_config.reset_mock()
        self.page.whisper_model_box.setCurrentIndex(
            self.page.whisper_model_box.findData('large-v3')
        )
        self.assertEqual(cfg.whisper_model, 'large-v3')
        self.assertFalse(cfg.whisper_enabled)
        self.assertFalse(self.page.whisper_enabled_box.isChecked())
        self.assertFalse(self.page.whisper_enabled_box.isEnabled())
        self.assertTrue(self.page.whisper_install_button.isEnabled())
        self.save_config.assert_called_once()
        self.page.ctx.playing_manager.updateWhisperLyrics.assert_called_once()

    def test_whisper_installed_model_change_preserves_enabled_recognition(
        self,
    ) -> None:
        self._setWhisperReady({model.name for model in WHISPER_MODELS})
        self.page.whisper_enabled_box.click()
        self.page.ctx.playing_manager.updateWhisperLyrics.reset_mock()
        self.page.whisper_model_box.setCurrentIndex(
            self.page.whisper_model_box.findData('tiny')
        )
        self.assertTrue(cfg.whisper_enabled)
        self.assertTrue(self.page.whisper_enabled_box.isChecked())
        self.assertTrue(self.page.whisper_enabled_box.isEnabled())
        self.page.ctx.playing_manager.updateWhisperLyrics.assert_called_once()

    def test_whisper_install_locks_controls_and_uses_selected_model(self) -> None:
        self.page.whisper_model_box.setCurrentIndex(
            self.page.whisper_model_box.findData('medium')
        )
        self.page.whisper_install_button.click()
        self.assertFalse(self.page.whisper_model_box.isEnabled())
        self.assertFalse(self.page.whisper_install_button.isEnabled())
        self.assertFalse(self.page.whisper_enabled_box.isEnabled())
        self.assertFalse(self.page.whisper_progress_bar.isHidden())
        self.async_task.assert_called_once()
        task = self.async_task.call_args.args[0]
        task()
        self.assertEqual(self.install_whisper.call_args.args[0], 'medium')
        self.assertTrue(callable(self.install_whisper.call_args.args[1]))
        self._app.processEvents()

    def test_whisper_progress_is_queued_from_worker_thread(self) -> None:
        self.page.whisper_install_button.click()
        worker = Thread(
            target=self.page.whisperInstallProgress.emit,
            args=('Downloading model', 0.35),
        )
        worker.start()
        worker.join(timeout=2)
        self.async_task.side_effect = self._runAsync
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.page.whisper_progress_bar.value(), 0)
        self._app.processEvents()
        self.assertEqual(self.page.whisper_progress_bar.value(), 350)
        self.assertIn('Downloading model', self.page.whisper_status_label.text())
        self.assertIn('35%', self.page.whisper_status_label.text())

    def test_whisper_progress_translates_backend_stages_and_preserves_details(
        self,
    ) -> None:
        self.page.whisper_install_button.click()
        stages = (
            ('Downloading runtime installer', '正在下载运行时安装程序'),
            (
                'Installing ONNX Runtime and Whisper dependencies',
                '正在安装 ONNX 运行时和 Whisper 依赖',
            ),
            ('Checking local runtime', '正在检查本地运行时'),
            ('Checking Whisper model', '正在检查 Whisper 模型'),
            ('Whisper is ready', 'Whisper 已就绪'),
            ('Downloading Whisper Large v3', '正在下载 Whisper Large v3'),
            (
                'Downloading Whisper Large v3: model.bin',
                '正在下载 Whisper Large v3：model.bin',
            ),
            ('Custom backend stage', 'Custom backend stage'),
        )
        for message, translated in stages:
            with self.subTest(message=message):
                self.page.whisperInstallProgress.emit(message, 0.5)
                self._app.processEvents()
                self.assertEqual(
                    self.page.whisper_status_label.text(), f'{translated} · 50%'
                )
                setLanguage('en_US')
                self.page.updateLanguage()
                self.assertEqual(
                    self.page.whisper_status_label.text(), f'{message} · 50%'
                )
                setLanguage('zh_CN')
                self.page.updateLanguage()
                self.assertEqual(
                    self.page.whisper_status_label.text(), f'{translated} · 50%'
                )
                self.assertEqual(self.page.whisper_progress_bar.value(), 500)

    def test_whisper_install_success_allows_enabling_without_auto_enable(self) -> None:
        self.page.whisper_install_button.click()
        task = self.async_task.call_args.args[0]
        self.whisper_ready.return_value = True
        self.async_task.side_effect = self._runAsync
        task()
        self.page._onWhisperInstallFinished('')
        self.assertTrue(self.page.whisper_enabled_box.isEnabled())
        self.assertFalse(self.page.whisper_enabled_box.isChecked())
        self.assertFalse(cfg.whisper_enabled)
        self.assertTrue(self.page.whisper_model_box.isEnabled())
        self.assertFalse(self.page.whisper_install_button.isEnabled())
        self.assertEqual(self.page.whisper_progress_bar.value(), 1000)
        self.assertEqual(
            self.page.whisper_status_label.text(), tr('setting_page.whisper_ready')
        )
        self.assertEqual(self.page.whisper_model_box.currentData(), 'auto')
        self.assertIn('已安装', self.page.whisper_model_box.itemText(0))
        index = self.page.whisper_model_box.findData('base')
        self.assertEqual(self.page.whisper_model_box.itemText(index), 'Base · 已安装')

    def test_whisper_failed_install_shows_error_and_allows_retry(self) -> None:
        self.install_whisper.side_effect = RuntimeError('Download interrupted')
        self.page.whisper_install_button.click()
        with self.assertLogs(self.page._logger, level='ERROR'):
            self.async_task.call_args.args[0]()
        self._app.processEvents()
        self.assertFalse(self.page.whisper_enabled_box.isEnabled())
        self.assertFalse(cfg.whisper_enabled)
        self.assertTrue(self.page.whisper_model_box.isEnabled())
        self.assertTrue(self.page.whisper_install_button.isEnabled())
        self.assertIn('Download interrupted', self.page.whisper_status_label.text())
        self.assertEqual(
            self.page.whisper_install_button.text(), tr('setting_page.whisper_retry')
        )
        self.install_whisper.side_effect = None
        self.page.whisper_install_button.click()
        self.whisper_ready.return_value = True
        task = self.async_task.call_args.args[0]
        self.async_task.side_effect = self._runAsync
        task()
        self._app.processEvents()
        self.assertEqual(self.install_whisper.call_count, 2)
        self.assertTrue(self.page.whisper_enabled_box.isEnabled())
        self.assertFalse(self.page.whisper_enabled_box.isChecked())
        self.assertNotIn('Download interrupted', self.page.whisper_status_label.text())

    def test_whisper_install_requires_manifest_validation_before_enabling(self) -> None:
        self.page.whisper_install_button.click()
        task = self.async_task.call_args.args[0]
        self.async_task.side_effect = self._runAsync
        task()
        self._app.processEvents()
        self.assertFalse(self.page.whisper_enabled_box.isEnabled())
        self.assertTrue(self.page.whisper_install_button.isEnabled())
        self.assertIn(
            tr('setting_page.whisper_install_incomplete'),
            self.page.whisper_status_label.text(),
        )
        setLanguage('en_US')
        self.page.updateLanguage()
        self.assertIn(
            tr('setting_page.whisper_install_incomplete'),
            self.page.whisper_status_label.text(),
        )
        self.assertIn('Retry', self.page.whisper_install_button.text())

    def test_whisper_search_matches_hardware_runtime_and_models(self) -> None:
        self.page.advanced_settings_box.setChecked(False)
        for query in (
            'Whisper',
            'ONNX',
            '硬件信息',
            'large-v3',
            *[m.label for m in WHISPER_MODELS],
        ):
            with self.subTest(query=query):
                self.page.search_input.setText(query)
                self.assertTrue(self.page.whisper_card.isVisible())
                self.assertFalse(self.page.no_results_label.isVisible())
        self.page.search_input.clear()
        self.assertTrue(self.page.whisper_section.isExpanded())
        self.assertGreater(self.page.whisper_section.content_view.height(), 0)

    def test_whisper_language_change_preserves_selection_and_progress(self) -> None:
        self.page.whisper_model_box.setCurrentIndex(
            self.page.whisper_model_box.findData('turbo')
        )
        self.page.whisper_install_button.click()
        self.page.whisperInstallProgress.emit('Downloading model', 0.5)
        self._app.processEvents()
        setLanguage('en_US')
        self.page.updateLanguage()
        self.assertEqual(cfg.whisper_model, 'turbo')
        self.assertEqual(self.page.whisper_model_box.currentData(), 'turbo')
        self.assertIn('recommended', self.page.whisper_model_box.itemText(0))
        self.assertIn(
            'Recommended ONNX runtime', self.page.whisper_runtime_label.text()
        )
        self.assertIn('Downloading model', self.page.whisper_status_label.text())
        self.assertIn('50%', self.page.whisper_status_label.text())
        self.assertEqual(
            self.page.whisper_install_button.text(),
            tr('setting_page.whisper_installing'),
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
        self.assertEqual(
            {section.title: section.isExpanded() for section in self.page._sections},
            expanded,
        )
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
