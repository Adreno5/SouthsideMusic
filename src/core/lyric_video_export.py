from __future__ import annotations

import base64
from bisect import bisect_right
from collections.abc import Callable
import json
import queue
from dataclasses import dataclass, field
import logging
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Literal

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _SRC_DIR in sys.path:
    sys.path.remove(_SRC_DIR)
sys.path.insert(0, _SRC_DIR)
sys.path.append(os.path.join(_SRC_DIR, 'utils'))
sys.path.append(os.path.join(_SRC_DIR, 'views'))
sys.path.append(os.path.join(_SRC_DIR, 'services'))

try:
    from core.audio_player import PatchedAudioSegment as AudioSegment_
    from core.color import mixColor
    from core.lyrics import LRCLyricParser, LyricInfo, YRCLyricInfo, YRCLyricParser
    from imports import (
        QApplication,
        QBuffer,
        QColor,
        QFont,
        QFontDatabase,
        QFontMetricsF,
        QIODevice,
        QImage,
        QPainter,
        QRect,
    )
except ImportError:
    from audio_player import PatchedAudioSegment as AudioSegment_
    from color import mixColor
    from lyrics import LRCLyricParser, LyricInfo, YRCLyricInfo, YRCLyricParser
    from imports import (
        QApplication,
        QBuffer,
        QColor,
        QFont,
        QFontDatabase,
        QFontMetricsF,
        QIODevice,
        QImage,
        QPainter,
        QRect,
    )


Alignment = Literal['left', 'center', 'right']
ProgressStage = Literal['render', 'merge']

_logger = logging.getLogger(__name__)

_VIDEO_WIDTH = 1920
_BASE_VIDEO_HEIGHT = 1080
_BASE_DISPLAY_LINE_COUNT = 5
_MAX_DISPLAY_LINE_COUNT = 21
_X_SCROLL_RESET_SECONDS = 0.3
_X_SCROLL_FLIP_RATIO = 0.95
_VIDEO_FPS = 30
_DEFAULT_REFRESH_RATE = 60.0
_TRANSLATION_TIME_TOLERANCE = 0.02
_MIN_PARALLEL_DURATION = 20.0
_MAX_SEGMENT_WORKERS = 6
_SEGMENT_CONTAINER_EXT = '.mkv'

_EXPORT_DEBUG_LOCK = threading.Lock()
_EXPORT_DEBUG_PIDS: dict[str, int] = {}
_EXPORT_DEBUG_INFO: dict[str, object] = {}


@dataclass(slots=True)
class LyricVideoExportProgress:
    progress: float
    current_frame: int
    frame_count: int
    fps: float
    preview_image: QImage | None = None
    stage: ProgressStage = 'render'


@dataclass(slots=True)
class LyricVideoExportOptions:
    video_ext: str = '.mp4'
    video_bitrate_kbps: int = 8000
    fps: int = _VIDEO_FPS
    display_line_count: int = 5
    word_by_word: bool = True
    pure_color: bool = False
    with_translation: bool = True
    alignment: Alignment = 'center'
    background_color: QColor = field(default_factory=lambda: QColor(0, 177, 64))
    with_audio: bool = True
    scroll_animation: bool = True
    x_axis_animation: bool = True


@dataclass(slots=True)
class LyricVideoSources:
    lyric: str
    translated_lyric: str
    yrc_lyric: str
    audio_path: str
    duration: float
    font_family: str
    theme_color: QColor | None
    is_dark: bool
    refresh_rate: float = _DEFAULT_REFRESH_RATE
    lyrics_smooth_factor: float = 0.028
    acceleration_smooth_factor: float = 0.068
    background_ratio: float = 0.4


class _ParallelExportUnavailable(RuntimeError):
    pass


def lyricVideoExportDebugProcessPids() -> dict[str, int]:
    with _EXPORT_DEBUG_LOCK:
        return dict(_EXPORT_DEBUG_PIDS)


def lyricVideoExportDebugInfo() -> list[str]:
    with _EXPORT_DEBUG_LOCK:
        if not _EXPORT_DEBUG_INFO:
            return []
        mode = str(_EXPORT_DEBUG_INFO.get('mode', 'idle'))
        frames = str(_EXPORT_DEBUG_INFO.get('frames', '0/0'))
        workers = str(_EXPORT_DEBUG_INFO.get('workers', '0'))
        fps = float(_EXPORT_DEBUG_INFO.get('fps', 0.0))  # type: ignore
        return [
            f'mode: {mode}',
            f'workers: {workers}',
            f'frames: {frames}',
            f'fps: {fps:.1f}',
        ]


def _setExportDebugState(
    pids: dict[str, int] | None = None,
    info: dict[str, object] | None = None,
) -> None:
    with _EXPORT_DEBUG_LOCK:
        if pids is not None:
            _EXPORT_DEBUG_PIDS.clear()
            _EXPORT_DEBUG_PIDS.update(pids)
        if info is not None:
            _EXPORT_DEBUG_INFO.clear()
            _EXPORT_DEBUG_INFO.update(info)


def _clearExportDebugState() -> None:
    with _EXPORT_DEBUG_LOCK:
        _EXPORT_DEBUG_PIDS.clear()
        _EXPORT_DEBUG_INFO.clear()


class _FrameEaseOutValue:
    def __init__(
        self,
        animation_time: float,
        power_number: int,
        current_value: float = 0.0,
    ) -> None:
        self.animation_time = max(0.001, animation_time)
        self.power_number = max(1, power_number)
        self.current_value = current_value
        self.target_value = current_value
        self._start_value = current_value
        self._difference = 0.0
        self._elapsed = 0.0

    def setTarget(self, value: float) -> None:
        if value == self.target_value:
            return
        self._start_value = self.current_value
        self._difference = value - self.current_value
        self.target_value = value
        self._elapsed = 0.0

    def step(self, delta: float) -> None:
        if self._difference == 0:
            return
        self._elapsed += delta
        if self._elapsed >= self.animation_time:
            self.current_value = self.target_value
            self._difference = 0.0
            return
        progress = max(0.0, min(1.0, self._elapsed / self.animation_time))
        eased = 1.0 - pow(1.0 - progress, self.power_number)
        self.current_value = self._start_value + self._difference * eased


def exportLyricVideo(
    sources: LyricVideoSources,
    options: LyricVideoExportOptions,
    output_path: str,
    progress_callback: Callable[[LyricVideoExportProgress], None] | None = None,
) -> str:
    renderer = _LyricVideoRenderer(sources, options)
    if not renderer.hasLyrics():
        raise ValueError('No lyrics to export.')

    output_path = _normalizeOutputPath(output_path, options.video_ext)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    ffmpeg = AudioSegment_.converter or 'ffmpeg'
    frame_count = renderer.frameCount()

    try:
        if _shouldUseParallelExport(frame_count, options.fps):
            return _exportLyricVideoParallel(
                sources,
                options,
                output_path,
                ffmpeg,
                frame_count,
                progress_callback,
            )
    except _ParallelExportUnavailable as e:
        _logger.info('lyric video parallel export unavailable: %s', e)

    return _exportLyricVideoSingle(
        renderer,
        sources,
        options,
        output_path,
        ffmpeg,
        frame_count,
        progress_callback,
    )


