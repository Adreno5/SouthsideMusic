from __future__ import annotations

import io
import logging
import sys
import unittest
from pathlib import Path
from typing import override
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from services.events import Events, event_bus
from views.log_handler import LogHandler


class LogHandlerTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.handler = LogHandler()
        self.handler.terminal_width = 2000
        self.logger = logging.Logger('core.playing_manager', logging.INFO)
        self.logger.addHandler(self.handler)
        self.addCleanup(self.handler.close)
        self.addCleanup(
            event_bus.unsubscribe,
            Events.TERMINAL_SIZE_CHANGED,
            self.handler._refreshTerminalWidth,
        )
        self.addCleanup(
            event_bus.unsubscribe,
            Events.SECOND_TICK,
            self.handler._refreshTerminalWidth,
        )

    def _emitMessage(self, message: str, encoding: str) -> str:
        buffer = io.BytesIO()
        with (
            io.TextIOWrapper(buffer, encoding=encoding, errors='strict') as stream,
            patch.object(sys, '__stdout__', stream),
        ):
            self.logger.info('applied %s lyrics for %s', 'cache', message)
            self.handler.flush()
            return buffer.getvalue().decode(encoding)

    def test_gbk_console_escapes_unsupported_song_name_characters(self) -> None:
        output = self._emitMessage('\u6b4c\u66f2\u30fb\U0001f3b5', 'gbk')

        self.assertIn('applied cache lyrics for \u6b4c\u66f2\\u30fb\\U0001f3b5', output)
        self.assertTrue(output.endswith('\n'))

    def test_utf8_console_preserves_unicode_song_name(self) -> None:
        message = '\u6b4c\u66f2\u30fb\U0001f3b5'
        output = self._emitMessage(message, 'utf-8')

        self.assertIn(f'applied cache lyrics for {message}', output)
        self.assertNotIn('\\u30fb', output)

    def test_ascii_console_escapes_unicode_song_name(self) -> None:
        output = self._emitMessage('\u6b4c\u66f2\u30fb', 'ascii')

        self.assertIn('applied cache lyrics for \\u6b4c\\u66f2\\u30fb', output)

    def test_utf8_console_escapes_lone_surrogate(self) -> None:
        output = self._emitMessage('song\ud800', 'utf-8')

        self.assertIn('applied cache lyrics for song\\ud800', output)


if __name__ == '__main__':
    unittest.main()
