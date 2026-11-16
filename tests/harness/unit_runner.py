from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import FrameType
from typing import Any
from unittest.mock import patch


def _copyIgnored(directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name.lower()
        in {
            '.venv',
            'torch_venv',
            'freethreaded_venv',
            '__pycache__',
            '.git',
            '.mypy_cache',
        }
        or (Path(directory).name == 'src' and name == 'data')
        or (Path(directory) / name).is_symlink()
    }


def _stageRoot(root: Path, staged: Path) -> None:
    for name in ('src', 'tests', 'scripts', 'fonts', 'icons', 'images'):
        source = root / name
        if source.is_dir() and not source.is_symlink():
            shutil.copytree(source, staged / name, ignore=_copyIgnored)
    for pattern in (
        '*.py',
        '*requirements*.txt',
        'README*',
        'LICENSE*',
        'pyproject.toml',
        'uv.lock',
        '.python-version',
        '.ruff.toml',
        '.requirements_sha256',
        'installer.iss',
    ):
        for source in root.glob(pattern):
            if source.is_file() and not source.is_symlink():
                shutil.copy2(source, staged / source.name)
    runner = staged / 'tests' / 'harness' / 'unit_runner.py'
    runner.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).resolve(), runner)
    for name in ('data', 'temp'):
        (staged / name).mkdir(exist_ok=True)
    for name in ('ffmpeg', 'ffprobe'):
        source = root / 'ffmpeg' / 'bin' / f'{name}.exe'
        if not source.is_file():
            found = shutil.which(name)
            if found is None:
                continue
            source = Path(found)
        destination = staged / 'ffmpeg' / 'bin' / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _stopProcess(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == 'win32':
        if process.poll() is None:
            try:
                subprocess.run(
                    ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def _runFile(
    staged: Path,
    relative: Path,
    report_dir: Path,
    timeout_seconds: float = 180,
) -> dict[str, Any]:
    logs = report_dir / 'unit-logs'
    logs.mkdir(parents=True, exist_ok=True)
    stdout_path = logs / f'{relative.stem}.stdout.log'
    stderr_path = logs / f'{relative.stem}.stderr.log'
    result_path = logs / f'{relative.stem}.json'
    result_path.unlink(missing_ok=True)
    result: dict[str, Any] = {
        'name': relative.as_posix(),
        'status': 'error',
        'tests_run': 0,
        'skipped': 0,
        'returncode': None,
        'error': '',
        'stdout': stdout_path.relative_to(report_dir).as_posix(),
        'stderr': stderr_path.relative_to(report_dir).as_posix(),
    }
    env = os.environ.copy()
    env.update({
        'PYTHONPATH': os.pathsep.join(
            str(staged / name) for name in ('src', 'tests', 'scripts', '.')
        ),
        'PYTHONUTF8': '1',
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONUNBUFFERED': '1',
        'PYTHONNOUSERSITE': '1',
        'TEMP': str(staged / 'temp'),
        'TMP': str(staged / 'temp'),
        'TMPDIR': str(staged / 'temp'),
        'PATH': os.pathsep.join((str(staged / 'ffmpeg' / 'bin'), env.get('PATH', ''))),
    })
    if os.name != 'nt':
        env.setdefault('QT_QPA_PLATFORM', 'offscreen')
    process: subprocess.Popen[bytes] | None = None
    started = time.monotonic()
    try:
        with stdout_path.open('wb') as stdout, stderr_path.open('wb') as stderr:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(staged / 'tests' / 'harness' / 'unit_runner.py'),
                    '--child',
                    str(staged / relative),
                    str(result_path),
                ],
                cwd=staged,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0),
                start_new_session=os.name != 'nt',
            )
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                result['status'] = 'timeout'
                result['error'] = f'Test file exceeded {timeout_seconds:g} seconds'
            finally:
                if process.poll() is None or os.name != 'nt':
                    _stopProcess(process)
                result['returncode'] = process.returncode
        if result['status'] != 'timeout':
            if result_path.is_file():
                child = json.loads(result_path.read_text(encoding='utf-8'))
                result.update({
                    key: child[key]
                    for key in ('status', 'tests_run', 'skipped', 'error')
                })
                if process.returncode != 0:
                    result['status'] = 'failed'
                    result['error'] = result['error'] or (
                        f'Test process exited with code {process.returncode}'
                    )
                elif result['tests_run'] == 0:
                    result['status'] = 'failed'
                    result['error'] = 'No tests executed'
            else:
                result['error'] = (
                    'Test process exited without a result '
                    f'(exit code {process.returncode})'
                )
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        result['status'] = 'error'
        result['error'] = str(error)
    finally:
        if process is not None and process.poll() is None:
            _stopProcess(process)
        result['duration_seconds'] = round(time.monotonic() - started, 6)
    return result


