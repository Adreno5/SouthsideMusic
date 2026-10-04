from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
import time

from core.app_context import AppContext
from core.lyrics import LyricInfo, YRCCharInfo, YRCLyricInfo
from core.models import SongStorable
from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QKeyEvent, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPlainTextEdit,
    QSizePolicy,
    QSpacerItem,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)
from core.i18n import tr
from services.events import PLAYBACK_LYRICS_UPDATED, PLAY_STATE_CHANGED, event_bus
from services.events.events import _200MS_TICK, _50MS_TICK
from qfluentwidgets import CaptionLabel, InfoBar, PrimaryPushButton, PushButton
from views.lyrics_viewer import LyricsViewer


_LRC_TIME_PREFIX_RE = re.compile(r'^(?:\[\d+:\d+[.:]\d+\])+')
_YRC_LINE_RE = re.compile(r'^\[\d+,\d+\](.*)$')
_YRC_CHAR_RE = re.compile(r'\(\d+,\d+,-?\d+\)([^()]*)')
_TOKEN_RE = re.compile(
    r'[\u4e00-\u9fff]|[^\s\u4e00-\u9fff]+',
    re.UNICODE,
)

_HOLD_STEP_SECONDS = 0.35


class ManualLyricsViewer(LyricsViewer):
    def __init__(self, ctx: AppContext) -> None:
        self.preview_position = 0.0
        super().__init__(ctx)

    def setPreviewPosition(self, position: float) -> None:
        self.preview_position = max(0.0, position)
        self.updateDatas()

    def _viewPosition(self) -> float:
        return self.preview_position

    def updateDatas(self, multiple_factor: float = 1.0) -> None:
        self._updateViewLayout(multiple_factor)
        self.update()


@dataclass
class LyricTokenTiming:
    global_index: int
    line_index: int
    token_index: int
    text: str
    start: float = 0.0
    duration: float = 0.0


def _plainTextFromLyric(lyric: str) -> str:
    lines: list[str] = []
    for raw_line in lyric.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r'^\[(?:by|ar|al|ti|offset|length|re|ve):', line):
            continue
        if _isJsonMetadataLine(line):
            continue

        yrc_match = _YRC_LINE_RE.match(line)
        if yrc_match:
            text = ''.join(_YRC_CHAR_RE.findall(yrc_match.group(1))).strip()
            if text:
                lines.append(text)
            continue

        line = _LRC_TIME_PREFIX_RE.sub('', line).strip()
        if line:
            lines.append(line)
    return '\n'.join(lines)


def _textLinesFromLyric(lyric: str) -> list[str]:
    return [line.strip() for line in _plainTextFromLyric(lyric).splitlines()]


def _formatLrcTime(seconds: float) -> str:
    seconds = max(0.0, seconds)
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    whole_seconds = int(rest)
    ms = int(round((rest - whole_seconds) * 1000))
    if ms >= 1000:
        whole_seconds += 1
        ms -= 1000
    return f'[{minutes:02d}:{whole_seconds:02d}.{ms:03d}]'


def _safeYrcText(text: str) -> str:
    return text.replace('(', '[').replace(')', ']')


def _isJsonMetadataLine(line: str) -> bool:
    if not line.startswith('{'):
        return False
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and 't' in obj and 'c' in obj


class LyricEditorPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self.ctx = ctx
        self._song: SongStorable | None = None
        self._lyrics_cache: dict[str, str] = {}
        self._translated_lines: list[str] = []
        self._ytlrc_lines: list[str] = []
        self._token_lines: list[list[LyricTokenTiming]] = []
        self._flat_tokens: list[LyricTokenTiming] = []
        self._token_source_lines: list[str] = []
        self._next_token_index = 0
        self._manual_position = 0.0
        self._last_press_at = 0.0
        self._current_yrc = ''
        self._started_beat = False
        self._finished_beat = False
        self._space_press_started_at = 0.0
        self._status_ticking = False
        self._finish_ticking = False

        self.setObjectName('lyric_editor_page')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.global_layout = QStackedLayout()
        self.edit_page = QWidget()
        self.beat_page = QWidget()
        self.beat_page.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.global_layout.addWidget(self.edit_page)
        self.global_layout.addWidget(self.beat_page)
        self.setLayout(self.global_layout)

        self._buildEditPage()
        self._buildBeatPage()

    def _setStatusTicking(self, ticking: bool) -> None:
        if ticking == self._status_ticking:
            return
        self._status_ticking = ticking
        if ticking:
            event_bus.subscribe(_50MS_TICK, self._updateHoldingStatus)
        else:
            event_bus.unsubscribe(_50MS_TICK, self._updateHoldingStatus)

    def _setFinishTicking(self, ticking: bool) -> None:
        if ticking == self._finish_ticking:
            return
        self._finish_ticking = ticking
        if ticking:
            event_bus.subscribe(_200MS_TICK, self._checkBeatFinished)
        else:
            event_bus.unsubscribe(_200MS_TICK, self._checkBeatFinished)

    def openForCurrentSong(self) -> bool:
        song = self._currentSong()
        if song is None:
            InfoBar.warning(
                tr('lyric_editor.edit_lyrics'),
                tr('lyric_editor.no_editable_song'),
                parent=self.ctx.main_window,
                duration=3000,
            )
            return False

        self._song = song
        self._loadLyricsCache(song)
        self._resetBeatState()
        self._current_yrc = ''
        self.editor.setPlainText(self._initialLyricText(song))
        self.global_layout.setCurrentWidget(self.edit_page)
        return True

    def nextStep(self) -> None:
        if self._song is None:
            return
        self.ctx.player.setPosition(0)
        self.ctx.player.pause()
        event_bus.emit(PLAY_STATE_CHANGED, False)
        self._started_beat = False
        self._finished_beat = False
        self._manual_position = 0.0
        self._last_press_at = 0.0
        self._setBeatActionsVisible(False)
        self._setStatusText('lyric_editor.beat_ready')
        self._syncPreview()
        if self.ctx.main_window and getattr(self.ctx.main_window, 'controller', None):
            self.ctx.main_window.controller.hideLyrics()
        self.global_layout.setCurrentWidget(self.beat_page)
        self.beat_page.setFocus(Qt.FocusReason.OtherFocusReason)

    def backToEdit(self) -> None:
        self._setFinishTicking(False)
        self._setStatusTicking(False)
        self._setBeatActionsVisible(False)
        self.ctx.player.pause()
        event_bus.emit(PLAY_STATE_CHANGED, False)
        if self.ctx.main_window and getattr(self.ctx.main_window, 'controller', None):
            self.ctx.main_window.controller.showLyrics()
        self.global_layout.setCurrentWidget(self.edit_page)

    def saveLyrics(self) -> None:
        song = self._song or self._currentSong()
        if song is None:
            return
        try:
            if not self._persistLyrics(finalize=True, emit_update=True):
                return
            if self.ctx.main_window and getattr(
                self.ctx.main_window, 'controller', None
            ):
                self.ctx.main_window.controller.showLyrics()
            InfoBar.success(
                tr('lyric_editor.edit_lyrics'),
                tr('lyric_editor.save_success'),
                parent=self.ctx.main_window,
                duration=3000,
            )
        except Exception as e:
            self._logger.exception(e)
            InfoBar.error(
                tr('lyric_editor.edit_lyrics'),
                tr('lyric_editor.save_failed'),
                parent=self.ctx.main_window,
                duration=5000,
            )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            self.global_layout.currentWidget() is self.beat_page
            and event.key() == Qt.Key.Key_Space
        ):
            self._startSpaceBeat(event)
            event.accept()
            return
        return super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if (
            self.global_layout.currentWidget() is self.beat_page
            and event.key() == Qt.Key.Key_Space
        ):
            if event.isAutoRepeat():
                event.accept()
                return
            self._stopSpaceBeat()
            event.accept()
            return
        return super().keyReleaseEvent(event)

    def _buildEditPage(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(tr('lyric_editor.empty_lyrics'))
        layout.addWidget(self.editor, 1)

        bottom_layout = QHBoxLayout()
        bottom_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        )
        self.next_button = PrimaryPushButton(tr('lyric_editor.next_step'))
        self.next_button.clicked.connect(self.nextStep)
        bottom_layout.addWidget(self.next_button)
        layout.addLayout(bottom_layout)

        self.edit_page.setLayout(layout)

    def _buildBeatPage(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.viewer = ManualLyricsViewer(self.ctx)
        layout.addWidget(self.viewer, 1)

        bottom_layout = QHBoxLayout()
        self.status_label = CaptionLabel(tr('lyric_editor.beat_ready'))
        bottom_layout.addWidget(self.status_label)
        bottom_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        )
        self.back_button = PushButton(tr('lyric_editor.rewrite_lyrics'))
        self.retry_button = PushButton(tr('lyric_editor.retry_beats'))
        self.save_button = PrimaryPushButton(tr('lyric_editor.save'))
        self.back_button.clicked.connect(self.backToEdit)
        self.retry_button.clicked.connect(self.retryBeats)
        self.save_button.clicked.connect(self.saveLyrics)
        bottom_layout.addWidget(self.back_button)
        bottom_layout.addWidget(self.retry_button)
        bottom_layout.addWidget(self.save_button)
        layout.addLayout(bottom_layout)
        self._setBeatActionsVisible(False)

        self.beat_page.setLayout(layout)
        for widget in (
            self.beat_page,
            self.viewer,
            self.back_button,
            self.retry_button,
            self.save_button,
        ):
            widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            self.global_layout.currentWidget() is self.beat_page
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Space
        ):
            self._startSpaceBeat(event)
            return True
        if (
            self.global_layout.currentWidget() is self.beat_page
            and event.type() == QEvent.Type.KeyRelease
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Space
        ):
            if event.isAutoRepeat():
                return True
            self._stopSpaceBeat()
            return True
        return super().eventFilter(watched, event)

    def _currentSong(self) -> SongStorable | None:
        song = self.ctx.playing_manager.current_song
        if song is not None:
            return song
        cur = self.ctx.playing_page.cur
        if cur is not None:
            return cur.storable
        return None

    def _loadLyricsCache(self, song: SongStorable) -> None:
        self._lyrics_cache = song.getLyrics()
        translated_source = self._lyrics_cache.get('translated_lyric', '')
        ytlrc_source = self._lyrics_cache.get('ytlrc_lyric', '')
        transmgr_source = self.ctx.transmgr.cur
        self._translated_lines = _textLinesFromLyric(translated_source)
        self._ytlrc_lines = _textLinesFromLyric(ytlrc_source)
        if (
            not self._translated_lines
            and not self._ytlrc_lines
            and transmgr_source
            and transmgr_source != '[00:00.000]'
        ):
            self._translated_lines = _textLinesFromLyric(transmgr_source)

    def _initialLyricText(self, song: SongStorable) -> str:
        if self.ctx.mgr.cur and self.ctx.mgr.cur != '[00:00.000]':
            return _plainTextFromLyric(self.ctx.mgr.cur)
        if not self._lyrics_cache:
            self._loadLyricsCache(song)
        return _plainTextFromLyric(self._lyrics_cache.get('lyric', ''))

    def _lyricLines(self) -> list[str]:
        return [
            line.strip()
            for line in self.editor.toPlainText().splitlines()
            if line.strip()
        ]

    def _resetBeatState(self) -> None:
        self._setStatusTicking(False)
        self._setFinishTicking(False)
        self._token_lines.clear()
        self._flat_tokens.clear()
        self._token_source_lines.clear()
        self._next_token_index = 0
        self._manual_position = 0.0
        self._last_press_at = 0.0
        self._started_beat = False
        self._finished_beat = False
        self._space_press_started_at = 0.0

    def _ensureTokenTimings(self) -> None:
        lines = self._lyricLines()
        if self._token_source_lines == lines:
            return

        self._resetBeatState()
        for line_index, line in enumerate(lines):
            token_line: list[LyricTokenTiming] = []
            for token_index, token in enumerate(self._tokenizeLine(line)):
                timing = LyricTokenTiming(
                    len(self._flat_tokens),
                    line_index,
                    token_index,
                    token,
                )
                token_line.append(timing)
                self._flat_tokens.append(timing)
            self._token_lines.append(token_line)
        self._token_source_lines = lines

    def _tokenizeLine(self, line: str) -> list[str]:
        matches = list(_TOKEN_RE.finditer(line))
        tokens: list[str] = []
        for index, match in enumerate(matches):
            next_start = (
                matches[index + 1].start() if index + 1 < len(matches) else len(line)
            )
            tokens.append(line[match.start() : next_start])
        return tokens

    def _startSpaceBeat(self, event: QKeyEvent) -> None:
        if event.isAutoRepeat() or self._finished_beat:
            return
        now = time.perf_counter()
        if not self._started_beat:
            self._started_beat = True
            self._manual_position = 0.0
            self.ctx.player.setPosition(0)
            self.ctx.player.resume()
            event_bus.emit(PLAY_STATE_CHANGED, True)
            self._setFinishTicking(True)
        else:
            self._manual_position = max(
                self._manual_position,
                self.ctx.playing_manager.getDisplayPosition(),
            )
        self._last_press_at = now
        self._space_press_started_at = time.perf_counter()
        self._setStatusTicking(True)
        self._updateHoldingStatus()
        self._recordBeat(self._manual_position)

    def _stopSpaceBeat(self) -> None:
        self._setStatusTicking(False)
        if not self._finished_beat:
            self._setStatusText('lyric_editor.beat_waiting')

    def _setStatusText(self, key: str, **kwargs: object) -> None:
        if hasattr(self, 'status_label'):
            self.status_label.setText(tr(key, **kwargs))

    def _updateHoldingStatus(self) -> None:
        if self._finished_beat:
            return
        pressed_seconds = max(0.0, time.perf_counter() - self._space_press_started_at)
        self._setStatusText(
            'lyric_editor.beat_holding',
            seconds=f'{pressed_seconds:.2f}',
        )

    def retryBeats(self) -> None:
        self.ctx.player.setPosition(0)
        self.ctx.player.pause()
        event_bus.emit(PLAY_STATE_CHANGED, False)
        self._resetBeatState()
        self._setBeatActionsVisible(False)
        self._setStatusText('lyric_editor.beat_ready')
        self._syncPreview()

    def _checkBeatFinished(self) -> None:
        if (
            self.global_layout.currentWidget() is not self.beat_page
            or not self._started_beat
            or self._finished_beat
        ):
            return
        duration = self.ctx.playing_manager.getDisplayLength()
        if duration <= 0:
            return
        position = self.ctx.playing_manager.getDisplayPosition()
        if position < max(0.0, duration - 0.2):
            return
        self._finishBeat()

    def _finishBeat(self) -> None:
        self._finished_beat = True
        self._setFinishTicking(False)
        self._setStatusTicking(False)
        self._manual_position = max(
            self._manual_position,
            self.ctx.playing_manager.getDisplayLength(),
        )
        self._persistLyrics(finalize=True, emit_update=True)
        self.viewer.setPreviewPosition(self._manual_position)
        self._setStatusText('lyric_editor.beat_finished')
        self._setBeatActionsVisible(True)
        if self.ctx.main_window and getattr(self.ctx.main_window, 'controller', None):
            self.ctx.main_window.controller.showLyrics()

    def _setBeatActionsVisible(self, visible: bool) -> None:
        for button_name in ('back_button', 'retry_button'):
            button = getattr(self, button_name, None)
            if button is not None:
                button.setVisible(visible)
        save_button = getattr(self, 'save_button', None)
        if save_button is not None:
            save_button.setVisible(True)

    def _recordBeat(self, position: float) -> bool:
        self._ensureTokenTimings()
        if not self._flat_tokens or self._next_token_index >= len(self._flat_tokens):
            return False

        position = max(0.0, position)
        if self._next_token_index > 0:
            previous = self._flat_tokens[self._next_token_index - 1]
            previous.duration = max(0.0, position - previous.start)

        token = self._flat_tokens[self._next_token_index]
        token.start = position
        token.duration = _HOLD_STEP_SECONDS
        self._next_token_index += 1
        self._syncPreview()
        return True

    def _syncPreview(self) -> None:
        self._ensureTokenTimings()
        lines = self._lyricLines()
        line_times = self._lineStartTimes(lines)
        yrc_parsed = self._buildYrcParsed()
        translated_parsed, ytlrc_parsed = self._buildAlignedTranslationsParsed(
            line_times
        )
        self._applyParsedLyricsToContext(
            self._buildLrcParsed(lines, line_times),
            translated_parsed,
            yrc_parsed,
            ytlrc_parsed,
        )
        self.viewer.setPreviewPosition(self._manual_position)

    def _refreshLyricsViews(self) -> None:
        self.viewer.prewarmFontMetrics()
        mwindow = self.ctx.main_window
        if mwindow and getattr(mwindow, 'controller', None):
            mwindow.controller.lyrics_viewer.prewarmFontMetrics()

    def _persistLyrics(self, finalize: bool, emit_update: bool) -> bool:
        song = self._song or self._currentSong()
        if song is None:
            return False

        lines = self._lyricLines()
        line_times = self._lineStartTimes(lines)
        lyric = self._buildLrc(lines, line_times)
        yrc_lyric = self._buildYrc(finalize=finalize)
        translated_lyric, ytlrc_lyric = self._buildAlignedTranslations(line_times)
        translated_lyric, ytlrc_lyric = self._preserveFallbackTranslations(
            translated_lyric,
            ytlrc_lyric,
        )
        song.writeLyrics(lyric, translated_lyric, yrc_lyric, ytlrc_lyric)
        self._lyrics_cache = song.getLyrics()
        yrc_parsed = self._buildYrcParsed(finalize=finalize)
        translated_parsed, ytlrc_parsed = self._buildAlignedTranslationsParsed(
            line_times
        )
        self._applyParsedLyricsToContext(
            self._buildLrcParsed(lines, line_times),
            translated_parsed,
            yrc_parsed,
            ytlrc_parsed,
            lyric=lyric,
            translated_lyric=translated_lyric,
            yrc_lyric=yrc_lyric,
            ytlrc_lyric=ytlrc_lyric,
        )
        self._refreshLyricsViews()
        if emit_update:
            event_bus.emit(PLAYBACK_LYRICS_UPDATED, song)
        return True

    def _applyParsedLyricsToContext(
        self,
        lyric_parsed: list[LyricInfo],
        translated_parsed: list[LyricInfo],
        yrc_parsed: list[YRCLyricInfo],
        ytlrc_parsed: list[LyricInfo],
        lyric: str | None = None,
        translated_lyric: str | None = None,
        yrc_lyric: str | None = None,
        ytlrc_lyric: str | None = None,
    ) -> None:
        self.ctx.mgr.setParsed(lyric_parsed, cur=lyric)
        self.ctx.ymgr.setParsed(yrc_parsed, cur=yrc_lyric)
        self.ctx.transmgr.setParsed(
            self._activeTranslationParsed(
                translated_parsed,
                ytlrc_parsed,
                yrc_parsed,
            ),
            cur=(
                self._activeTranslationLyric(
                    translated_lyric or '',
                    ytlrc_lyric or '',
                    yrc_lyric or '',
                )
                if (
                    translated_lyric is not None
                    or ytlrc_lyric is not None
                    or yrc_lyric is not None
                )
                else None
            ),
        )
        self.ctx.lyrics_manager.syncParsed()

    def _activeTranslationLyric(
        self,
        translated_lyric: str,
        ytlrc_lyric: str,
        yrc_lyric: str,
    ) -> str:
        if yrc_lyric and ytlrc_lyric:
            return ytlrc_lyric
        return translated_lyric or ytlrc_lyric

    def _activeTranslationParsed(
        self,
        translated_parsed: list[LyricInfo],
        ytlrc_parsed: list[LyricInfo],
        yrc_parsed: list[YRCLyricInfo],
    ) -> list[LyricInfo]:
        if yrc_parsed and ytlrc_parsed:
            return ytlrc_parsed
        return translated_parsed or ytlrc_parsed

    def _buildLrc(self, lines: list[str], line_times: list[float]) -> str:
        result: list[str] = []
        for index, line in enumerate(lines):
            time_value = line_times[index] if index < len(line_times) else 0.0
            result.append(f'{_formatLrcTime(float(time_value))}{line}')
        return '\n'.join(result) or '[00:00.000]'

    def _buildLrcParsed(
        self,
        lines: list[str],
        line_times: list[float],
    ) -> list[LyricInfo]:
        result: list[LyricInfo] = []
        for index, line in enumerate(lines):
            time_value = line_times[index] if index < len(line_times) else 0.0
            result.append(LyricInfo(time=max(0.0, float(time_value)), content=line))
        return result

    def _lineStartTimes(self, lines: list[str]) -> list[float]:
        self._ensureTokenTimings()
        result: list[float] = []
        for index, _line in enumerate(lines):
            token_line = (
                self._token_lines[index] if index < len(self._token_lines) else []
            )
            if not token_line:
                result.append(float((index + 1) * _HOLD_STEP_SECONDS))
                continue

            first_token = token_line[0]
            if first_token.global_index < self._next_token_index:
                result.append(max(0.0, first_token.start))
                continue

            _token, start, _duration = self._previewTokenTiming(
                first_token,
                finalize=False,
            )
            result.append(max(0.0, start))
        return result

    def _buildAlignedTranslations(
        self,
        line_times: list[float],
    ) -> tuple[str, str]:
        translated_lyric = self._buildAlignedLyric(
            self._translated_lines,
            line_times,
        )
        ytlrc_lyric = self._buildAlignedLyric(self._ytlrc_lines, line_times)
        if translated_lyric and not ytlrc_lyric:
            ytlrc_lyric = translated_lyric
        elif ytlrc_lyric and not translated_lyric:
            translated_lyric = ytlrc_lyric
        return translated_lyric, ytlrc_lyric

    def _buildAlignedTranslationsParsed(
        self,
        line_times: list[float],
    ) -> tuple[list[LyricInfo], list[LyricInfo]]:
        translated_parsed = self._buildAlignedParsed(
            self._translated_lines,
            line_times,
        )
        ytlrc_parsed = self._buildAlignedParsed(self._ytlrc_lines, line_times)
        if translated_parsed and not ytlrc_parsed:
            ytlrc_parsed = list(translated_parsed)
        elif ytlrc_parsed and not translated_parsed:
            translated_parsed = list(ytlrc_parsed)
        return translated_parsed, ytlrc_parsed

    def _buildAlignedLyric(
        self,
        source_lines: list[str],
        line_times: list[float],
    ) -> str:
        result: list[str] = []
        for index, source_line in enumerate(source_lines):
            if index >= len(line_times):
                break
            line = source_line.strip()
            if not line:
                continue
            result.append(f'{_formatLrcTime(line_times[index])}{line}')
        return '\n'.join(result)

    def _buildAlignedParsed(
        self,
        source_lines: list[str],
        line_times: list[float],
    ) -> list[LyricInfo]:
        result: list[LyricInfo] = []
        for index, source_line in enumerate(source_lines):
            if index >= len(line_times):
                break
            line = source_line.strip()
            if not line:
                continue
            result.append(LyricInfo(time=max(0.0, line_times[index]), content=line))
        return result

    def _preserveFallbackTranslations(
        self,
        translated_lyric: str,
        ytlrc_lyric: str,
    ) -> tuple[str, str]:
        if (
            not translated_lyric
            and not self._translated_lines
            and self._lyrics_cache.get('translated_lyric')
        ):
            translated_lyric = self._lyrics_cache.get('translated_lyric', '')
        if (
            not ytlrc_lyric
            and not self._ytlrc_lines
            and self._lyrics_cache.get('ytlrc_lyric')
        ):
            ytlrc_lyric = self._lyrics_cache.get('ytlrc_lyric', '')
        if translated_lyric and not ytlrc_lyric:
            ytlrc_lyric = translated_lyric
        elif ytlrc_lyric and not translated_lyric:
            translated_lyric = ytlrc_lyric
        return translated_lyric, ytlrc_lyric

    def _buildYrc(self, finalize: bool = False) -> str:
        if not self._flat_tokens or self._next_token_index <= 0:
            return ''

        total_duration = self._songDuration()
        result: list[str] = []
        for token_index, token in enumerate(self._flat_tokens):
            if token_index >= self._next_token_index:
                break
            if token_index + 1 < self._next_token_index:
                next_token = self._flat_tokens[token_index + 1]
                token.duration = max(0.0, next_token.start - token.start)
            elif finalize:
                token.duration = max(0.0, total_duration - token.start)

        for token_line in self._token_lines:
            if not token_line:
                continue
            if finalize and not any(
                token.global_index < self._next_token_index for token in token_line
            ):
                continue
            result.append(self._buildYrcLine(token_line, finalize))
        return '\n'.join(result)

    def _buildYrcParsed(self, finalize: bool = False) -> list[YRCLyricInfo]:
        if not self._flat_tokens or self._next_token_index <= 0:
            return []

        total_duration = self._songDuration()
        result: list[YRCLyricInfo] = []
        for token_index, token in enumerate(self._flat_tokens):
            if token_index >= self._next_token_index:
                break
            if token_index + 1 < self._next_token_index:
                next_token = self._flat_tokens[token_index + 1]
                token.duration = max(0.0, next_token.start - token.start)
            elif finalize:
                token.duration = max(0.0, total_duration - token.start)

        for token_line in self._token_lines:
            if not token_line:
                continue
            if finalize and not any(
                token.global_index < self._next_token_index for token in token_line
            ):
                continue
            line = self._buildYrcInfo(token_line, finalize)
            if line is not None:
                result.append(line)
        return result

    def _buildYrcLine(
        self,
        tokens: list[LyricTokenTiming],
        finalize: bool,
    ) -> str:
        preview_tokens = [
            self._previewTokenTiming(token, finalize)
            for token in tokens
            if not finalize or token.global_index < self._next_token_index
        ]
        if not preview_tokens:
            preview_tokens = [self._previewTokenTiming(tokens[0], finalize=False)]

        line_start = max(0, int(round(preview_tokens[0][1] * 1000)))
        last_token = preview_tokens[-1]
        line_end = max(
            preview_tokens[0][1],
            last_token[1] + max(0.0, last_token[2]),
        )
        line_duration = max(0, int(round((line_end - preview_tokens[0][1]) * 1000)))
        char_parts: list[str] = []
        for token, start, duration in preview_tokens:
            text = _safeYrcText(token.text)
            start_ms = max(0, int(round(start * 1000)))
            duration_ms = max(0, int(round(duration * 1000)))
            char_parts.append(f'({start_ms},{duration_ms},0){text}')
        return f'[{line_start},{line_duration}]{"".join(char_parts)}'

    def _buildYrcInfo(
        self,
        tokens: list[LyricTokenTiming],
        finalize: bool,
    ) -> YRCLyricInfo | None:
        preview_tokens = [
            self._previewTokenTiming(token, finalize)
            for token in tokens
            if not finalize or token.global_index < self._next_token_index
        ]
        if not preview_tokens:
            preview_tokens = [self._previewTokenTiming(tokens[0], finalize=False)]

        line_start = max(0.0, preview_tokens[0][1])
        last_token = preview_tokens[-1]
        line_end = max(
            preview_tokens[0][1],
            last_token[1] + max(0.0, last_token[2]),
        )
        chars: list[YRCCharInfo] = []
        content_builder: list[str] = []
        for token, start, duration in preview_tokens:
            text = _safeYrcText(token.text)
            if not text:
                continue
            content_builder.append(text)
            chars.append(
                YRCCharInfo(
                    start=max(0.0, start),
                    duration=max(0.0, duration),
                    char=text,
                )
            )
        content = ''.join(content_builder)
        if not content:
            return None
        return YRCLyricInfo(
            time=line_start,
            duration=max(0.0, line_end - line_start),
            content=content,
            chars=chars,
        )

    def _previewTokenTiming(
        self,
        token: LyricTokenTiming,
        finalize: bool,
    ) -> tuple[LyricTokenTiming, float, float]:
        if token.global_index < self._next_token_index:
            duration = token.duration if finalize else 0.0
            return token, token.start, duration

        if finalize:
            return token, token.start, token.duration

        if self._next_token_index <= 0:
            start = self._manual_position + token.global_index * _HOLD_STEP_SECONDS
        else:
            previous = self._flat_tokens[self._next_token_index - 1]
            base = previous.start + max(previous.duration, _HOLD_STEP_SECONDS)
            start = (
                base
                + (token.global_index - self._next_token_index) * _HOLD_STEP_SECONDS
            )
        duration = _HOLD_STEP_SECONDS if finalize else 0.0
        return token, start, duration

    def _songDuration(self) -> float:
        song = self._song
        display_length = self.ctx.playing_manager.getDisplayLength()
        storable_length = song.duration / 1000 if song and song.duration > 0 else 0.0
        return max(display_length, storable_length, 0.0)
