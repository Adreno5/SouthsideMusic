from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import cast, override

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPaintEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from services.events import (
    BACKGROUND_RATIO_CHANGED,
    POST_THEME_CHANGED,
    REPAINT,
    event_bus,
)
from views.line_edit import SearchLineEdit


class CountingSearchLineEdit(SearchLineEdit):
    def __init__(self, parent: QWidget) -> None:
        self.paint_count = 0
        super().__init__(None, 'Segoe UI')
        self.setParent(parent)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        self.paint_count += 1
        super().paintEvent(event)


class SearchLineEditTests(unittest.TestCase):
    _app: QApplication | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = cast(QApplication, QApplication.instance()) or QApplication([])

    @override
    def setUp(self) -> None:
        self.window = QWidget()
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.window.resize(800, 60)
        self.edit = CountingSearchLineEdit(self.window)
        self.edit.resize(450, 35)
        self.edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.timer = QTimer(self.window)
        self.timer.timeout.connect(lambda: event_bus.emit(REPAINT, 1.0))
        self.timer.start(4)
        self.window.show()
        QTest.qWait(350)

    @override
    def tearDown(self) -> None:
        self.timer.stop()
        event_bus.unsubscribe(REPAINT, self.edit._repaintTick)
        event_bus.unsubscribe(POST_THEME_CHANGED, self.edit._onThemeChanged)
        event_bus.unsubscribe(BACKGROUND_RATIO_CHANGED, self.edit._onThemeChanged)
        self.window.close()
        self.window.deleteLater()
        cast(QApplication, self._app).processEvents()

    def test_idle_search_stops_repainting(self) -> None:
        self.edit.paint_count = 0
        QTest.qWait(80)
        self.assertEqual(self.edit.paint_count, 0)

        self.edit.update()
        QTest.qWait(80)
        self.assertEqual(self.edit.paint_count, 1)

    def test_text_expansion_and_collapse_settle(self) -> None:
        collapsed = self.edit.textMargins()
        self.edit.setText('SouthsideMusic')
        QTest.qWait(400)
        expanded = self.edit.textMargins()
        self.assertLess(expanded.left(), collapsed.left())
        self.assertLess(expanded.right(), collapsed.right())
        self.assertLess(expanded.left() + expanded.right(), self.edit.width())

        self.edit.paint_count = 0
        QTest.qWait(80)
        self.assertEqual(self.edit.paint_count, 0)

        self.edit.clear()
        QTest.qWait(400)
        self.assertEqual(self.edit.textMargins(), collapsed)
        self.edit.paint_count = 0
        QTest.qWait(80)
        self.assertEqual(self.edit.paint_count, 0)

    def test_resize_updates_margins_and_stops_repainting(self) -> None:
        original = self.edit.textMargins()
        self.edit.resize(650, 40)
        QTest.qWait(80)
        self.assertGreater(self.edit.textMargins().left(), original.left())
        self.assertGreater(self.edit.textMargins().right(), original.right())
        self.assertEqual(self.edit.draw_pixmap.deviceIndependentSize().height(), 34)

        self.edit.paint_count = 0
        QTest.qWait(80)
        self.assertEqual(self.edit.paint_count, 0)


if __name__ == '__main__':
    unittest.main()
