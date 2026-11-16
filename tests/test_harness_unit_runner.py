from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from functools import partial
from pathlib import Path
from textwrap import dedent
from typing import Any, override
from unittest.mock import patch

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import unit_runner


class UnitRunnerTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix='unit-runner-test-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'project'
        self.root.mkdir()
        (self.root / 'tests').mkdir()
        self.report_dir = Path(directory.name) / 'reports'

    def _writeTest(self, name: str, source: str) -> None:
        (self.root / 'tests' / name).write_text(dedent(source), encoding='utf-8')

    def _report(self) -> dict[str, Any]:
        return json.loads(
            (self.report_dir / 'unit-results.json').read_text(encoding='utf-8')
        )

    def testRunsUnittestMainAssertMainAndFunctionsInSeparateProcesses(self) -> None:
        self._writeTest(
            'test_unittest.py',
            """
            import os
            import unittest

            class Checks(unittest.TestCase):
                def test_execution(self):
                    self.assertEqual(2 + 2, 4)
                    print(f'pid={os.getpid()}')

            if __name__ == '__main__':
                unittest.main()
            """,
        )
        self._writeTest(
            'test_assert.py',
            """
            import os

            def main():
                assert 3 * 3 == 9
                print(f'pid={os.getpid()}')

            if __name__ == '__main__':
                main()
            """,
        )
        self._writeTest(
            'test_function.py',
            """
            import os

            def test_execution():
                assert 4 * 4 == 16
                print(f'pid={os.getpid()}')
            """,
        )
        self.assertEqual(unit_runner.runUnitSuite(self.root, self.report_dir), 0)
        report = self._report()
        self.assertEqual(report['total'], 3)
        self.assertEqual(report['tests_run'], 3)
        pids = {
            int((self.report_dir / result['stdout']).read_text().strip().split('=')[1])
            for result in report['results']
        }
        self.assertEqual(len(pids), 3)
        self.assertNotIn(os.getpid(), pids)

    def testReflectsUnittestClassesAndFunctionsAndContinuesAfterFailure(self) -> None:
        self._writeTest(
            'test_functions.py',
            """
            import unittest

            class Checks(unittest.TestCase):
                def test_method(self):
                    print('method ran')

            def test_first():
                assert False, 'first failed'

            def test_second():
                print('second ran')
            """,
        )
        self.assertEqual(unit_runner.runUnitSuite(self.root, self.report_dir), 1)
        result = self._report()['results'][0]
        self.assertEqual(result['tests_run'], 3)
        stdout = (self.report_dir / result['stdout']).read_text()
        stderr = (self.report_dir / result['stderr']).read_text()
        self.assertIn('method ran', stdout)
        self.assertIn('second ran', stdout)
        self.assertIn('first failed', stderr)

    def testCallsMainWithoutGuardAndRecordsReturnedFailure(self) -> None:
        self._writeTest(
            'test_main.py',
            """
            def main():
                print('main ran')
                assert 1 == 1
                return 7
            """,
        )
        self.assertEqual(unit_runner.runUnitSuite(self.root, self.report_dir), 1)
        result = self._report()['results'][0]
        self.assertEqual(result['tests_run'], 1)
        self.assertIn('code 7', result['error'])
        self.assertIn('main ran', (self.report_dir / result['stdout']).read_text())

    def testStagesAssetsMetadataAndPathBasedWritesAwayFromSource(self) -> None:
        for name in ('src', 'scripts', 'fonts', 'icons', 'images'):
            directory = self.root / name
            directory.mkdir()
            (directory / 'asset.txt').write_text(name, encoding='utf-8')
        (self.root / 'bootstrap.py').write_text('value = 7\n', encoding='utf-8')
        (self.root / 'pyproject.toml').write_text('[project]\n', encoding='utf-8')
        (self.root / 'uv.lock').write_text('version = 1\n', encoding='utf-8')
        (self.root / 'config.json').write_text('original', encoding='utf-8')
        (self.root / 'data').mkdir()
        (self.root / 'data' / 'original.txt').write_text('original', encoding='utf-8')
        for name in ('data', '.mypy_cache'):
            cached = self.root / 'src' / name
            cached.mkdir()
            (cached / 'user-cache.txt').write_text('user cache', encoding='utf-8')
        self._writeTest(
            'test_writes.py',
            """
            import os
            from pathlib import Path
            import bootstrap

            def test_writes():
                root = Path(__file__).resolve().parents[1]
                assert root == Path.cwd()
                assert bootstrap.value == 7
                for name in ('src', 'scripts', 'fonts', 'icons', 'images'):
                    assert (root / name / 'asset.txt').read_text() == name
                assert (root / 'pyproject.toml').is_file()
                assert (root / 'uv.lock').is_file()
                assert not (root / 'data' / 'original.txt').exists()
                assert not (root / 'src' / 'data').exists()
                assert not (root / 'src' / '.mypy_cache').exists()
                assert not (root / 'config.json').exists()
                assert Path(os.environ['TEMP']).parent == root
                (root / 'config.json').write_text('changed')
                (root / 'data' / 'generated.txt').write_text('generated')
                print(root)
            """,
        )
        self.assertEqual(unit_runner.runUnitSuite(self.root, self.report_dir), 0)
        self.assertEqual((self.root / 'config.json').read_text(), 'original')
        self.assertFalse((self.root / 'data' / 'generated.txt').exists())
        result = self._report()['results'][0]
        staged = Path((self.report_dir / result['stdout']).read_text().strip())
        self.assertNotEqual(staged, self.root)
        self.assertFalse(staged.exists())

    def testExcludesVirtualEnvironmentsWithoutWalkingTheirContents(self) -> None:
        with patch.object(Path, 'is_symlink', return_value=False):
            ignored = unit_runner._copyIgnored(
                str(self.root),
                ['torch_venv', 'freethreaded_venv', '.venv', '__pycache__', 'src'],
            )
        self.assertEqual(
            ignored, {'torch_venv', 'freethreaded_venv', '.venv', '__pycache__'}
        )

    def testEmptyCollectionProducesFailingJsonXmlAndSummary(self) -> None:
        self.assertEqual(unit_runner.runUnitSuite(self.root, self.report_dir), 1)
        report = self._report()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['tests_run'], 0)
        suite = ET.parse(self.report_dir / 'unit-results.xml').getroot()
        self.assertEqual(suite.get('failures'), '1')
        self.assertIsNotNone(suite.find('testcase/failure'))
        self.assertTrue((self.report_dir / 'unit-summary.md').is_file())

    def testImportOnlyAndEmptyUnittestCannotPass(self) -> None:
        self._writeTest('test_import.py', "print('imported only')\n")
        self._writeTest(
            'test_empty.py',
            """
            import unittest

            if __name__ == '__main__':
                unittest.main()
            """,
        )
        self.assertEqual(unit_runner.runUnitSuite(self.root, self.report_dir), 1)
        report = self._report()
        self.assertEqual(report['failed'], 2)
        self.assertTrue(
            all('No tests executed' in result['error'] for result in report['results'])
        )

    def testNonzeroExitHardCrashAndFailedAssertionFailAndKeepRunning(self) -> None:
        self._writeTest('test_exit.py', 'raise SystemExit(5)\n')
        self._writeTest('test_crash.py', 'import os\nos._exit(0)\n')
        self._writeTest('test_assert.py', 'def test_failure():\n    assert False\n')
        self._writeTest('test_pass.py', 'def test_success():\n    print("ran last")\n')
        self.assertEqual(unit_runner.runUnitSuite(self.root, self.report_dir), 1)
        report = self._report()
        self.assertEqual(report['failed'], 3)
        self.assertEqual(report['passed'], 1)
        results = {result['name']: result for result in report['results']}
        self.assertEqual(results['tests/test_exit.py']['returncode'], 1)
        crash = results['tests/test_crash.py']
        self.assertEqual(crash['status'], 'error')
        self.assertEqual(crash['returncode'], 0)
        self.assertIn('without a result', crash['error'])

    def testUnittestFailureCannotBeHiddenByExitZero(self) -> None:
        self._writeTest(
            'test_hidden_failure.py',
            """
            import unittest

            class Checks(unittest.TestCase):
                def test_failure(self):
                    self.fail('real failure')

            if __name__ == '__main__':
                unittest.main(exit=False)
            """,
        )
        self.assertEqual(unit_runner.runUnitSuite(self.root, self.report_dir), 1)
        result = self._report()['results'][0]
        self.assertEqual(result['tests_run'], 1)
        self.assertEqual(result['status'], 'failed')

    def testRecordsUnittestDependencySkips(self) -> None:
        self._writeTest(
            'test_skip.py',
            """
            import unittest

            class Checks(unittest.TestCase):
                @unittest.skip('optional dependency missing')
                def test_skip(self):
                    raise AssertionError('must not execute skipped test')

                def test_run(self):
                    self.assertTrue(True)

            if __name__ == '__main__':
                unittest.main()
            """,
        )
        self.assertEqual(unit_runner.runUnitSuite(self.root, self.report_dir), 0)
        report = self._report()
        self.assertEqual(report['tests_run'], 2)
        self.assertEqual(report['skipped'], 1)

    def testCapturesBothLogsAndWritesParseableXmlWithControlCharacters(self) -> None:
        self._writeTest(
            'test_logs.py',
            """
            import sys

            def test_logs():
                print('stdout <entry> & value' + chr(0))
                print('stderr <entry> & value' + chr(1), file=sys.stderr)
            """,
        )
        self.assertEqual(unit_runner.runUnitSuite(self.root, self.report_dir), 0)
        case = ET.parse(self.report_dir / 'unit-results.xml').getroot().find('testcase')
        self.assertIsNotNone(case)
        self.assertIn('stdout <entry> & value', case.findtext('system-out'))
        self.assertIn('stderr <entry> & value', case.findtext('system-err'))

    def testStagingFailureProducesReports(self) -> None:
        self._writeTest('test_pass.py', 'def test_success():\n    assert True\n')
        with patch.object(
            unit_runner, '_stageRoot', side_effect=PermissionError('denied')
        ):
            self.assertEqual(unit_runner.runUnitSuite(self.root, self.report_dir), 1)
        result = self._report()['results'][0]
        self.assertEqual(result['status'], 'error')
        self.assertIn('denied', result['error'])

    def testTimeoutKillsSubprocessTreeAndStillRunsNextFile(self) -> None:
        self._writeTest(
            'test_a_timeout.py',
            """
            import subprocess
            import sys
            import time

            def main():
                child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'])
                print(f'child={child.pid}', flush=True)
                time.sleep(90)

            if __name__ == '__main__':
                main()
            """,
        )
        self._writeTest('test_z_pass.py', 'def test_pass():\n    assert True\n')
        run_file = partial(unit_runner._runFile, timeout_seconds=1)
        with patch.object(unit_runner, '_runFile', run_file):
            self.assertEqual(unit_runner.runUnitSuite(self.root, self.report_dir), 1)
        report = self._report()
        self.assertEqual(report['passed'], 1)
        result = report['results'][0]
        self.assertEqual(result['status'], 'timeout')
        stdout = (self.report_dir / result['stdout']).read_text()
        self.assertIn('child=', stdout)
        pid = int(stdout.strip().split('=')[1])
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                child = psutil.Process(pid)
                if not child.is_running() or child.status() == psutil.STATUS_ZOMBIE:
                    break
            except psutil.NoSuchProcess:
                break
            time.sleep(0.05)
        else:
            child.kill()
            self.fail('Timeout left a descendant running')
        suite = ET.parse(self.report_dir / 'unit-results.xml').getroot()
        self.assertEqual(suite.get('errors'), '1')

    def testDefaultFileTimeoutIs180Seconds(self) -> None:
        self.assertEqual(
            inspect
            .signature(unit_runner._runFile)
            .parameters['timeout_seconds']
            .default,
            180,
        )


if __name__ == '__main__':
    unittest.main()
