from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Callable

import shiboken6

from core.frame_profiler import frame_profiler
from services.events.events import Events


if TYPE_CHECKING:
    from views.launch_window import LaunchWindow


Listener = Callable[..., Any]
_Entry = tuple[Listener, Any]

_EMPTY: tuple[_Entry, ...] = ()
_QUIET_EVENTS = frozenset({Events.IMAGE_ASSET_PERSISTED, Events.STORABLE_COUNT_CHANGED})
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
        self._listeners: dict[Events, list[_Entry]] = defaultdict(list)
        self._snapshots: dict[Events, tuple[_Entry, ...]] = {}
        self._lock = threading.Lock() if thread_safe else None
        self._lw = launchwindow
        self.enabled = True

        self._logger = logging.getLogger('event_bus')

    def subscribe(self, event: Events, listener: Listener) -> None:
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

    def unsubscribe(self, event: Events, listener: Listener) -> None:
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

    def _drop(self, event: Events, listener: Listener) -> None:
        entries = self._listeners.get(event)
        if not entries:
            return
        for index, entry in enumerate(entries):
            if entry[0] == listener:
                del entries[index]
                self._snapshots.pop(event, None)
                return

    def _snapshot(self, event: Events) -> tuple[_Entry, ...]:
        if self._lock is not None:
            with self._lock:
                return self._rebuild(event)
        return self._rebuild(event)

    def _rebuild(self, event: Events) -> tuple[_Entry, ...]:
        entries = tuple(self._listeners.get(event, _EMPTY))
        self._snapshots[event] = entries
        return entries

    def emit(self, event: Events, *args: Any, **kwargs: Any) -> None:
        if not self.enabled:
            return
        entries = self._snapshots.get(event)
        if entries is None:
            entries = self._snapshot(event)
        if not entries:
            return
        is_valid = _isValid
        profiler = frame_profiler
        recording = profiler.enabled and profiler.isRecording()
        dead: list[Listener] | None = None
        for listener, owner in entries:
            if owner is not None and not is_valid(owner):
                if dead is None:
                    dead = []
                dead.append(listener)
                continue
            if recording:
                profiler.beginSection(
                    f'{getattr(listener, "__module__", type(listener).__module__)}.'
                    f'{getattr(listener, "__qualname__", type(listener).__qualname__)}'
                )
                try:
                    listener(*args, **kwargs)
                finally:
                    profiler.endSection()
            else:
                listener(*args, **kwargs)
        if dead is not None:
            self._prune(event, dead)

    def _prune(self, event: Events, dead: list[Listener]) -> None:
        if self._lock is not None:
            with self._lock:
                self._pruneEntries(event, dead)
        else:
            self._pruneEntries(event, dead)

    def _pruneEntries(self, event: Events, dead: list[Listener]) -> None:
        entries = self._listeners.get(event)
        if not entries:
            return
        dead_ids = {id(listener) for listener in dead}
        self._listeners[event] = [
            entry for entry in entries if id(entry[0]) not in dead_ids
        ]
        self._snapshots.pop(event, None)


event_bus = EventBus()
