from __future__ import annotations

import logging
import threading
from typing import Any

from core.free_threaded_worker import FreeThreadedJsonSender

_logger = logging.getLogger(__name__)
_worker: FreeThreadedJsonSender | None = None
_worker_lock = threading.Lock()
_capabilities: dict[str, bool] | None = None


def _getWorker() -> FreeThreadedJsonSender:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = FreeThreadedJsonSender(
                logger=_logger,
                max_workers=2,
            )
        return _worker


def callCompute(
    operation: str,
    payload: dict[str, object],
    timeout: float = 30.0,
) -> Any:
    worker = _getWorker()
    if _supports(worker, operation):
        result = worker.call(operation, payload, timeout)
        if result is not None:
            return result
    return _fallback(operation, payload)


def _supports(worker: FreeThreadedJsonSender, operation: str) -> bool:
    global _capabilities
    with _worker_lock:
        capabilities = _capabilities
    if capabilities is None:
        result = worker.call('capabilities', {}, 5.0)
        capabilities = (
            {
                str(name): bool(value)
                for name, value in result.items()
                if isinstance(result, dict)
            }
            if isinstance(result, dict)
            else {}
        )
        with _worker_lock:
            _capabilities = capabilities
    return capabilities.get(operation, False)


def shutdownComputeWorker() -> None:
    global _capabilities, _worker
    with _worker_lock:
        worker = _worker
        _worker = None
        _capabilities = None
    if worker is not None:
        worker.shutdown()


def _fallback(operation: str, payload: dict[str, object]) -> Any:
    if operation == 'render_markdown_blocks':
        from core.markdown_renderer import renderMarkdownBlocks

        return renderMarkdownBlocks(
            str(payload.get('markdown', '')),
            str(payload.get('math_color', '#ffffff')),
        )
    if operation == 'parse_lyrics':
        from core.lyrics import parseLyricsPayload

        return parseLyricsPayload(
            str(payload.get('lyric', '')),
            str(payload.get('yrc_lyric', '')),
            str(payload.get('translated_lyric', '')),
        )
    if operation == 'build_equalizer_kernel':
        from core.audio_processing import buildEqualizerKernel

        raw_bands = payload.get('bands', [])
        bands = raw_bands if isinstance(raw_bands, list) else []
        raw_sample_rate = payload.get('sample_rate', 0)
        sample_rate = (
            int(raw_sample_rate)
            if isinstance(raw_sample_rate, (int, float, str))
            else 0
        )
        kernel = buildEqualizerKernel(
            [
                (float(band[0]), float(band[1]))
                for band in bands
                if isinstance(band, (list, tuple)) and len(band) >= 2
            ],
            sample_rate,
        )
        if kernel is None:
            return {'kernel': None}
        return {
            'kernel': kernel.tobytes(),
            'dtype': str(kernel.dtype),
            'shape': tuple(kernel.shape),
        }
    raise ValueError(f'unsupported compute operation: {operation}')