def _exportLyricVideoSingle(
    renderer: _LyricVideoRenderer,
    sources: LyricVideoSources,
    options: LyricVideoExportOptions,
    output_path: str,
    ffmpeg: str,
    frame_count: int,
    progress_callback: Callable[[LyricVideoExportProgress], None] | None,
) -> str:
    _setExportDebugState(
        {},
        {
            'mode': 'single',
            'workers': 1,
            'frames': f'0/{frame_count}',
            'fps': 0.0,
        },
    )
    command = _buildFfmpegCommand(ffmpeg, sources, options, output_path)
    _logger.info('export lyric video with ffmpeg: %s', command)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    preview_interval = _previewInterval(frame_count)
    frame_samples: list[tuple[float, float]] = []
    last_frame_time: float | None = None
    try:
        stdin = process.stdin
        if stdin is None:
            raise RuntimeError('Failed to open FFmpeg stdin.')
        if progress_callback is not None:
            progress_callback(LyricVideoExportProgress(0.0, 0, frame_count, 0.0))
        for frame_index in range(frame_count):
            frame_time = time.perf_counter()
            if last_frame_time is not None:
                frame_samples.append((frame_time, frame_time - last_frame_time))
                cutoff = frame_time - 1.0
                while frame_samples and frame_samples[0][0] < cutoff:
                    frame_samples.pop(0)
            last_frame_time = frame_time

            image = renderer.renderFrame(frame_index / options.fps)
            stdin.write(_qimageBytes(image))
            if progress_callback is not None and frame_count > 0:
                current_frame = frame_index + 1
                preview_image = (
                    image.copy()
                    if _shouldSendPreview(
                        current_frame,
                        frame_count,
                        preview_interval,
                    )
                    else None
                )
                progress_callback(
                    LyricVideoExportProgress(
                        current_frame / frame_count,
                        current_frame,
                        frame_count,
                        _fpsFromFrameSamples(frame_samples),
                        preview_image,
                    )
                )
                _setExportDebugState(
                    info={
                        'mode': 'single',
                        'workers': 1,
                        'frames': f'{current_frame}/{frame_count}',
                        'fps': _fpsFromFrameSamples(frame_samples),
                    },
                )
        stdin.close()
        stderr_bytes = process.stderr.read() if process.stderr is not None else b''
        returncode = process.wait()
    except Exception:
        _terminateProcess(process)
        _clearExportDebugState()
        raise

    if returncode != 0:
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            pass
        stderr = stderr_bytes.decode('utf-8', 'ignore').strip()
        _clearExportDebugState()
        raise RuntimeError(
            'FFmpeg failed with code {0}: {1}'.format(
                returncode,
                stderr[-2000:] if stderr else 'no error output',
            )
        )

    _clearExportDebugState()
    return output_path


def _shouldUseParallelExport(frame_count: int, fps: int) -> bool:
    return _segmentWorkerCount(frame_count, fps) > 1


def _segmentWorkerCount(frame_count: int, fps: int) -> int:
    min_parallel_frames = max(1, int(round(fps * _MIN_PARALLEL_DURATION)))
    if frame_count < min_parallel_frames:
        return 1
    cpu_count = os.cpu_count() or 2
    by_frame_count = max(1, frame_count // min_parallel_frames)
    return max(1, min(cpu_count, _MAX_SEGMENT_WORKERS, by_frame_count))


def _exportLyricVideoParallel(
    sources: LyricVideoSources,
    options: LyricVideoExportOptions,
    output_path: str,
    ffmpeg: str,
    frame_count: int,
    progress_callback: Callable[[LyricVideoExportProgress], None] | None,
) -> str:
    interpreter = _findQtSidecarInterpreter()
    if interpreter is None:
        raise _ParallelExportUnavailable('no Python with PySide6 is available')

    worker_count = _segmentWorkerCount(frame_count, options.fps)
    if worker_count <= 1:
        raise _ParallelExportUnavailable('not enough frames for parallel export')

    temp_dir = tempfile.mkdtemp(prefix='southside-lyric-video-')
    processes: list[subprocess.Popen[str]] = []
    reader_threads: list[threading.Thread] = []
    message_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    rendered_by_worker: dict[int, int] = {}
    fps_by_worker: dict[int, float] = {}
    segment_paths: list[str] = []
    error_messages: list[str] = []
    try:
        for worker_index, (start_frame, end_frame) in enumerate(
            _frameSegments(frame_count, worker_count)
        ):
            segment_path = os.path.join(
                temp_dir,
                f'segment_{worker_index:03d}{_SEGMENT_CONTAINER_EXT}',
            )
            payload_path = os.path.join(temp_dir, f'segment_{worker_index:03d}.json')
            segment_paths.append(segment_path)
            _writeJsonPayload(
                payload_path,
                {
                    'worker_index': worker_index,
                    'start_frame': start_frame,
                    'end_frame': end_frame,
                    'frame_count': frame_count,
                    'segment_path': segment_path,
                    'ffmpeg': ffmpeg,
                    'sources': _sourcesPayload(sources),
                    'options': _optionsPayload(options),
                },
            )
            process = _startSegmentWorker(interpreter, payload_path)
            processes.append(process)
            rendered_by_worker[worker_index] = 0
            fps_by_worker[worker_index] = 0.0
            reader = threading.Thread(
                target=_readWorkerMessages,
                args=(worker_index, process, message_queue),
                daemon=True,
                name=f'southside-lyric-video-reader-{worker_index}',
            )
            reader.start()
            reader_threads.append(reader)

        _setExportDebugState(
            {
                f'lyric-video-{index}': process.pid
                for index, process in enumerate(processes)
            },
            {
                'mode': 'parallel',
                'workers': len(processes),
                'frames': f'0/{frame_count}',
                'fps': 0.0,
            },
        )

        if progress_callback is not None:
            progress_callback(LyricVideoExportProgress(0.0, 0, frame_count, 0.0))

        finished_workers: set[int] = set()
        while len(finished_workers) < len(processes):
            try:
                message = message_queue.get(timeout=0.1)
            except queue.Empty:
                for index, process in enumerate(processes):
                    if index in finished_workers:
                        continue
                    returncode = process.poll()
                    if returncode is None:
                        continue
                    finished_workers.add(index)
                    if returncode != 0:
                        error_messages.append(
                            _workerFailureMessage(index, returncode, process)
                        )
                continue

            msg_type = str(message.get('type', ''))
            worker_index = int(message.get('worker_index', -1))
            if msg_type == 'progress':
                rendered_by_worker[worker_index] = int(message.get('rendered', 0))
                fps_by_worker[worker_index] = float(message.get('fps', 0.0))
                current_frame = min(frame_count, sum(rendered_by_worker.values()))
                fps = sum(fps_by_worker.values())
                preview_image = _imageFromBase64(str(message.get('preview', '')))
                _setExportDebugState(
                    info={
                        'mode': 'parallel',
                        'workers': len(processes),
                        'frames': f'{current_frame}/{frame_count}',
                        'fps': fps,
                    },
                )
                if progress_callback is not None:
                    progress_callback(
                        LyricVideoExportProgress(
                            current_frame / frame_count,
                            current_frame,
                            frame_count,
                            fps,
                            preview_image,
                        )
                    )
            elif msg_type == 'done':
                finished_workers.add(worker_index)
            elif msg_type == 'error':
                finished_workers.add(worker_index)
                error_messages.append(str(message.get('error', 'unknown error')))

        for index, process in enumerate(processes):
            returncode = process.wait()
            if returncode != 0:
                error_messages.append(_workerFailureMessage(index, returncode, process))
        for reader in reader_threads:
            reader.join(timeout=0.5)

        if error_messages:
            raise RuntimeError(error_messages[0])

        concat_file = os.path.join(temp_dir, 'segments.txt')
        _writeConcatFile(concat_file, segment_paths)
        _mergeSegments(
            ffmpeg,
            concat_file,
            sources,
            options,
            output_path,
            frame_count,
            progress_callback,
        )
        return output_path
    finally:
        for process in processes:
            if process.poll() is None:
                _terminateTextProcess(process)
        shutil.rmtree(temp_dir, ignore_errors=True)
        _clearExportDebugState()


def _findQtSidecarInterpreter() -> str | None:
    root = Path(__file__).resolve().parents[2]
    candidates: list[Path] = []
    for env_name in (
        'SOUTHSIDE_QT_SIDECAR_PYTHON',
        'SOUTHSIDE_PYSIDE_PYTHON',
    ):
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value))
    candidates.extend(
        [
            root / '.venv' / 'Scripts' / 'python.exe',
            root / 'venv' / 'Scripts' / 'python.exe',
            Path(sys.executable),
        ]
    )
    for candidate in candidates:
        if candidate.is_file() and _pythonCanImportPySide(candidate):
            return str(candidate)
    return None


