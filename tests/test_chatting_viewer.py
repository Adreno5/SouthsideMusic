from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast, override
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QUrl, qVersion
from PySide6.QtGui import (
    QFont,
    QImage,
    QPalette,
    QPixmap,
    QTextDocument,
    QTextImageFormat,
    QTextListFormat,
    QTextOption,
    QTextTable,
)
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QTextEdit

from harness.markdown_cases import MARKDOWN_CASES
from views.chatting_viewer import ChattingViewer, renderMarkdown


class RenderedHTML(HTMLParser):
    def __init__(self, rendered: str) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.feed(rendered)
        self.close()

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, {name: value or '' for name, value in attrs}))


class MarkdownRendererTests(unittest.TestCase):
    def test_case_manifest(self) -> None:
        self.assertGreater(len(MARKDOWN_CASES), 50)
        self.assertEqual(
            len({case['id'] for case in MARKDOWN_CASES}), len(MARKDOWN_CASES)
        )
        for case in MARKDOWN_CASES:
            with self.subTest(case=case['id']):
                self.assertIsInstance(case['id'], str)
                self.assertTrue(case['id'])
                self.assertIsInstance(case['source'], str)
                for field in ['html_contains', 'plain_contains']:
                    self.assertIsInstance(case[field], list)
                    self.assertTrue(
                        all(isinstance(value, str) for value in case[field])
                    )
                for field in [
                    'html_not_contains',
                    'plain_not_contains',
                    'math_inline',
                    'math_block',
                ]:
                    if field in case:
                        self.assertIsInstance(case[field], list)
                        self.assertTrue(
                            all(isinstance(value, str) for value in case[field])
                        )
                for filename, encoded in case.get('image_files', {}).items():
                    self.assertFalse(Path(filename).is_absolute())
                    self.assertNotIn('..', Path(filename).parts)
                    self.assertTrue(base64.b64decode(encoded, validate=True))

    def test_markdown_cases(self) -> None:
        for case in MARKDOWN_CASES:
            with self.subTest(case=case['id']):
                rendered = renderMarkdown(case['source'])
                self.assertIsInstance(rendered, str)
                for fragment in case['html_contains']:
                    self.assertTrue(
                        fragment in rendered,
                        f'Missing {ascii(fragment)}; HTML prefix: {ascii(rendered[:300])}',
                    )
                for fragment in case.get('html_not_contains', []):
                    self.assertTrue(
                        fragment not in rendered,
                        f'Unexpected {ascii(fragment)}; HTML prefix: {ascii(rendered[:300])}',
                    )

    def test_formula_images_have_valid_svg_and_expected_display_mode(self) -> None:
        for case in MARKDOWN_CASES:
            expected = [
                *[('math-inline', formula) for formula in case.get('math_inline', [])],
                *[('math-block', formula) for formula in case.get('math_block', [])],
            ]
            if not expected:
                continue
            with self.subTest(case=case['id']):
                parsed = RenderedHTML(renderMarkdown(case['source']))
                images = [
                    attrs
                    for tag, attrs in parsed.elements
                    if tag == 'img'
                    and attrs.get('class') in ['math-inline', 'math-block']
                ]
                self.assertCountEqual(
                    [(attrs['class'], attrs.get('alt', '')) for attrs in images],
                    expected,
                )
                for image in images:
                    source = image.get('src', '')
                    self.assertTrue(source.startswith('data:image/svg+xml;base64,'))
                    svg = ElementTree.fromstring(
                        base64.b64decode(source.split(',', 1)[1])
                    )
                    self.assertEqual(svg.tag.rsplit('}', 1)[-1], 'svg')
                    self.assertGreater(float(svg.attrib['width'].removesuffix('px')), 0)
                    self.assertGreater(
                        float(svg.attrib['height'].removesuffix('px')), 0
                    )
                    self.assertTrue(
                        any(node.tag.endswith('path') for node in svg.iter())
                    )

    def test_formula_color_is_applied_inside_and_outside_latex_fences(self) -> None:
        for source in [r'$x^2$', r'\(x^2\)', '$$x^2$$', '```latex\n$x^2$\n```']:
            with self.subTest(source=source):
                parsed = RenderedHTML(renderMarkdown(source, '#123abc'))
                images = [attrs for tag, attrs in parsed.elements if tag == 'img']
                self.assertEqual(len(images), 1)
                svg = base64.b64decode(images[0]['src'].split(',', 1)[1]).decode(
                    'utf-8'
                )
                self.assertTrue(
                    '#123abc' in svg, 'Formula SVG ignored math_color=#123abc'
                )

    def test_formula_rendering_does_not_leak_color_between_calls(self) -> None:
        for color in ['#123abc', '#fedcba', '#123abc']:
            with self.subTest(color=color):
                parsed = RenderedHTML(renderMarkdown('$x^2$', color))
                image = next(attrs for tag, attrs in parsed.elements if tag == 'img')
                svg = base64.b64decode(image['src'].split(',', 1)[1]).decode('utf-8')
                self.assertIn(color, svg)


