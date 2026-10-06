import ctypes
import gc
import json
import logging
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Iterator
from ctypes import wintypes
from dataclasses import asdict, fields, is_dataclass
from queue import Queue
from typing import override

import numpy as np
import psutil
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QHideEvent,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QShowEvent,
    Qt,
    QWheelEvent,
)
from PySide6.QtWidgets import QLabel, QPushButton, QWidget
from shiboken6 import isValid

from core import theme
from core.app_context import AppContext
from core.frame_profiler import FrameProfile, frame_profiler
from core.lyric_video_export import lyricVideoExportDebugProcessPids
from core.models import DATA_DIR
from core.smooth import EaseOutTimer
from services.events import REPAINT_ALWAYS, event_bus

_logger = logging.getLogger(__name__)


def _residentMemory(
    pid: int,
    ranges: list[tuple[int, int, str]],
    object_addresses: set[int],
) -> tuple[dict[str, int], dict[str, str]]:
    class MemoryRegion(ctypes.Structure):
        _fields_ = [
            ('base_address', ctypes.c_void_p),
            ('allocation_base', ctypes.c_void_p),
            ('allocation_protect', wintypes.DWORD),
            ('region_size', ctypes.c_size_t),
            ('state', wintypes.DWORD),
            ('protect', wintypes.DWORD),
            ('kind', wintypes.DWORD),
        ]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.VirtualQueryEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    kernel.VirtualQueryEx.restype = ctypes.c_size_t
    kernel.GetProcessHeaps.argtypes = [wintypes.DWORD, ctypes.c_void_p]
    kernel.GetProcessHeaps.restype = wintypes.DWORD
    psapi.QueryWorkingSet.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    psapi.GetMappedFileNameW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    handle = kernel.OpenProcess(0x410, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        capacity = psutil.Process(pid).memory_info().rss // 4096 + 8192
        for _ in range(4):
            working_set = (ctypes.c_size_t * (capacity + 1))()
            if psapi.QueryWorkingSet(handle, working_set, ctypes.sizeof(working_set)):
                break
            error = ctypes.get_last_error()
            if error != 24:
                raise ctypes.WinError(error)
            capacity = max(capacity * 2, int(working_set[0]) + 8192)
        else:
            raise ctypes.WinError(24)
        entries = np.ctypeslib.as_array(working_set)[1 : int(working_set[0]) + 1]
        pages = np.sort(entries & np.uintp(np.iinfo(np.uintp).max - 4095))
        assigned = np.zeros(len(pages), dtype=np.bool_)
        values: dict[str, int] = {}
        details: dict[str, str] = {}
        for address, size, name in ranges:
            if not size:
                continue
            left = int(pages.searchsorted(address & ~4095))
            right = int(pages.searchsorted((address + size + 4095) & ~4095))
            count = right - left - int(np.count_nonzero(assigned[left:right]))
            if not count:
                continue
            assigned[left:right] = True
            values[name] = values.get(name, 0) + count * 4096
            details[name] = (
                f'{name}\nResident pages intersecting live buffers.\n'
                'Shared pages are counted once; ownership is a buffer snapshot.'
            )

        heap_addresses: set[int] = set()
        if pid == os.getpid():
            heap_count = kernel.GetProcessHeaps(0, None)
            heaps = (ctypes.c_void_p * heap_count)()
            heap_count = min(heap_count, kernel.GetProcessHeaps(heap_count, heaps))
            heap_addresses.update(int(address) for address in heaps[:heap_count])
        objects = np.array(sorted(object_addresses), dtype=np.uintp)
        region = MemoryRegion()
        path_buffer = ctypes.create_unicode_buffer(32768)
        mapped_paths: dict[int, tuple[str, str]] = {}
        private: dict[int, tuple[int, int, bool]] = {}
        index = 0
        while index < len(pages):
            address = int(pages[index])
            if not kernel.VirtualQueryEx(
                handle, address, ctypes.byref(region), ctypes.sizeof(region)
            ):
                if not assigned[index]:
                    name = 'Pages changed during sampling'
                    values[name] = values.get(name, 0) + 4096
                index += 1
                continue
            end = int(region.base_address) + int(region.region_size)
            right = max(index + 1, int(pages.searchsorted(end)))
            count = right - index - int(np.count_nonzero(assigned[index:right]))
            index = right
            if not count:
                continue
            size = count * 4096
            allocation = int(region.allocation_base or region.base_address or address)
            if region.kind in (0x1000000, 0x40000):
                if allocation not in mapped_paths:
                    length = psapi.GetMappedFileNameW(
                        handle, address, path_buffer, len(path_buffer)
                    )
                    path = path_buffer.value if length else ''
                    prefix = 'DLL' if region.kind == 0x1000000 else 'Mapped file'
                    name = f'{prefix} / {os.path.basename(path) or hex(allocation)}'
                    mapped_paths[allocation] = (name, path)
                name, path = mapped_paths[allocation]
                values[name] = values.get(name, 0) + size
                detail = f'{name}\n{path or "Unresolved mapping path"}'
                if name not in details:
                    details[name] = detail
                elif path and path not in details[name]:
                    details[name] += f'\n{path}'
            else:
                resident, committed, contains_objects = private.get(
                    allocation, (0, 0, False)
                )
                object_index = int(objects.searchsorted(int(region.base_address)))
                contains_objects |= (
                    object_index < len(objects) and int(objects[object_index]) < end
                )
                private[allocation] = (
                    resident + size,
                    committed + int(region.region_size),
                    contains_objects,
                )
        private_counts: dict[str, int] = {}
        for allocation, (resident, committed, contains_objects) in sorted(
            private.items(), key=lambda item: item[1][0], reverse=True
        ):
            if allocation in heap_addresses:
                name = f'Windows heap / {hex(allocation)}'
                meaning = (
                    'Shared Windows heap; native objects and retained free blocks.'
                )
            elif resident >= 4 * 1024**2:
                name = f'Native allocation / {hex(allocation)}'
                meaning = 'Independent private allocation; allocator owner unavailable.'
            elif contains_objects:
                name = 'Python object regions / mixed'
                meaning = 'Private regions containing reachable Python objects.'
            else:
                name = 'Native private / small blocks'
                meaning = 'Private allocations below 4 MB; allocator owner unavailable.'
            values[name] = values.get(name, 0) + resident
            detail = (
                f'{hex(allocation)}: {resident / 1024**2:.2f} MB resident, '
                f'{committed / 1024**2:.2f} MB in sampled regions'
            )
            if name not in details:
                details[name] = f'{name}\n{meaning}'
            private_counts[name] = private_counts.get(name, 0) + 1
            if private_counts[name] <= 8:
                details[name] += f'\n{detail}'
        for name, count in private_counts.items():
            if count > 8:
                details[name] += f'\n+ {count - 8} smaller allocations'
        return values, details
    finally:
        kernel.CloseHandle(handle)


class PerformanceOverlay(QWidget):
    _exclude_from_profile = True

    def __init__(self, ctx: AppContext, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.ctx = ctx
        self.title_ft = QFont(ctx.harmony_font_family, 15, QFont.Weight.Bold)
        self.content_ft = QFont(ctx.harmony_font_family, 10, QFont.Weight.Normal)
        self.title_height = int(QFontMetricsF(self.title_ft).height())
        self.content_height = int(QFontMetricsF(self.content_ft).height())
        self.content_metri = QFontMetricsF(self.content_ft)
        self.item_colors: dict[str, QColor] = {}
        self._profile_history: deque[FrameProfile] = deque(maxlen=30)
        self._profile_sections: list[tuple[str, float, float]] = []
        self._profile_duration_ns = 0.0
        self._last_frame_update_ns = 0
        self._ticks_active = False
        self._memory_sampling = False
        self._memory_resident = False
        self._memory_images: list[QImage | QPixmap] = []
        self._memory_details: dict[str, str] = {}
        self._memory_rows: list[tuple[QRect, str]] = []
        self._memory_sections: list[tuple[str, int]] = []
        self._memory_cache_sources: dict[str, object] = {}
        self._memory_module_count = 0
        self.setMouseTracking(True)

        self.dragging = False
        self.drag_pos: QPoint = QPoint(0, 0)
        self.resizing = False
        self.resize_edges: tuple[bool, bool] = (False, False)
        self.resize_origin = QPoint(0, 0)
        self.geometry_origin = self.geometry()
        self.resize_margin = 10
        self.setMinimumSize(640, 400)

        self.offset_timer = EaseOutTimer(0.3, 2)

        self.process_cache: dict[int, psutil.Process] = {}
        self.mem_values: dict[str, int] = {}
        self.mem_deltas: dict[str, int] = {}
        self.system_total = psutil.virtual_memory().total
        self.system_used = 0

        self.memory_timer = QTimer(self)
        self.memory_timer.setInterval(2000)
        self.memory_timer.timeout.connect(self.updateMemory)
        self.export_timer = QTimer(self)
        self.export_timer.setSingleShot(True)
        self.export_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.export_timer.timeout.connect(self._finishExport)
        self.export_button = QPushButton('Export Frames', self)
        self.export_button.clicked.connect(self._startExport)
        self.export_memory_button = QPushButton('Export Memory', self)
        self.export_memory_button.clicked.connect(self._startMemoryExport)
        self._capturing = False
        self._memory_exporting = False
        self._capture_deadline_ns = 0
        self._memory_export_deadline_ns = 0
        self._memory_export_samples: list[dict[str, int]] = []
        self._debugging_restore = False
        self._resume_debug_collection = False
        self.hide()

    def toggle(self) -> None:
        if not self.ctx.config.debug_mode:
            return
        if self.isVisible():
            self.hide()
            return
        self.show()

    def showEvent(self, event: QShowEvent) -> None:
        self._startTicks()
        self._placeExportButtons()
        self.raise_()
        return super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        if not self._memory_exporting:
            self._stopTicks()
        return super().hideEvent(event)

    def _placeExportButtons(self) -> None:
        right = self.width() - 20
        for button in (self.export_memory_button, self.export_button):
            button.adjustSize()
            button.move(right - button.width(), 12)
            right -= button.width() + 8

    def _startTicks(self) -> None:
        if self._ticks_active:
            return
        self._ticks_active = True
        self._profile_history.clear()
        self._profile_sections.clear()
        self._profile_duration_ns = 0.0
        self._last_frame_update_ns = 0
        frame_profiler.setPerformanceEnabled(True)
        event_bus.subscribe(REPAINT_ALWAYS, self.refresh)
        self.memory_timer.start()
        self.updateMemory()

    def _stopTicks(self) -> None:
        if not self._ticks_active:
            return
        self._ticks_active = False
        event_bus.unsubscribe(REPAINT_ALWAYS, self.refresh)
        self.memory_timer.stop()
        frame_profiler.setPerformanceEnabled(False)

    def refresh(
        self, _multiple_factor: float = 1.0, raise_overlay: bool = False
    ) -> None:
        if not self.isVisible():
            return
        profile = frame_profiler.snapshot
        if (
            profile is not None
            and profile.complete
            and profile.duration_ns > 0
            and (not self._profile_history or self._profile_history[-1] is not profile)
        ):
            self._profile_history.append(profile)
        if raise_overlay:
            self.raise_()
        now = time.perf_counter_ns()
        if not raise_overlay and now - self._last_frame_update_ns < 200_000_000:
            if self.offset_timer.is_animating:
                self.update()
            return
        self._last_frame_update_ns = now
        if not self._profile_history:
            return
        sample_count = len(self._profile_history)
        self._profile_duration_ns = (
            sum(profile.duration_ns for profile in self._profile_history) / sample_count
        )
        section_totals: dict[str, tuple[float, int]] = {}
        for profile in self._profile_history:
            for name, duration_ns in profile.sections:
                percentage_sum, duration_sum = section_totals.get(name, (0.0, 0))
                section_totals[name] = (
                    percentage_sum + duration_ns / profile.duration_ns * 100,
                    duration_sum + duration_ns,
                )
        self._profile_sections = sorted(
            (
                (
                    name,
                    percentage_sum / sample_count,
                    duration_sum / sample_count,
                )
                for name, (percentage_sum, duration_sum) in section_totals.items()
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        self.update()

    def _processPids(self) -> dict[str, int]:
        pids = {'main': os.getpid()}
        for name, owner, attribute in (
            ('ws json sender', self.ctx.ws_handler, '_ft_json_sender'),
            ('ffmpeg decode', self.ctx.playing_manager, '_ft_worker'),
        ):
            process = getattr(getattr(owner, attribute, None), '_process', None)
            if process is not None and process.pid is not None:
                pids[name] = process.pid
        for process in tuple(self.ctx.playing_manager._stream_processes):
            if process.poll() is None:
                pids[f'ffmpeg stream {process.pid}'] = process.pid
        pids.update(lyricVideoExportDebugProcessPids())
        return pids

    def _memorySize(
        self,
        roots: tuple[object, ...],
        seen: set[int | tuple[str, int]],
        blocked: set[int],
        ranges: list[tuple[int, int, str]] | None = None,
        component: str = '',
    ) -> Iterator[int]:
        pending = list(roots)
        allowed = {id(root) for root in roots}
        size = 0
        batch_start = time.perf_counter_ns()
        while pending:
            if time.perf_counter_ns() - batch_start >= 2_000_000:
                yield size
                batch_start = time.perf_counter_ns()
            item = pending.pop()
            identity = id(item)
            if identity in seen or (identity in blocked and identity not in allowed):
                continue
            if isinstance(item, QObject) and not isValid(item):
                continue
            seen.add(identity)
            if ranges is not None and isinstance(item, (str, int, float, type)):
                continue
            size += sys.getsizeof(item, 0)
            if isinstance(item, np.ndarray):
                if item.base is not None:
                    pending.append(item.base)
            elif isinstance(item, memoryview):
                pending.append(item.obj)
            elif isinstance(item, (QImage, QPixmap)):
                key = (type(item).__name__, item.cacheKey())
                if key not in seen:
                    seen.add(key)
                    size += (
                        item.sizeInBytes()
                        if isinstance(item, QImage)
                        else item.width() * item.height() * item.depth() // 8
                    )
                elif ranges is not None:
                    continue
            elif isinstance(item, dict):
                snapshot = item.copy()
                if ranges is None:
                    pending.extend(snapshot.keys())
                pending.extend(snapshot.values())
            elif isinstance(item, (list, tuple, set, frozenset, deque)):
                pending.extend(tuple(item))
            elif isinstance(item, Queue):
                with item.mutex:
                    pending.extend(tuple(item.queue))
            elif isinstance(item, QObject):
                if isinstance(item, QLabel) and (pixmap := item.pixmap()) is not None:
                    self._memory_images.append(pixmap)
                    pending.append(pixmap)
                pending.extend(item.children())
                pending.append(vars(item))
            elif not isinstance(item, type) and is_dataclass(item):
                pending.extend(getattr(item, field.name) for field in fields(item))
            elif type(item).__module__.startswith((
                'core.',
                'views.',
                'pydub.',
            )) and hasattr(item, '__dict__'):
                pending.append(vars(item))
            if ranges is not None:
                if isinstance(item, np.ndarray) and item.size:
                    ranges.append((int(item.ctypes.data), int(item.nbytes), component))
                elif isinstance(item, (bytes, bytearray)) and len(item) >= 4096:
                    data = np.frombuffer(item, dtype=np.uint8)
                    ranges.append((int(data.ctypes.data), int(data.nbytes), component))
                elif isinstance(item, (QImage, QPixmap)) and not item.isNull():
                    image = item.toImage() if isinstance(item, QPixmap) else item
                    self._memory_images.append(image)
                    data = np.frombuffer(image.constBits(), dtype=np.uint8)
                    ranges.append((
                        int(data.ctypes.data),
                        int(data.nbytes),
                        f'Images / {component}',
                    ))
        yield size

    def _process(self, pid: int) -> psutil.Process:
        process = self.process_cache.get(pid)
        if process is None:
            process = psutil.Process(pid)
            self.process_cache[pid] = process
        return process

    def updateMemory(self) -> None:
        if self._memory_exporting and (
            time.perf_counter_ns() >= self._memory_export_deadline_ns
        ):
            self._finishMemoryExport()
            return
        if self._memory_sampling or (
            not self.isVisible() and not self._memory_exporting
        ):
            return
        self._memory_images.clear()
        active = self._processPids()
        values: dict[str, int] = {}
        sampled_pids: set[int] = set()
        process_pids: dict[str, int] = {}
        for name, pid in active.items():
            try:
                process = self._process(pid)
                if not process.is_running():
                    self.process_cache.pop(pid, None)
                    continue
                processes = [(name, process)]
                if name != 'main':
                    for child in process.children(recursive=True):
                        if child.pid in active.values():
                            continue
                        try:
                            child_name = child.name()
                        except psutil.Error:
                            continue
                        processes.append((f'{name} / {child_name} {child.pid}', child))
            except psutil.Error:
                self.process_cache.pop(pid, None)
                continue
            for process_name, process in processes:
                if process.pid in sampled_pids:
                    continue
                try:
                    values[process_name] = process.memory_info().rss
                    process_pids[process_name] = process.pid
                    sampled_pids.add(process.pid)
                except psutil.Error:
                    continue

        ranges: list[tuple[int, int, str]] = []
        seen: set[int | tuple[str, int]] = set()
        estimates: dict[str, int] = {}
        audio_roots: tuple[object, ...] = ()
        components: dict[str, tuple[object, ...]] = {}
        blocked: set[int] = set()
        main_rss = values.pop('main', 0)
        if main_rss:
            ctx = self.ctx
            player = ctx.player
            manager = ctx.playing_manager
            audio_roots = tuple(
                getattr(player, name, None)
                for name in (
                    'samples',
                    '_timeline',
                    '_queued_restore',
                    '_growing_stream_buffer',
                    '_scrub_samples',
                )
            )
            components = {
                'Preload / crossfade': tuple(
                    getattr(manager, name, None)
                    for name in (
                        'next_song_audio',
                        '_next_song_buffer',
                        'crossfade_info',
                    )
                ),
                'Current decoded audio': (manager.current_song_audio,),
                'DSP / FFT / audio queues': (player,),
                'Lyrics data': (ctx.lyrics_manager, ctx.mgr, ctx.transmgr, ctx.ymgr),
                'Library / config': (
                    ctx.favs,
                    ctx.config,
                    ctx.llm_song_handles,
                    ctx.llm_folder_handles,
                ),
                'Playback manager': (manager,),
                'Playing page': (ctx.playing_page,),
                'Desktop lyrics': (ctx.desktop_lyrics_page,),
                'Search page': (ctx.search_page,),
                'Favorites page': (ctx.favorites_page,),
                'Playlist page': (ctx.playlist_page,),
                'Home page': (ctx.home_page,),
                'Rediscovery page': (ctx.rediscovery_page,),
                'Lyric editor': (ctx.lyric_editor_page,),
                'Comments page': (ctx.comments_page,),
                'Settings page': (ctx.setting_page,),
                'Onerad': (ctx.llm,),
                'WebSocket bridge': (ctx.ws_server, ctx.ws_handler),
                'Main window / controls': (ctx.main_window,),
                'Background services': (ctx.events_service, ctx.smtc),
            }
            if self._memory_module_count != len(sys.modules):
                self._memory_cache_sources.clear()
                for module_name, module in tuple(sys.modules.items()):
                    if (
                        not module_name.startswith(('core.', 'views.'))
                        or module is None
                    ):
                        continue
                    for name, value in tuple(vars(module).items()):
                        if isinstance(value, type):
                            cache_sources = tuple(vars(value).items())
                        else:
                            cache_sources = ((name, value),)
                        for cache_name, cache in cache_sources:
                            if (
                                type(cache).__module__ == 'functools'
                                and hasattr(cache, 'cache_info')
                                and hasattr(cache, 'cache_clear')
                            ):
                                label = f'Cache / {module_name}.{name}'
                                if isinstance(value, type):
                                    label += f'.{cache_name}'
                                self._memory_cache_sources[label] = cache
                self._memory_module_count = len(sys.modules)
            components.update(
                (label, tuple(gc.get_referents(cache)))
                for label, cache in self._memory_cache_sources.items()
            )
            seen = {
                id(None),
                id(ctx),
                id(ctx.app),
                id(ctx.debugging_obj),
                id(self),
            }
            debug_overlay = getattr(ctx.main_window, 'debug_overlay', None)
            if debug_overlay is not None:
                seen.add(id(debug_overlay))
            blocked = {id(root) for roots in components.values() for root in roots}
            blocked.update(id(root) for root in audio_roots)
        active_pids = set(active.values())
        for pid in list(self.process_cache):
            if pid not in active_pids:
                self.process_cache.pop(pid, None)

        objects: set[int] = set()
        self._memory_sampling = True
        measurements = (
            (name, size)
            for name, roots in (
                ('Audio PCM buffers', audio_roots),
                *components.items(),
            )
            for size in self._memorySize(roots, seen, blocked, ranges, name)
        )

        def _sample() -> None:
            sampled = values.copy()
            details: dict[str, str] = {}
            resident = False
            try:
                main_values, main_details = _residentMemory(
                    os.getpid(), ranges, objects
                )
            except (OSError, psutil.Error) as error:
                details['Main RSS / breakdown unavailable'] = str(error)
            else:
                resident = True
                sampled = {
                    name: value
                    for name, value in values.items()
                    if name not in estimates
                }
                sampled.pop('Main RSS / breakdown unavailable', None)
                sampled.update(main_values)
                details.update(main_details)
            for process_name, pid in process_pids.items():
                if process_name == 'main':
                    continue
                try:
                    process_values, process_details = _residentMemory(pid, [], set())
                except (OSError, psutil.Error) as error:
                    details[process_name] = (
                        f'PID {pid}\nRSS breakdown unavailable: {error}'
                    )
                    continue
                sampled.pop(process_name, None)
                for name, value in process_values.items():
                    label = f'{process_name} / {name}'
                    sampled[label] = value
                    details[label] = f'PID {pid}\n{process_details.get(name, name)}'
            memory = psutil.virtual_memory()

            def _apply() -> None:
                if not isValid(self):
                    return
                self._memory_sampling = False
                self._memory_images.clear()
                if not self.isVisible() and not self._memory_exporting:
                    return
                self.mem_deltas = {
                    name: value - self.mem_values.get(name, value)
                    for name, value in sampled.items()
                }
                self.mem_values = sampled
                self._memory_sections = sorted(
                    sampled.items(), key=lambda item: item[1], reverse=True
                )
                self._memory_resident = resident
                self._memory_details = details
                self.system_total = memory.total
                self.system_used = memory.total - memory.available
                self.update()
                if self._memory_exporting:
                    self._memory_export_samples.append(dict(self.mem_values))
                    if len(self._memory_export_samples) >= 5:
                        self._finishMemoryExport()

            self.ctx.addScheduledTask(_apply)

        def _collect() -> None:
            if not self.isVisible() and not self._memory_exporting:
                self._memory_sampling = False
                self._memory_images.clear()
                return
            deadline = time.perf_counter_ns() + 3_000_000
            while time.perf_counter_ns() < deadline:
                try:
                    name, size = next(measurements)
                except StopIteration:
                    estimated_total = sum(estimates.values())
                    scale = (
                        min(1.0, main_rss / estimated_total) if estimated_total else 1.0
                    )
                    values.update(
                        (name, int(value * scale))
                        for name, value in estimates.items()
                        if value > 0
                    )
                    values['Main RSS / breakdown unavailable'] = main_rss - sum(
                        int(value * scale) for value in estimates.values()
                    )
                    objects.update(
                        address for address in seen if isinstance(address, int)
                    )
                    threading.Thread(target=_sample, daemon=True).start()
                    return
                estimates[name] = size
            QTimer.singleShot(0, self, _collect)

        QTimer.singleShot(0, self, _collect)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self.offset_timer.target_value += event.angleDelta().y()
        return super().wheelEvent(event)

    def adjustToParent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        bounds = parent.rect()
        width = min(self.width(), bounds.width())
        height = min(self.height(), bounds.height())
        x = max(0, min(self.x(), bounds.width() - width))
        y = max(0, min(self.y(), bounds.height() - height))
        self.setGeometry(
            x, y, max(self.minimumWidth(), width), max(self.minimumHeight(), height)
        )
        self._placeExportButtons()

    def _resizeEdges(self, position: QPoint) -> tuple[bool, bool]:
        return (
            position.x() >= self.width() - self.resize_margin,
            position.y() >= self.height() - self.resize_margin,
        )

    def _updateResizeCursor(self, position: QPoint) -> None:
        right, bottom = self._resizeEdges(position)
        if right and bottom:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif right:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif bottom:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        event.accept()
        edges = self._resizeEdges(event.pos())
        if any(edges):
            self.resizing = True
            self.resize_edges = edges
            self.resize_origin = event.globalPosition().toPoint()
            self.geometry_origin = self.geometry()
            return
        self.dragging = True
        self.drag_pos = QPoint(event.x(), event.y())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.resizing:
            delta = event.globalPosition().toPoint() - self.resize_origin
            right, bottom = self.resize_edges
            width = self.geometry_origin.width() + (delta.x() if right else 0)
            height = self.geometry_origin.height() + (delta.y() if bottom else 0)
            self.resize(
                max(self.minimumWidth(), width), max(self.minimumHeight(), height)
            )
            self.adjustToParent()
            event.accept()
            return
        if not self.dragging:
            self._updateResizeCursor(event.pos())
            text = ''
            for rect, name in self._memory_rows:
                if rect.contains(event.pos()):
                    text = (
                        f'{self._memory_details.get(name, name)}\n'
                        f'RSS: {self.mem_values.get(name, 0) / 1024**2:.2f} MB'
                    )
                    break
            if self.toolTip() != text:
                self.setToolTip(text)
            return super().mouseMoveEvent(event)
        event.accept()
        self.move(self.pos() + event.pos() - self.drag_pos)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        event.accept()
        self.dragging = False
        self.resizing = False
        self._updateResizeCursor(event.pos())

    def _hideDebugLayers(self) -> None:
        self._debugging_restore = self.ctx.debugging
        self._resume_debug_collection = self.ctx.debugging_obj.isCollecting()
        self.ctx.debugging_obj.setCollecting(False)
        self.ctx.debugging = False
        debug_overlay = getattr(self.ctx.main_window, 'debug_overlay', None)
        if debug_overlay is not None:
            debug_overlay.hide()

    def _showDebugLayers(self, restore: bool) -> None:
        self.ctx.debugging = restore
        if not restore:
            return
        if self._resume_debug_collection:
            self.ctx.debugging_obj.setCollecting(True)
        debug_overlay = getattr(self.ctx.main_window, 'debug_overlay', None)
        if debug_overlay is not None:
            debug_overlay.show()

    def _startExport(self) -> None:
        if (
            self._capturing
            or self._memory_exporting
            or not self.isVisible()
            or not frame_profiler.isRecording()
        ):
            return
        self._capturing = True
        self.export_button.setText('Export Frames')
        self.export_button.setEnabled(False)
        self.export_memory_button.setEnabled(False)
        self._hideDebugLayers()
        self.hide()
        frame_profiler.setPerformanceEnabled(True)
        frame_profiler.startCapture()
        self._capture_deadline_ns = time.perf_counter_ns() + 10_000_000_000
        self.export_timer.start(10_000)

    def _finishExport(self) -> None:
        if not self._capturing:
            return
        if frame_profiler.enabled and frame_profiler.isRecording():
            remaining_ns = self._capture_deadline_ns - time.perf_counter_ns()
            if remaining_ns > 0:
                self.export_timer.start(max(1, (remaining_ns + 999_999) // 1_000_000))
                return
        try:
            frames = frame_profiler.finishCapture()
            if not frame_profiler.enabled:
                frames = ()
            if frames:
                path = self._savePerformanceReport(frames)
                self.export_button.setText('PNG + JSON saved - Export Frames')
                self.export_button.setToolTip(path)
                _logger.info('performance report saved to %s', path)
            else:
                self.export_button.setText('Export Frames')
                self.export_button.setToolTip('Capture cancelled: profiling disabled')
        except Exception as error:
            _logger.exception('performance report export failed')
            self.export_button.setText('Export failed - Retry')
            self.export_button.setToolTip(str(error))
        finally:
            self._capturing = False
            self.export_button.setEnabled(True)
            self.export_memory_button.setEnabled(True)
            self._showDebugLayers(self._debugging_restore and frame_profiler.enabled)
            self.show()
            self.update()

    def _savePerformanceReport(self, frames: tuple[FrameProfile, ...]) -> str:
        all_frames = frames
        frames = tuple(frame for frame in frames if frame.complete)
        totals: dict[str, int] = {}
        counts: dict[str, int] = {}
        for frame in frames:
            for name, duration in frame.sections:
                totals[name] = totals.get(name, 0) + duration
            for name, count in frame.section_counts:
                counts[name] = counts.get(name, 0) + count
        duration_ns = sum(frame.duration_ns for frame in frames)
        wall_ns = sum(frame.wall_duration_ns or frame.duration_ns for frame in frames)
        frame_count = sum(frame.frame_count for frame in frames)
        if duration_ns <= 0 or frame_count <= 0:
            raise ValueError('No performance samples were recorded')
        refresh_rate = self.ctx.events_service.refresh_rate
        budget_ns = 1_000_000_000 / refresh_rate
        interval_ns = np.array([frame.wall_duration_ns for frame in frames])
        work_ns = np.array([frame.work_duration_ns for frame in frames])
        statistics = {
            'interval_percentiles_ms': (
                np.percentile(interval_ns, [50, 95, 99]) / 1_000_000
            ).tolist(),
            'work_percentiles_ms': (
                np.percentile(work_ns, [50, 95, 99]) / 1_000_000
            ).tolist(),
            'interval_over_budget_ratio': float(np.mean(interval_ns > budget_ns)),
            'work_over_budget_ratio': float(np.mean(work_ns > budget_ns)),
            'interval_over_two_budgets_ratio': float(
                np.mean(interval_ns > 2 * budget_ns)
            ),
        }
        idle_name = 'Idle / uninstrumented'
        top = sorted(
            (
                (name, duration)
                for name, duration in totals.items()
                if name != idle_name
            ),
            key=lambda item: item[1],
            reverse=True,
        )[:10]
        other_ns = duration_ns - totals.get(idle_name, 0) - sum(v for _, v in top)
        slices = [*top, ('Other modules', max(0, other_ns))]
        slices.append((idle_name, totals.get(idle_name, 0)))
        series = ['Refresh interval (wall time)', *(name for name, _ in top)]
        width = 1680
        height = 1260 + ((len(series) + 1) // 2) * 30
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor('#171a20'))
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QColor('#edf0f5'))
            painter.setFont(QFont(self.ctx.harmony_font_family, 22, QFont.Weight.Bold))
            painter.drawText(40, 48, 'SouthsideMusic - Performance capture')
            painter.setFont(QFont(self.ctx.harmony_font_family, 12))
            painter.drawText(
                40,
                82,
                f'{wall_ns / 1_000_000_000:.3f}s | {frame_count} refresh cycles | '
                f'{frame_count * 1_000_000_000 / max(1, wall_ns):.1f} Hz | '
                f'Screen: {refresh_rate:.1f} Hz | '
                f'Average interval: {wall_ns / frame_count / 1_000_000:.3f} ms | '
                'Debug overlay excluded',
            )
            painter.setFont(QFont(self.ctx.harmony_font_family, 16, QFont.Weight.Bold))
            painter.drawText(40, 126, 'Average time distribution')
            painter.drawText(410, 126, 'Top 10 modules - average over complete cycles')
            pie_rect = QRect(40, 154, 310, 310)
            angle = 0
            elapsed_ns = 0
            painter.setPen(Qt.PenStyle.NoPen)
            for name, duration in slices:
                elapsed_ns += duration
                end_angle = round(elapsed_ns / duration_ns * 5760)
                painter.setBrush(self._itemColor(name))
                painter.drawPie(pie_rect, angle, end_angle - angle)
                angle = end_angle
            painter.setFont(QFont(self.ctx.harmony_font_family, 11))
            for i, (name, duration) in enumerate(top):
                row_y = 166 + i * 50
                painter.fillRect(QRect(410, row_y - 14, 12, 12), self._itemColor(name))
                painter.setPen(QColor('#edf0f5'))
                text = painter.fontMetrics().elidedText(
                    f'{i + 1}. {name}', Qt.TextElideMode.ElideRight, 1200
                )
                painter.drawText(432, row_y, text)
                painter.setPen(QColor('#aeb8c8'))
                painter.drawText(
                    432,
                    row_y + 22,
                    f'{duration / frame_count / 1_000_000:.4f} ms/cycle | '
                    f'{duration / duration_ns * 100:.2f}% | '
                    f'{counts.get(name, 0)} calls | '
                    f'{duration / max(1, counts.get(name, 0)) / 1_000_000:.4f} ms/call',
                )
            for i, (name, duration) in enumerate(slices[-2:]):
                row_y = 500 + i * 35
                painter.fillRect(QRect(40, row_y - 14, 12, 12), self._itemColor(name))
                painter.setPen(QColor('#edf0f5'))
                painter.drawText(
                    62, row_y, f'{name}: {duration / duration_ns * 100:.2f}%'
                )
            painter.setPen(QColor('#edf0f5'))
            painter.setFont(QFont(self.ctx.harmony_font_family, 16, QFont.Weight.Bold))
            painter.drawText(
                40, 708, 'Per-cycle time (ms) - refresh interval and top 10 modules'
            )
            plot = QRect(85, 746, width - 130, 340)
            painter.setFont(QFont(self.ctx.harmony_font_family, 11))
            max_ms = max(frame.wall_duration_ns / 1_000_000 for frame in frames) * 1.1
            max_ms = max(1.0, max_ms)
            for tick in range(6):
                grid_y = plot.bottom() - round(plot.height() * tick / 5)
                painter.setPen(QPen(QColor('#343c48'), 1))
                painter.drawLine(plot.left(), grid_y, plot.right(), grid_y)
                painter.setPen(QColor('#aeb8c8'))
                painter.drawText(15, grid_y + 5, f'{max_ms * tick / 5:.1f}')
                tick_x = plot.left() + round(plot.width() * tick / 5)
                painter.drawText(
                    tick_x - 16, plot.bottom() + 26, f'{wall_ns / 1e9 * tick / 5:.1f}s'
                )
            samples = [dict(frame.sections) for frame in frames]
            painter.save()
            painter.setClipRect(plot.adjusted(-1, -1, 1, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for series_index, name in enumerate(series):
                path = QPainterPath()
                elapsed = 0
                for index, frame in enumerate(frames):
                    elapsed += frame.wall_duration_ns or frame.duration_ns
                    value_ns = (
                        frame.wall_duration_ns
                        if series_index == 0
                        else samples[index].get(name, 0)
                    )
                    x = plot.left() + (plot.width() - 1) * elapsed / max(1, wall_ns)
                    y = (
                        plot.bottom()
                        - (plot.height() - 1) * value_ns / 1_000_000 / max_ms
                    )
                    (path.moveTo if index == 0 else path.lineTo)(x, y)
                color = (
                    QColor('#edf0f5') if series_index == 0 else self._itemColor(name)
                )
                painter.setPen(QPen(color, 2 if series_index == 0 else 1.2))
                painter.drawPath(path)
                if len(frames) == 1:
                    painter.drawEllipse(QPointF(x, y), 3, 3)
            painter.restore()
            for index, name in enumerate(series):
                x = 40 + (index % 2) * 820
                y = 1150 + (index // 2) * 30
                color = QColor('#edf0f5') if index == 0 else self._itemColor(name)
                painter.fillRect(QRect(x, y - 12, 12, 12), color)
                painter.setPen(QColor('#edf0f5'))
                painter.drawText(
                    x + 22,
                    y,
                    painter.fontMetrics().elidedText(
                        name, Qt.TextElideMode.ElideRight, 760
                    ),
                )
            interval_text = ' / '.join(
                f'{v:.3f}' for v in statistics['interval_percentiles_ms']
            )
            work_text = ' / '.join(
                f'{v:.3f}' for v in statistics['work_percentiles_ms']
            )
            painter.drawText(
                40, height - 70, f'Interval P50 / P95 / P99: {interval_text} ms'
            )
            painter.drawText(
                40, height - 45, f'Instrumented work P50 / P95 / P99: {work_text} ms'
            )
            painter.drawText(
                40,
                height - 20,
                f'Budget: {budget_ns / 1_000_000:.3f} ms | '
                f'Interval over budget: {statistics["interval_over_budget_ratio"]:.1%} | '
                f'Work over budget: {statistics["work_over_budget_ratio"]:.1%} | '
                f'Interval over 2x budget: {statistics["interval_over_two_budgets_ratio"]:.1%}',
            )
        finally:
            painter.end()
        directory = os.path.join(DATA_DIR, 'debug', 'performance')
        os.makedirs(directory, exist_ok=True)
        filename = f'performance-{time.strftime("%Y%m%d-%H%M%S")}-{time.time_ns() % 1_000_000_000:09d}.png'
        file_path = os.path.join(directory, filename)
        if not image.save(file_path):
            raise OSError(f'Could not save performance report: {file_path}')
        with open(
            os.path.splitext(file_path)[0] + '.json', 'w', encoding='utf-8'
        ) as file:
            json.dump(
                {
                    'refresh_rate_hz': refresh_rate,
                    'window_size': [
                        self.ctx.main_window.width(),
                        self.ctx.main_window.height(),
                    ],
                    'device_pixel_ratio': self.ctx.main_window.devicePixelRatioF(),
                    'window_visible': self.ctx.main_window.isVisible(),
                    'window_minimized': self.ctx.main_window.isMinimized(),
                    'window_exposed': bool(
                        (handle := self.ctx.main_window.windowHandle()) is not None
                        and handle.isExposed()
                    ),
                    'budget_ns': budget_ns,
                    'statistics': statistics,
                    'frames': [asdict(frame) for frame in all_frames],
                },
                file,
                ensure_ascii=False,
            )
        return file_path

    def _startMemoryExport(self) -> None:
        if self._memory_exporting or self._capturing or not self.isVisible():
            return
        self._memory_exporting = True
        self._memory_export_samples.clear()
        self.export_button.setEnabled(False)
        self.export_memory_button.setText('Export Memory')
        self.export_memory_button.setEnabled(False)
        self._hideDebugLayers()
        self._memory_export_deadline_ns = time.perf_counter_ns() + 60_000_000_000
        self.hide()
        self.updateMemory()

    def _finishMemoryExport(self) -> None:
        if not self._memory_exporting:
            return
        try:
            path = self._saveMemoryReport(self._memory_export_samples)
            self.export_memory_button.setText('PNG + JSON saved - Export Memory')
            self.export_memory_button.setToolTip(path)
            _logger.info('memory report saved to %s', path)
        except Exception as error:
            _logger.exception('memory report export failed')
            self.export_memory_button.setText('Export failed - Retry')
            self.export_memory_button.setToolTip(str(error))
        finally:
            self._memory_export_samples.clear()
            self._memory_exporting = False
            self.export_button.setEnabled(True)
            self.export_memory_button.setEnabled(True)
            self._showDebugLayers(self._debugging_restore)
            self.show()
            self.update()

    def _saveMemoryReport(self, samples: list[dict[str, int]]) -> str:
        if not samples:
            raise ValueError('No memory samples were recorded')
        names = {name for sample in samples for name in sample}
        averages = {
            name: sum(sample.get(name, 0) for sample in samples) / len(samples)
            for name in names
        }
        image = self._memoryReportImage(samples, averages)
        directory = os.path.join(DATA_DIR, 'debug', 'performance')
        os.makedirs(directory, exist_ok=True)
        filename = (
            f'memory-{time.strftime("%Y%m%d-%H%M%S")}-'
            f'{time.time_ns() % 1_000_000_000:09d}.png'
        )
        file_path = os.path.join(directory, filename)
        if not image.save(file_path):
            raise OSError(f'Could not save memory report: {file_path}')
        with open(
            os.path.splitext(file_path)[0] + '.json', 'w', encoding='utf-8'
        ) as file:
            json.dump(
                {
                    'sample_count': len(samples),
                    'system_total': self.system_total,
                    'system_used': self.system_used,
                    'total_rss': sum(averages.values()),
                    'averages': averages,
                    'samples': samples,
                    'details': self._memory_details,
                },
                file,
                ensure_ascii=False,
            )
        return file_path

    def _memoryReportImage(
        self, samples: list[dict[str, int]], averages: dict[str, float]
    ) -> QImage:
        total = max(1.0, sum(averages.values()))
        top = sorted(averages.items(), key=lambda item: item[1], reverse=True)[:10]
        other = max(0.0, total - sum(value for _, value in top))
        slices = [*top, ('Other components', other)]
        series = [name for name, _ in top]
        width = 1680
        height = 1260 + ((len(series) + 1) // 2) * 30
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor('#171a20'))
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QColor('#edf0f5'))
            painter.setFont(QFont(self.ctx.harmony_font_family, 22, QFont.Weight.Bold))
            painter.drawText(40, 48, 'SouthsideMusic - Memory capture')
            painter.setFont(QFont(self.ctx.harmony_font_family, 12))
            painter.drawText(
                40,
                82,
                f'{len(samples)} samples averaged | Total: {total / 1024**2:.2f} MB RSS | '
                f'System: {self.system_used / 1024**3:.2f} / '
                f'{self.system_total / 1024**3:.2f} GB | Debug overlays hidden',
            )
            painter.setFont(QFont(self.ctx.harmony_font_family, 16, QFont.Weight.Bold))
            painter.drawText(40, 126, 'Average resident set')
            painter.drawText(410, 126, 'Top 10 components - average over samples')
            pie_rect = QRect(40, 154, 310, 310)
            angle = 0
            elapsed = 0.0
            painter.setPen(Qt.PenStyle.NoPen)
            for name, value in slices:
                elapsed += value
                end_angle = round(elapsed / total * 5760)
                painter.setBrush(self._itemColor(name))
                painter.drawPie(pie_rect, angle, end_angle - angle)
                angle = end_angle
            painter.setFont(QFont(self.ctx.harmony_font_family, 11))
            for index, (name, value) in enumerate(top):
                row_y = 166 + index * 50
                painter.fillRect(QRect(410, row_y - 14, 12, 12), self._itemColor(name))
                painter.setPen(QColor('#edf0f5'))
                painter.drawText(
                    432,
                    row_y,
                    painter.fontMetrics().elidedText(
                        f'{index + 1}. {name}', Qt.TextElideMode.ElideRight, 1200
                    ),
                )
                painter.setPen(QColor('#aeb8c8'))
                painter.drawText(
                    432,
                    row_y + 22,
                    f'{value / 1024**2:.2f} MB | {value / total * 100:.2f}%',
                )
            painter.drawText(
                410,
                166 + len(top) * 50,
                f'Other components: {other / 1024**2:.2f} MB | '
                f'{other / total * 100:.2f}%',
            )
            painter.setPen(QColor('#edf0f5'))
            painter.setFont(QFont(self.ctx.harmony_font_family, 16, QFont.Weight.Bold))
            painter.drawText(
                40, 708, 'Resident set per sample (MB) - top 10 components'
            )
            plot = QRect(85, 746, width - 130, 340)
            painter.setFont(QFont(self.ctx.harmony_font_family, 11))
            max_mb = (
                max(
                    (
                        sample.get(name, 0) / 1024**2
                        for sample in samples
                        for name in series
                    ),
                    default=1.0,
                )
                * 1.1
            )
            max_mb = max(1.0, max_mb)
            for tick in range(6):
                grid_y = plot.bottom() - round(plot.height() * tick / 5)
                painter.setPen(QPen(QColor('#343c48'), 1))
                painter.drawLine(plot.left(), grid_y, plot.right(), grid_y)
                painter.setPen(QColor('#aeb8c8'))
                painter.drawText(15, grid_y + 5, f'{max_mb * tick / 5:.1f}')
            step = plot.width() / max(1, len(samples))
            for index in range(len(samples)):
                tick_x = round(plot.left() + step * (index + 0.5))
                painter.drawText(tick_x - 8, plot.bottom() + 26, f'#{index + 1}')
            painter.save()
            painter.setClipRect(plot.adjusted(-1, -1, 1, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for name in series:
                points = [
                    QPointF(
                        plot.left() + step * (index + 0.5),
                        plot.bottom()
                        - (plot.height() - 1) * sample.get(name, 0) / 1024**2 / max_mb,
                    )
                    for index, sample in enumerate(samples)
                ]
                path = QPainterPath()
                path.moveTo(points[0])
                for point in points[1:]:
                    path.lineTo(point)
                painter.setPen(QPen(self._itemColor(name), 2))
                painter.drawPath(path)
                for point in points:
                    painter.drawEllipse(point, 3, 3)
            painter.restore()
            for index, name in enumerate(series):
                x = 40 + (index % 2) * 820
                y = 1150 + (index // 2) * 30
                painter.fillRect(QRect(x, y - 12, 12, 12), self._itemColor(name))
                painter.setPen(QColor('#edf0f5'))
                painter.drawText(
                    x + 22,
                    y,
                    painter.fontMetrics().elidedText(
                        name, Qt.TextElideMode.ElideRight, 760
                    ),
                )
            totals = [sum(sample.values()) / 1024**2 for sample in samples]
            painter.drawText(
                40,
                height - 70,
                f'Top 10 coverage: {sum(value for _, value in top) / total * 100:.1f}% | '
                f'Other: {other / 1024**2:.2f} MB | Components: {len(averages)}',
            )
            painter.drawText(
                40,
                height - 45,
                f'Per-sample total (MB): {min(totals):.2f} / '
                f'{sum(totals) / len(totals):.2f} / {max(totals):.2f} '
                '(min / mean / max)',
            )
            painter.drawText(
                40,
                height - 20,
                f'Sample interval: {self.memory_timer.interval() / 1000:.1f}s | '
                f'System in use: {self.system_used / 1024**2:.0f} / '
                f'{self.system_total / 1024**2:.0f} MB',
            )
        finally:
            painter.end()
        return image

    def _itemColor(self, name: str) -> QColor:
        if name not in self.item_colors:
            self.item_colors[name] = QColor.fromHsvF(
                len(self.item_colors) * 0.61803398875 % 1, 0.65, 0.95
            )
        return self.item_colors[name]

    def _drawText(self, painter: QPainter, x: int, y: int, text: str) -> bool:
        bounds = painter.fontMetrics().boundingRect(text).translated(x, y)
        if not self.rect().contains(painter.worldTransform().mapRect(bounds)):
            return False
        if painter.hasClipping() and not painter.clipBoundingRect().contains(bounds):
            return False
        painter.drawText(x, y, text)
        return True

    def _paintPie(
        self,
        painter: QPainter,
        x: float,
        width: float,
        sections: list[tuple[str, float]],
    ) -> int:
        pie_size = int(min(180.0, max(90.0, width - 60)))
        pie_rect = QRect(int(x + (width - pie_size) / 2), 20, pie_size, pie_size)
        legend_y = pie_rect.bottom() + 15 + self.content_height + 4
        if not self.rect().intersects(painter.worldTransform().mapRect(pie_rect)):
            return legend_y
        angle = 0
        elapsed_percentage = 0.0
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        for name, percentage in sections:
            elapsed_percentage += percentage
            end_angle = round(elapsed_percentage / 100 * 5760)
            if end_angle <= angle:
                continue
            painter.setBrush(self._itemColor(name))
            painter.drawPie(pie_rect, angle, end_angle - angle)
            angle = end_angle
        painter.restore()
        return legend_y

    def _paintFrame(self, painter: QPainter, x: float, width: float) -> None:
        if not self._profile_sections:
            painter.setFont(self.title_ft)
            self._drawText(painter, int(x), 20, 'Frame occupancy')
            painter.setFont(self.content_ft)
            self._drawText(
                painter, int(x), 20 + self.title_height + 14, 'Collecting samples...'
            )
            return

        row_height = self.content_height + 4
        legend_y = self._paintPie(
            painter,
            x,
            width,
            [(name, percentage) for name, percentage, _ in self._profile_sections],
        )
        self._drawText(
            painter,
            int(x),
            legend_y,
            f'Frame: {self._profile_duration_ns / 1_000_000:.3f} ms',
        )
        legend_y += row_height
        for name, percentage, mean_duration_ns in self._profile_sections:
            row_rect = QRect(
                int(x), legend_y - self.content_height, int(width), row_height
            )
            if not self.rect().intersects(painter.worldTransform().mapRect(row_rect)):
                legend_y += row_height
                continue
            value_text = f'{percentage:5.1f}%  {mean_duration_ns / 1_000_000:.3f} ms'
            value_width = self.content_metri.horizontalAdvance(value_text)
            name_width = max(1.0, width - value_width - 25)
            name_text = self.content_metri.elidedText(
                name, Qt.TextElideMode.ElideRight, name_width
            )
            self._drawText(painter, int(x + 15), legend_y, name_text)
            self._drawText(
                painter,
                int(x + width - value_width),
                legend_y,
                value_text,
            )
            color_rect = QRect(int(x), legend_y - self.content_height + 3, 10, 10)
            if self.rect().contains(painter.worldTransform().mapRect(color_rect)):
                painter.fillRect(color_rect, self._itemColor(name))
            legend_y += row_height

    def _paintMemory(self, painter: QPainter, x: float, width: float) -> None:
        self._memory_rows.clear()
        if not self.mem_values:
            painter.setFont(self.title_ft)
            self._drawText(painter, int(x), 20, 'Memory')
            painter.setFont(self.content_ft)
            self._drawText(
                painter, int(x), 20 + self.title_height + 14, 'Collecting samples...'
            )
            return
        total = sum(self.mem_values.values())
        sections = self._memory_sections
        row_height = self.content_height + 4
        row_y = self._paintPie(
            painter,
            x,
            width,
            [(name, value / total * 100 if total else 0.0) for name, value in sections],
        )
        self._drawText(painter, int(x), row_y, f'Memory: {total / 1024**2:.2f} MB RSS')
        row_y += row_height
        text_color = QColor(255, 255, 255) if theme.isDark() else QColor(0, 0, 0)
        for name, value in sections:
            if not value:
                continue
            row_rect = painter.worldTransform().mapRect(
                QRect(
                    int(x),
                    row_y - self.content_height,
                    int(width),
                    row_height * 2 + 4,
                )
            )
            if not self.rect().intersects(row_rect):
                row_y += row_height * 2 + 4
                continue
            self._memory_rows.append((row_rect, name))
            delta = self.mem_deltas.get(name, 0)
            if delta > 512 * 1024:
                delta_color = QColor(210, 105, 105)
            elif delta < -512 * 1024:
                delta_color = QColor(110, 190, 130)
            else:
                delta_color = QColor(150, 150, 150)
            value_text = f'{value / total * 100:5.1f}%  {value / 1024**2:.2f} MB'
            value_width = self.content_metri.horizontalAdvance(value_text)
            name_width = max(1.0, width - value_width - 25)
            painter.setPen(text_color)
            self._drawText(
                painter,
                int(x + 15),
                row_y,
                self.content_metri.elidedText(
                    name, Qt.TextElideMode.ElideRight, name_width
                ),
            )
            self._drawText(painter, int(x + width - value_width), row_y, value_text)
            color_rect = QRect(int(x), row_y - self.content_height + 3, 10, 10)
            if self.rect().contains(painter.worldTransform().mapRect(color_rect)):
                painter.fillRect(color_rect, self._itemColor(name))
            row_y += row_height
            painter.setPen(delta_color)
            self._drawText(
                painter,
                int(x + 15),
                row_y,
                f'{delta / 1024**2:+.2f} MB since last sample',
            )
            painter.setPen(text_color)
            row_y += row_height + 4

        ratio = self.system_used / self.system_total * 100 if self.system_total else 0.0
        self._drawText(
            painter,
            int(x + 10),
            row_y + 8,
            f'System: {ratio:.1f}% '
            f'({self.system_used / 1024**3:.2f} / {self.system_total / 1024**3:.2f} GB)',
        )

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.translate(0, 50 + self.offset_timer.current_value)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(
                QColor(255, 255, 255, 100) if theme.isLight() else QColor(0, 0, 0, 100)
            )
            painter.drawRect(
                0,
                -int(self.offset_timer.current_value) - 50,
                self.width(),
                self.height(),
            )
            painter.setPen(
                QPen(
                    QColor(255, 255, 255) if theme.isDark() else QColor(0, 0, 0),
                    1,
                )
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setFont(self.content_ft)
            column_width = self.width() / 2
            content_width = max(40.0, column_width - 30)
            self._paintFrame(painter, 10.0, content_width)
            self._paintMemory(painter, column_width + 10.0, content_width)
        finally:
            painter.end()
