from __future__ import annotations

import errno
import subprocess
import sys
import unittest
from pathlib import Path
from typing import BinaryIO, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtGui import QImage

from core.lyric_video_export import _writeFrame


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
        process.wait()
        with self.assertRaises(RuntimeError) as caught:
            _writeFrame(
                cast(BinaryIO, BrokenPipe()),
                QImage(2, 2, QImage.Format.Format_RGB888),
                cast(subprocess.Popen[bytes], process),
            )
        self.assertIn('boom', str(caught.exception))
        self.assertIn(str(process.returncode), str(caught.exception))

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


if __name__ == '__main__':
    unittest.main()
