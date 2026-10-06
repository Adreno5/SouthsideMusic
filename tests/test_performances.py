from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from typing import cast, override
from unittest.mock import Mock, call, patch

import numpy as np
import psutil
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.app_context import AppContext
from core.debugging import Debugging
from core.frame_profiler import FrameProfiler
from views.performances import PerformanceOverlay, _residentMemory


class FrameProfilerTests(unittest.TestCase):
    def testPerformanceWithoutDebugging(self) -> None:
        profiler = FrameProfiler()
        profiler.setPerformanceEnabled(True)
        profiler.beginSection('work')
        profiler.endSection()
        profiler.beginFrame()
        self.assertIsNotNone(profiler.snapshot)
        profiler.setPerformanceEnabled(False)
        self.assertFalse(profiler.enabled)

    def testIndependentRequestsPreserveCapture(self) -> None:
        profiler = FrameProfiler()
        profiler.setEnabled(True)
        profiler.setPerformanceEnabled(True)
        profiler.startCapture()
        profiler.beginFrame()
        snapshot = profiler.snapshot
        profiler.setPerformanceEnabled(False)
        self.assertIs(profiler.snapshot, snapshot)
        self.assertTrue(profiler.enabled)
        self.assertTrue(profiler.finishCapture())
        profiler.setEnabled(False)
        self.assertFalse(profiler.enabled)

    def testF3TogglesIndependentlyOfPerformance(self) -> None:
        profiler = FrameProfiler()
        profiler.setPerformanceEnabled(True)
        debugging = SimpleNamespace(
            _enabled=True,
            ctx=SimpleNamespace(
                debugging=False, config=SimpleNamespace(debug_mode=True)
            ),
            setCollecting=Mock(),
            collectInfo=Mock(),
        )
        with patch('core.debugging.frame_profiler', profiler):
            Debugging.toggle(debugging)
            self.assertTrue(debugging.ctx.debugging)
            Debugging.toggle(debugging)
            self.assertFalse(debugging.ctx.debugging)
            self.assertTrue(profiler.enabled)
        profiler.setPerformanceEnabled(False)
        self.assertFalse(profiler.enabled)


class ResidentMemoryTests(unittest.TestCase):
    def testResidentCategoriesAndSharedPages(self) -> None:
        regions = (
            (0x10000, 8192, 0x1000000, 'C:\\Windows\\Qt6Gui.dll'),
            (0x20000, 4096, 0x40000, 'C:\\Windows\\Fonts\\font.ttf'),
            (0x100000, 5 * 1024**2, 0x20000, ''),
            (0x900000, 8192, 0x20000, ''),
            (0xA00000, 4096, 0x20000, ''),
            (0xB00000, 4096, 0x20000, ''),
            (0xC00000, 4096, 0, ''),
        )
        pages = [
            address
            for base, size, _, _ in regions
            for address in range(base, base + size, 4096)
        ]
        kernel, psapi = Mock(), Mock()
        kernel.OpenProcess.return_value = 1

        def workingSet(handle: int, buffer: object, size: int) -> int:
            buffer[0] = len(pages)
            for index, address in enumerate(pages, 1):
                buffer[index] = address | 0x100
            return 1

        def queryRegion(handle: int, address: int, pointer: object, size: int) -> int:
            for base, length, kind, _ in regions:
                if base <= address < base + length:
                    if not kind:
                        return 0
                    region = pointer._obj
                    region.base_address = base
                    region.allocation_base = base
                    region.region_size = length
                    region.kind = kind
                    return ctypes.sizeof(region)
            return 0

        def mappedName(handle: int, address: int, buffer: object, size: int) -> int:
            for base, length, _, path in regions:
                if base <= address < base + length:
                    buffer.value = path
                    return len(path)
            return 0

        def processHeaps(count: int, heaps: object) -> int:
            if count:
                heaps[0] = 0x900000
            return 1

        kernel.VirtualQueryEx.side_effect = queryRegion
        kernel.GetProcessHeaps.side_effect = processHeaps
        psapi.QueryWorkingSet.side_effect = workingSet
        psapi.GetMappedFileNameW.side_effect = mappedName
        process = Mock()
        process.memory_info.return_value = SimpleNamespace(rss=len(pages) * 4096)
        with (
            patch('views.performances.ctypes.WinDLL', side_effect=[kernel, psapi]),
            patch('views.performances.psutil.Process', return_value=process),
        ):
            values, details = _residentMemory(
                os.getpid(),
                [
                    (0x100000, 4096, 'PCM'),
                    (0x100000, 4096, 'Shared PCM'),
                    (0xC00000, 4096, 'Changed buffer'),
                ],
                {0xA00040},
            )
        self.assertEqual(sum(values.values()), len(pages) * 4096)
        self.assertEqual(values['PCM'], 4096)
        self.assertNotIn('Shared PCM', values)
        self.assertNotIn('Pages changed during sampling', values)
        self.assertEqual(values['DLL / Qt6Gui.dll'], 8192)
        self.assertEqual(values['Mapped file / font.ttf'], 4096)
        self.assertEqual(values['Windows heap / 0x900000'], 8192)
        self.assertEqual(values['Python object regions / mixed'], 4096)
        self.assertEqual(values['Native private / small blocks'], 4096)
        self.assertIn('Native allocation / 0x100000', values)
        self.assertIn('C:\\Windows\\Qt6Gui.dll', details['DLL / Qt6Gui.dll'])
        kernel.CloseHandle.assert_called_once_with(1)


