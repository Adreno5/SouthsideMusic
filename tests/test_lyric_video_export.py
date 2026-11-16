from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from typing import BinaryIO, cast
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from core.lyric_video_export import (
    LyricVideoExportOptions,
    LyricVideoSources,
    _writeFrame,
    exportLyricVideo,
)


class BrokenPipe:
    def write(self, data: bytes) -> int:
        raise OSError(errno.EINVAL, 'Invalid argument')


class WriteFrameTest(unittest.TestCase):
    def testReportsFfmpegStderrWhenPipeIsBroken(self) -> None:
        process = subprocess.Popen(
            [sys.executable, '-c', "import sys; sys.stderr.write('boom')"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if process.stderr is not None:
            self.addCleanup(process.stderr.close)
        process.wait()
        with self.assertRaises(RuntimeError) as caught:
            _writeFrame(
                cast(BinaryIO, BrokenPipe()),
                QImage(2, 2, QImage.Format.Format_RGB888),
                cast(subprocess.Popen[bytes], process),
            )
        self.assertIn('boom', str(caught.exception))
        self.assertIn(str(process.returncode), str(caught.exception))

    def testWaitsForFfmpegErrorAfterPipeCloses(self) -> None:
        detail = 'Permission denied\n' * 200
        process = subprocess.Popen(
            [
                sys.executable,
                '-c',
                'import sys, time; time.sleep(0.1); '
                f'sys.stderr.write({detail!r}); sys.exit(1)',
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            with self.assertRaises(RuntimeError) as caught:
                _writeFrame(
                    cast(BinaryIO, BrokenPipe()),
                    QImage(2, 2, QImage.Format.Format_RGB888),
                    process,
                )
            self.assertIn(detail.strip(), str(caught.exception).replace('\r\n', '\n'))
            self.assertIn('code 1', str(caught.exception))
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stderr is not None:
                process.stderr.close()

    def testKeepsPipeErrorWhenFfmpegIsAlive(self) -> None:
        process = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(30)'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            with self.assertRaises(OSError):
                _writeFrame(
                    cast(BinaryIO, BrokenPipe()),
                    QImage(2, 2, QImage.Format.Format_RGB888),
                    cast(subprocess.Popen[bytes], process),
                )
        finally:
            process.kill()
            process.wait()
            if process.stderr is not None:
                process.stderr.close()


class ParallelExportTest(unittest.TestCase):
    def testExportsSegmentsAndAudioInDestinationDirectory(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ffmpeg = root / 'ffmpeg' / 'bin' / 'ffmpeg.exe'
        ffprobe = root / 'ffmpeg' / 'bin' / 'ffprobe.exe'
        if not ffmpeg.is_file() or not ffprobe.is_file():
            self.skipTest('Bundled FFmpeg and FFprobe are required')
        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        with tempfile.TemporaryDirectory(prefix='歌词导出 ', dir=root) as directory:
            audio_path = os.path.join(directory, 'audio.wav')
            with wave.open(audio_path, 'wb') as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(bytes(8000 * 2 * 40))
            sources = LyricVideoSources(
                lyric='[00:00.00]First line\n[00:20.00]Second line',
                translated_lyric='',
                yrc_lyric='',
                audio_path=audio_path,
                duration=40,
                font_family='Segoe UI',
                theme_color=None,
                is_dark=True,
            )
            options = LyricVideoExportOptions(fps=1, display_line_count=1)
            output_path = os.path.join(directory, 'lyrics.mp4')
            progress = []
            with (
                patch('core.lyric_video_export.AudioSegment_.converter', str(ffmpeg)),
                patch('core.lyric_video_export.os.cpu_count', return_value=2),
                patch(
                    'core.lyric_video_export.tempfile.mkdtemp',
                    wraps=tempfile.mkdtemp,
                ) as make_temp,
            ):
                result = exportLyricVideo(
                    sources, options, output_path, progress.append
                )
            self.assertEqual(result, output_path)
            self.assertEqual(make_temp.call_args.kwargs['dir'], directory)
            self.assertEqual(sorted(os.listdir(directory)), ['audio.wav', 'lyrics.mp4'])
            self.assertEqual({status.stage for status in progress}, {'render', 'merge'})
            probe = subprocess.run(
                [
                    str(ffprobe),
                    '-v',
                    'error',
                    '-count_frames',
                    '-show_streams',
                    '-show_format',
                    '-of',
                    'json',
                    output_path,
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            )
            metadata = json.loads(probe.stdout)
            video = next(s for s in metadata['streams'] if s['codec_type'] == 'video')
            self.assertEqual(int(video['nb_read_frames']), 40)
            self.assertTrue(
                any(s['codec_type'] == 'audio' for s in metadata['streams'])
            )
            self.assertAlmostEqual(float(metadata['format']['duration']), 40, delta=0.2)


if __name__ == '__main__':
    unittest.main()
