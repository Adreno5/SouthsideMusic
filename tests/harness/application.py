from __future__ import annotations

import faulthandler
import json
import logging
import os
import runpy
import socket
import sys
import threading
import traceback
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


def _clearProjectModules() -> None:
    for name in tuple(sys.modules):
        if name.partition('.')[0] in {'core', 'services', 'views', 'ncm'}:
            sys.modules.pop(name, None)


def runApplication(
    root: Path,
    address: str,
    token: str,
    media_url: str,
    output: Path,
    native: bool = False,
) -> None:
    os.chdir(root)
    _clearProjectModules()
    sys.path.insert(0, str(root / 'src'))
    sys.argv = [str(root / 'src' / 'main.py')]
    host, port = address.split(':')
    connection = socket.create_connection((host, int(port)), timeout=60)
    connection.settimeout(None)
    channel = connection.makefile('rwb')
    lock = threading.Lock()
    errors: list[str] = []
    holder: dict[str, Any] = {}
    output.mkdir(parents=True, exist_ok=True)
    stack = (output / 'threads.log').open('w', encoding='utf-8')
    faulthandler.enable(stack)
    faulthandler.dump_traceback_later(85, repeat=True, file=stack)

    def reply(value: dict[str, Any]) -> None:
        with lock:
            channel.write((json.dumps(value, ensure_ascii=False) + '\n').encode())
            channel.flush()

    reply({'token': token})
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    import shiboken6
    import sounddevice
    import core.netease_backend as backend_module
    import core.smtc as smtc_module
    import core.lyric_sources as lyric_sources
    import core.playing_manager as playing_module
    import core.llm_tools as tool_module
    import core.ws_server as ws_module
    import services.update as update_module
    import views.dependences_window as dependency_module
    import views.error_popup as error_module
    import views.main_window as window_module
    from core.config import encryptSecret, cfg
    from services.events import event_bus
    import requests
    from harness.fixtures import (
        FakeSmtc,
        FixtureBackend,
        VirtualOutputStream,
        fixtureLyrics,
        virtualDevices,
        saveFixtureState,
    )

    backend = FixtureBackend(root, media_url)
    smtc = FakeSmtc()
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_send = requests.Session.send

    def networkSend(
        self: requests.Session, request: requests.PreparedRequest, **kwargs: Any
    ) -> requests.Response:
        kwargs['timeout'] = kwargs.get('timeout') or 8
        response = original_send(self, request, **kwargs)
        if backend.online:
            response.raise_for_status()
        return response

    def checkAddress(address: Any) -> None:
        if (
            isinstance(address, tuple)
            and address[0] not in ('localhost', '127.0.0.1', '::1')
            and not backend.online
        ):
            message = 'Unexpected outbound network: ' + str(address)
            errors.append(message)
            raise OSError(message)

    def connect(self: socket.socket, address: Any) -> None:
        checkAddress(address)
        original_connect(self, address)

    def connectEx(self: socket.socket, address: Any) -> int:
        checkAddress(address)
        return original_connect_ex(self, address)

    for provider in cfg.llm_providers:
        provider['api_key_encrypted'] = encryptSecret('harness-unused')

    def snapshot() -> dict[str, Any]:
        from harness.app_cases import snapshotState

        state = snapshotState(holder['ctx'])
        state['errors'] = errors.copy()
        state['backend_records'] = backend.records.copy()
        return state

    def command(value: dict[str, Any]) -> None:
        try:
            from harness.app_cases import performAction, runCase

            operation = value.get('op')
            if operation == 'snapshot':
                result = snapshot()
            elif operation == 'run_case':
                case_output = output / 'cases' / str(value['case'])
                case_output.mkdir(parents=True, exist_ok=True)
                result = runCase(
                    holder['ctx'],
                    str(value['case']),
                    dict(value.get('data', {})),
                    case_output,
                    backend,
                    smtc,
                )
                if errors:
                    raise AssertionError('Unhandled app errors: ' + '\n'.join(errors))
            elif operation == 'capture':
                file = output / 'manual.png'
                holder['ctx'].main_window.grab().save(str(file))
                result = {'path': str(file)}
            elif operation == 'shutdown':
                saveFixtureState(root, backend)
                result = snapshot()
                reply({'ok': True, 'result': result})
                holder['ctx'].app.quit()
                return
            else:
                result = performAction(
                    holder['ctx'], str(operation), dict(value.get('data', {})), backend
                )
            reply({'ok': True, 'result': result})
        except unittest.SkipTest as error:
            reply({'ok': True, 'result': {'skipped': str(error)}})
        except BaseException:
            faulthandler.dump_traceback(file=stack)
            detail = traceback.format_exc()
            (output / 'failure.txt').write_text(detail, encoding='utf-8')
            if holder.get('ctx'):
                holder['ctx'].main_window.grab().save(str(output / 'failure.png'))
            reply({'ok': False, 'error': detail})

    def receive() -> None:
        try:
            for line in channel:
                holder['ctx'].addScheduledTask(command, json.loads(line))
        except (OSError, ValueError):
            pass

    original_init = window_module.MainWindow.__init__

    def windowInit(self: Any, *args: Any, **kwargs: Any) -> None:
        handler = logging.FileHandler(output / 'runtime.log', encoding='utf-8')
        logging.getLogger().addHandler(handler)
        original_init(self, *args, **kwargs)
        ctx = self.ctx
        holder['ctx'] = ctx
        ctx.llm.timeout = 3
        timer = QTimer(self)
        holder['ready_timer'] = timer

        def ready() -> None:
            if event_bus._lw is not None or not self.isVisible():
                return
            timer.stop()
            for provider in cfg.llm_providers:
                provider['api_key_encrypted'] = encryptSecret('harness-unused')
            self.llm_viewer_panel.refreshLLMModelBox()
            reply({'ok': True, 'result': snapshot()})
            threading.Thread(target=receive, daemon=True).start()

        timer.timeout.connect(ready)
        timer.start(50)

    original_error_init = error_module.ErrorPopupWindow.__init__

    def errorInit(self: Any, detail: str, *args: Any, **kwargs: Any) -> None:
        errors.append(str(detail))
        (output / 'app-errors.txt').write_text('\n'.join(errors), encoding='utf-8')
        original_error_init(self, detail, *args, **kwargs)
        QTimer.singleShot(0, self.reject)

    def networkCheck(self: Any) -> None:
        import requests

        try:
            response = requests.head(media_url + '/cover.png', timeout=3)
            response.raise_for_status()
            self.checkDone.emit('Network', True, 'Harness loopback HTTP')
        except requests.RequestException as error:
            self.checkDone.emit('Network', False, str(error))

    with socket.socket() as port_probe:
        port_probe.bind(('127.0.0.1', 0))
        ws_module.ws_server.port = port_probe.getsockname()[1]

    patches: list[Any] = [
        patch.object(requests.Session, 'send', networkSend),
        patch.object(socket.socket, 'connect', connect),
        patch.object(socket.socket, 'connect_ex', connectEx),
        patch.object(backend_module, 'NeteaseCloudMusicBackend', return_value=backend),
        patch.object(smtc_module, 'initAppIdentity', return_value=None),
        patch.object(lyric_sources, 'iterLyricUpdates', fixtureLyrics),
        patch.object(playing_module, 'iterLyricUpdates', fixtureLyrics),
        patch.object(tool_module, 'iterLyricUpdates', fixtureLyrics),
        patch.object(update_module, 'startUpdateCheck', return_value=None),
        patch.object(dependency_module.DependencesWindow, 'checkNetwork', networkCheck),
        patch.object(window_module.MainWindow, '__init__', windowInit),
        patch.object(error_module.ErrorPopupWindow, '__init__', errorInit),
    ]
    if not native:
        patches.extend([
            patch.object(sounddevice, 'query_devices', virtualDevices),
            patch.object(sounddevice, 'check_output_settings', return_value=None),
            patch.object(sounddevice, 'OutputStream', VirtualOutputStream),
            patch.object(smtc_module.interop, 'get_for_window', return_value=smtc),
        ])
    try:
        for item in patches:
            item.start()
        runpy.run_path(str(root / 'src' / 'main.py'), run_name='__main__')
    except BaseException:
        detail = traceback.format_exc()
        (output / 'startup-error.txt').write_text(detail, encoding='utf-8')
        try:
            reply({'ok': False, 'error': detail})
        except OSError:
            pass
        raise
    finally:
        faulthandler.cancel_dump_traceback_later()
        faulthandler.disable()
        if holder.get('ctx') and shiboken6.isValid(holder['ctx'].main_window):
            QTest.qWait(20)
        stack.close()