class ChattingViewerTests(unittest.TestCase):
    _app: QApplication | None = None

    @classmethod
    @override
    def setUpClass(cls) -> None:
        cls._app = cast(QApplication, QApplication.instance()) or QApplication([])

    @override
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix='southside-markdown-')
        self.viewer = ChattingViewer()
        self.viewer.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.viewer.resize(640, 24)
        self.viewer.browser.document().setBaseUrl(
            QUrl.fromLocalFile(self.temp_dir.name + os.sep)
        )
        self.viewer.show()
        cast(QApplication, self._app).processEvents()

    @override
    def tearDown(self) -> None:
        self.viewer.reset()
        self.viewer.close()
        self.viewer.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        cast(QApplication, self._app).processEvents()
        self.temp_dir.cleanup()

    def _loadCase(self, case: dict[str, Any]) -> None:
        self.viewer.reset()
        for filename, encoded in case.get('image_files', {}).items():
            target = Path(self.temp_dir.name) / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(encoded, validate=True))
        self.viewer.appendChunk(case['source'])
        self.viewer.finishStream()
        cast(QApplication, self._app).processEvents()

    def _imageFormats(self) -> list[QTextImageFormat]:
        images: list[QTextImageFormat] = []
        block = self.viewer.browser.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isImageFormat():
                    images.append(fragment.charFormat().toImageFormat())
                iterator += 1
            block = block.next()
        return images

    def _assertImageLoaded(self, image_format: QTextImageFormat) -> None:
        name = image_format.name()
        document = self.viewer.browser.document()
        resource = document.resource(
            QTextDocument.ResourceType.ImageResource, QUrl(name)
        )
        diagnostic = (
            f'Qt {qVersion()} image resource '
            f'{ascii(name[:100])}: {type(resource).__name__}'
        )
        self.assertIsInstance(resource, (QImage, QPixmap), diagnostic)
        self.assertFalse(resource.isNull(), diagnostic)
        self.assertGreater(resource.width(), 0, diagnostic)
        self.assertGreater(resource.height(), 0, diagnostic)

    def _visualLineCount(self) -> int:
        line_count = 0
        block = self.viewer.browser.document().begin()
        while block.isValid():
            line_count += block.layout().lineCount()
            block = block.next()
        return line_count

    def test_real_qtextdocument_cases(self) -> None:
        for case in MARKDOWN_CASES:
            with self.subTest(case=case['id']):
                self._loadCase(case)
                self.assertIsInstance(self.viewer.browser.document(), QTextDocument)
                self.assertEqual(self.viewer.toMarkdown(), case['source'])
                plain = self.viewer.browser.toPlainText()
                for fragment in case['plain_contains']:
                    self.assertTrue(
                        fragment in plain,
                        f'Missing {ascii(fragment)}; plain text: {ascii(plain[:500])}',
                    )
                for fragment in case.get('plain_not_contains', []):
                    self.assertTrue(
                        fragment not in plain,
                        f'Unexpected {ascii(fragment)}; plain text: {ascii(plain[:500])}',
                    )

    def test_document_has_real_heading_emphasis_lists_table_and_links(self) -> None:
        source = (
            '# Heading\n\n'
            '**bold** *italic* ~~deleted~~\n\n'
            '- Parent\n'
            '  - Child\n'
            '    1. Grandchild\n\n'
            '| Name | Count |\n'
            '| --- | --- |\n'
            '| Track | 7 |\n\n'
            '[Southside](https://example.invalid/music)'
        )
        self.viewer.appendChunk(source)
        self.viewer.finishStream()
        document = self.viewer.browser.document()
        for text, property_name, expected in [
            ('bold', 'fontWeight', QFont.Weight.Bold),
            ('italic', 'fontItalic', True),
            ('deleted', 'fontStrikeOut', True),
        ]:
            with self.subTest(format=property_name):
                cursor = document.find(text)
                self.assertFalse(cursor.isNull())
                self.assertEqual(
                    getattr(cursor.charFormat(), property_name)(), expected
                )
        self.assertEqual(document.find('Heading').blockFormat().headingLevel(), 1)
        list_indents: list[int] = []
        for text in ['Parent', 'Child', 'Grandchild']:
            with self.subTest(list_item=text):
                text_list = document.find(text).block().textList()
                self.assertIsNotNone(text_list)
                list_indents.append(text_list.format().indent())
        self.assertLess(list_indents[0], list_indents[1])
        self.assertLess(list_indents[1], list_indents[2])
        self.assertEqual(
            document.find('Grandchild').block().textList().format().style(),
            QTextListFormat.Style.ListDecimal,
        )
        tables = [
            frame
            for frame in document.rootFrame().childFrames()
            if isinstance(frame, QTextTable)
        ]
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].rows(), 2)
        self.assertEqual(tables[0].columns(), 2)
        for row, column, text in [
            (0, 0, 'Name'),
            (0, 1, 'Count'),
            (1, 0, 'Track'),
            (1, 1, '7'),
        ]:
            with self.subTest(cell=(row, column)):
                cell = tables[0].cellAt(row, column)
                cursor = cell.firstCursorPosition()
                cursor.setPosition(
                    cell.lastCursorPosition().position(), cursor.MoveMode.KeepAnchor
                )
                self.assertIn(text, cursor.selectedText())
        link_format = document.find('Southside').charFormat()
        self.assertTrue(link_format.isAnchor())
        self.assertEqual(link_format.anchorHref(), 'https://example.invalid/music')

    def test_local_inline_and_formula_resources_are_actually_loaded(self) -> None:
        for case in MARKDOWN_CASES:
            expected_math = len(case.get('math_inline', [])) + len(
                case.get('math_block', [])
            )
            if not expected_math and case['id'] not in [
                'local_image',
                'inline_image',
                'linked_image',
            ]:
                continue
            with self.subTest(case=case['id']):
                self._loadCase(case)
                images = self._imageFormats()
                self.assertEqual(len(images), expected_math or 1)
                for image_format in images:
                    self._assertImageLoaded(image_format)
                self.assertEqual(
                    self.viewer.browser.toPlainText().count('\ufffc'), len(images)
                )

    def test_every_case_survives_fragmented_append_and_finish(self) -> None:
        for case in MARKDOWN_CASES:
            with self.subTest(case=case['id']):
                self._loadCase(case)
                complete_plain = self.viewer.browser.toPlainText()
                complete_images = [image.name() for image in self._imageFormats()]
                self.viewer.reset()
                received = QSignalSpy(self.viewer.charReceived)
                finished = QSignalSpy(self.viewer.finished)
                source = case['source']
                position = 0
                for width in [1, 2, 3, 5, 8]:
                    self.viewer.appendChunk(source[position : position + width])
                    position += width
                for position in range(position, len(source), 7):
                    self.viewer.appendChunk(source[position : position + 7])
                self.viewer.finishStream()
                self.assertEqual(self.viewer.toMarkdown(), source)
                self.assertEqual(self.viewer.browser.toPlainText(), complete_plain)
                self.assertEqual(
                    [image.name() for image in self._imageFormats()], complete_images
                )
                self.assertEqual(
                    sum(received.at(index)[0] for index in range(received.count())),
                    len(source),
                )
                self.assertEqual(finished.count(), 1)

    def test_timer_renders_incomplete_syntax_then_completes_it(self) -> None:
        for source in [
            '**bold** and *italic* and ~~deleted~~',
            '[link](https://example.invalid/stream)',
            '![cover](markdown-cover.png)',
            '```python\nvalue = "$x$"\n```',
            '$x^2$ and $$y^2$$',
            r'\(x^2\) and \[y^2\]',
            '| Name | Value |\n| --- | --- |\n| Track | 7 |',
        ]:
            for split in [1, len(source) // 2, len(source) - 1]:
                with self.subTest(source=source, split=split):
                    self.viewer.reset()
                    received = QSignalSpy(self.viewer.charReceived)
                    finished = QSignalSpy(self.viewer.finished)
                    self.viewer.appendChunk(source[:split])
                    self.assertTrue(received.wait(1000))
                    self.assertEqual(self.viewer.toMarkdown(), source[:split])
                    self.assertEqual(
                        sum(received.at(index)[0] for index in range(received.count())),
                        split,
                    )
                    self.assertEqual(finished.count(), 0)
                    self.viewer.appendChunk(source[split:])
                    self.viewer.finishStream()
                    self.assertEqual(self.viewer.toMarkdown(), source)
                    self.assertEqual(finished.count(), 1)
                    self.assertEqual(
                        sum(received.at(index)[0] for index in range(received.count())),
                        len(source),
                    )
                    final_plain = self.viewer.browser.toPlainText()
                    self.viewer.reset()
                    self.viewer.appendChunk(source)
                    self.viewer.finishStream()
                    self.assertEqual(self.viewer.browser.toPlainText(), final_plain)

    def test_empty_chunks_and_empty_finish_signals(self) -> None:
        received = QSignalSpy(self.viewer.charReceived)
        finished = QSignalSpy(self.viewer.finished)
        self.viewer.appendChunk('')
        QTest.qWait(120)
        self.assertEqual(received.count(), 0)
        self.assertEqual(finished.count(), 0)
        self.viewer.finishStream()
        self.assertEqual(received.count(), 0)
        self.assertEqual(finished.count(), 1)
        self.assertEqual(self.viewer.toMarkdown(), '')
        self.assertTrue(self.viewer.browser.document().isEmpty())

    def test_signals_count_unicode_characters_and_finish_after_render(self) -> None:
        events: list[tuple[str, int | str]] = []
        self.viewer.charReceived.connect(
            lambda length: events.append(('chars', length))
        )
        self.viewer.finished.connect(
            lambda: events.append(('finished', self.viewer.browser.toPlainText()))
        )
        chunks = ['南方', '🎵', 'e\u0301', '👩\u200d💻', '**bold**']
        for chunk in chunks:
            self.viewer.appendChunk(chunk)
        self.viewer.finishStream()
        self.assertEqual(events[0], ('chars', len(''.join(chunks))))
        self.assertEqual(events[-1][0], 'finished')
        self.assertIn('南方🎵e\u0301👩\u200d💻bold', cast(str, events[-1][1]))
        self.assertEqual(self.viewer.toMarkdown(), ''.join(chunks))

    def test_finish_flushes_pending_buffer_and_cancels_delayed_callbacks(self) -> None:
        received = QSignalSpy(self.viewer.charReceived)
        finished = QSignalSpy(self.viewer.finished)
        self.viewer.appendChunk('First ')
        self.viewer.appendChunk('**chunk**')
        self.viewer.finishStream()
        self.assertEqual(self.viewer.toMarkdown(), 'First **chunk**')
        self.assertIn('First chunk', self.viewer.browser.toPlainText())
        self.assertEqual(received.count(), 1)
        self.assertEqual(received.at(0), [len('First **chunk**')])
        self.assertEqual(finished.count(), 1)
        QTest.qWait(150)
        self.assertEqual(received.count(), 1)
        self.assertEqual(finished.count(), 1)

    def test_reset_clears_rendered_and_pending_content_without_signals(self) -> None:
        self.viewer.appendChunk('# Old heading\n\n$x^2$')
        self.viewer.finishStream()
        self.assertTrue(self._imageFormats())
        received = QSignalSpy(self.viewer.charReceived)
        finished = QSignalSpy(self.viewer.finished)
        self.viewer.appendChunk(' stale pending text')
        self.viewer.reset()
        QTest.qWait(150)
        self.assertEqual(self.viewer.toMarkdown(), '')
        self.assertTrue(self.viewer.browser.document().isEmpty())
        self.assertEqual(self._imageFormats(), [])
        self.assertEqual(received.count(), 0)
        self.assertEqual(finished.count(), 0)
        self.viewer.appendChunk('**New** stream')
        self.viewer.finishStream()
        self.assertEqual(self.viewer.toMarkdown(), '**New** stream')
        self.assertIn('New stream', self.viewer.browser.toPlainText())
        self.assertNotIn('Old', self.viewer.browser.toPlainText())
        self.assertNotIn('stale', self.viewer.browser.toPlainText())
        self.assertEqual(received.at(0), [len('**New** stream')])
        self.assertEqual(finished.count(), 1)

    def test_append_after_finish_and_reset_can_start_another_stream(self) -> None:
        finished = QSignalSpy(self.viewer.finished)
        for chunk in ['First', ' second']:
            self.viewer.appendChunk(chunk)
            self.viewer.finishStream()
        self.assertEqual(self.viewer.toMarkdown(), 'First second')
        self.assertEqual(finished.count(), 2)
        self.viewer.reset()
        self.viewer.appendChunk('Third')
        self.viewer.finishStream()
        self.assertEqual(self.viewer.toMarkdown(), 'Third')
        self.assertEqual(finished.count(), 3)

    def test_wrap_modes_and_scrollbar_policies(self) -> None:
        self.assertTrue(self.viewer.browser.isReadOnly())
        self.assertTrue(self.viewer.browser.openExternalLinks())
        self.assertEqual(
            self.viewer.browser.lineWrapMode(), QTextEdit.LineWrapMode.WidgetWidth
        )
        self.assertEqual(
            self.viewer.browser.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self.assertEqual(
            self.viewer.browser.verticalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        for source in ['ordinary text', '```text\n' + 'x' * 400 + '\n```']:
            with self.subTest(source=source[:30]):
                self.viewer.reset()
                self.viewer.appendChunk(source)
                self.viewer.finishStream()
                self.assertEqual(
                    self.viewer.browser.document().defaultTextOption().wrapMode(),
                    QTextOption.WrapMode.WrapAnywhere,
                )
                block = self.viewer.browser.document().begin()
                while block.isValid():
                    self.assertFalse(block.blockFormat().nonBreakableLines())
                    block = block.next()

    def test_width_changes_wrap_prose_and_code_and_fit_document_height(self) -> None:
        for source in [
            'wrap text ' * 120,
            'x' * 700,
            '```python\n' + 'long_identifier_' * 80 + '\n```',
        ]:
            with self.subTest(source=source[:30]):
                self.viewer.reset()
                self.viewer.setFixedWidth(720)
                self.viewer.appendChunk(source)
                self.viewer.finishStream()
                cast(QApplication, self._app).processEvents()
                document = self.viewer.browser.document()
                wide_height = document.size().height()
                wide_lines = self._visualLineCount()
                original_plain = self.viewer.browser.toPlainText()
                self.viewer.setFixedWidth(240)
                QTest.qWait(30)
                narrow_height = document.size().height()
                self.assertGreater(narrow_height, wide_height)
                self.assertGreater(self._visualLineCount(), wide_lines)
                self.assertEqual(self.viewer.browser.toPlainText(), original_plain)
                self.assertLessEqual(
                    document.textWidth(), self.viewer.browser.viewport().width()
                )
                self.assertGreaterEqual(self.viewer.browser.height(), narrow_height)
                self.assertEqual(self.viewer.browser.horizontalScrollBar().maximum(), 0)
                self.viewer.setFixedWidth(720)
                QTest.qWait(30)
                self.assertLess(document.size().height(), narrow_height)
                self.assertGreaterEqual(
                    self.viewer.browser.height(), document.size().height()
                )

    def test_resize_can_shrink_viewer_after_initial_render(self) -> None:
        self.viewer.resize(720, 24)
        self.viewer.appendChunk('wrap text ' * 120)
        self.viewer.finishStream()
        cast(QApplication, self._app).processEvents()
        wide_width = self.viewer.width()
        self.viewer.resize(240, self.viewer.height())
        QTest.qWait(30)
        self.assertLess(
            self.viewer.width(),
            wide_width,
            f'Resize to 240 kept viewer width={self.viewer.width()}, '
            f'minimum width={self.viewer.minimumWidth()}, '
            f'browser width={self.viewer.browser.width()}',
        )

    def test_formula_resource_uses_browser_palette_color(self) -> None:
        palette = self.viewer.browser.palette()
        palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.darkMagenta)
        self.viewer.browser.setPalette(palette)
        self.viewer.appendChunk('Before $x^2$ after.')
        self.viewer.finishStream()
        images = self._imageFormats()
        self.assertEqual(len(images), 1)
        self._assertImageLoaded(images[0])
        svg = base64.b64decode(images[0].name().split(',', 1)[1]).decode('utf-8')
        self.assertIn(palette.text().color().name(), svg)


if __name__ == '__main__':
    unittest.main()