class PerformanceOverlayTests(unittest.TestCase):
    _app: QApplication | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = cast(QApplication, QApplication.instance()) or QApplication([])

    @override
    def setUp(self) -> None:
        self.ctx = AppContext()
        self.ctx.harmony_font_family = 'Segoe UI'
        self.ctx.player = QObject()
        self.ctx.player.samples = np.zeros((8192, 2), dtype=np.float32)
        self.ctx.playing_manager = QObject()
        self.ctx.playing_manager.current_song_audio = None
        self.ctx.playing_manager._stream_processes = set()
        self.window = QWidget()
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.window.resize(1000, 800)
        self.ctx.main_window = self.window
        self.overlay = PerformanceOverlay(self.ctx, self.window)
        self.overlay.resize(900, 700)
        self.profiler = FrameProfiler()
        self.profiler_patch = patch('views.performances.frame_profiler', self.profiler)
        self.profiler_patch.start()
        self.memory_patch = patch(
            'views.performances._residentMemory',
            side_effect=OSError('test unavailable'),
        )
        self.memory_patch.start()
        self.window.show()
        self.overlay.show()
        self._waitMemory()

    @override
    def tearDown(self) -> None:
        self.overlay.hide()
        self.window.close()
        self.profiler_patch.stop()
        self.memory_patch.stop()

    def _waitMemory(self) -> None:
        deadline = time.perf_counter() + 5
        while self.overlay._memory_sampling and time.perf_counter() < deadline:
            time.sleep(0.005)
            QTest.qWait(1)
        self.assertFalse(self.overlay._memory_sampling)

    def _measureMemory(
        self,
        roots: tuple[object, ...],
        seen: set[int | tuple[str, int]],
        blocked: set[int],
        ranges: list[tuple[int, int, str]] | None = None,
        component: str = '',
    ) -> int:
        total = 0
        for total in self.overlay._memorySize(roots, seen, blocked, ranges, component):
            continue
        return total

    def testVisibilityControlsSampling(self) -> None:
        self.assertTrue(self.profiler.enabled)
        self.profiler.beginSection('test work')
        self.profiler.endSection()
        self.profiler.beginFrame()
        self.overlay.refresh(raise_overlay=True)
        self.assertTrue(self.overlay._profile_sections)
        self.overlay.hide()
        self.assertFalse(self.profiler.enabled)
        self.assertFalse(self.overlay.memory_timer.isActive())
        self.overlay.show()
        self.assertTrue(self.profiler.enabled)
        self.assertFalse(self.overlay._profile_history)

    def testFrameExportWorksWithoutF3(self) -> None:
        self.ctx.debugging_obj = SimpleNamespace(
            isCollecting=Mock(return_value=False), setCollecting=Mock(), infos=[]
        )
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                self.ctx.debugging = False
                self.overlay.show()
                try:
                    self.assertFalse(self.profiler.debug_enabled)
                    self.overlay._startExport()
                    self.assertTrue(self.overlay._capturing)
                    self.assertTrue(self.profiler.enabled)
                    self.profiler.beginFrame()
                    self.profiler.beginFrame()
                    self.overlay._capture_deadline_ns = 0
                    if cancelled:
                        self.profiler.setPerformanceEnabled(False)
                    with patch.object(
                        self.overlay,
                        '_savePerformanceReport',
                        return_value='test report',
                    ) as save:
                        self.overlay._finishExport()
                    self.assertEqual(save.call_count, int(not cancelled))
                    self.assertFalse(self.ctx.debugging)
                    self.assertTrue(self.profiler.enabled)
                    self.assertFalse(self.overlay._capturing)
                    self.assertTrue(self.overlay.isVisible())
                finally:
                    self.overlay.export_timer.stop()

    def testMemoryExportHidesLayersAndRestores(self) -> None:
        self.ctx.debugging_obj = SimpleNamespace(
            isCollecting=Mock(return_value=True), setCollecting=Mock(), infos=[]
        )
        self.ctx.debugging = True
        with patch.object(
            self.overlay, '_saveMemoryReport', return_value='test report'
        ) as save:
            self.overlay._startMemoryExport()
            self.assertTrue(self.overlay._memory_exporting)
            self.assertFalse(self.ctx.debugging)
            self.assertFalse(self.overlay.isVisible())
            for _ in range(5):
                self.overlay.updateMemory()
                self._waitMemory()
            self.assertEqual(save.call_count, 1)
        self.assertFalse(self.overlay._memory_exporting)
        self.assertTrue(self.overlay.isVisible())
        self.assertTrue(self.ctx.debugging)
        self.assertFalse(self.overlay._memory_export_samples)
        self.assertEqual(
            self.ctx.debugging_obj.setCollecting.call_args_list,
            [call(False), call(True)],
        )

    def testMemoryReportWritesPngAndJson(self) -> None:
        samples = [
            {'Audio PCM buffers': 1024, 'DLL / Qt6Gui.dll': 4096},
            {'Audio PCM buffers': 2048, 'DLL / Qt6Gui.dll': 2048},
        ]
        with tempfile.TemporaryDirectory() as directory:
            with patch('views.performances.DATA_DIR', directory):
                path = self.overlay._saveMemoryReport(samples)
            self.assertTrue(os.path.exists(path))
            with open(os.path.splitext(path)[0] + '.json', encoding='utf-8') as file:
                report = json.load(file)
        self.assertEqual(report['sample_count'], 2)
        self.assertEqual(report['averages']['Audio PCM buffers'], 1536)
        self.assertEqual(report['total_rss'], 4608)
        self.assertEqual(report['samples'], samples)
        self.assertTrue(os.path.basename(path).startswith('memory-'))

    def testArrayViewsCountBackingAllocationOnce(self) -> None:
        samples = np.zeros((2048, 2), dtype=np.float32)
        view = samples[:128]
        seen: set[int | tuple[str, int]] = set()
        size = self._measureMemory((view, samples), seen, set())
        self.assertEqual(size, sys.getsizeof(samples) + sys.getsizeof(view))
        self.assertEqual(self._measureMemory((samples,), seen, set()), 0)

    def testDataclassQueueAndCycles(self) -> None:
        @dataclass(slots=True)
        class Buffer:
            samples: np.ndarray

        samples = np.zeros(4096, dtype=np.float32)
        buffer = Buffer(samples)
        queue: Queue[object] = Queue()
        queue.put(buffer)
        cycle: list[object] = [queue]
        cycle.append(cycle)
        seen: set[int | tuple[str, int]] = set()
        size = self._measureMemory((cycle,), seen, set())
        self.assertGreater(size, samples.nbytes)
        self.assertIn(id(samples), seen)
        self.assertEqual(self._measureMemory((buffer,), seen, set()), 0)

    def testSharedImagesCountPixelsOnce(self) -> None:
        image = QImage(64, 64, QImage.Format.Format_RGBA8888)
        image.fill(Qt.GlobalColor.red)
        pixmap = QPixmap.fromImage(image)
        for original, duplicate, pixels in (
            (image, QImage(image), image.sizeInBytes()),
            (pixmap, QPixmap(pixmap), 64 * 64 * pixmap.depth() // 8),
        ):
            size = self._measureMemory((original, duplicate), set(), set())
            self.assertEqual(
                size, sys.getsizeof(original) + sys.getsizeof(duplicate) + pixels
            )

    def testComponentBoundaries(self) -> None:
        first, second = QObject(), QObject()
        first.other = second
        second.samples = np.zeros(8192, dtype=np.float32)
        seen: set[int | tuple[str, int]] = set()
        blocked = {id(first), id(second)}
        self._measureMemory((first,), seen, blocked)
        self.assertNotIn(id(second), seen)
        self.assertGreater(
            self._measureMemory((second,), seen, blocked), second.samples.nbytes
        )

    def testMemoryConservesRssAndDeduplicatesProcesses(self) -> None:
        main = Mock(pid=os.getpid())
        main.memory_info.return_value = SimpleNamespace(rss=1024)
        worker = Mock(pid=101)
        worker.memory_info.return_value = SimpleNamespace(rss=2048)
        child = Mock(pid=102)
        child.memory_info.return_value = SimpleNamespace(rss=4096)
        worker.children.return_value = [child]
        child.children.return_value = []
        with (
            patch.object(
                self.overlay,
                '_processPids',
                return_value={
                    'main': main.pid,
                    'worker': worker.pid,
                    'child': child.pid,
                },
            ),
            patch.object(
                self.overlay,
                '_process',
                side_effect={main.pid: main, 101: worker, 102: child}.get,
            ),
        ):
            self.overlay.updateMemory()
            self._waitMemory()
        self.assertEqual(sum(self.overlay.mem_values.values()), 1024 + 2048 + 4096)
        self.assertTrue(all(value >= 0 for value in self.overlay.mem_values.values()))
        self.assertGreater(self.overlay.mem_values['Audio PCM buffers'], 0)

    def testLabelPixmapStorageIsIncluded(self) -> None:
        label = QLabel()
        image = QImage(512, 512, QImage.Format.Format_RGBA8888)
        image.fill(Qt.GlobalColor.red)
        label.setPixmap(QPixmap.fromImage(image))
        ranges: list[tuple[int, int, str]] = []
        size = self._measureMemory((label,), set(), set(), ranges, 'Cover images')
        self.assertGreater(size, 512 * 512 * 4)
        self.assertTrue(any(length == 512 * 512 * 4 for _, length, _ in ranges))
        self.assertTrue(all(name == 'Images / Cover images' for _, _, name in ranges))

    def testResidentBreakdownIsApplied(self) -> None:
        values = {'DLL / Qt6Gui.dll': 1024**2, 'Windows heap / 0x123': 2 * 1024**2}
        details = {'DLL / Qt6Gui.dll': 'Qt DLL full path'}
        with patch(
            'views.performances._residentMemory', return_value=(values, details)
        ):
            self.overlay.updateMemory()
            self._waitMemory()
        self.assertEqual(self.overlay.mem_values, values)
        self.assertEqual(self.overlay._memory_details, details)
        self.assertFalse(self.overlay._memory_sampling)

    def testRealResidentSamplingRunsInBackground(self) -> None:
        self.overlay.memory_timer.stop()
        with patch('views.performances._residentMemory', _residentMemory):
            self.overlay.updateMemory()
            self._waitMemory()
        self.assertTrue(self.overlay._memory_resident)
        self.assertNotIn('Main RSS / breakdown unavailable', self.overlay.mem_values)
        self.assertTrue(
            any(name.startswith('DLL / ') for name in self.overlay.mem_values)
        )

    def testNativeResidentPagesMatchRealBuffer(self) -> None:
        samples = np.ones(4 * 1024**2, dtype=np.uint8)
        values, details = _residentMemory(
            os.getpid(), [(int(samples.ctypes.data), samples.nbytes, 'PCM')], set()
        )
        self.assertGreaterEqual(values['PCM'], samples.nbytes)
        self.assertLessEqual(values['PCM'], samples.nbytes + 4096)
        self.assertTrue(any(name.startswith('DLL / ') for name in values))
        self.assertIn('Resident pages', details['PCM'])
        rss = psutil.Process().memory_info().rss
        self.assertLess(abs(sum(values.values()) - rss), 32 * 1024**2)

    def testProcessDiscoveryWithoutF3(self) -> None:
        self.ctx.ws_handler = SimpleNamespace(
            _ft_json_sender=SimpleNamespace(_process=SimpleNamespace(pid=123))
        )
        self.ctx.playing_manager._ft_worker = SimpleNamespace(
            _process=SimpleNamespace(pid=234)
        )
        running = Mock(pid=345)
        running.poll.return_value = None
        stopped = Mock(pid=456)
        stopped.poll.return_value = 0
        self.ctx.playing_manager._stream_processes = {running, stopped}
        with patch(
            'views.performances.lyricVideoExportDebugProcessPids', return_value={}
        ):
            pids = self.overlay._processPids()
        self.assertEqual(
            pids,
            {
                'main': os.getpid(),
                'ws json sender': 123,
                'ffmpeg decode': 234,
                'ffmpeg stream 345': 345,
            },
        )

    def testBothChartsPaint(self) -> None:
        self.profiler.beginFrame()
        self.overlay.refresh(raise_overlay=True)
        image = QImage(900, 700, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            painter.setFont(self.overlay.content_ft)
            self.overlay._paintFrame(painter, 10.0, 420.0)
            self.overlay._paintMemory(painter, 460.0, 420.0)
        finally:
            painter.end()
        self.assertNotEqual(image.pixelColor(220, 80).alpha(), 0)
        self.assertNotEqual(image.pixelColor(670, 80).alpha(), 0)

    def testMemoryWalkYieldsPartialResults(self) -> None:
        buffers = [bytearray(1024) for _ in range(100)]
        ticks = iter(range(0, 1_000_000_000, 1_000_000))
        with patch(
            'views.performances.time.perf_counter_ns', side_effect=lambda: next(ticks)
        ):
            sizes = list(self.overlay._memorySize((buffers,), set(), set()))
        self.assertGreater(len(sizes), 1)
        self.assertLess(sizes[0], sizes[-1])
        self.assertGreater(sizes[-1], 100 * 1024)

    def testMemoryPaintSkipsHiddenRowsAndStatusCopy(self) -> None:
        self.overlay.mem_values = {f'Component {index}': 1024 for index in range(500)}
        self.overlay._memory_sections = list(self.overlay.mem_values.items())
        image = QImage(900, 700, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            painter.setFont(self.overlay.content_ft)
            with patch.object(
                self.overlay, '_drawText', wraps=self.overlay._drawText
            ) as draw:
                self.overlay._paintMemory(painter, 460.0, 420.0)
            texts = [call.args[3] for call in draw.call_args_list]
        finally:
            painter.end()
        self.assertLess(len(self.overlay._memory_rows), 20)
        self.assertLess(len(texts), 60)
        self.assertFalse(
            any('refresh ' in text or 'updated ' in text for text in texts)
        )
        self.assertFalse(
            any('estimates' in text or 'Resident pages' in text for text in texts)
        )


if __name__ == '__main__':
    unittest.main()
