from __future__ import annotations

import threading
from dataclasses import dataclass
from time import perf_counter_ns


@dataclass(frozen=True)
class FrameProfile:
    duration_ns: int
    sections: tuple[tuple[str, int], ...]
    frame_count: int
    wall_duration_ns: int = 0
    section_counts: tuple[tuple[str, int], ...] = ()
    complete: bool = True

    @property
    def work_duration_ns(self) -> int:
        return sum(v for name, v in self.sections if name != 'Idle / uninstrumented')


class FrameProfiler:
    def __init__(self) -> None:
        self.enabled = False
        self.debug_enabled = False
        self._performance_enabled = False
        self.snapshot: FrameProfile | None = None
        self._thread_id = 0
        self._window_start = 0
        self._frame_count = 0
        self._last_ns = 0
        self._stack: list[str | None] = []
        self._sections: dict[str, int] = {}
        self._counts: dict[str, int] = {}
        self._excluded_ns = 0
        self._capture: list[FrameProfile] | None = None
        self._capture_first = False

    def setEnabled(self, enabled: bool) -> None:
        self.debug_enabled = enabled
        self._setEnabled(enabled or self._performance_enabled)

    def setPerformanceEnabled(self, enabled: bool) -> None:
        self._performance_enabled = enabled
        self._setEnabled(enabled or self.debug_enabled)

    def _setEnabled(self, enabled: bool) -> None:
        if self.enabled == enabled:
            return
        self.enabled = enabled
        self.snapshot = None
        self._stack.clear()
        self._sections.clear()
        self._counts.clear()
        self._frame_count = 0
        self._excluded_ns = 0
        self._capture = None
        if enabled:
            self._thread_id = threading.get_ident()
            self._window_start = self._last_ns = perf_counter_ns()
        else:
            self._window_start = self._last_ns = 0

    def isRecording(self) -> bool:
        return self.enabled and threading.get_ident() == self._thread_id

    def _charge(self, now: int) -> None:
        if self._stack:
            name = self._stack[-1]
            if name is None:
                self._excluded_ns += now - self._last_ns
            else:
                self._sections[name] = self._sections.get(name, 0) + now - self._last_ns
        self._last_ns = now

    def beginSection(self, name: str) -> None:
        if not self.isRecording():
            return
        self._charge(perf_counter_ns())
        excluded = (bool(self._stack) and self._stack[-1] is None) or name.startswith((
            'views.debug_overlay.',
            'views.performances.',
            'core.debugging.',
        ))
        self._stack.append(None if excluded else name)
        if not excluded:
            self._counts[name] = self._counts.get(name, 0) + 1

    def endSection(self) -> None:
        if not self.isRecording() or not self._stack:
            return
        self._charge(perf_counter_ns())
        self._stack.pop()

    def beginFrame(self, complete: bool = True) -> None:
        if not self.isRecording():
            return
        now = perf_counter_ns()
        self._charge(now)
        complete = complete and not self._capture_first
        self._capture_first = False
        self._frame_count = int(complete)
        wall_duration = now - self._window_start
        duration = max(0, wall_duration - self._excluded_ns)
        sections = self._sections.copy()
        sections['Idle / uninstrumented'] = max(0, duration - sum(sections.values()))
        self.snapshot = FrameProfile(
            duration,
            tuple(sorted(sections.items(), key=lambda item: item[1], reverse=True)),
            self._frame_count,
            wall_duration,
            tuple(self._counts.items()),
            complete,
        )
        if self._capture is not None:
            self._capture.append(self.snapshot)
        self._sections.clear()
        self._counts.clear()
        self._window_start = now
        self._frame_count = 0
        self._excluded_ns = 0

    def startCapture(self) -> None:
        if not self.isRecording():
            return
        now = perf_counter_ns()
        self._charge(now)
        self._sections.clear()
        self._counts.clear()
        self._excluded_ns = 0
        self._window_start = now
        self._frame_count = 0
        self._capture = []
        self._capture_first = True

    def finishCapture(self) -> tuple[FrameProfile, ...]:
        if self._capture is None or not self.isRecording():
            return ()
        self.beginFrame(complete=False)
        frames = tuple(self._capture)
        self._capture = None
        return frames


frame_profiler = FrameProfiler()
