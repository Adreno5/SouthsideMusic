from __future__ import annotations

import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.catalog import CASES, scenarioFor
from harness.environment import userFingerprint
from harness.gateway import Gateway
from harness.reports import writeReports
from harness.runner import runSuite, selectCases


class HarnessRunnerTests(unittest.TestCase):
    def testAllScenariosValidateAndIdsAreUnique(self) -> None:
        self.assertEqual(len(CASES), len({case['id'] for case in CASES}))
        gateway = Gateway()
        for case in CASES:
            with self.subTest(case=case['id']):
                gateway.setScenario(scenarioFor(case['id']))
        self.assertTrue(selectCases(['online'], None))
        self.assertTrue(
            all(case['group'] == 'online' for case in selectCases(['online'], None))
        )
        with self.assertRaises(ValueError):
            selectCases(['typo'], None)
        with self.assertRaises(ValueError):
            selectCases(None, ['typo'])

    def testReportsKeepFailureAndSkipDetailsWithEscaping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = [
                {'id': 'ok', 'group': 'core', 'status': 'passed', 'duration': 0.5},
                {
                    'id': '<failure>',
                    'group': 'core',
                    'status': 'failed',
                    'duration': 1,
                    'detail': 'Traceback\n<script>&错误',
                },
                {
                    'id': 'offline',
                    'group': 'online',
                    'status': 'skipped',
                    'duration': 0,
                    'detail': 'Network unavailable',
                },
            ]
            writeReports(root, results)
            suite = ET.parse(root / 'junit.xml').getroot()
            self.assertEqual(suite.attrib['failures'], '1')
            self.assertEqual(suite.attrib['skipped'], '1')
            self.assertIn('<script>&错误', suite.find('testcase/failure').text)
            self.assertNotIn(
                '<script>', (root / 'index.html').read_text(encoding='utf-8')
            )
            self.assertEqual(
                json.loads((root / 'results.json').read_text(encoding='utf-8'))[
                    'counts'
                ]['failed'],
                1,
            )

    def testUserDataFingerprintDetectsCreationDeletionAndChanges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = userFingerprint(root)
            (root / 'data').mkdir()
            file = root / 'data' / 'lyrics.txt'
            file.write_text('test')
            self.assertNotEqual(before, userFingerprint(root))
            file.unlink()
            self.assertEqual(before, userFingerprint(root))
            (root / 'config.json').write_text('{"volume":0.5}')
            self.assertNotEqual(before, userFingerprint(root))

    def testFailureContinuesAndReturnsNonzeroWhileSkipRemainsSkip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            closed: list[bool] = []

            class Session:
                def __init__(
                    self, source: Path, url: str, output: Path, native: bool = False
                ) -> None:
                    self.output = output

                def request(self, operation: str, case: str = '') -> dict[str, Any]:
                    if case == 'boot':
                        raise AssertionError('Application state mismatch')
                    return {'skipped': 'Offline dependency'}

                def close(self) -> None:
                    closed.append(True)

            with patch('harness.session.ApplicationSession', Session):
                code = runSuite(
                    root, root / 'reports', selectCases(None, ['boot', 'online_search'])
                )
            self.assertEqual(code, 1)
            records = json.loads(
                (root / 'reports' / 'results.json').read_text(encoding='utf-8')
            )
            self.assertEqual(
                [case['status'] for case in records['cases']], ['failed', 'skipped']
            )
            self.assertEqual(len(closed), 2)


if __name__ == '__main__':
    unittest.main()
