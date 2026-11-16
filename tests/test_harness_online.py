from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.netease_backend import NeteaseCloudMusicBackend
from harness.app_cases import runCase


class HarnessOnlineTests(unittest.TestCase):
    def testUnavailableNetworkAndExplicitApiRestrictionAreSkips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            for response in (
                requests.ConnectionError('offline'),
                requests.Timeout('offline'),
                {'code': 403},
                {'code': 429},
                {'code': 460},
                {'code': 503},
            ):
                with self.subTest(response=response):
                    backend = SimpleNamespace(online=False)
                    with (
                        patch(
                            'core.backend.getBackend',
                            return_value=NeteaseCloudMusicBackend(),
                        ),
                        patch(
                            'ncm.apis.cloudsearch.getSearchResult',
                            side_effect=response
                            if isinstance(response, Exception)
                            else None,
                            return_value=response,
                        ),
                    ):
                        with self.assertRaises(unittest.SkipTest):
                            runCase(None, 'online_search', {}, output, backend, None)
                    self.assertFalse(backend.online)

    def testMalformedSuccessResponsesFailInsteadOfSkipping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            for response in (
                b'not-json',
                {'code': 200, 'result': {'songs': [{'id': 1}]}},
                {'code': 200, 'result': {'songs': []}},
            ):
                with self.subTest(response=response):
                    backend = SimpleNamespace(online=False)
                    with (
                        patch(
                            'core.backend.getBackend',
                            return_value=NeteaseCloudMusicBackend(),
                        ),
                        patch(
                            'ncm.apis.cloudsearch.getSearchResult',
                            return_value=response,
                        ),
                    ):
                        with self.assertRaises((AssertionError, KeyError, TypeError)):
                            runCase(None, 'online_search', {}, output, backend, None)
                    self.assertFalse(backend.online)

    def testHttpAccessFailureIsSkipWhileClientErrorFails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            for status in (403, 503, 400):
                response = requests.Response()
                response.status_code = status
                exception = requests.HTTPError('provider response', response=response)
                with (
                    self.subTest(status=status),
                    patch(
                        'core.backend.getBackend',
                        return_value=NeteaseCloudMusicBackend(),
                    ),
                    patch(
                        'ncm.apis.cloudsearch.getSearchResult',
                        side_effect=exception,
                    ),
                ):
                    with self.assertRaises(
                        unittest.SkipTest if status != 400 else requests.HTTPError
                    ):
                        runCase(
                            None,
                            'online_search',
                            {},
                            Path(temporary),
                            SimpleNamespace(online=False),
                            None,
                        )


if __name__ == '__main__':
    unittest.main()
