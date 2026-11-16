from __future__ import annotations

import copy
import datetime
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit

import numpy as np
from mutagen.flac import FLAC
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from qfluentwidgets import InfoBar

from harness.app_cases import enterText, performAction, waitUntil


def _mediaCommand(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        capture_output=True,
        check=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )


def _decode(ffmpeg: str, path: Path) -> np.ndarray:
    result = _mediaCommand([
        ffmpeg,
        '-v',
        'error',
        '-i',
        str(path),
        '-map',
        '0:a:0',
        '-ac',
        '2',
        '-ar',
        '44100',
        '-f',
        'f32le',
        '-acodec',
        'pcm_f32le',
        'pipe:1',
    ])
    samples = np.frombuffer(result.stdout, dtype='<f4').reshape(-1, 2)
    assert len(samples) > 0 and np.isfinite(samples).all()
    assert float(np.sqrt(np.mean(samples**2))) > 0.001
    return samples


def runExportCase(ctx: Any, case_id: str, output: Path, backend: Any) -> dict[str, Any]:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if case_id == 'whisper':
        from core import whisper_lyrics
        from core.downloader import asyncTask

        audio = os.environ.get('SOUTHSIDE_HARNESS_WHISPER_AUDIO', '')
        model = os.environ.get('SOUTHSIDE_HARNESS_WHISPER_MODEL_DIR', '')
        if not audio or not model:
            raise unittest.SkipTest(
                'Set SOUTHSIDE_HARNESS_WHISPER_AUDIO and '
                'SOUTHSIDE_HARNESS_WHISPER_MODEL_DIR to existing local files'
            )
        audio_path = Path(audio).expanduser().resolve()
        model_path = Path(model).expanduser().resolve()
        if not audio_path.is_file() or not all(
            (model_path / name).is_file()
            for name in ('model.bin', 'config.json', 'tokenizer.json')
        ):
            raise unittest.SkipTest('Local Whisper audio or model files are missing')
        candidates = [
            base / 'runtime' / f'{sys.implementation.cache_tag}-{device}'
            for base in (whisper_lyrics.WHISPER_DATA_DIR, model_path.parent.parent)
            for device in ('cpu', 'cuda')
        ]
        installed = importlib.util.find_spec('faster_whisper')
        if installed is not None and installed.origin:
            candidates.append(Path(installed.origin).parent.parent)
        runtime = next(
            (
                directory
                for directory in candidates
                if all(
                    (directory / name / '__init__.py').is_file()
                    for name in ('faster_whisper', 'ctranslate2', 'onnxruntime', 'av')
                )
            ),
            None,
        )
        if runtime is None:
            raise unittest.SkipTest('An existing local Whisper runtime is required')
        try:
            probe = _mediaCommand([
                sys.executable,
                '-I',
                str(Path(whisper_lyrics.__file__).with_name('whisper_worker.py')),
                '--probe',
                str(runtime),
                'cpu',
                str(model_path),
            ])
        except subprocess.CalledProcessError as error:
            detail = error.stderr.decode('utf-8', errors='replace')
            if any(
                name in detail
                for name in (
                    'ModuleNotFoundError',
                    'ImportError',
                    'DLL load failed',
                    'WinError 126',
                )
            ):
                raise unittest.SkipTest(
                    'Local Whisper runtime dependencies are unavailable: '
                    + detail[-1500:]
                ) from error
            raise
        assert any(
            json.loads(line).get('ready') is True
            for line in probe.stdout.decode('utf-8').splitlines()
            if line.startswith('{')
        ), probe.stderr.decode('utf-8', errors='replace')
        result_path = output / 'whisper-result.json'
        result: dict[str, Any] = {}
        updates: list[dict[str, Any]] = []
        finished = threading.Event()
        cancelled = threading.Event()
        deadline = time.monotonic() + 180

        def transcribe() -> None:
            try:
                result['log'] = whisper_lyrics._runWorker(
                    [
                        str(runtime),
                        str(model_path),
                        'cpu',
                        str(audio_path),
                        str(result_path),
                    ],
                    lambda: cancelled.is_set() or time.monotonic() >= deadline,
                    updates.append,
                )
            except Exception as error:
                result['error'] = error
            finally:
                finished.set()

        manager = asyncTask(transcribe, (), ctx.main_window)
        worker = manager._thread
        try:
            waitUntil(finished.is_set, 'local Whisper worker', 190)
            assert worker is not None
            waitUntil(lambda: not worker.is_alive(), 'Whisper worker shutdown', 5)
            if 'error' in result:
                raise result['error']
            assert result.get('log') is not None, 'Whisper worker timed out'
            raw = json.loads(result_path.read_text(encoding='utf-8'))
            assert raw['segments'], 'No speech recognized in the supplied audio'
            for segment in raw['segments']:
                assert segment['text'].strip()
                assert 0 <= segment['start'] <= segment['end']
                for word in segment['words']:
                    assert 0 <= word['start'] <= word['end']
            lyrics = whisper_lyrics.whisperResultToLyrics(raw)
            assert lyrics['lyric'] and lyrics['yrc_lyric']
            (output / 'whisper.lrc').write_text(lyrics['lyric'], encoding='utf-8')
            (output / 'whisper.yrc').write_text(lyrics['yrc_lyric'], encoding='utf-8')
            (output / 'whisper-worker.log').write_text(result['log'], encoding='utf-8')
            return {
                'path': str(result_path),
                'runtime': str(runtime),
                'model': str(model_path),
                'language': raw['language'],
                'segments': len(raw['segments']),
                'updates': len(updates),
            }
        finally:
            cancelled.set()
            if worker is not None and worker.is_alive():
                waitUntil(lambda: not worker.is_alive(), 'cancelled Whisper worker', 10)

    if case_id in ('native_audio', 'native_smtc'):
        if case_id == 'native_audio':
            import sounddevice

            if sounddevice.OutputStream.__module__ != 'sounddevice':
                raise unittest.SkipTest(
                    'Native audio requires an unpatched output stream'
                )
            try:
                device = sounddevice.query_devices(kind='output')
            except sounddevice.PortAudioError as error:
                raise unittest.SkipTest(
                    'Native audio output is unavailable: ' + str(error)
                ) from error
            assert device['max_output_channels'] > 0
        else:
            from winrt.windows.media import (
                MediaPlaybackStatus,
                SystemMediaTransportControls,
            )

            controls = ctx.smtc._smtc
            if not isinstance(controls, SystemMediaTransportControls):
                raise unittest.SkipTest(
                    'Native SMTC requires an unpatched Windows controller'
                )
            ctx.smtc.setEnabled(True)
        performAction(ctx, 'play', {}, backend)
        song = backend.songs[0]
        player = ctx.player
        try:
            position = player.getPosition()
            waitUntil(
                lambda: player.getPosition() > position + 0.3,
                'native playback progress',
            )
            if case_id == 'native_audio':
                stream = player.stream
                assert stream is not None and stream.active
                result = {
                    'device': device['name'],
                    'samplerate': stream.samplerate,
                    'channels': stream.channels,
                    'position': player.getPosition(),
                }
            else:
                waitUntil(
                    lambda: (
                        controls.display_updater.music_properties.title == song.name
                        and controls.display_updater.music_properties.album_title
                        == backend.getTrackDetail(song.id).album_name
                    ),
                    'native SMTC metadata',
                )
                assert controls.is_enabled
                assert controls.playback_status == MediaPlaybackStatus.PLAYING
                assert controls.display_updater.music_properties.artist == ', '.join(
                    artist.name for artist in song.artists
                )
                result = {
                    'title': controls.display_updater.music_properties.title,
                    'artist': controls.display_updater.music_properties.artist,
                    'album': controls.display_updater.music_properties.album_title,
                }
            QTest.mouseClick(
                ctx.main_window.controller.play_pausebtn, Qt.MouseButton.LeftButton
            )
            waitUntil(lambda: not player.isPlaying(), 'native pause')
            if case_id == 'native_audio':
                assert not stream.active
            else:
                assert controls.playback_status == MediaPlaybackStatus.PAUSED
            QTest.mouseClick(
                ctx.main_window.controller.play_pausebtn, Qt.MouseButton.LeftButton
            )
            waitUntil(player.isPlaying, 'native resume')
            if case_id == 'native_smtc':
                assert controls.playback_status == MediaPlaybackStatus.PLAYING
            assert ctx.main_window.grab().save(str(output / (case_id + '.png')))
            return result
        finally:
            if player.isPlaying():
                QTest.mouseClick(
                    ctx.main_window.controller.play_pausebtn, Qt.MouseButton.LeftButton
                )

    if case_id not in ('export_original', 'export_effects', 'export_video'):
        raise ValueError('Unknown export case: ' + case_id)
    from core.config import cfg, saveConfig
    from core.downloader import TaskManager
    from core.i18n import tr
    from views.error_popup import ErrorPopupWindow
    from views import playing_page, song_export

    ffmpeg = shutil.which('ffmpeg')
    ffprobe = shutil.which('ffprobe')
    binary_dir = Path(backend.root) / 'ffmpeg' / 'bin'
    if not ffmpeg and (binary_dir / 'ffmpeg.exe').is_file():
        ffmpeg = str(binary_dir / 'ffmpeg.exe')
    if not ffprobe and (binary_dir / 'ffprobe.exe').is_file():
        ffprobe = str(binary_dir / 'ffprobe.exe')
    if not ffmpeg or not ffprobe:
        raise unittest.SkipTest('Local FFmpeg and FFprobe are required')
    video_case = case_id == 'export_video'
    song = backend.songs[1 if video_case else 0]
    audio_path = Path(song.getMusicPath()).resolve()
    source = FLAC(str(audio_path))
    detail = backend.getTrackDetail(song.id)
    assert urlsplit(detail.cover_url).hostname in ('127.0.0.1', 'localhost', '::1')
    destination = output / (case_id + ('.mp4' if video_case else '.flac'))
    destination.unlink(missing_ok=True)
    config_names = (
        (
            'lyric_video_export_' + name
            for name in (
                'ext',
                'bitrate_kbps',
                'fps',
                'display_line_count',
                'word_by_word',
                'pure_color',
                'with_translation',
                'alignment',
                'background_color',
                'with_audio',
                'scroll_animation',
            )
        )
        if video_case
        else (
            'play_speed',
            'play_pitch',
            'stereo',
            'stereo_haas_index',
            'enable_reverb',
            'reverb_intensity',
            'eq_bands',
        )
    )
    previous_config = {name: copy.deepcopy(getattr(cfg, name)) for name in config_names}
    window = ctx.main_window
    old_bars = window.findChildren(InfoBar)
    progress_type = (
        playing_page.LyricVideoExportProgressDialog
        if video_case
        else song_export.SongEffectExportProgressDialog
    )
    old_progress = window.findChildren(progress_type)
    dialog_type = (
        playing_page.LyricVideoExportDialog
        if video_case
        else song_export.SongEffectExportDialog
    )
    chosen: list[dict[str, Any]] = []
    errors: list[str] = []
    threads: list[threading.Thread] = []
    success_text = tr(
        'playing_page.exported_lyric_video_song_name'
        if video_case
        else 'song_card.exported_song_song_name',
        song_name=song.name,
    )
    timer = QTimer(window)
    deadline = time.monotonic() + 8

    def chooseDialog() -> None:
        for popup in window.findChildren(ErrorPopupWindow):
            if popup.isVisible():
                errors.append(popup.detail_text)
                popup.reject()
        for dialog in window.findChildren(dialog_type):
            if not dialog.isVisible() or chosen:
                continue
            try:
                if video_case:
                    dialog.type_box.setCurrentIndex(0)
                    enterText(dialog.bitrate_box.lineEdit(), '1000')
                    QTest.keyClick(dialog.bitrate_box, Qt.Key.Key_Tab)
                    enterText(dialog.display_line_count_box.lineEdit(), '3')
                    QTest.keyClick(dialog.display_line_count_box, Qt.Key.Key_Tab)
                    dialog.fps_slider.setFocus()
                    QTest.keyClick(dialog.fps_slider, Qt.Key.Key_Home)
                    for widget, checked in (
                        (dialog.word_box, True),
                        (dialog.translation_box, True),
                        (dialog.audio_box, True),
                        (dialog.pure_color_box, False),
                    ):
                        assert widget.isEnabled()
                        if widget.isChecked() != checked:
                            QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
                    options = dialog.options()
                    assert options.fps == 10 and options.display_line_count == 3
                    assert (
                        options.with_audio
                        and options.word_by_word
                        and options.with_translation
                    )
                    chosen.append({
                        'fps': options.fps,
                        'lines': options.display_line_count,
                        'with_audio': options.with_audio,
                    })
                    assert dialog.grab().save(str(output / 'export_video-options.png'))
                    QTest.mouseClick(dialog.yesButton, Qt.MouseButton.LeftButton)
                else:
                    with_effects = case_id == 'export_effects'
                    button = dialog.yesButton if with_effects else dialog.cancelButton
                    chosen.append({'with_effects': with_effects})
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    assert dialog.withEffects() == with_effects
            except Exception as error:
                errors.append(str(error))
                dialog.reject()
            return
        if not chosen and time.monotonic() >= deadline:
            errors.append('Export options dialog did not appear')
            modal = QApplication.activeModalWidget()
            if modal is not None and hasattr(modal, 'reject'):
                modal.reject()
            timer.stop()

    timer.timeout.connect(chooseDialog)
    try:
        if video_case:
            performAction(ctx, 'play', {'index': 1}, backend)
            waitUntil(
                lambda: (
                    ctx.playing_page.cur is not None
                    and ctx.playing_page.cur.storable is song
                ),
                'playing page song',
            )
            assert (
                song.getLyrics()['yrc_lyric'] and song.getLyrics()['translated_lyric']
            )
            waitUntil(lambda: not window.pl_animating, 'playlist animation')
            if window.pl_expanded:
                QTest.mouseClick(
                    window.controller.playlist_btn, Qt.MouseButton.LeftButton
                )
                waitUntil(lambda: not window.pl_animating, 'closed playlist')
            waitUntil(lambda: not window.dp_animating, 'playing page animation')
            if not window.dp_expanded:
                QTest.mouseClick(
                    window.controller,
                    Qt.MouseButton.LeftButton,
                    pos=window.controller.rect().center(),
                )
            waitUntil(
                lambda: (
                    window.dp_expanded
                    and not window.dp_animating
                    and ctx.playing_page.lyric_video_export_button.isVisible()
                ),
                'video export button',
            )
        else:
            cfg.play_speed = 1.25
            cfg.play_pitch = 0
            cfg.stereo = False
            cfg.enable_reverb = False
            cfg.eq_bands = [(20.0, 0.0), (20000.0, 0.0)]
        old_workers = window.findChildren(TaskManager)
        timer.start(20)
        if video_case:
            with patch.object(
                playing_page.QFileDialog,
                'getSaveFileName',
                return_value=(str(destination), 'MP4'),
            ) as chooser:
                QTest.mouseClick(
                    ctx.playing_page.lyric_video_export_button,
                    Qt.MouseButton.LeftButton,
                )
            assert chooser.call_count == 1
        else:
            song_export.exportSong(song, str(audio_path), str(destination), window)
        assert chosen and not errors, errors
        for manager in window.findChildren(TaskManager):
            if manager not in old_workers and manager._thread is not None:
                threads.append(manager._thread)
        assert threads, 'The real export TaskManager did not start'

        def completed() -> bool:
            if errors:
                return True
            return not any(thread.is_alive() for thread in threads) and any(
                bar not in old_bars and bar.content == success_text
                for bar in window.findChildren(InfoBar)
            )

        waitUntil(completed, case_id + ' worker and completion UI', 80)
        assert not errors, '\n'.join(errors)
        assert destination.is_file() and destination.stat().st_size > 0
        dialogs = [
            dialog
            for dialog in window.findChildren(progress_type)
            if dialog not in old_progress
        ]
        if video_case or case_id == 'export_effects':
            assert dialogs and all(dialog.yesButton.isEnabled() for dialog in dialogs)
        assert window.grab().save(str(output / (case_id + '.png')))
        pcm = _decode(ffmpeg, destination)
        if video_case:
            probe = _mediaCommand([
                ffprobe,
                '-v',
                'error',
                '-count_frames',
                '-show_streams',
                '-show_format',
                '-of',
                'json',
                str(destination),
            ])
            metadata = json.loads(probe.stdout)
            (output / 'export_video-ffprobe.json').write_text(
                json.dumps(metadata, indent=2), encoding='utf-8'
            )
            video = next(
                stream
                for stream in metadata['streams']
                if stream['codec_type'] == 'video'
            )
            audio_stream = next(
                stream
                for stream in metadata['streams']
                if stream['codec_type'] == 'audio'
            )
            expected_duration = max(
                ctx.playing_manager.getDisplayLength(), song.duration / 1000, 1
            )
            assert abs(float(metadata['format']['duration']) - expected_duration) < 0.3
            assert int(video['nb_read_frames']) == round(
                expected_duration * chosen[0]['fps']
            )
            assert video['width'] > 0 and video['height'] > 0
            assert int(audio_stream['sample_rate']) == source.info.sample_rate
            assert abs(len(pcm) / 44100 - source.info.length) < 0.3
            return {
                'path': str(destination),
                'duration': metadata['format']['duration'],
                'frames': video['nb_read_frames'],
                'video_codec': video['codec_name'],
                'audio_codec': audio_stream['codec_name'],
                'options': chosen[0],
            }
        exported = FLAC(str(destination))
        assert exported.tags is not None
        assert exported['title'] == [song.name]
        assert exported['artist'] == [
            '\u3001'.join(artist.name for artist in song.artists)
        ]
        assert exported['album'] == [detail.album_name]
        assert exported['date'] == [
            str(datetime.datetime.fromtimestamp(detail.publish_time / 1000).year)
        ]
        assert exported['tracknumber'] == [f'{detail.cd}/{detail.track_no}']
        for line in song.lyric.splitlines():
            text = re.sub(r'^(?:\[\d+:\d+(?:\.\d+)?\])+', '', line).strip()
            if text:
                assert text in exported['lyrics'][0]
        assert len(exported.pictures) == 1
        assert exported.pictures[0].data == song.getImageBytes()
        assert exported.info.sample_rate == source.info.sample_rate
        assert exported.info.channels == source.info.channels == 2
        original = _decode(ffmpeg, audio_path)
        expected_duration = source.info.length / (
            1.25 if case_id == 'export_effects' else 1
        )
        assert abs(exported.info.length - expected_duration) < 0.05
        if case_id == 'export_original':
            np.testing.assert_array_equal(pcm, original)
        else:
            assert len(pcm) < len(original)
            assert abs(len(pcm) / len(original) - 0.8) < 0.01
        return {
            'path': str(destination),
            'duration': exported.info.length,
            'source_duration': source.info.length,
            'frames': len(pcm),
            'tags': {key: list(value) for key, value in exported.tags.items()},
            'options': chosen[0],
        }
    finally:
        timer.stop()
        timer.deleteLater()
        for dialog in window.findChildren(progress_type):
            if dialog not in old_progress:
                if dialog.yesButton.isEnabled():
                    QTest.mouseClick(dialog.yesButton, Qt.MouseButton.LeftButton)
                else:
                    dialog.reject()
        for name, value in previous_config.items():
            setattr(cfg, name, value)
        if video_case:
            saveConfig()
