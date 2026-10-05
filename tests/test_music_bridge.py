from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtGui import QColor, QFont, QFontMetricsF
from PySide6.QtWidgets import QApplication

from core.free_threaded_worker import dumpJsonPayload
from core.lyrics import LyricInfo, YRCLyricInfo
from core.ws_server import QObjectHandler
from views.lyrics_viewer import LyricsViewer
from views.playing_controller import PlayingController, cfg
from views.playing_page import PlayingPage
from views.song_card import DummyCard


class ViewerFixture:
    _colorPayload = LyricsViewer._colorPayload
    _payloadColor = LyricsViewer._payloadColor
    _renderLinePayload = LyricsViewer._renderLinePayload
    lyricLayoutPayload = LyricsViewer.lyricLayoutPayload
    _paintLyrics = LyricsViewer._paintLyrics

    def __init__(self, line: LyricInfo | YRCLyricInfo) -> None:
        self.ctx = SimpleNamespace(debugging=False)
        self._cfg = SimpleNamespace(show_translation=True)
        self.ft = QFont('Arial', 14)
        self.tft = QFont('Arial', 10)
        self.metri = QFontMetricsF(self.ft)
        self.tmetri = QFontMetricsF(self.tft)
        self.font_height = self.metri.height()
        self.theight = self.tmetri.height()
        self._view_lines = [line]
        self._view_current_index = 0
        self._view_position = 1.0
        self._view_use_yrc = isinstance(line, YRCLyricInfo)
        self._view_top_offset = 140.4
        self._view_y_offsets = [0.0]
        self._line_alphas: dict[int, object] = {}
        self._shown_lines = [0]
        self.x_pad = 12.0
        self.draw_x_offset = -7.0
        self.beat_flash_timer = SimpleNamespace(current_value=0.5)
        self.clip_w_timer = SimpleNamespace(current_value=30.0, target_value=0.0)
        self.mouse_pos = None

    def height(self) -> int:
        return 301

    def width(self) -> int:
        return 480

    def isVisible(self) -> bool:
        return True

    def logicalDpiY(self) -> int:
        return 96

    def _primaryColorForLine(self, *args: object) -> QColor:
        return QColor(1, 2, 3, 255)

    def _translationColor(self, alpha: float) -> QColor:
        return QColor(4, 5, 6, int(alpha * 0.6))

    def _shouldDrawTranslationForLine(self, *args: object) -> bool:
        return self._cfg.show_translation

    def _translationTextForLine(self, *args: object) -> str:
        return 'translation'

    def _yrcClipPayload(self, *args: object) -> tuple[float, float]:
        return 0.5, 50.0

    def _textWidth(self, text: str) -> float:
        return 100.0


class MusicBridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_painter_and_payload_share_positions_colors_and_yrc_clip(self) -> None:
        viewer = ViewerFixture(YRCLyricInfo(0.0, 2.0, 'song line'))
        layout = viewer.lyricLayoutPayload()
        line = cast(list[dict[str, object]], layout['lines'])[0]
        painter = Mock()
        viewer._paintLyrics(painter)
        center = cast(float, layout['center_y'])
        primary_calls = painter.drawText.call_args_list[:2]
        for call in primary_calls:
            self.assertEqual(
                call.args,
                (line['x'], line['baseline_y_from_center'] + center, line['text']),
            )
        self.assertEqual(
            painter.drawText.call_args_list[-1].args,
            (
                line['translation_x'],
                line['translation_baseline_y_from_center'] + center,
                line['translation'],
            ),
        )
        clip = painter.setClipRect.call_args.args[0]
        self.assertEqual(clip.x(), line['x'])
        self.assertEqual(clip.y(), line['top_y_from_center'] + center)
        self.assertEqual(clip.width(), line['yrc_clip_width'])
        self.assertEqual(
            clip.height(), (line['bottom_y_from_center'] - line['top_y_from_center'])
        )
        self.assertEqual(line['yrc_clip_ratio'], 0.3)
        colors = [call.args[0] for call in painter.setPen.call_args_list]
        self.assertEqual(colors[0], viewer._payloadColor(line['yrc_base_color']))
        self.assertEqual(colors[1], viewer._payloadColor(line['primary_color']))
        self.assertEqual(colors[2], viewer._payloadColor(line['translation_color']))
        self.assertAlmostEqual(layout['primary_font_size_px'], 14 * 96 / 72)

    def test_metadata_has_no_word_highlight_and_translation_can_be_disabled(
        self,
    ) -> None:
        viewer = ViewerFixture(YRCLyricInfo(0.0, 2.0, 'metadata', isMetadata=True))
        viewer._cfg.show_translation = False
        line = viewer._renderLinePayload(0)
        self.assertFalse(line['has_yrc'])
        self.assertEqual(line['yrc_clip_width'], 0.0)
        self.assertEqual(line['translation'], '')

    def test_empty_layout_keeps_canvas_and_explicit_clear_state(self) -> None:
        viewer = ViewerFixture(LyricInfo(0.0, 'line'))
        viewer._view_lines = []
        viewer._shown_lines = []
        viewer._view_current_index = -1
        layout = viewer.lyricLayoutPayload()
        self.assertFalse(layout['ready'])
        self.assertEqual(layout['schema'], 'southside_lyric_layout_v2')
        self.assertEqual(layout['canvas_height'], 301)
        self.assertEqual(layout['lines'], [])

    def test_seek_back_resets_yrc_clip_immediately(self) -> None:
        viewer = ViewerFixture(YRCLyricInfo(0.0, 2.0, 'song line'))
        viewer.clip_w_timer.current_value = 80.0
        line = viewer._renderLinePayload(0)
        self.assertEqual(line['yrc_clip_width'], 50.0)
        self.assertEqual(line['yrc_clip_ratio'], 0.5)

    def test_protocol_version_belongs_to_each_connection(self) -> None:
        first = SimpleNamespace(protocol_version=1)
        second = SimpleNamespace(protocol_version=1)
        handler = SimpleNamespace(_current_handler=first)
        QObjectHandler.protocol_version.fset(handler, 2)
        self.assertEqual(QObjectHandler.protocol_version.fget(handler), 2)
        handler._current_handler = second
        self.assertEqual(QObjectHandler.protocol_version.fget(handler), 1)
        handler._current_handler = first
        self.assertEqual(QObjectHandler.protocol_version.fget(handler), 2)
        handler._current_handler = None
        self.assertEqual(QObjectHandler.protocol_version.fget(handler), 1)

    def _controller(self, version: int, has_lyrics: bool = True) -> SimpleNamespace:
        current = LyricInfo(0.0, 'current') if has_lyrics else None
        parser = SimpleNamespace(
            parsed=[current] if current else [],
            getCurrentIndex=lambda position: 0 if current else -1,
            hasYrcTiming=lambda: False,
        )
        empty_layout = ViewerFixture(LyricInfo(0.0, 'scrolled away'))
        empty_layout._shown_lines = []
        controller = SimpleNamespace(
            ctx=SimpleNamespace(
                playing_manager=SimpleNamespace(
                    getDisplayPosition=lambda: 1.0,
                    getDisplayLength=lambda: 10.0,
                ),
                config=SimpleNamespace(ws_lyrics_interval=0.0),
            ),
            _dp=SimpleNamespace(
                viewer=empty_layout,
                cur=SimpleNamespace(storable=SimpleNamespace(id=123)),
            ),
            _ws_handler=SimpleNamespace(
                is_open=True, protocol_version=version, sendJson=Mock()
            ),
            _player=SimpleNamespace(isPlaying=lambda: False),
            _mgr=parser,
            _ymgr=parser,
            last_lyric=current,
            _last_ws_lyric_send=0.0,
        )
        for name in (
            '_lyricLinePayload',
            '_lyricWindowPayload',
            '_translationTextForLine',
        ):
            setattr(
                controller, name, getattr(PlayingController, name).__get__(controller)
            )
        return controller

    def test_card_lyric_is_independent_of_scrolled_layout(self) -> None:
        controller = self._controller(2)
        with patch.object(cfg, 'show_translation', True):
            PlayingController._updateLyric(controller)
        packets = {
            call.args[0]['option']: call.args[0]
            for call in controller._ws_handler.sendJson.call_args_list
        }
        self.assertEqual(set(packets), {'lyric_layout', 'main_menu_lyric'})
        self.assertEqual(packets['lyric_layout']['layout']['lines'], [])
        self.assertEqual(packets['main_menu_lyric']['text'], 'current')
        self.assertEqual(packets['main_menu_lyric']['song_id'], '123')
        self.assertEqual(set(packets['lyric_layout']), {'option', 'layout'})

    def test_no_lyric_clears_card_and_v1_keeps_legacy_option(self) -> None:
        controller = self._controller(2, has_lyrics=False)
        PlayingController._updateLyric(controller)
        card = controller._ws_handler.sendJson.call_args_list[1].args[0]
        self.assertEqual(card['text'], '')
        self.assertEqual(card['index'], -1)
        self.assertFalse(card['has_yrc'])
        controller = self._controller(1)
        PlayingController._updateLyric(controller)
        packet = controller._ws_handler.sendJson.call_args.args[0]
        self.assertEqual(packet['option'], 'update_lyric')
        self.assertEqual(packet['layout']['schema'], 'southside_lyric_layout_v1')
        self.assertEqual(len(packet['lines']), 5)

    def test_paused_playback_snapshot(self) -> None:
        controller = self._controller(2)
        PlayingController.sendMainMenuPlayback(controller)
        packet = controller._ws_handler.sendJson.call_args.args[0]
        self.assertEqual(packet['option'], 'main_menu_playback')
        self.assertFalse(packet['is_playing'])
        self.assertEqual(packet['ratio'], 0.1)
        self.assertEqual(packet['position'], 1.0)

    def test_song_metadata_is_sent_without_a_cover(self) -> None:
        song = SimpleNamespace(id=123, name='song', artists=[], duration=10000)
        card = DummyCard(song)
        page = SimpleNamespace(
            cur=card,
            img_label=SimpleNamespace(pixmap=lambda: None),
            _ws_handler=SimpleNamespace(
                is_open=True, protocol_version=2, sendJsonFactory=Mock()
            ),
            playing_manager=SimpleNamespace(
                getDisplayPosition=lambda: 1.0,
                getDisplayLength=lambda: 10.0,
            ),
            ctx=SimpleNamespace(player=SimpleNamespace(isPlaying=lambda: False)),
            _ymgr=SimpleNamespace(hasYrcTiming=lambda: False),
        )
        PlayingPage.sendSongCoverAndInfo(page)
        factory = page._ws_handler.sendJsonFactory.call_args.args[0]
        packet = json.loads(dumpJsonPayload(factory()))
        self.assertEqual(packet['option'], 'main_menu_song')
        self.assertEqual(packet['song_name'], 'song')
        self.assertEqual(packet['song_id'], '123')
        self.assertEqual(packet['image'], '')
        self.assertNotIn('position', packet)


if __name__ == '__main__':
    unittest.main()
