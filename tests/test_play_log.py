from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from ncm import apis

from core.models import ArtistInfo, SongInfo, SongStorable
from core.netease_backend import NeteaseCloudMusicBackend


class PlayLogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[dict] = []
        self._original = apis.user.setWeblog

        def fakeSetWeblog(log: dict) -> dict:
            self.sent.append(log)
            return {}

        apis.user.setWeblog = fakeSetWeblog
        self.backend = NeteaseCloudMusicBackend()
        self.song = SongStorable(
            SongInfo(
                name='Test Song',
                artists=[ArtistInfo(id=1, name='Artist')],
                id='347230',
                privilege=0,
                duration=215000,
            )
        )

    def tearDown(self) -> None:
        apis.user.setWeblog = self._original

    def testRecordPlaySendsPlv(self) -> None:
        self.backend.recordPlay(self.song)
        self.assertEqual(len(self.sent), 1)
        log = self.sent[0]
        self.assertEqual(log['action'], '_plv')
        self.assertEqual(log['json']['id'], 347230)
        self.assertEqual(log['json']['type'], 'song')
        self.assertEqual(log['json']['resource_time'], 215)
        self.assertNotIn('time', log['json'])
        self.assertNotIn('mainsiteWeb', log['json'])

    def testRecordPlayedSendsPld(self) -> None:
        self.backend.recordPlayed(self.song, 42.7)
        self.assertEqual(len(self.sent), 1)
        log = self.sent[0]
        self.assertEqual(log['action'], '_pld')
        self.assertEqual(log['json']['id'], 347230)
        self.assertEqual(log['json']['time'], 42)
        self.assertEqual(log['json']['realtime'], 42)
        self.assertEqual(log['json']['end'], 'playend')
        self.assertEqual(log['json']['resource_time'], 215)


if __name__ == '__main__':
    unittest.main()