def _writeReports(report_dir: Path, results: list[dict[str, Any]]) -> int:
    failed = sum(result['status'] != 'passed' for result in results)
    report = {
        'suite': 'unit',
        'status': 'failed' if failed else 'passed',
        'total': len(results),
        'passed': len(results) - failed,
        'failed': failed,
        'tests_run': sum(result['tests_run'] for result in results),
        'skipped': sum(result['skipped'] for result in results),
        'duration_seconds': sum(result['duration_seconds'] for result in results),
        'results': results,
    }
    (report_dir / 'unit-results.json').write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
    )
    suite = ET.Element(
        'testsuite',
        name='SouthsideMusic.unit',
        tests=str(len(results)),
        failures=str(sum(result['status'] == 'failed' for result in results)),
        errors=str(sum(result['status'] in ('error', 'timeout') for result in results)),
        time=f'{report["duration_seconds"]:.6f}',
    )
    summary = [
        '## Legacy unit suite',
        '',
        f'{report["passed"]}/{report["total"]} files passed; '
        f'{report["tests_run"]} tests/checks, {report["skipped"]} skipped.',
        '',
        '| File | Status | Tests/checks | Seconds | Detail |',
        '| --- | --- | ---: | ---: | --- |',
    ]
    for result in results:
        case = ET.SubElement(
            suite,
            'testcase',
            classname='SouthsideMusic.unit',
            name=result['name'],
            time=f'{result["duration_seconds"]:.6f}',
        )
        if result['status'] != 'passed':
            tag = 'failure' if result['status'] == 'failed' else 'error'
            ET.SubElement(case, tag, type=result['status']).text = re.sub(
                r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', result['error']
            )
        for key, tag in (('stdout', 'system-out'), ('stderr', 'system-err')):
            log = report_dir / result[key] if result.get(key) else None
            if log is not None and log.is_file():
                ET.SubElement(case, tag).text = re.sub(
                    r'[\x00-\x08\x0b\x0c\x0e-\x1f]',
                    '',
                    log.read_text(encoding='utf-8', errors='replace'),
                )
        detail = result['error'].replace('|', '\\|').replace('\n', ' ')
        summary.append(
            f'| {result["name"]} | {result["status"]} | {result["tests_run"]} '
            f'| {result["duration_seconds"]:.2f} | {detail} |'
        )
    ET.indent(suite)
    ET.ElementTree(suite).write(
        report_dir / 'unit-results.xml', encoding='utf-8', xml_declaration=True
    )
    (report_dir / 'unit-summary.md').write_text(
        '\n'.join(summary) + '\n', encoding='utf-8'
    )
    print(summary[2])
    return int(failed != 0)


