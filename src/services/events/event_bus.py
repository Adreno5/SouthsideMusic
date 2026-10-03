from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Callable

import shiboken6

from core.frame_profiler import frame_profiler


if TYPE_CHECKING:
    from views.launch_window import LaunchWindow


Listener = Callable[..., Any]
_Entry = tuple[Listener, Any]

_EMPTY: tuple[_Entry, ...] = ()
_QUIET_EVENTS = frozenset({'image_asset_persisted', 'storable_count_changed'})
_isValid = shiboken6.isValid


def _resolveOwner(listener: Listener) -> Any:
    owner = getattr(listener, '__self__', None)
    if owner is None:
        return None
    try:
        _isValid(owner)
    except TypeError:
        return None
    return owner


def _listenerName(listener: Listener) -> str:
    try:
        name = getattr(listener, '__name__', None)
    except RuntimeError:
        return '<deleted>'
    return name if isinstance(name, str) else type(listener).__name__


class EventBus:
    def __init__(
        self, thread_safe: bool = True, launchwindow: LaunchWindow | None = None
    ) -> None:
        self._listeners: dict[str, list[_Entry]] = defaultdict(list)
        self._snapshots: dict[str, tuple[_Entry, ...]] = {}
        self._lock = threading.Lock() if thread_safe else None
        self._lw = launchwindow
        self.enabled = True

        self._logger = logging.getLogger('event_bus')

    def subscribe(self, event: str, listener: Listener) -> None:
        if self._logger.isEnabledFor(logging.INFO) and event not in _QUIET_EVENTS:
            self._logger.info(
                'subscribing %s to %s.%s',
                event,
                getattr(listener, '__module__', '?'),
                _listenerName(listener),
            )
        entry = (listener, _resolveOwner(listener))
        if self._lock is not None:
            with self._lock:
                self._listeners[event].append(entry)
                self._snapshots.pop(event, None)
        else:
            self._listeners[event].append(entry)
            self._snapshots.pop(event, None)

    def unsubscribe(self, event: str, listener: Listener) -> None:
        if self._logger.isEnabledFor(logging.INFO) and event not in _QUIET_EVENTS:
            self._logger.info(
                'unsubscribing %s from %s',
                event,
                _listenerName(listener),
            )
        if self._lock is not None:
            with self._lock:
                self._drop(event, listener)
        else:
            self._drop(event, listener)

    def _drop(self, event: str, listener: Listener) -> None:
        entries = self._listeners.get(event)
        if not entries:
            return
        for index, entry in enumerate(entries):
            if entry[0] == listener:
                del entries[index]
                self._snapshots.pop(event, None)
                return

    def _snapshot(self, event: str) -> tuple[_Entry, ...]:
        if self._lock is not None:
            with self._lock:
                return self._rebuild(event)
        return self._rebuild(event)

    def _rebuild(self, event: str) -> tuple[_Entry, ...]:
        entries = tuple(self._listeners.get(event, _EMPTY))
        self._snapshots[event] = entries
        return entries

    def emit(self, event: str, *args: Any, **kwargs: Any) -> None:
        if not self.enabled:
            return
        entries = self._snapshots.get(event)
        if entries is None:
            entries = self._snapshot(event)
        if not entries:
            return
        dead: list[Listener] | None = None
        recording = frame_profiler.enabled and frame_profiler.isRecording()
        for listener, owner in entries:
            if owner is not None and not _isValid(owner):
                if dead is None:
                    dead = []
                dead.append(listener)
                continue
            if recording and frame_profiler.enabled:
                frame_profiler.beginSection(
                    f'{getattr(listener, "__module__", type(listener).__module__)}.'
                    f'{getattr(listener, "__qualname__", type(listener).__qualname__)}'
                )
                try:
                    listener(*args, **kwargs)
                finally:
                    frame_profiler.endSection()
            else:
                listener(*args, **kwargs)
        if dead is not None:
            self._prune(event, dead)

    def _prune(self, event: str, dead: list[Listener]) -> None:
        if self._lock is not None:
            with self._lock:
                self._pruneEntries(event, dead)
        else:
            self._pruneEntries(event, dead)

    def _pruneEntries(self, event: str, dead: list[Listener]) -> None:
        entries = self._listeners.get(event)
        if not entries:
            return
        self._listeners[event] = [
            entry for entry in entries if not any(entry[0] is d for d in dead)
        ]
        self._snapshots.pop(event, None)


event_bus = EventBus()
