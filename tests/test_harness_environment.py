from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from textwrap import dedent
from typing import Any, override
from unittest.mock import Mock, patch
from urllib.request import urlopen

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import environment, session
from harness.media_server import MediaServer

_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


class HarnessEnvironmentTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix='harness-lifecycle-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'source'
        self.root.mkdir()
        for name in ('src', 'icons', 'images', 'fonts', 'scripts'):
            (self.root / name).mkdir()
            (self.root / name / 'fixture.txt').write_text(name, encoding='utf-8')
        (self.root / 'pyproject.toml').write_text('[project]\n', encoding='utf-8')
        (self.root / '.python-version').write_text('3.14\n', encoding='utf-8')
        self.binary = self.root / 'ffmpeg' / 'bin'
        self.binary.mkdir(parents=True)
        names = (
            ('ffmpeg.exe', 'ffprobe.exe') if os.name == 'nt' else ('ffmpeg', 'ffprobe')
        )
        for name in names:
            (self.binary / name).write_bytes(b'fixture')
        self.output = Path(temporary.name) / 'reports'
        self.sandboxes: list[environment.Sandbox] = []
        self.servers: list[MediaServer] = []
        self.addCleanup(self._cleanup)
        self.dependencies = patch(
            'harness.environment.findWorker', return_value=sys.executable
        )
        self.dependencies.start()
        self.addCleanup(self.dependencies.stop)
        binary_patch = patch('harness.environment.shutil.which', return_value=None)
        binary_patch.start()
        self.addCleanup(binary_patch.stop)
        media_patch = patch.object(
            environment.Sandbox,
            '_createMedia',
            autospec=True,
            side_effect=self._createMedia,
        )
        media_patch.start()
        self.addCleanup(media_patch.stop)

    def _createMedia(self, sandbox: environment.Sandbox) -> None:
        (sandbox.root / 'fixtures').mkdir()
        (sandbox.root / 'fixtures' / 'cover.png').write_bytes(b'fixture media')

    def _newSandbox(
        self, root: Path, url: str, native: bool = False
    ) -> environment.Sandbox:
        sandbox = environment.Sandbox(root, url, native)
        self.sandboxes.append(sandbox)
        return sandbox

    def _newServer(self, root: Path) -> MediaServer:
        server = MediaServer(root)
        self.servers.append(server)
        return server

    def _cleanup(self) -> None:
        for server in self.servers:
            if server.thread.is_alive():
                server.stop()
            else:
                server.server.server_close()
        for sandbox in self.sandboxes:
            sandbox.close()

    def _writeApp(
        self, startup: str = '', shutdown: str = '', action: str = ''
    ) -> None:
        source = (
            'import argparse, json, os, socket, subprocess, sys, time\n'
            'from pathlib import Path\n'
            'parser = argparse.ArgumentParser()\n'
            "for name in ('root', 'address', 'token', 'media-url', 'report-dir'):\n"
            "    parser.add_argument('--' + name, required=True)\n"
            "parser.add_argument('--native', action='store_true')\n"
            "parser.add_argument('mode')\n"
            'args = parser.parse_args()\n'
            'root = Path(args.root)\n'
            + dedent(startup)
            + '\n'
            + "host, port = args.address.split(':')\n"
            + 'connection = socket.create_connection((host, int(port)))\n'
            + "channel = connection.makefile('rwb')\n"
            + 'def reply(value: dict[str, object]) -> None:\n'
            + "    channel.write((json.dumps(value) + '\\n').encode())\n"
            + '    channel.flush()\n'
            + "reply({'token': args.token})\n"
            + "reply({'ok': True, 'result': {'visible': True}})\n"
            + 'for line in channel:\n'
            + '    value = json.loads(line)\n'
            + "    if value['op'] == 'shutdown':\n"
            + ''.join(
                '        ' + line + '\n' for line in dedent(shutdown).splitlines()
            )
            + "        reply({'ok': True, 'result': {}})\n"
            + '        break\n'
            + ''.join('    ' + line + '\n' for line in dedent(action).splitlines())
            + "    reply({'ok': True, 'result': {'value': 7}})\n"
        )
        (self.root / 'scripts' / 'test_harness.py').write_text(source, encoding='utf-8')

    def _startSession(self) -> session.ApplicationSession:
        with (
            patch('harness.session.Sandbox', side_effect=self._newSandbox),
            patch('harness.session.MediaServer', side_effect=self._newServer),
        ):
            instance = session.ApplicationSession(self.root, '', self.output)
        self.addCleanup(instance.close)
        return instance

    def _assertDead(self, pid: int) -> None:
        try:
            process = psutil.Process(pid)
            self.assertFalse(
                process.is_running() and process.status() != psutil.STATUS_ZOMBIE
            )
        except psutil.NoSuchProcess:
            pass

    def testSandboxIsolatesConfigurationAssetsAndTemporaryPaths(self) -> None:
        for name in ('data', '.mypy_cache'):
            cached = self.root / 'src' / name
            cached.mkdir()
            (cached / 'user-cache.txt').write_text('user cache', encoding='utf-8')
        (self.root / 'config.json').write_text('user settings', encoding='utf-8')
        (self.root / 'favorites.json').write_text('user favorites', encoding='utf-8')
        sandbox = self._newSandbox(self.root, '')
        self.assertNotEqual(sandbox.root, self.root)
        self.assertIn('Harness Chat', (sandbox.root / 'config.json').read_text())
        self.assertFalse((sandbox.root / 'favorites.json').exists())
        self.assertFalse((sandbox.root / 'src' / 'data').exists())
        self.assertFalse((sandbox.root / 'src' / '.mypy_cache').exists())
        self.assertEqual(Path(sandbox.environment['TEMP']).parent, sandbox.root)
        for name in ('src', 'icons', 'images', 'fonts'):
            self.assertEqual((sandbox.root / name / 'fixture.txt').read_text(), name)
        self.assertEqual((self.root / 'config.json').read_text(), 'user settings')
        sandbox.close()
        sandbox.close()
        self.assertFalse(sandbox.root.exists())

    def testMissingWorkerFailsBeforeAllocatingTemporaryRoot(self) -> None:
        with (
            patch('harness.environment.findWorker', return_value=None),
            patch(
                'harness.environment.tempfile.TemporaryDirectory',
                side_effect=AssertionError(
                    'Temporary root allocated before validation'
                ),
            ) as temporary,
        ):
            with self.assertRaisesRegex(RuntimeError, 'Free-threaded worker'):
                environment.Sandbox(self.root, '')
        temporary.assert_not_called()

    def testSandboxCopyFailureCleansAllocatedRoot(self) -> None:
        original = tempfile.TemporaryDirectory
        temporary_roots: list[Path] = []

        def allocate(*args: Any, **kwargs: Any) -> tempfile.TemporaryDirectory[str]:
            result = original(*args, **kwargs)
            temporary_roots.append(Path(result.name))
            return result

        with (
            patch(
                'harness.environment.tempfile.TemporaryDirectory', side_effect=allocate
            ),
            patch(
                'harness.environment.shutil.copytree',
                side_effect=OSError('copy failed'),
            ),
        ):
            with self.assertRaisesRegex(OSError, 'copy failed'):
                environment.Sandbox(self.root, '')
        self.assertEqual(len(temporary_roots), 1)
        self.assertFalse(temporary_roots[0].exists())

    def testWorkerProbeTimeoutAndLaunchErrorAreUnavailableDependencies(self) -> None:
        self.dependencies.stop()
        with (
            patch.object(Path, 'is_file', return_value=True),
            patch(
                'harness.environment.subprocess.run',
                side_effect=subprocess.TimeoutExpired('probe', 20),
            ),
        ):
            self.assertIsNone(environment.findWorker(self.root))
        with (
            patch.object(Path, 'is_file', return_value=True),
            patch(
                'harness.environment.subprocess.run',
                side_effect=OSError('cannot launch'),
            ),
        ):
            self.assertIsNone(environment.findWorker(self.root))

    def testPopenFailureStopsMediaClosesLogAndDeletesOwnedSandbox(self) -> None:
        self._writeApp()
        with (
            patch('harness.session.Sandbox', side_effect=self._newSandbox),
            patch('harness.session.MediaServer', side_effect=self._newServer),
            patch(
                'harness.session.subprocess.Popen', side_effect=OSError('launch failed')
            ),
        ):
            with self.assertRaisesRegex(OSError, 'launch failed'):
                session.ApplicationSession(self.root, '', self.output)
        self.assertFalse(self.sandboxes[0].root.exists())
        self.assertFalse(self.servers[0].thread.is_alive())
        self.assertEqual(self.servers[0].server.fileno(), -1)
        with (self.output / 'application.log').open('ab') as stream:
            stream.write(b'log released')

    def testExternalSandboxIsRetainedAfterLaunchFailure(self) -> None:
        sandbox = self._newSandbox(self.root, '')
        with (
            patch('harness.session.MediaServer', side_effect=self._newServer),
            patch(
                'harness.session.subprocess.Popen', side_effect=OSError('launch failed')
            ),
        ):
            with self.assertRaises(OSError):
                session.ApplicationSession(self.root, '', self.output, sandbox=sandbox)
        self.assertTrue(sandbox.root.exists())
        self.assertFalse(self.servers[0].thread.is_alive())

    def testMediaStartFailureClosesBoundServerAndSandbox(self) -> None:
        with (
            patch('harness.session.Sandbox', side_effect=self._newSandbox),
            patch('harness.session.MediaServer', side_effect=self._newServer),
            patch.object(MediaServer, 'start', side_effect=OSError('start failed')),
        ):
            with self.assertRaisesRegex(OSError, 'start failed'):
                session.ApplicationSession(self.root, '', self.output)
        self.assertFalse(self.sandboxes[0].root.exists())
        self.assertEqual(self.servers[0].server.fileno(), -1)

    def testRealServerSessionPreservesCrashLogsAndCloseIsIdempotent(self) -> None:
        self._writeApp(
            shutdown="""
            crashes = root / 'data' / 'crashes' / 'session'
            crashes.mkdir(parents=True)
            (crashes / 'report.txt').write_text('crash evidence')
            (crashes / 'native.log').write_text('native evidence')
            (root / 'application.log').write_text('sandbox runtime')
        """
        )
        instance = self._startSession()
        with urlopen(instance.media.url + '/cover.png', timeout=2) as response:
            self.assertEqual(response.read(), b'fixture media')
        self.assertEqual(instance.request('snapshot'), {'value': 7})
        with patch.object(instance.media, 'stop', wraps=instance.media.stop) as stop:
            instance.close()
            instance.close()
        stop.assert_called_once()
        self._assertDead(instance.process.pid)
        self.assertFalse(instance.sandbox.root.exists())
        self.assertEqual(
            (self.output / 'crashes' / 'session' / 'report.txt').read_text(),
            'crash evidence',
        )
        self.assertEqual(
            (self.output / 'crashes' / 'session' / 'native.log').read_text(),
            'native evidence',
        )
        self.assertEqual(
            (self.output / 'sandbox-logs' / 'application.log').read_text(),
            'sandbox runtime',
        )

    def testChildDeathBeforeHandshakeIsDetectedPromptly(self) -> None:
        self._writeApp(
            startup="print('startup failed', flush=True)\nraise SystemExit(7)\n"
        )
        original_socket = socket.socket

        class FastSocket(original_socket):
            @override
            def settimeout(self, timeout: float | None) -> None:
                super().settimeout(min(timeout, 0.2) if timeout else timeout)

        started: list[float] = []

        def newSandbox(
            root: Path, gateway_url: str, native: bool = False
        ) -> environment.Sandbox:
            sandbox = self._newSandbox(root, gateway_url, native)
            started.append(time.monotonic())
            return sandbox

        with (
            patch('harness.session.Sandbox', side_effect=newSandbox),
            patch('harness.session.MediaServer', side_effect=self._newServer),
            patch('harness.session.socket.socket', FastSocket),
        ):
            with self.assertRaisesRegex(RuntimeError, 'exit.*7'):
                session.ApplicationSession(self.root, '', self.output)
        self.assertTrue(started)
        self.assertLess(time.monotonic() - started[0], 5)
        self.assertFalse(self.sandboxes[0].root.exists())
        self.assertIn('startup failed', (self.output / 'application.log').read_text())

    def testRequestTimeoutStillCleansRealChildAndServer(self) -> None:
        self._writeApp(action='time.sleep(30)')
        instance = self._startSession()
        with self.assertRaises(TimeoutError):
            instance.request('snapshot', timeout=0.1)
        with patch.object(
            instance, 'request', side_effect=TimeoutError('shutdown blocked')
        ):
            instance.close()
        self._assertDead(instance.process.pid)
        self.assertFalse(instance.media.thread.is_alive())
        self.assertFalse(instance.sandbox.root.exists())

    def testStopProcessTreeWaitsAndKillsTerminatingSurvivors(self) -> None:
        process = Mock()
        process.pid = 123
        process.poll.return_value = 0
        child = Mock(spec=psutil.Process)
        child.pid = 321
        child.children.return_value = []
        child.is_running.return_value = True
        with (
            patch(
                'harness.environment.psutil.Process',
                side_effect=psutil.NoSuchProcess(123),
            ),
            patch(
                'harness.environment._waitSurvivors',
                side_effect=[[child], [child], []],
            ) as wait,
        ):
            environment.stopProcessTree(process, [child], timeout=0)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertGreaterEqual(wait.call_count, 3)

    def testStopProcessTreeForgetsDescendantsThatDenyAccess(self) -> None:
        process = Mock()
        process.pid = 123
        process.poll.return_value = 0
        child = Mock(spec=psutil.Process)
        child.pid = 321
        child.children.side_effect = psutil.AccessDenied(321)
        child.wait.side_effect = psutil.AccessDenied(321)
        with patch(
            'harness.environment.psutil.Process',
            side_effect=psutil.NoSuchProcess(123),
        ):
            environment.stopProcessTree(process, [child], timeout=0.5)
        child.terminate.assert_not_called()
        child.kill.assert_not_called()

    def testStopProcessTreeIgnoresDescendantsWhosePidWasReused(self) -> None:
        process = Mock()
        process.pid = 123
        process.poll.return_value = 0
        child = Mock(spec=psutil.Process)
        child.pid = 321
        child.children.return_value = []
        child.is_running.return_value = False
        with (
            patch(
                'harness.environment.psutil.Process',
                side_effect=psutil.NoSuchProcess(123),
            ),
            patch(
                'harness.environment._waitSurvivors',
                side_effect=[[child], [child], []],
            ) as wait,
        ):
            environment.stopProcessTree(process, [child], timeout=0)
        child.terminate.assert_not_called()
        child.kill.assert_not_called()
        self.assertEqual(wait.call_args.args[0], [])

    def testStopProcessTreeFindsDescendantsBehindDeadParents(self) -> None:
        script = (
            'import os, subprocess, sys, time\n'
            'print(os.getpid(), flush=True)\n'
            'time.sleep(1)\n'
            'child = subprocess.Popen('
            '[sys.executable, "-c", "import time; time.sleep(60)"])\n'
            'print(child.pid, flush=True)\n'
            'time.sleep(60)\n'
        )
        temporary = tempfile.TemporaryDirectory(prefix='harness-tree-test-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / 'child.py').write_text(script, encoding='utf-8')
        process = subprocess.Popen(
            [sys.executable, str(root / 'child.py')],
            cwd=root,
            stdout=subprocess.PIPE,
            text=True,
            creationflags=_CREATIONFLAGS,
            start_new_session=os.name != 'nt',
        )
        group = os.getpgid(process.pid) if os.name != 'nt' else None
        self.addCleanup(process.kill)
        self.addCleanup(process.stdout.close)
        intermediate = int(process.stdout.readline().strip())
        snapshot = [psutil.Process(intermediate)]
        grandchild = int(process.stdout.readline().strip())
        snapshot[0].kill()
        process.wait(timeout=30)
        time.sleep(0.3)
        environment.stopProcessTree(process, snapshot, group=group)
        self._assertDead(grandchild)
        (root / 'child.py').unlink()
        root.rmdir()

    def testShutdownCollectsLateCrashViewerAndPreservesReport(self) -> None:
        monitor = dedent("""
            import json, os, subprocess, sys, time
            from pathlib import Path
            root = Path(sys.argv[1])
            (root / 'monitor.ready').touch()
            while not (root / 'shutting-down').exists():
                time.sleep(0.01)
            time.sleep(0.2)
            viewer = subprocess.Popen(
                [sys.executable, '-c', 'import time; time.sleep(60)'],
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            (root / 'fixture-state.json').write_text(json.dumps({
                'monitor': os.getpid(), 'viewer': viewer.pid,
            }))
            crashes = root / 'data' / 'crashes' / 'late'
            crashes.mkdir(parents=True)
            (crashes / 'report.txt').write_text('late crash evidence')
            time.sleep(60)
        """)
        self._writeApp(
            startup=(
                f"(root / 'monitor.py').write_text({monitor!r})\n"
                "subprocess.Popen([sys.executable, str(root / 'monitor.py'), str(root)], "
                "creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))\n"
                "while not (root / 'monitor.ready').exists():\n"
                '    time.sleep(0.01)\n'
            ),
            shutdown="(root / 'shutting-down').touch()",
        )
        instance = self._startSession()
        instance.close()
        pids = json.loads((self.output / 'fixture-state.json').read_text())
        for pid in pids.values():
            self._assertDead(pid)
        self.assertEqual(
            (self.output / 'crashes' / 'late' / 'report.txt').read_text(),
            'late crash evidence',
        )
        self.assertFalse(instance.sandbox.root.exists())


if __name__ == '__main__':
    unittest.main()
