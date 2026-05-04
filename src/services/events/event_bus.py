from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from views.launch_window import LaunchWindow


Listener = Callable[..., Any]


class EventBus:
    def __init__(self, thread_safe: bool = True, launchwindow: LaunchWindow | None = None) -> None:
        self._listeners: dict[str, list[Listener]] = defaultdict(list)
        self._lock = threading.Lock() if thread_safe else None
        self._lw = launchwindow
        self.enabled = True

        self._logger = logging.getLogger('event_bus')

    def subscribe(self, event: str, listener: Listener) -> None:
        msg = f'subscribing {event} to {listener.__module__}.{listener.__name__}'
        self._logger.info(msg)
        if self._lw:
            self._lw.push(msg)
        if self._lock is not None:
            with self._lock:
                self._listeners[event].append(listener)
        else:
            self._listeners[event].append(listener)

    def unsubscribe(self, event: str, listener: Listener) -> None:
        self._logger.info(f'unsubscribing {event} from {listener.__name__}')
        if self._lock is not None:
            with self._lock:
                listeners = self._listeners.get(event)
        else:
            listeners = self._listeners.get(event)
        if listeners:
            try:
                listeners.remove(listener)
            except ValueError:
                pass

    def emit(self, event: str, *args: Any, **kwargs: Any) -> None:
        if not self.enabled:
            return
        if self._lock is not None:
            with self._lock:
                listeners = list(self._listeners.get(event, []))
        else:
            listeners = list(self._listeners.get(event, []))
        for listener in listeners:
            listener(*args, **kwargs)


event_bus = EventBus()