def _pythonCanImportPySide(interpreter: Path) -> bool:
    creationflags = 0
    if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
        creationflags = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            [str(interpreter), '-c', 'import PySide6'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            creationflags=creationflags,
        )
    except Exception:
        return False
    return result.returncode == 0


def _frameSegments(frame_count: int, worker_count: int) -> list[tuple[int, int]]:
    segments: list[tuple[int, int]] = []
    for index in range(worker_count):
        start_frame = frame_count * index // worker_count
        end_frame = frame_count * (index + 1) // worker_count
        if start_frame < end_frame:
            segments.append((start_frame, end_frame))
    return segments


def _writeJsonPayload(path: str, payload: dict[str, Any]) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))


def _startSegmentWorker(
    interpreter: str,
    payload_path: str,
) -> subprocess.Popen[str]:
    root = Path(__file__).resolve().parents[2]
    src_dir = root / 'src'
    env = os.environ.copy()
    env['QT_QPA_PLATFORM'] = 'offscreen'
    env['PYTHONIOENCODING'] = 'utf-8'
    old_pythonpath = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = (
        str(src_dir)
        if not old_pythonpath
        else str(src_dir) + os.pathsep + old_pythonpath
    )
    creationflags = 0
    if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
        creationflags = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(
        [
            interpreter,
            str(Path(__file__).resolve()),
            '--lyric-video-segment-worker',
            payload_path,
        ],
        cwd=str(root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
        creationflags=creationflags,
    )


def _readWorkerMessages(
    worker_index: int,
    process: subprocess.Popen[str],
    message_queue: queue.Queue[dict[str, Any]],
) -> None:
    stdout = process.stdout
    if stdout is None:
        return
    for line in stdout:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _logger.debug('ignore lyric video worker stdout: %s', line)
            continue
        if isinstance(message, dict):
            message['worker_index'] = worker_index
            message_queue.put(message)


def _workerFailureMessage(
    worker_index: int,
    returncode: int,
    process: subprocess.Popen[str],
) -> str:
    stderr = ''
    if process.stderr is not None:
        try:
            stderr = process.stderr.read()
        except Exception:
            stderr = ''
    stderr = stderr.strip()
    if stderr:
        stderr = stderr[-2000:]
    else:
        stderr = 'no error output'
    return f'Lyric video worker {worker_index} failed with code {returncode}: {stderr}'


def _writeConcatFile(path: str, segment_paths: list[str]) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        for segment_path in segment_paths:
            escaped = os.path.abspath(segment_path).replace('\\', '/')
            escaped = escaped.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")


def _mergeSegments(
    ffmpeg: str,
    concat_file: str,
    sources: LyricVideoSources,
    options: LyricVideoExportOptions,
    output_path: str,
    frame_count: int,
    progress_callback: Callable[[LyricVideoExportProgress], None] | None,
) -> None:
    command = _buildFfmpegConcatCommand(
        ffmpeg, concat_file, sources, options, output_path
    )
    _logger.info('merge lyric video segments with ffmpeg: %s', command)
    _runFfmpegProgressCommand(
        command,
        sources.duration,
        frame_count,
        progress_callback,
        options.fps,
    )


def _runFfmpegProgressCommand(
    command: list[str],
    duration: float,
    frame_count: int,
    progress_callback: Callable[[LyricVideoExportProgress], None] | None,
    fps: int = _VIDEO_FPS,
) -> None:
    if progress_callback is not None:
        progress_callback(
            LyricVideoExportProgress(
                0.0,
                0,
                frame_count,
                0.0,
                stage='merge',
            )
        )
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    frame_samples: list[tuple[float, int]] = []
    last_fps = 0.0
    try:
        stdout = process.stdout
        if stdout is not None:
            for line in stdout:
                if not line.startswith('out_time_ms='):
                    continue
                if progress_callback is None:
                    continue
                try:
                    out_time = int(line.split('=', 1)[1].strip()) / 1_000_000
                except ValueError:
                    continue
                merge_progress = max(0.0, min(1.0, out_time / max(duration, 0.001)))
                current_frame = min(
                    frame_count,
                    max(0, int(round(out_time * fps))),
                )
                now = time.perf_counter()
                frame_samples.append((now, current_frame))
                cutoff = now - 1.0
                while frame_samples and frame_samples[0][0] < cutoff:
                    frame_samples.pop(0)
                last_fps = _fpsFromProgressSamples(frame_samples)
                progress_callback(
                    LyricVideoExportProgress(
                        merge_progress,
                        current_frame,
                        frame_count,
                        last_fps,
                        stage='merge',
                    )
                )
        stderr = process.stderr.read() if process.stderr is not None else ''
        returncode = process.wait()
    except Exception:
        _terminateTextProcess(process)
        raise

    if returncode != 0:
        if os.path.exists(command[-1]):
            try:
                os.remove(command[-1])
            except OSError:
                pass
        stderr = stderr.strip()
        raise RuntimeError(
            'FFmpeg failed with code {0}: {1}'.format(
                returncode,
                stderr[-2000:] if stderr else 'no error output',
            )
        )
    if progress_callback is not None:
        progress_callback(
            LyricVideoExportProgress(
                1.0,
                frame_count,
                frame_count,
                last_fps,
                stage='merge',
            )
        )


def _terminateTextProcess(process: subprocess.Popen[str]) -> None:
    try:
        if process.stdin is not None:
            process.stdin.close()
    except OSError:
        pass
    if process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=0.5)
        except Exception:
            try:
                process.kill()
            except OSError:
                pass


