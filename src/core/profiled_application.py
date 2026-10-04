from typing import override

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication

from core.frame_profiler import frame_profiler


class ProfiledApplication(QApplication):
    @override
    def notify(self, receiver: QObject, event: QEvent) -> bool:
        if not frame_profiler.enabled or not frame_profiler.isRecording():
            return super().notify(receiver, event)
        receiver_type = type(receiver)
        owner: QObject | None = receiver
        excluded = False
        while owner is not None:
            if getattr(owner, '_exclude_from_profile', False):
                excluded = True
                break
            owner = owner.parent()
        if (
            not excluded
            and isinstance(receiver, QTimer)
            and (parent := receiver.parent()) is not None
        ):
            receiver_type = type(parent)
        name = (
            f'{receiver_type.__module__}.{receiver_type.__qualname__}'
            f'.{event.type().name}'
        )
        frame_profiler.beginSection('views.debug_overlay.excluded' if excluded else name)
        try:
            return super().notify(receiver, event)
        finally:
            frame_profiler.endSection()
