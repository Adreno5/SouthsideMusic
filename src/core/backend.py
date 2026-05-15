from __future__ import annotations

from core.models import MusicServiceBackend

_backend: MusicServiceBackend | None = None


def get_backend() -> MusicServiceBackend:
    assert _backend is not None, 'Backend not initialized'
    return _backend


def init_backend(backend: MusicServiceBackend) -> None:
    global _backend
    _backend = backend