class _LyricVideoRenderer:
    def __init__(
        self,
        sources: LyricVideoSources,
        options: LyricVideoExportOptions,
    ) -> None:
        self.sources = sources
        self.options = options
        self.refresh_rate = self._normalizedRefreshRate(sources.refresh_rate)
        self.delta = 1 / self.refresh_rate

        self.lrc = LRCLyricParser()
        self.lrc.cur = sources.lyric
        self.lrc.parse()

        self.trans = LRCLyricParser()
        self.trans.cur = sources.translated_lyric
        self.trans.parse()

        self.yrc = YRCLyricParser()
        self.yrc.cur = sources.yrc_lyric
        self.yrc.parse()

        self.use_yrc = options.word_by_word and self.yrc.hasYrcTiming()
        self.lines: list[LyricInfo | YRCLyricInfo] = (
            list(self.yrc.parsed) if self.use_yrc else list(self.lrc.parsed)
        )
        self.times = [line.time for line in self.lines]
        self.display_line_count = _normalizedDisplayLineCount(
            options.display_line_count
        )
        self.video_height = _videoHeightForLineCount(self.display_line_count)

        self.primary_font = QFont(sources.font_family, 54)
        self.translation_font = QFont(sources.font_family, 31)
        self.primary_metrics = QFontMetricsF(self.primary_font)
        self.translation_metrics = QFontMetricsF(self.translation_font)

        self.translations = [
            self._translationTextForLine(line, self.use_yrc)
            if options.with_translation
            else ''
            for line in self.lines
        ]
        self.translation_progress = 1.0 if options.with_translation else 0.0
        self.draw_offset = 0.0
        self.target_draw_offset = 0.0
        self.acc = 0.0
        self.target_acc = 0.0
        self._line_alphas: dict[int, _FrameEaseOutValue] = {}
        self._x_scroll_ease = _FrameEaseOutValue(_X_SCROLL_RESET_SECONDS, 2)
        self._last_render_position: float | None = None
        self._layout_y_offsets: list[float] = []
        self._layout_top_offset = 0.0
        self._layout_current_index = -1
        self._layout_visible_indexes: list[int] = []

    def hasLyrics(self) -> bool:
        return bool(self.lines)

    def frameCount(self) -> int:
        duration = max(self.sources.duration, self._lyricsDuration(), 1.0)
        return max(1, int(math.ceil(duration * self.options.fps)))

    def renderFrame(self, position: float) -> QImage:
        if (
            self._last_render_position is not None
            and position < self._last_render_position
        ):
            self._resetAnimationState()
        image = QImage(_VIDEO_WIDTH, self.video_height, QImage.Format.Format_RGB888)
        image.fill(self.options.background_color)

        painter = QPainter(image)
        try:
            painter.setRenderHints(
                QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
            )
            self._drawLyrics(painter, position)
        finally:
            painter.end()
        return image

    def _drawLyrics(self, painter: QPainter, position: float) -> None:
        self._advanceLayout(position)
        current_index = self._layout_current_index
        if current_index < 0:
            return
        for index in self._layout_visible_indexes:
            alpha = self._lineAlpha(index)
            if alpha <= 1:
                continue
            self._drawLine(
                painter,
                self.lines[index],
                index,
                current_index,
                position,
                self._layout_top_offset + self._layout_y_offsets[index],
                alpha,
            )

    def _drawLine(
        self,
        painter: QPainter,
        line: LyricInfo | YRCLyricInfo,
        index: int,
        current_index: int,
        position: float,
        baseline_y: float,
        alpha: int,
    ) -> None:
        text = line.content.strip()
        if not text:
            return

        is_current = index == current_index
        color = self._primaryColor(line, is_current, alpha)

        painter.setFont(self.primary_font)
        text_width = self.primary_metrics.horizontalAdvance(text)
        text_x = self._textX(text_width, is_current, line, position)
        clip_rect = QRect(120, 0, _VIDEO_WIDTH - 240, self.video_height)

        if (
            self.options.x_axis_animation
            and is_current
            and text_width > clip_rect.width()
        ):
            painter.save()
            painter.setClipRect(clip_rect)
            self._drawPrimaryText(
                painter,
                line,
                text,
                text_x,
                baseline_y,
                color,
                position,
            )
            painter.restore()
        else:
            self._drawPrimaryText(
                painter,
                line,
                text,
                text_x,
                baseline_y,
                color,
                position,
            )

        translation = self.translations[index]
        if translation:
            painter.setFont(self.translation_font)
            t_color = self._translationColor(alpha)
            painter.setPen(t_color)
            translation_width = self.translation_metrics.horizontalAdvance(translation)
            t_x = self._alignedX(translation_width)
            painter.drawText(
                int(t_x),
                int(
                    baseline_y
                    + self.primary_metrics.descent()
                    + 2
                    + self.translation_metrics.ascent()
                ),
                translation,
            )

    def _drawPrimaryText(
        self,
        painter: QPainter,
        line: LyricInfo | YRCLyricInfo,
        text: str,
        x: float,
        baseline_y: float,
        color: QColor,
        position: float,
    ) -> None:
        if (
            self.options.word_by_word
            and self.use_yrc
            and isinstance(line, YRCLyricInfo)
            and not line.isMetadata
        ):
            base_color = QColor(color)
            base_color.setAlpha(120)
            painter.setPen(base_color)
            painter.drawText(int(x), int(baseline_y), text)

            clip_width = self._yrcClipWidth(line, position)
            if clip_width > 0:
                painter.save()
                painter.setClipRect(
                    int(x),
                    int(baseline_y - self.primary_metrics.ascent()),
                    int(math.ceil(clip_width)),
                    int(math.ceil(self.primary_metrics.height())),
                )
                painter.setPen(color)
                painter.drawText(int(x), int(baseline_y), text)
                painter.restore()
            return

        painter.setPen(color)
        painter.drawText(int(x), int(baseline_y), text)

    def _currentIndex(self, position: float) -> int:
        if not self.times:
            return 0
        index = bisect_right(self.times, position) - 1
        return max(0, min(index, len(self.times) - 1))

    def _currentBaseline(self, current_index: int) -> float:
        has_translation = (
            bool(self.translations[current_index])
            if 0 <= current_index < len(self.translations)
            else False
        )
        return self._currentLineBaseline(has_translation)

    def _lineStep(self, has_translation: bool) -> float:
        if has_translation:
            return self.primary_metrics.height() * (
                1.85 - (0.1 * self.translation_progress)
            ) + (self.translation_metrics.height() * self.translation_progress)
        return self.primary_metrics.height() * 1.85

    def _currentLineBaseline(self, has_translation: bool = False) -> float:
        block_height = self.primary_metrics.height()
        if has_translation:
            block_height += (2 + self.translation_metrics.height()) * (
                self.translation_progress
            )
        return (self.video_height - block_height) * 0.5 + self.primary_metrics.ascent()

    def _lineOffsets(self) -> tuple[list[float], float]:
        y_offsets: list[float] = []
        y = 0.0
        for index in range(len(self.lines)):
            y_offsets.append(y)
            y += self._lineStep(bool(self.translations[index]))
        return y_offsets, y

    def _advanceLayout(self, position: float) -> None:
        if self._last_render_position is None:
            self._last_render_position = 0.0
            self._layoutStep(0.0, 0.0)
            if position <= 0:
                return
        duration = max(0.0, position - self._last_render_position)
        self._last_render_position = position
        if duration <= 0:
            self._layoutStep(position, 0.0)
            self._stepXScroll(position, 0.0)
            return
        elapsed = 0.0
        tick_delta = self.delta
        while elapsed < duration:
            step_delta = min(tick_delta, duration - elapsed)
            elapsed += step_delta
            self._layoutStep(position - duration + elapsed, step_delta)
        self._stepXScroll(position, duration)

    def _layoutStep(self, position: float, elapsed: float) -> None:
        current_index = self._currentIndex(position)
        self._layout_current_index = current_index
        if current_index < 0:
            return
        y_offsets, total_height = self._lineOffsets()
        if self.options.scroll_animation:
            self.target_draw_offset = -y_offsets[current_index]
            if self.target_draw_offset > 0:
                self.target_draw_offset = 0
            if self.target_draw_offset < -total_height:
                self.target_draw_offset = -total_height
            self._updateDrawOffset(elapsed * self.refresh_rate)
        else:
            self.draw_offset = -y_offsets[current_index]
            self.target_draw_offset = self.draw_offset
            self.acc = 0.0
            self.target_acc = 0.0

        current_baseline = self._currentBaseline(current_index)
        top_offset = self.draw_offset + current_baseline
        visible_indexes = self._visibleIndexes(y_offsets, top_offset)
        self._layout_y_offsets = y_offsets
        self._layout_top_offset = top_offset
        self._layout_visible_indexes = self._displayIndexes(
            visible_indexes,
            current_index,
        )
        self._stepLineAlphas(visible_indexes, current_index, elapsed)
        self._cleanupLineAlphas(visible_indexes)

    def _stepXScroll(self, position: float, elapsed: float) -> None:
        current_index = self._layout_current_index
        if current_index < 0 or current_index >= len(self.lines):
            return
        line = self.lines[current_index]
        if not (
            self.options.x_axis_animation
            and isinstance(line, YRCLyricInfo)
            and not line.isMetadata
        ):
            return
        text_width = self.primary_metrics.horizontalAdvance(line.content.strip())
        clip_width = _VIDEO_WIDTH - 240
        overflow = max(0.0, text_width - clip_width)
        if overflow <= 0:
            self._x_scroll_ease.setTarget(0.0)
            self._x_scroll_ease.step(elapsed)
            return
        clip_w = self._yrcClipWidth(line, position)
        page_width = clip_width * _X_SCROLL_FLIP_RATIO
        page = int(clip_w // page_width)
        target = max(-overflow, min(0.0, -page * page_width))
        self._x_scroll_ease.setTarget(target)
        self._x_scroll_ease.step(elapsed)

    def _updateDrawOffset(self, multiple_factor: float = 1.0) -> None:
        self.target_acc = (
            (self.target_draw_offset - self.draw_offset)
            * self.delta
            * (self.sources.lyrics_smooth_factor * self.refresh_rate)
            * multiple_factor
        )
        self.acc += (
            (self.target_acc - self.acc)
            * self.delta
            * (self.sources.acceleration_smooth_factor * self.refresh_rate)
            * multiple_factor
        )

        if self.draw_offset != self.target_draw_offset:
            self.draw_offset += self.acc
        if not all(
            math.isfinite(value)
            for value in (self.draw_offset, self.target_draw_offset, self.acc)
        ):
            self.draw_offset = 0.0
            self.target_draw_offset = 0.0
            self.acc = 0.0

    def _visibleIndexes(
        self,
        y_offsets: list[float],
        top_offset: float,
    ) -> list[int]:
        shown: list[int] = []
        for index in range(len(self.lines)):
            y_pos = top_offset + y_offsets[index]
            line_bottom = y_pos + self.primary_metrics.height()
            if (
                line_bottom >= 0
                and y_pos - self.primary_metrics.height() <= self.video_height
            ):
                shown.append(index)
        return shown

    def _displayIndexes(
        self,
        visible_indexes: list[int],
        current_index: int,
    ) -> list[int]:
        if self.display_line_count <= 0 or current_index not in visible_indexes:
            return visible_indexes
        half_count = self.display_line_count // 2
        start = current_index - half_count
        end = current_index + half_count
        return [index for index in visible_indexes if start <= index <= end]

    def _stepLineAlphas(
        self,
        visible_indexes: list[int],
        current_index: int,
        elapsed: float,
    ) -> None:
        visible = set(visible_indexes)
        for index, timer in self._line_alphas.items():
            if index in visible:
                timer.step(elapsed)
        for index in visible_indexes:
            timer = self._line_alphas.get(index)
            if timer is None:
                timer = _FrameEaseOutValue(0.2, 2)
                self._line_alphas[index] = timer
            timer.setTarget(255 if index == current_index else 120)

    def _lineAlpha(self, index: int) -> int:
        timer = self._line_alphas.get(index)
        return int(timer.current_value) if timer is not None else 0

    def _cleanupLineAlphas(self, visible_indexes: list[int]) -> None:
        visible = set(visible_indexes)
        for index in list(self._line_alphas):
            if index not in visible:
                self._line_alphas.pop(index)

    def _resetAnimationState(self) -> None:
        self.draw_offset = 0.0
        self.target_draw_offset = 0.0
        self.acc = 0.0
        self.target_acc = 0.0
        self._line_alphas.clear()
        self._last_render_position = None
        self._layout_y_offsets = []
        self._layout_top_offset = 0.0
        self._layout_current_index = -1
        self._layout_visible_indexes = []

    def _textX(
        self,
        text_width: float,
        is_current: bool,
        line: LyricInfo | YRCLyricInfo,
        position: float,
    ) -> float:
        clip_width = _VIDEO_WIDTH - 240
        if self.options.x_axis_animation and is_current and text_width > clip_width:
            if isinstance(line, YRCLyricInfo) and not line.isMetadata:
                return 120 + self._x_scroll_ease.current_value
            progress = self._lineProgress(line, position)
            return 120 - (text_width - clip_width) * progress
        return self._alignedX(text_width)

    def _alignedX(self, text_width: float) -> float:
        if self.options.alignment == 'left':
            return 120
        if self.options.alignment == 'right':
            return _VIDEO_WIDTH - 120 - text_width
        return (_VIDEO_WIDTH - text_width) * 0.5

    def _primaryColor(
        self,
        line: LyricInfo | YRCLyricInfo,
        is_current: bool,
        alpha: int,
    ) -> QColor:
        if is_current:
            if line.isMetadata:
                foreground = QColor(255, 255, 255)
            else:
                foreground = (
                    QColor(255, 255, 255) if self.sources.is_dark else QColor(0, 0, 0)
                )
        else:
            foreground = (
                QColor(240, 240, 240, 120)
                if self.sources.is_dark
                else QColor(55, 55, 55, 120)
            )
        foreground.setAlpha(max(0, min(255, int(alpha))))

        if (
            self.options.pure_color
            or self.sources.theme_color is None
            or not self.sources.theme_color.isValid()
        ):
            return foreground

        return mixColor(
            self.sources.theme_color,
            foreground,
            self.sources.background_ratio / 2,
        )

    def _translationColor(self, alpha: int) -> QColor:
        color = (
            QColor(255, 255, 255, int(alpha * self.translation_progress * 0.6))
            if self.sources.is_dark
            else QColor(0, 0, 0, int(alpha * self.translation_progress * 0.6))
        )
        return color

    def _yrcClipWidth(self, line: YRCLyricInfo, position: float) -> float:
        content = line.content.strip()
        total_width = self.primary_metrics.horizontalAdvance(content)
        if total_width <= 0:
            return 0.0
        if not line.chars:
            return total_width * self._lineProgress(line, position)

        filled_width = 0.0
        for char in line.chars:
            text_width = self.primary_metrics.horizontalAdvance(char.char)
            if char.duration <= 0:
                progress = 1.0 if position >= char.start else 0.0
            else:
                progress = (position - char.start) / char.duration
            filled_width += text_width * max(0.0, min(1.0, progress))
        return max(0.0, min(total_width, filled_width))

    def _lineProgress(
        self,
        line: LyricInfo | YRCLyricInfo,
        position: float,
    ) -> float:
        index = self._currentIndex(position)
        next_time = self.sources.duration
        if index + 1 < len(self.lines):
            next_time = self.lines[index + 1].time
        if isinstance(line, YRCLyricInfo) and line.duration > 0:
            next_time = max(next_time, line.time + line.duration)
        duration = max(0.001, next_time - line.time)
        return max(0.0, min(1.0, (position - line.time) / duration))

    def _lyricsDuration(self) -> float:
        if not self.lines:
            return 0.0
        last = self.lines[-1]
        duration = getattr(last, 'duration', 0.0)
        return last.time + max(float(duration), 3.0)

    def _timeKey(self, value: float) -> int:
        return round(value * 1000)

    def _timesClose(self, left: float, right: float) -> bool:
        return abs(left - right) <= _TRANSLATION_TIME_TOLERANCE

    def _translationTextForLine(
        self,
        line: LyricInfo | YRCLyricInfo,
        use_yrc: bool,
    ) -> str:
        if not line.content.strip() or line.isMetadata or not self.trans.parsed:
            return ''
        trans_time = self._translationTimeForLine(line, use_yrc)
        for trans_line in self.trans.parsed:
            if self._timesClose(trans_line.time, trans_time):
                return trans_line.content.strip()
        if use_yrc and self.lrc.parsed:
            lrc_line = self.lrc.getCurrentLyric(line.time)
            for trans_line in self.trans.parsed:
                if self._timesClose(trans_line.time, lrc_line.time):
                    return trans_line.content.strip()
        return ''

    def _translationTimeForLine(
        self,
        line: LyricInfo | YRCLyricInfo,
        use_yrc: bool,
    ) -> float:
        if not hasattr(line, 'chars'):
            return line.time
        for trans_line in self.trans.parsed:
            if self._timesClose(trans_line.time, line.time):
                return line.time
        if use_yrc and self.lrc.parsed:
            lrc_line = self.lrc.getCurrentLyric(line.time)
            if lrc_line.content.strip():
                return lrc_line.time
        return line.time

    def _normalizedRefreshRate(self, refresh_rate: float) -> float:
        try:
            value = float(refresh_rate)
        except (TypeError, ValueError):
            return _DEFAULT_REFRESH_RATE
        if not math.isfinite(value):
            return _DEFAULT_REFRESH_RATE
        return max(_DEFAULT_REFRESH_RATE, value)


def _sourcesPayload(sources: LyricVideoSources) -> dict[str, Any]:
    return {
        'lyric': sources.lyric,
        'translated_lyric': sources.translated_lyric,
        'yrc_lyric': sources.yrc_lyric,
        'audio_path': sources.audio_path,
        'duration': sources.duration,
        'font_family': sources.font_family,
        'theme_color': _colorPayload(sources.theme_color),
        'is_dark': sources.is_dark,
        'refresh_rate': sources.refresh_rate,
        'lyrics_smooth_factor': sources.lyrics_smooth_factor,
        'acceleration_smooth_factor': sources.acceleration_smooth_factor,
        'background_ratio': sources.background_ratio,
    }


def _sourcesFromPayload(payload: dict[str, Any]) -> LyricVideoSources:
    return LyricVideoSources(
        lyric=str(payload.get('lyric', '')),
        translated_lyric=str(payload.get('translated_lyric', '')),
        yrc_lyric=str(payload.get('yrc_lyric', '')),
        audio_path=str(payload.get('audio_path', '')),
        duration=float(payload.get('duration', 1.0)),
        font_family=str(payload.get('font_family', '')),
        theme_color=_colorFromPayload(payload.get('theme_color')),
        is_dark=bool(payload.get('is_dark', False)),
        refresh_rate=float(payload.get('refresh_rate', _DEFAULT_REFRESH_RATE)),
        lyrics_smooth_factor=float(payload.get('lyrics_smooth_factor', 0.028)),
        acceleration_smooth_factor=float(
            payload.get('acceleration_smooth_factor', 0.068)
        ),
        background_ratio=float(payload.get('background_ratio', 0.4)),
    )


def _optionsPayload(options: LyricVideoExportOptions) -> dict[str, Any]:
    return {
        'video_ext': options.video_ext,
        'video_bitrate_kbps': options.video_bitrate_kbps,
        'fps': options.fps,
        'display_line_count': options.display_line_count,
        'word_by_word': options.word_by_word,
        'pure_color': options.pure_color,
        'with_translation': options.with_translation,
        'alignment': options.alignment,
        'background_color': _colorPayload(options.background_color),
        'with_audio': options.with_audio,
        'scroll_animation': options.scroll_animation,
        'x_axis_animation': options.x_axis_animation,
    }


def _optionsFromPayload(payload: dict[str, Any]) -> LyricVideoExportOptions:
    alignment = str(payload.get('alignment', 'center'))
    if alignment not in ('left', 'center', 'right'):
        alignment = 'center'
    background_color = _colorFromPayload(payload.get('background_color'))
    if background_color is None or not background_color.isValid():
        background_color = QColor(0, 177, 64)
    return LyricVideoExportOptions(
        video_ext=str(payload.get('video_ext', '.mp4')),
        video_bitrate_kbps=int(payload.get('video_bitrate_kbps', 8000)),
        fps=int(payload.get('fps', _VIDEO_FPS)),
        display_line_count=int(payload.get('display_line_count', 5)),
        word_by_word=bool(payload.get('word_by_word', True)),
        pure_color=bool(payload.get('pure_color', False)),
        with_translation=bool(payload.get('with_translation', True)),
        alignment=alignment,  # type: ignore[arg-type]
        background_color=background_color,
        with_audio=bool(payload.get('with_audio', True)),
        scroll_animation=bool(payload.get('scroll_animation', True)),
        x_axis_animation=bool(payload.get('x_axis_animation', True)),
    )


def _colorPayload(color: QColor | None) -> dict[str, int] | None:
    if color is None or not color.isValid():
        return None
    return {
        'r': color.red(),
        'g': color.green(),
        'b': color.blue(),
        'a': color.alpha(),
    }


def _colorFromPayload(payload: object) -> QColor | None:
    if not isinstance(payload, dict):
        return None
    return QColor(
        int(payload.get('r', 0)),
        int(payload.get('g', 0)),
        int(payload.get('b', 0)),
        int(payload.get('a', 255)),
    )


def _segmentWorkerMain(payload_path: str) -> int:
    with open(payload_path, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise TypeError('segment payload must be a dict')

    _ensureWorkerApplication()
    sources = _sourcesFromPayload(_dictPayload(payload.get('sources')))
    options = _optionsFromPayload(_dictPayload(payload.get('options')))
    renderer = _LyricVideoRenderer(sources, options)
    command = _buildFfmpegSegmentCommand(
        str(payload.get('ffmpeg', 'ffmpeg')),
        str(payload.get('segment_path', '')),
        options,
    )
    start_frame = int(payload.get('start_frame', 0))
    end_frame = int(payload.get('end_frame', 0))
    frame_count = int(payload.get('frame_count', 0))
    worker_index = int(payload.get('worker_index', 0))
    if end_frame <= start_frame:
        raise ValueError('invalid segment frame range')

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    frame_samples: list[tuple[float, float]] = []
    last_frame_time: float | None = None
    preview_interval = _previewInterval(frame_count)
    first_image: QImage | None = None
    try:
        stdin = process.stdin
        if stdin is None:
            raise RuntimeError('Failed to open FFmpeg stdin.')

        if start_frame > 0:
            first_image = renderer.renderFrame(start_frame / options.fps)

        for frame_index in range(start_frame, end_frame):
            frame_time = time.perf_counter()
            if last_frame_time is not None:
                frame_samples.append((frame_time, frame_time - last_frame_time))
                cutoff = frame_time - 1.0
                while frame_samples and frame_samples[0][0] < cutoff:
                    frame_samples.pop(0)
            last_frame_time = frame_time

            if first_image is not None and frame_index == start_frame:
                image = first_image
            else:
                image = renderer.renderFrame(frame_index / options.fps)
            stdin.write(_qimageBytes(image))

            rendered = frame_index - start_frame + 1
            current_frame = frame_index + 1
            preview = (
                _imageToBase64(image)
                if _shouldSendPreview(current_frame, frame_count, preview_interval)
                else ''
            )
            _writeJsonLine(
                {
                    'type': 'progress',
                    'worker_index': worker_index,
                    'rendered': rendered,
                    'fps': _fpsFromFrameSamples(frame_samples),
                    'preview': preview,
                }
            )

        stdin.close()
        stderr_bytes = process.stderr.read() if process.stderr is not None else b''
        returncode = process.wait()
    except Exception:
        _terminateProcess(process)
        raise

    if returncode != 0:
        stderr = stderr_bytes.decode('utf-8', 'ignore').strip()
        raise RuntimeError(
            'FFmpeg segment failed with code {0}: {1}'.format(
                returncode,
                stderr[-2000:] if stderr else 'no error output',
            )
        )

    _writeJsonLine({'type': 'done', 'worker_index': worker_index})
    return 0


def _ensureWorkerApplication() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    font_path = (
        Path(__file__).resolve().parents[2]
        / 'fonts'
        / ('HARMONYOS_SANS_SC_REGULAR.ttf')
    )
    if font_path.is_file():
        QFontDatabase.addApplicationFont(str(font_path))
    return app  # type: ignore


def _dictPayload(payload: object) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def _writeJsonLine(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
    sys.stdout.write('\n')
    sys.stdout.flush()


def _normalizedDisplayLineCount(line_count: int) -> int:
    try:
        value = int(line_count)
    except (TypeError, ValueError):
        value = _BASE_DISPLAY_LINE_COUNT
    value = max(1, min(_MAX_DISPLAY_LINE_COUNT, value))
    if value % 2 == 0:
        value += 1 if value < _MAX_DISPLAY_LINE_COUNT else -1
    return value


def _videoHeightForLineCount(line_count: int) -> int:
    line_count = _normalizedDisplayLineCount(line_count)
    return int(_BASE_VIDEO_HEIGHT * line_count / _BASE_DISPLAY_LINE_COUNT)


def _normalizeOutputPath(output_path: str, video_ext: str) -> str:
    root, ext = os.path.splitext(output_path)
    if ext:
        return output_path
    return root + video_ext


def _previewInterval(frame_count: int) -> int:
    if frame_count <= 300:
        return 10
    if frame_count >= 1800:
        return 20
    return 10 + round((frame_count - 300) / 1500 * 10)


def _shouldSendPreview(
    current_frame: int,
    frame_count: int,
    preview_interval: int,
) -> bool:
    return (
        current_frame == 1
        or current_frame == frame_count
        or current_frame % preview_interval == 0
    )


def _fpsFromFrameSamples(frame_samples: list[tuple[float, float]]) -> float:
    if not frame_samples:
        return 0.0
    average_interval = sum(interval for _stamp, interval in frame_samples) / len(
        frame_samples
    )
    if average_interval <= 0:
        return 0.0
    return math.floor((1 / average_interval) * 10) / 10


def _fpsFromProgressSamples(frame_samples: list[tuple[float, int]]) -> float:
    if len(frame_samples) < 2:
        return 0.0
    first_time, first_frame = frame_samples[0]
    last_time, last_frame = frame_samples[-1]
    elapsed = last_time - first_time
    if elapsed <= 0:
        return 0.0
    return math.floor(max(0.0, last_frame - first_frame) / elapsed * 10) / 10


def _buildFfmpegCommand(
    ffmpeg: str,
    sources: LyricVideoSources,
    options: LyricVideoExportOptions,
    output_path: str,
) -> list[str]:
    ext = os.path.splitext(output_path)[1].lower() or options.video_ext
    video_height = _videoHeightForLineCount(options.display_line_count)
    command = [
        ffmpeg,
        '-y',
        '-hide_banner',
        '-loglevel',
        'error',
        '-f',
        'rawvideo',
        '-pix_fmt',
        'rgb24',
        '-s',
        f'{_VIDEO_WIDTH}x{video_height}',
        '-r',
        str(options.fps),
        '-i',
        'pipe:0',
    ]
    if options.with_audio and sources.audio_path:
        command.extend(['-i', sources.audio_path])

    if options.with_audio and sources.audio_path:
        command.extend(['-map', '0:v:0', '-map', '1:a:0', '-shortest'])

    command.extend(_videoCodecArgs(ffmpeg, ext, options.video_bitrate_kbps))

    if options.with_audio and sources.audio_path:
        command.extend(_audioCodecArgs(ext))

    if ext == '.av1':
        command.extend(['-f', 'matroska'])
    command.append(output_path)
    return command


def _buildFfmpegSegmentCommand(
    ffmpeg: str,
    output_path: str,
    options: LyricVideoExportOptions,
) -> list[str]:
    video_height = _videoHeightForLineCount(options.display_line_count)
    return [
        ffmpeg,
        '-y',
        '-hide_banner',
        '-loglevel',
        'error',
        '-f',
        'rawvideo',
        '-pix_fmt',
        'rgb24',
        '-s',
        f'{_VIDEO_WIDTH}x{video_height}',
        '-r',
        str(options.fps),
        '-i',
        'pipe:0',
        '-an',
        '-c:v',
        'ffv1',
        '-pix_fmt',
        'rgb24',
        output_path,
    ]


def _buildFfmpegConcatCommand(
    ffmpeg: str,
    concat_file: str,
    sources: LyricVideoSources,
    options: LyricVideoExportOptions,
    output_path: str,
) -> list[str]:
    ext = os.path.splitext(output_path)[1].lower() or options.video_ext
    command = [
        ffmpeg,
        '-y',
        '-hide_banner',
        '-loglevel',
        'error',
        '-nostats',
        '-progress',
        'pipe:1',
        '-f',
        'concat',
        '-safe',
        '0',
        '-i',
        concat_file,
    ]
    if options.with_audio and sources.audio_path:
        command.extend(['-i', sources.audio_path])
        command.extend(['-map', '0:v:0', '-map', '1:a:0', '-shortest'])
    else:
        command.extend(['-map', '0:v:0'])

    command.extend(_videoCodecArgs(ffmpeg, ext, options.video_bitrate_kbps))

    if options.with_audio and sources.audio_path:
        command.extend(_audioCodecArgs(ext))

    if ext == '.av1':
        command.extend(['-f', 'matroska'])
    command.append(output_path)
    return command


def _videoCodecArgs(ffmpeg: str, ext: str, bitrate_kbps: int) -> list[str]:
    bitrate = f'{max(100, int(bitrate_kbps))}k'
    encoders = _availableEncoders(ffmpeg)

    if ext == '.av1':
        codec = _firstAvailableEncoder(
            encoders,
            ('libsvtav1', 'libaom-av1', 'librav1e'),
        )
        if not codec:
            raise RuntimeError('No AV1 encoder found in FFmpeg.')
        args = ['-c:v', codec, '-b:v', bitrate, '-pix_fmt', 'yuv420p']
        if codec == 'libsvtav1':
            args.extend(['-preset', '8'])
        elif codec == 'libaom-av1':
            args.extend(['-cpu-used', '6'])
        return args

    if ext == '.webm':
        codec = _firstAvailableEncoder(encoders, ('libvpx-vp9', 'libvpx')) or 'libvpx'
        return ['-c:v', codec, '-b:v', bitrate, '-pix_fmt', 'yuv420p']

    codec = _firstAvailableEncoder(encoders, ('libx264',)) or 'mpeg4'
    args = ['-c:v', codec, '-b:v', bitrate, '-pix_fmt', 'yuv420p']
    if codec == 'libx264':
        args.extend(['-preset', 'veryfast'])
    if ext == '.mp4':
        args.extend(['-movflags', '+faststart'])
    return args


def _audioCodecArgs(ext: str) -> list[str]:
    if ext == '.webm':
        return ['-c:a', 'libopus', '-b:a', '160k']
    return ['-c:a', 'aac', '-b:a', '192k']


def _availableEncoders(ffmpeg: str) -> set[str]:
    try:
        result = subprocess.run(
            [ffmpeg, '-hide_banner', '-encoders'],
            text=True,
            capture_output=True,
            timeout=8,
        )
    except Exception:
        return set()
    encoders: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith('V'):
            encoders.add(parts[1])
    return encoders


def _firstAvailableEncoder(encoders: set[str], names: tuple[str, ...]) -> str | None:
    if not encoders:
        return names[0] if names else None
    for name in names:
        if name in encoders:
            return name
    return None


def _qimageBytes(image: QImage) -> bytes:
    bits = image.constBits()
    try:
        return bits.tobytes(image.sizeInBytes())  # type: ignore[call-arg]
    except TypeError:
        return bits.tobytes()  # type: ignore[union-attr]


def _imageToBase64(image: QImage) -> str:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')  # type: ignore
    data = buffer.data().data()
    buffer.close()
    return base64.b64encode(data).decode('ascii')


def _imageFromBase64(data: str) -> QImage | None:
    if not data:
        return None
    try:
        image = QImage()
        image.loadFromData(base64.b64decode(data), 'PNG')  # type: ignore
    except Exception:
        return None
    return image if not image.isNull() else None


def _terminateProcess(process: subprocess.Popen[bytes]) -> None:
    try:
        if process.stdin is not None:
            process.stdin.close()
    except OSError:
        pass
    if process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=0.5)
        except Exception:
            try:
                process.kill()
            except OSError:
                pass


if __name__ == '__main__' and '--lyric-video-segment-worker' in sys.argv:
    index = sys.argv.index('--lyric-video-segment-worker')
    try:
        payload = sys.argv[index + 1]
    except IndexError as e:
        raise SystemExit('missing lyric video segment payload') from e
    raise SystemExit(_segmentWorkerMain(payload))
