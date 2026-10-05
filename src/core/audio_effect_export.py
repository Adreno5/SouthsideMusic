from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import time
from typing import Literal

import numpy as np
from pydub import AudioSegment

from core.audio_decode import PatchedAudioSegment
from core.audio_processing import AudioProcessingSettings, AudioProcessor
from core.config import cfg

_logger = logging.getLogger(__name__)

_RENDER_BLOCK_FRAMES = 16384

_ENCODE_FORMATS: dict[str, str] = {
    '.mp3': 'mp3',
    '.flac': 'flac',
    '.m4a': 'mp4',
    '.wav': 'wav',
    '.ogg': 'ogg',
    '.opus': 'opus',
}

_ENCODE_CODECS: dict[str, str] = {
    '.m4a': 'aac',
    '.opus': 'libopus',
}

_ENCODE_BITRATES: dict[str, str] = {
    '.mp3': '320k',
    '.m4a': '256k',
    '.ogg': '256k',
    '.opus': '192k',
}

ExportStage = Literal['decode', 'process', 'encode']


@dataclass(slots=True)
class AudioEffectExportProgress:
    progress: float
    stage: ExportStage
    processed_seconds: float
    total_seconds: float
    eta_seconds: float | None = None


def _floatSamples(audio: PatchedAudioSegment) -> np.ndarray:
    samples = np.frombuffer(audio.raw_data, dtype=audio.array_type).astype(np.float32)
    max_value = np.iinfo(audio.array_type).max if audio.sample_width != 4 else 2**31
    np.divide(samples, max_value, out=samples)

    if audio.channels <= 1:
        return samples.reshape(-1, 1)

    frame_count = len(samples) // audio.channels
    multi = samples.reshape(frame_count, audio.channels)
    if audio.channels == 2:
        return multi

    left = multi[:, ::2].mean(axis=1)
    right = multi[:, 1::2].mean(axis=1)
    return np.stack((left, right), axis=1).astype(np.float32, copy=False)


def _renderSettings() -> AudioProcessingSettings:
    return AudioProcessingSettings(
        play_speed=max(0.1, cfg.play_speed),
        play_pitch=cfg.play_pitch,
        speed_animating=False,
        stereo=cfg.stereo,
        stereo_haas_index=cfg.stereo_haas_index,
        enable_reverb=cfg.enable_reverb,
        reverb_intensity=cfg.reverb_intensity,
    )


def renderSongWithEffects(
    audio_path: str,
    output_ext: str,
    progress_callback: Callable[[AudioEffectExportProgress], None] | None = None,
) -> bytes:
    output_ext = output_ext.lower()
    output_format = _ENCODE_FORMATS.get(output_ext)
    if output_format is None:
        raise ValueError(f'Unsupported audio format: {output_ext}')

    _logger.info('render song with effects: %s -> %s', audio_path, output_ext)

    if progress_callback:
        progress_callback(AudioEffectExportProgress(0.0, 'decode', 0.0, 0.0))

    audio = PatchedAudioSegment.from_file(audio_path)
    samples = _floatSamples(audio)
    sample_rate = audio.frame_rate
    total_frames = len(samples)
    if total_frames == 0:
        raise ValueError('Empty audio file')
    total_seconds = total_frames / sample_rate

    processor = AudioProcessor()
    processor.samples = samples
    processor.sample_rate = sample_rate
    processor.channels = samples.shape[1]
    processor.settings = _renderSettings()

    expected_frames = max(1, round(total_frames / processor.settings.play_speed))
    chunks: list[bytes] = []
    produced_frames = 0
    start_index = 0
    started_at = time.monotonic()

    while start_index < total_frames and produced_frames < expected_frames:
        chunk, source_frames = processor.render(start_index, _RENDER_BLOCK_FRAMES)
        if len(chunk) == 0 or source_frames <= 0:
            break
        if produced_frames + len(chunk) > expected_frames:
            chunk = chunk[: expected_frames - produced_frames]
        if len(chunk) == 0:
            break
        pcm = np.clip(chunk, -1.0, 1.0) * 32767.0
        chunks.append(pcm.astype('<i2').tobytes())
        produced_frames += len(chunk)
        start_index += source_frames

        if progress_callback:
            progress = min(0.99, start_index / total_frames)
            elapsed = time.monotonic() - started_at
            eta = elapsed * (1.0 - progress) / progress if progress > 0.01 else None
            progress_callback(
                AudioEffectExportProgress(
                    progress,
                    'process',
                    start_index / sample_rate,
                    total_seconds,
                    eta,
                )
            )

    if progress_callback:
        progress_callback(
            AudioEffectExportProgress(0.99, 'encode', total_seconds, total_seconds, 0.0)
        )

    segment = AudioSegment(
        data=b''.join(chunks),
        sample_width=2,
        frame_rate=sample_rate,
        channels=2,
    )
    encoded = segment.export(
        format=output_format,
        codec=_ENCODE_CODECS.get(output_ext),
        bitrate=_ENCODE_BITRATES.get(output_ext),
    )
    data = encoded.read()

    if progress_callback:
        progress_callback(
            AudioEffectExportProgress(1.0, 'encode', total_seconds, total_seconds, 0.0)
        )
    return data
