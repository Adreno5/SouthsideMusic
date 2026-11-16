from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.media_server import MediaServer


class HarnessMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        (root / 'audio.flac').write_bytes(bytes(range(100)))
        self.server = MediaServer(root)
        self.url = self.server.start() + '/audio.flac'
        self.addCleanup(self.server.stop)

    def testStreamingRangesAndSuffixes(self) -> None:
        for header, expected in [
            ('bytes=20-39', bytes(range(20, 40))),
            ('bytes=95-', bytes(range(95, 100))),
            ('bytes=-5', bytes(range(95, 100))),
        ]:
            with self.subTest(range=header):
                response = requests.get(self.url, headers={'Range': header}, timeout=3)
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, expected)
                self.assertEqual(int(response.headers['Content-Length']), len(expected))

    def testInvalidRangesReturn416WithoutKillingServer(self) -> None:
        for header in ('bytes=500-', 'bytes=no-', 'bytes=-0', 'bytes=1-2,4-5'):
            response = requests.get(self.url, headers={'Range': header}, timeout=3)
            self.assertEqual(response.status_code, 416)
        self.assertEqual(requests.get(self.url, timeout=3).content, bytes(range(100)))

    def testHeadAndMissingRoute(self) -> None:
        response = requests.head(self.url, timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Length'], '100')
        self.assertEqual(response.content, b'')
        self.assertEqual(
            requests.get(self.server.url + '/missing', timeout=3).status_code, 404
        )


if __name__ == '__main__':
    unittest.main()
