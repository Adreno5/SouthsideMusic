from __future__ import annotations

import time
import base64
import asyncio
import json
import sys
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def waitUntil(
    predicate: Callable[[], bool], description: str, timeout: float = 20
) -> None:
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= end:
            raise AssertionError('Timed out waiting for ' + description)
        QTest.qWait(25)


def enterText(widget: Any, text: str) -> None:
    widget.setFocus()
    QTest.keyClick(widget, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(widget, Qt.Key.Key_Backspace)
    event = QInputMethodEvent()
    event.setCommitString(text)
    QApplication.sendEvent(widget, event)


def snapshotState(ctx: Any) -> dict[str, Any]:
    from core.favorites import favorites_manager

    pm = ctx.playing_manager
    panel = ctx.main_window.llm_viewer_panel
    return {
        'visible': ctx.main_window.isVisible(),
        'page': ctx.main_window.contents_widget.currentWidget().objectName(),
        'folders': {
            folder.folder_name: [song.id for song in folder.songs]
            for folder in favorites_manager.folders
        },
        'playlist': [song.id for song in pm.playlist],
        'song': pm.current_song.id if pm.current_song else None,
        'position': ctx.player.getPosition(),
        'messages': panel.llm_messages,
        'lyrics': ctx.mgr.cur,
        'translated_lyrics': ctx.transmgr.cur,
        'word_lyrics': ctx.ymgr.cur,
        'ws_port': ctx.ws_server.port,
    }


def performAction(ctx: Any, operation: str, data: dict[str, Any], backend: Any) -> Any:
    if operation == 'performance':
        from core.frame_profiler import frame_profiler
        from views.performances import PerformanceOverlay

        performAction(ctx, 'play', {}, backend)
        overlay = PerformanceOverlay(ctx, ctx.main_window)
        QTest.qWait(2500)
        frame_profiler.setPerformanceEnabled(True)
        frame_profiler.startCapture()
        QTest.qWait(10000)
        frames = frame_profiler.finishCapture()
        frame_profiler.setPerformanceEnabled(False)
        context = overlay._performanceReportContext(frames)
        return {'path': overlay._savePerformanceReport(frames, context)}

    if operation == 'play':
        ctx.playing_manager.setPlaylist(backend.songs[:3])
        ctx.playing_manager.playSongAtIndex(int(data.get('index', 0)))
        waitUntil(
            lambda: (
                ctx.playing_manager.current_song
                is backend.songs[int(data.get('index', 0))]
                and ctx.player.isPlaying()
                and ctx.player.getLength() > 0
            ),
            'decoded current song',
            40,
        )
    elif operation == 'search':
        enterText(ctx.main_window.search_input, str(data.get('text', 'Harness')))
        QTest.keyClick(ctx.main_window.search_input, Qt.Key.Key_Return)
        waitUntil(lambda: not ctx.search_page.searching, 'search results')
    elif operation == 'llm':
        panel = ctx.main_window.llm_viewer_panel
        if not panel.expanded:
            QTest.mouseClick(ctx.main_window.llm_viewer_btn, Qt.MouseButton.LeftButton)
            waitUntil(lambda: not panel.animating, 'assistant panel')
        provider = str(data.get('provider', 'Harness Chat'))
        index = next(
            index
            for index in range(panel.llm_model_box.count())
            if panel.llm_model_box.itemData(index)[0] == provider
        )
        panel.llm_model_box.setCurrentIndex(index)
        if data.get('clear', True):
            QTest.mouseClick(panel.llm_clear_btn, Qt.MouseButton.LeftButton)
        enterText(panel.llm_input, str(data.get('text', 'Harness test')))
        QTest.mouseClick(panel.llm_send_btn, Qt.MouseButton.LeftButton)
        if data.get('wait', True):
            waitUntil(lambda: not panel.llm_streaming, 'assistant completion', 45)
            assert panel.llm_messages and panel.llm_messages[-1]['role'] == 'assistant'
            waitUntil(lambda: not panel.llm_typing_viewers, 'rendered assistant', 20)
    elif operation == 'confirm':
        panel = ctx.main_window.llm_viewer_panel
        assert panel.llm_confirm_buttons
        button = panel.llm_confirm_buttons[int(data.get('cancel', False))]
        panel.llm_chat_scroller.ensureWidgetVisible(button)
        waitUntil(
            lambda: button.isVisible() and button.isEnabled() and button.height() > 0,
            'confirmation button',
        )
        QTest.qWait(450)
        from PySide6.QtTest import QSignalSpy

        clicked = QSignalSpy(button.clicked)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
        assert clicked.count() == 1, (
            'Confirmation click ignored',
            button.geometry(),
            button.isVisible(),
            button.isEnabled(),
        )
        waitUntil(lambda: not panel.llm_streaming, 'confirmed tools', 30)
    elif operation == 'stop':
        panel = ctx.main_window.llm_viewer_panel
        if panel.llm_streaming:
            QTest.mouseClick(panel.llm_send_btn, Qt.MouseButton.LeftButton)
    else:
        raise ValueError('Unknown action: ' + operation)
    return snapshotState(ctx)


def runCase(
    ctx: Any, case_id: str, data: dict[str, Any], output: Path, backend: Any, smtc: Any
) -> dict[str, Any]:
    from core.favorites import favorites_manager
    from services.events import Events, event_bus

    if case_id == 'boot':
        assert ctx.dependences_available
        assert ctx.main_window.isVisible()
        assert len(favorites_manager.folders[0].songs) == 6
    elif case_id.startswith('md_'):
        from harness.markdown_cases import MARKDOWN_CASES
        from views.chatting_viewer import ChattingViewer, renderMarkdown

        case = next(case for case in MARKDOWN_CASES if 'md_' + case['id'] == case_id)
        viewer = ChattingViewer()
        viewer.setParent(ctx.main_window)
        viewer.resize(400, 300)
        for name, encoded in case.get('image_files', {}).items():
            (output / name).write_bytes(base64.b64decode(encoded))
        viewer.browser.document().setBaseUrl(QUrl.fromLocalFile(str(output) + '/'))
        viewer.show()
        source = case['source']
        for offset in range(0, len(source), 7):
            viewer.appendChunk(source[offset : offset + 7])
        viewer.finishStream()
        waitUntil(lambda: viewer.toMarkdown() == source, 'Markdown stream', 20)
        waitUntil(
            lambda: not viewer._render_finish and not viewer._render_running,
            'Markdown render',
            20,
        )
        rendered = renderMarkdown(source)
        for fragment in case['html_contains']:
            assert fragment in rendered, fragment
        for fragment in case.get('html_not_contains', []):
            assert fragment not in rendered, fragment
        plain = viewer.browser.toPlainText()
        for fragment in case['plain_contains']:
            assert fragment in plain, repr((fragment, plain))
        viewer.grab().save(str(output / (case_id + '.png')))
        viewer.deleteLater()
    elif case_id in (
        'playback',
        'effects',
        'lyrics',
        'lyric_editor',
        'queue',
        'smtc',
        'bridge',
    ):
        performAction(ctx, 'play', {}, backend)
        player = ctx.player
        pm = ctx.playing_manager
        if case_id == 'playback':
            from harness.fixtures import VirtualOutputStream

            waitUntil(lambda: player.getPosition() > 0.15, 'PCM progress')
            assert VirtualOutputStream.streams
            waitUntil(
                lambda: any(
                    any(block.any() for block in stream.blocks)
                    for stream in VirtualOutputStream.streams
                ),
                'nonzero PCM',
            )
            assert all(not stream.errors for stream in VirtualOutputStream.streams)
            QTest.mouseClick(
                ctx.main_window.controller.play_pausebtn, Qt.MouseButton.LeftButton
            )
            assert not player.isPlaying()
            paused = player.getPosition()
            QTest.qWait(180)
            assert abs(player.getPosition() - paused) < 0.06
            QTest.mouseClick(
                ctx.main_window.controller.play_pausebtn, Qt.MouseButton.LeftButton
            )
            waitUntil(lambda: player.getPosition() > paused + 0.1, 'resumed audio')
            player.setPosition(3)
            assert abs(player.getPosition() - 3) < 0.2
            QTest.mouseClick(
                ctx.main_window.controller.next_btn, Qt.MouseButton.LeftButton
            )
            waitUntil(
                lambda: pm.current_song is backend.songs[1] and player.isPlaying(),
                'next song',
            )
            QTest.mouseClick(
                ctx.main_window.controller.last_btn, Qt.MouseButton.LeftButton
            )
            waitUntil(
                lambda: pm.current_song is backend.songs[0] and player.isPlaying(),
                'previous song',
            )
        elif case_id == 'queue':
            ctx.playlist_page.movePlaylistSong(backend.songs[0], 1)
            assert pm.playlist[:2] == [backend.songs[1], backend.songs[0]]
            assert pm.current_index == 1
            clicked: list[bool] = []
            timer = QTimer(ctx.main_window)

            def acceptQueue() -> None:
                for widget in QApplication.allWidgets():
                    if widget.isVisible() and hasattr(widget, 'yesButton'):
                        QTest.mouseClick(widget.yesButton, Qt.MouseButton.LeftButton)
                        clicked.append(True)
                        timer.stop()
                        return

            timer.timeout.connect(acceptQueue)
            timer.start(30)
            QTest.mouseClick(
                ctx.main_window.controller.playlist_btn, Qt.MouseButton.LeftButton
            )
            QTest.mouseClick(ctx.playlist_page.removeall_btn, Qt.MouseButton.LeftButton)
            timer.stop()
            assert clicked
            assert pm.playlist == [backend.songs[0]] and pm.current_index == 0
        elif case_id == 'effects':
            from harness.fixtures import VirtualOutputStream

            player.setVolume(0.4)
            pm.setPlaySpeed(1.15)
            pm.setPlayPitch(1.1)
            ctx.config.eq_bands = [
                (frequency, 3.0) for frequency, _ in ctx.config.eq_bands
            ]
            player.setEqualizer()
            waitUntil(lambda: player.getPosition() > 0.3, 'processed audio', 35)
            assert all(not stream.errors for stream in VirtualOutputStream.streams)
            assert player.volume_gain == 0.4
            pm.setPlaySpeed(1.0)
            pm.setPlayPitch(0.0)
            ctx.config.eq_bands = [
                (frequency, 0.0) for frequency, _ in ctx.config.eq_bands
            ]
            player.setEqualizer()
        elif case_id == 'lyrics':
            waitUntil(lambda: bool(ctx.mgr.parsed), 'parsed LRC')
            assert any('Hello' in line.content for line in ctx.mgr.parsed)
            assert ctx.transmgr.parsed
            performAction(ctx, 'play', {'index': 1}, backend)
            waitUntil(lambda: bool(ctx.ymgr.parsed), 'parsed YRC')
            assert ctx.ymgr.parsed[0].chars
            ctx.desktop_lyrics_page.onResetPos()
            assert ctx.config.desktop_lyrics_x == 0 and ctx.config.desktop_lyrics_y == 0
        elif case_id == 'lyric_editor':
            editor = ctx.lyric_editor_page
            editor.openForCurrentSong()
            ctx.main_window.contents_widget.setCurrentWidget(editor)
            waitUntil(lambda: editor.next_button.isEnabled(), 'lyric editor loaded')
            enterText(editor.editor, 'Alpha beta\nGamma delta')
            QTest.mouseClick(editor.next_button, Qt.MouseButton.LeftButton)
            assert editor.global_layout.currentWidget() is editor.beat_page
            for _ in range(4):
                QTest.keyPress(editor.beat_page, Qt.Key.Key_Space)
                QTest.qWait(90)
                QTest.keyRelease(editor.beat_page, Qt.Key.Key_Space)
                QTest.qWait(40)
            QTest.mouseClick(editor.save_button, Qt.MouseButton.LeftButton)
            waitUntil(
                lambda: (
                    'Alpha beta' in pm.current_song.getLyrics()['lyric']
                    and len(ctx.ymgr.parsed) == 2
                ),
                'saved lyrics',
            )
            saved = pm.current_song.getLyrics()
            assert 'Alpha beta' in saved['lyric'] and 'Gamma delta' in saved['lyric']
            assert saved['yrc_lyric'] and len(ctx.ymgr.parsed) == 2
            assert ctx.ymgr.parsed[0].chars[1].start > ctx.ymgr.parsed[0].chars[0].start
        elif case_id == 'smtc':
            if sys.platform != 'win32':
                raise unittest.SkipTest('SMTC is only available on Windows')
            from winrt.windows.media import SystemMediaTransportControlsButton

            waitUntil(
                lambda: (
                    smtc.display_updater.music_properties.title == pm.current_song.name
                ),
                'SMTC metadata',
            )
            assert smtc.timeline is not None
            assert smtc.button_callback is not None
            from types import SimpleNamespace

            smtc.button_callback(
                smtc, SimpleNamespace(button=SystemMediaTransportControlsButton.PAUSE)
            )
            waitUntil(lambda: not player.isPlaying(), 'SMTC pause')
            smtc.button_callback(
                smtc, SimpleNamespace(button=SystemMediaTransportControlsButton.PLAY)
            )
            waitUntil(player.isPlaying, 'SMTC play')
        else:
            result: dict[str, Any] = {}

            def bridgeClient() -> None:
                try:
                    from tornado.websocket import websocket_connect

                    async def exchange() -> None:
                        client = await websocket_connect(
                            f'ws://127.0.0.1:{ctx.ws_server.port}/', connect_timeout=12
                        )
                        await client.write_message(
                            json.dumps({
                                'option': 'bridge_hello',
                                'protocol_version': 2,
                            })
                        )
                        packets: list[dict[str, Any]] = []
                        while not any(
                            packet.get('option') == 'playlist_update'
                            for packet in packets
                        ):
                            message = await client.read_message()
                            assert message is not None, (
                                'Bridge disconnected before state'
                            )
                            packets.append(json.loads(message))
                        packet = next(
                            packet
                            for packet in packets
                            if packet.get('option') == 'playlist_update'
                        )
                        assert (
                            packet['count'] == 3
                            and packet['current_song_id'] == '90001'
                        )
                        assert packet['items'][0]['duration_ms'] == 12000
                        await client.write_message(
                            json.dumps({
                                'option': 'music_control',
                                'command': 'seek',
                                'position': 4,
                            })
                        )
                        await client.write_message(
                            json.dumps({
                                'option': 'playlist_control',
                                'action': 'play_index',
                                'index': 1,
                            })
                        )
                        result['packet'] = packet
                        client.close()

                    asyncio.run(exchange())
                except BaseException as error:
                    result['error'] = repr(error)

            thread = threading.Thread(target=bridgeClient, daemon=True)
            thread.start()
            waitUntil(lambda: not thread.is_alive(), 'WebSocket exchange', 20)
            assert 'error' not in result, result
            waitUntil(lambda: pm.current_song is backend.songs[1], 'bridge play index')
        player.pause()
        event_bus.emit(Events.PLAY_STATE_CHANGED, False)
    elif case_id == 'search_ui':
        performAction(ctx, 'search', {'text': 'Harness'}, backend)
        assert len(ctx.search_page.cards) >= 30
        ctx.search_page.search('Harness', 30)
        waitUntil(lambda: not ctx.search_page.searching, 'search pagination')
        assert ctx.search_page.curr_offset == 36
        performAction(ctx, 'search', {'text': 'no-results'}, backend)
        assert not ctx.search_page.cards
        assert not ctx.search_page.can_load_more
        ctx.search_page.search_type.setCurrentIndex(1)
        performAction(ctx, 'search', {'text': 'Harness'}, backend)
        assert len(ctx.search_page.cards) == 1
        ctx.search_page.search_type.setCurrentIndex(0)
    elif case_id == 'favorites_ui':
        folder = favorites_manager.folders[0]
        ctx.main_window._openFolder(folder)
        page = ctx.favorites_page
        enterText(page.search_input, 'Harness Song 02')
        waitUntil(lambda: page.song_viewer.count() == 1, 'favorite filter')
        enterText(page.search_input, '')
        waitUntil(lambda: page.song_viewer.count() == 6, 'favorite list')
        QTest.mouseClick(page.batch_btn, Qt.MouseButton.LeftButton)
        QTest.mouseClick(page.selectall_btn, Qt.MouseButton.LeftButton)
        assert len(page._selected_song_ids) == 6
        QTest.mouseClick(page.clear_selection_btn, Qt.MouseButton.LeftButton)
        assert not page._selected_song_ids
        QTest.mouseClick(page.batch_btn, Qt.MouseButton.LeftButton)
    elif case_id == 'settings':
        from core.config import saveConfig

        ctx.main_window.contents_widget.setCurrentWidget(ctx.setting_page)
        box = ctx.setting_page.enableFFT_box
        previous = ctx.config.enable_fft
        box.setChecked(not previous)
        assert ctx.config.enable_fft == (not previous)
        saveConfig()
        saved = json.loads(Path('config.json').read_text(encoding='utf-8'))
        assert saved['enable_fft'] == (not previous)
        box.setChecked(previous)
    elif case_id.startswith('llm_'):
        if case_id == 'llm_lyrics':
            performAction(ctx, 'play', {}, backend)
            ctx.player.pause()
        provider = {
            'llm_responses': 'Harness Responses',
            'llm_responses_tools': 'Harness Responses',
            'llm_anthropic': 'Harness Anthropic',
        }.get(case_id, 'Harness Chat')
        performAction(
            ctx,
            'llm',
            {
                'provider': provider,
                'text': data.get('prompt', case_id),
                'wait': case_id != 'llm_stop',
            },
            backend,
        )
        panel = ctx.main_window.llm_viewer_panel
        if case_id == 'llm_stop':
            waitUntil(
                lambda: panel.llm_stream_viewer is not None, 'first streamed text'
            )
            performAction(ctx, 'stop', {}, backend)
            assert not panel.llm_streaming
            messages = [message.copy() for message in panel.llm_messages]
            QTest.qWait(220)
            assert messages == panel.llm_messages
        elif case_id == 'llm_history':
            performAction(ctx, 'llm', {'clear': False, 'text': 'Follow up'}, backend)
            assert len(panel.llm_messages) == 4
            panel.editLLMMessage(0)
            enterText(panel.llm_input, 'Edited prompt')
            QTest.mouseClick(panel.llm_send_btn, Qt.MouseButton.LeftButton)
            waitUntil(lambda: not panel.llm_streaming, 'edited response')
            assert len(panel.llm_messages) == 2
            assert panel.llm_messages[0]['content'] == 'Edited prompt'
        elif case_id in ('llm_confirm', 'llm_cancel'):
            name = (
                'Harness Approved' if case_id == 'llm_confirm' else 'Harness Cancelled'
            )
            assert name not in snapshotState(ctx)['folders']
            assert panel.llm_pending_tools and panel.llm_confirm_buttons
            performAction(ctx, 'confirm', {'cancel': case_id == 'llm_cancel'}, backend)
            assert not panel.llm_pending_tools
            assert (name in snapshotState(ctx)['folders']) == (case_id == 'llm_confirm')
        elif case_id == 'llm_organize':
            assert set(snapshotState(ctx)['folders']['Harness Mix']) == {
                '90001',
                '90002',
            }
            saved = json.loads(Path('favorites.json').read_text(encoding='utf-8'))
            assert any(
                folder['folder_name'] == 'Harness Mix' and len(folder['songs']) == 2
                for folder in saved
            )
        elif case_id == 'llm_lyrics':
            waitUntil(
                lambda: (
                    'Harness edited' in ctx.mgr.cur
                    and '测试歌词' in ctx.transmgr.cur
                    and 'Harness edited' in ctx.ymgr.cur
                ),
                'edited lyrics',
            )
            assert 'Harness edited' in ctx.mgr.cur
            assert '测试歌词' in ctx.transmgr.cur
            assert 'Harness edited' in ctx.ymgr.cur
            assert (
                'Harness edited'
                in ctx.playing_manager.current_song.getLyrics()['lyric']
            )
        elif case_id in ('llm_http_error', 'llm_disconnect', 'llm_timeout'):
            assert panel.llm_messages[-1]['content'].startswith('Error:'), (
                panel.llm_messages
            )
            assert panel.llm_send_btn.isEnabled()
        else:
            assert not panel.llm_messages[-1]['content'].startswith('Error:'), (
                panel.llm_messages
            )
    elif case_id.startswith('export_') or case_id in (
        'whisper',
        'native_audio',
        'native_smtc',
    ):
        from harness.export_cases import runExportCase

        return runExportCase(ctx, case_id, output, backend)
    elif case_id == 'online_search':
        import requests
        from unittest.mock import patch
        from ncm.apis import cloudsearch
        from core.backend import getBackend

        original_search = cloudsearch.getSearchResult

        def checkedSearch(*args: Any, **kwargs: Any) -> Any:
            response = original_search(*args, **kwargs)
            if isinstance(response, dict) and response.get('code') in (
                401,
                403,
                429,
                460,
                500,
                502,
                503,
                504,
            ):
                raise unittest.SkipTest(
                    'NetEase unavailable: code=' + str(response['code'])
                )
            return response

        backend.online = True
        try:
            with patch.object(cloudsearch, 'getSearchResult', checkedSearch):
                results = getBackend().searchSong('周杰伦', limit=5)
                assert results and all(song.id and song.name for song in results)
                playlists = getBackend().searchPlaylist('周杰伦', limit=3)
                assert playlists and all(
                    folder.id and folder.folder_name for folder in playlists
                )
        except (requests.ConnectionError, requests.Timeout) as error:
            raise unittest.SkipTest('NetEase unavailable: ' + str(error)) from error
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code in (
                401,
                403,
                429,
                500,
                502,
                503,
                504,
            ):
                raise unittest.SkipTest(
                    'NetEase access unavailable: ' + str(error)
                ) from error
            raise
        finally:
            backend.online = False
    else:
        raise ValueError('Unknown case: ' + case_id)
    state = snapshotState(ctx)
    if not case_id.startswith('md_'):
        ctx.main_window.grab().save(str(output / (case_id + '.png')))
    return state