def runUnitSuite(root: Path, report_dir: Path) -> int:
    root = root.resolve()
    report_dir = report_dir.resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(
        path for path in (root / 'tests').glob('test_*.py') if path.is_file()
    )
    results: list[dict[str, Any]] = []
    if files:
        try:
            with tempfile.TemporaryDirectory(prefix='southside-unit-') as directory:
                staged = Path(directory)
                _stageRoot(root, staged)
                for path in files:
                    result = _runFile(staged, path.relative_to(root), report_dir)
                    results.append(result)
                    print(f'{result["status"]}: {result["name"]}', flush=True)
        except OSError as error:
            results.append({
                'name': 'collection',
                'status': 'error',
                'tests_run': 0,
                'skipped': 0,
                'duration_seconds': 0,
                'error': str(error),
            })
    else:
        results.append({
            'name': 'collection',
            'status': 'failed',
            'tests_run': 0,
            'skipped': 0,
            'duration_seconds': 0,
            'error': 'No tests/test_*.py files collected',
        })
    return _writeReports(report_dir, results)


def _runChild(path: Path, result_path: Path) -> int:
    path = path.resolve()
    result: dict[str, Any] = {
        'status': 'passed',
        'tests_run': 0,
        'skipped': 0,
        'error': '',
    }
    plain_tests = 0
    main_called = False
    unittest_seen = False
    successful = True
    original_run = unittest.TextTestRunner.run
    original_main = sys.modules.get('__main__')

    def recordCall(frame: FrameType, event: str, argument: Any) -> None:
        nonlocal plain_tests, main_called
        code = frame.f_code
        if (
            event == 'call'
            and code.co_filename == str(path)
            and code.co_qualname == code.co_name
        ):
            if code.co_name.startswith('test_'):
                plain_tests += 1
            elif code.co_name == 'main':
                main_called = True

    def runTests(
        runner: unittest.TextTestRunner, test: unittest.TestSuite
    ) -> unittest.TestResult:
        nonlocal unittest_seen, successful
        unittest_seen = True
        outcome = original_run(runner, test)
        result['tests_run'] += outcome.testsRun
        result['skipped'] += len(outcome.skipped)
        successful = successful and outcome.wasSuccessful()
        return outcome

    try:
        sys.argv = [str(path)]
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location('__main__', path)
        if spec is None or spec.loader is None:
            raise ImportError(f'Cannot load {path}')
        module = importlib.util.module_from_spec(spec)
        sys.modules['__main__'] = module
        sys.setprofile(recordCall)
        with patch.object(unittest.TextTestRunner, 'run', runTests):
            spec.loader.exec_module(module)
            if not unittest_seen and not main_called and plain_tests == 0:
                entry = getattr(module, 'main', None)
                if callable(entry) and getattr(entry, '__module__', '') == '__main__':
                    code = entry()
                    if isinstance(code, int) and code != 0:
                        raise SystemExit(code)
                else:
                    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
                    for name, function in sorted(vars(module).items()):
                        if (
                            name.startswith('test_')
                            and inspect.isfunction(function)
                            and function.__module__ == '__main__'
                        ):
                            suite.addTest(unittest.FunctionTestCase(function))
                    unittest.TextTestRunner(verbosity=2).run(suite)
    except SystemExit as error:
        if error.code not in (None, 0):
            result['status'] = 'failed'
            result['error'] = f'Test entry point exited with code {error.code}'
            traceback.print_exc()
    except BaseException as error:
        result['status'] = 'failed' if isinstance(error, AssertionError) else 'error'
        result['error'] = f'{type(error).__name__}: {error}'
        traceback.print_exc()
    finally:
        sys.setprofile(None)
        if original_main is not None:
            sys.modules['__main__'] = original_main
        if not unittest_seen:
            result['tests_run'] = plain_tests or int(main_called)
        if not successful:
            result['status'] = 'failed'
            result['error'] = result['error'] or 'Unittest reported failures or errors'
        if result['tests_run'] == 0:
            result['status'] = 'failed'
            result['error'] = (
                f'No tests executed; {result["error"]}'
                if result['error']
                else 'No tests executed'
            )
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return int(result['status'] != 'passed')


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--child':
        raise SystemExit('Use scripts/test_harness.py unit')
    raise SystemExit(_runChild(Path(sys.argv[2]), Path(sys.argv[3])))
