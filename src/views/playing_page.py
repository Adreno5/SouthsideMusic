from __future__ import annotations

from collections import deque
import logging

import os

from core import theme
from core.app_context import AppContext
from core.color import mixColor
from core.config import cfg
from core.config import saveConfig
from core.downloader import asyncTask
from core.free_threaded_worker import jsonBase64Bytes
from core.icons import bindIcon
from core.lyric_video_export import (
    LyricVideoExportOptions,
    LyricVideoExportProgress,
    LyricVideoSources,
    exportLyricVideo,
)
from core.models import MUSIC_DATA_DIR
from core.models import SongStorable
from core.theme import isDark
from core.playing_manager import PlayMode
from imports import (
    BACKGROUND_RATIO_CHANGED,
    PLAY_STATE_CHANGED,
    PLAY_START_PLAYLIST,
    PLAYBACK_ERROR,
    PLAYBACK_IMAGE_LOADED,
    PLAYBACK_LYRICS_UPDATED,
    PLAYBACK_SONG_LOADING,
    POST_PLAY_STORABLE,
    POST_THEME_CHANGED,
    SONG_CHANGED,
    START_PROGRESS_LOADING,
    STOP_PROGRESS_LOADING,
    UPDATE_COVER,
    UPDATE_LOADING_PROGRESS,
    QColorDialog,
    QBuffer,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QIODevice,
    QLabel,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QRect,
    QResizeEvent,
    QSizePolicy,
    QSpinBox,
    QSpacerItem,
    Qt,
    QVBoxLayout,
    QWidget,
    event_bus,
    tr,
)
from imports import QColor, QImage, QPixmap
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    IndeterminateProgressRing,
    InfoBar,
    MessageBoxBase,
    ProgressBar,
    PushButton,
    Slider,
    SubtitleLabel,
    PillToolButton,
)
from views.lyrics_viewer import LyricsViewer
from views.song_card import DummyCard


def _artists_text(song: SongStorable) -> str:
    return '、'.join([artist.name for artist in song.artists])


def _lyric_video_default_path(song: SongStorable, ext: str) -> str:
    artists_text = _artists_text(song)
    if artists_text:
        return f'./{song.name} - {artists_text} lyrics{ext}'
    return f'./{song.name} lyrics{ext}'


class LyricVideoExportDialog(MessageBoxBase):
    def __init__(self, parent: QWidget, has_translation: bool) -> None:
        super().__init__(parent)
        self._background_color = self._colorFromConfig(
            cfg.lyric_video_export_background_color
        )

        self.title_label = SubtitleLabel(tr('playing_page.export_lyric_video'))
        self.viewLayout.addWidget(self.title_label)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)

        self.type_box = ComboBox()
        for label, ext in (
            ('MP4 (.mp4)', '.mp4'),
            ('AV1 (.av1)', '.av1'),
            ('Matroska (.mkv)', '.mkv'),
            ('WebM (.webm)', '.webm'),
        ):
            self.type_box.addItem(label, userData=ext)
        self._setComboData(self.type_box, cfg.lyric_video_export_ext)
        form.addRow(QLabel(tr('playing_page.export_video_type')), self.type_box)

        self.bitrate_box = QSpinBox()
        self.bitrate_box.setRange(100, 100000)
        self.bitrate_box.setSingleStep(500)
        self.bitrate_box.setValue(int(cfg.lyric_video_export_bitrate_kbps))
        self.bitrate_box.setSuffix(' kbps')
        form.addRow(QLabel(tr('playing_page.export_video_bitrate')), self.bitrate_box)

        self.display_line_count_box = QSpinBox()
        self.display_line_count_box.setRange(1, 21)
        self.display_line_count_box.setSingleStep(2)
        self.display_line_count_box.setValue(
            self._normalizedLineCount(cfg.lyric_video_export_display_line_count)
        )
        self.display_line_count_box.valueChanged.connect(self._ensureOddLineCount)
        form.addRow(
            QLabel(tr('playing_page.export_display_line_count')),
            self.display_line_count_box,
        )

        self.fps_slider = Slider(Qt.Orientation.Horizontal)
        self.fps_slider.setRange(10, 360)
        self.fps_slider.setSingleStep(10)
        self.fps_slider.setPageStep(10)
        self.fps_slider.wheelEvent = lambda e: e.ignore()
        self.fps_slider.setValue(self._normalizedFps(cfg.lyric_video_export_fps))
        self.fps_value_label = QLabel(
            tr('playing_page.export_fps_status', value=self.fps_slider.value())
        )
        self.fps_value_label.setMinimumWidth(90)
        self.fps_slider.valueChanged.connect(self._updateFpsValueLabel)

        fps_layout = QHBoxLayout()
        fps_layout.setContentsMargins(0, 0, 0, 0)
        fps_layout.setSpacing(8)
        fps_layout.addWidget(self.fps_slider)
        fps_layout.addWidget(self.fps_value_label)
        fps_container = QWidget()
        fps_container.setLayout(fps_layout)
        form.addRow(QLabel(tr('playing_page.export_fps')), fps_container)

        self.word_box = CheckBox(tr('playing_page.export_word_by_word'))
        self.word_box.setChecked(cfg.lyric_video_export_word_by_word)
        form.addRow(self.word_box)

        self.pure_color_box = CheckBox(tr('playing_page.export_pure_color'))
        self.pure_color_box.setChecked(cfg.lyric_video_export_pure_color)
        form.addRow(self.pure_color_box)

        self.translation_box = CheckBox(tr('playing_page.export_with_translation'))
        self.translation_box.setChecked(
            has_translation and cfg.lyric_video_export_with_translation
        )
        self.translation_box.setEnabled(has_translation)
        form.addRow(self.translation_box)

        self.align_box = ComboBox()
        self.align_box.addItem(
            tr('playing_page.export_align_center'), userData='center'
        )
        self.align_box.addItem(tr('playing_page.export_align_left'), userData='left')
        self.align_box.addItem(tr('playing_page.export_align_right'), userData='right')
        self._setComboData(self.align_box, cfg.lyric_video_export_alignment)
        form.addRow(QLabel(tr('playing_page.export_alignment')), self.align_box)

        self.background_button = PushButton()
        self.background_button.clicked.connect(self._chooseBackgroundColor)
        self._refreshBackgroundButton()
        form.addRow(
            QLabel(tr('playing_page.export_background_color')),
            self.background_button,
        )

        self.audio_box = CheckBox(tr('playing_page.export_with_audio'))
        self.audio_box.setChecked(cfg.lyric_video_export_with_audio)
        form.addRow(self.audio_box)

        self.scroll_box = CheckBox(tr('playing_page.export_scroll_animation'))
        self.scroll_box.setChecked(cfg.lyric_video_export_scroll_animation)
        form.addRow(self.scroll_box)

        self.viewLayout.addLayout(form)
        self.yesButton.setText(tr('playing_page.export'))

    def selectedExt(self) -> str:
        return str(self.type_box.currentData() or '.mp4')

    def options(self) -> LyricVideoExportOptions:
        alignment = str(self.align_box.currentData() or 'center')
        if alignment not in ('left', 'center', 'right'):
            alignment = 'center'
        return LyricVideoExportOptions(
            video_ext=self.selectedExt(),
            video_bitrate_kbps=int(self.bitrate_box.value()),
            fps=int(self.fps_slider.value()),
            display_line_count=int(self.display_line_count_box.value()),
            word_by_word=self.word_box.isChecked(),
            pure_color=self.pure_color_box.isChecked(),
            with_translation=self.translation_box.isChecked(),
            alignment=alignment,  # type: ignore[arg-type]
            background_color=QColor(self._background_color),
            with_audio=self.audio_box.isChecked(),
            scroll_animation=self.scroll_box.isChecked(),
            x_axis_animation=True,
        )

    def _ensureOddLineCount(self, value: int) -> None:
        if value % 2 == 1:
            return
        fixed_value = (
            value + 1 if value < self.display_line_count_box.maximum() else value - 1
        )
        self.display_line_count_box.blockSignals(True)
        self.display_line_count_box.setValue(fixed_value)
        self.display_line_count_box.blockSignals(False)

    def _updateFpsValueLabel(self, value: int) -> None:
        self.fps_value_label.setText(tr('playing_page.export_fps_status', value=value))

    def _normalizedFps(self, value: int) -> int:
        value = max(10, min(360, int(value)))
        return round(value / 10) * 10

    def saveOptionsToConfig(self) -> None:
        options = self.options()
        cfg.lyric_video_export_ext = options.video_ext
        cfg.lyric_video_export_bitrate_kbps = options.video_bitrate_kbps
        cfg.lyric_video_export_fps = options.fps
        cfg.lyric_video_export_display_line_count = options.display_line_count
        cfg.lyric_video_export_word_by_word = options.word_by_word
        cfg.lyric_video_export_pure_color = options.pure_color
        cfg.lyric_video_export_with_translation = options.with_translation
        cfg.lyric_video_export_alignment = options.alignment
        cfg.lyric_video_export_background_color = self._backgroundColorHex()
        cfg.lyric_video_export_with_audio = options.with_audio
        cfg.lyric_video_export_scroll_animation = options.scroll_animation
        saveConfig()

    def _backgroundColorHex(self) -> str:
        return '#{0:02X}{1:02X}{2:02X}'.format(
            self._background_color.red(),
            self._background_color.green(),
            self._background_color.blue(),
        )

    def _colorFromConfig(self, text: str) -> QColor:
        color = QColor(text)
        if color.isValid():
            return color
        return QColor(0, 177, 64)

    def _normalizedLineCount(self, value: int) -> int:
        value = max(1, min(21, int(value)))
        if value % 2 == 0:
            value += 1 if value < 21 else -1
        return value

    def _setComboData(self, combo_box: ComboBox, data: str) -> None:
        for index in range(combo_box.count()):
            if combo_box.itemData(index) == data:
                combo_box.setCurrentIndex(index)
                return

    def _chooseBackgroundColor(self) -> None:
        color = QColorDialog.getColor(
            self._background_color,
            self,
            tr('playing_page.export_background_color'),
        )
        if not color.isValid():
            return
        self._background_color = color
        self._refreshBackgroundButton()

    def _refreshBackgroundButton(self) -> None:
        text = self._backgroundColorHex()
        self.background_button.setText(text)
        self.background_button.setStyleSheet(
            'PushButton { background: %s; color: %s; }'
            % (text, 'white' if self._backgroundIsDark() else 'black')
        )

    def _backgroundIsDark(self) -> bool:
        color = self._background_color
        luminance = color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114
        return luminance < 128


class LyricVideoExportProgressDialog(MessageBoxBase):
    def __init__(
        self,
        parent: QWidget,
        title_key: str = 'playing_page.exporting_lyric_video',
        show_preview: bool = True,
    ) -> None:
        super().__init__(parent)
        self._show_preview = show_preview
        self.title_label = SubtitleLabel(tr(title_key))
        self.progress_label = QLabel(
            tr('playing_page.export_progress_percent', value=0)
        )
        self.progress_bar = ProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.frame_label = CaptionLabel(
            tr('playing_page.export_frame_status', current=0, total=0)
        )
        self.fps_label = CaptionLabel(tr('playing_page.export_fps_status', value='0.0'))
        self.eta_label = CaptionLabel(tr('playing_page.export_eta_status', value='---'))
        self.preview_label = QLabel()
        self.preview_label.setFixedSize(360, 203)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet('QLabel { background: black; }')

        self.eta_datas: deque[float] = deque(maxlen=10)

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.progress_label)
        self.viewLayout.addWidget(self.progress_bar)
        labels_layout = QHBoxLayout()
        labels_layout.addWidget(self.frame_label)
        labels_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        )
        labels_layout.addWidget(self.fps_label)
        labels_layout.addSpacerItem(
            QSpacerItem(15, 0, QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        )
        labels_layout.addWidget(self.eta_label)
        self.viewLayout.addLayout(labels_layout)
        if show_preview:
            self.viewLayout.addWidget(self.preview_label)
        self.cancelButton.hide()
        self.yesButton.setEnabled(False)
        self.yesButton.setText(tr('dependences_window.ok'))

    def setProgress(self, progress: float) -> None:
        progress = max(0.0, min(1.0, progress))
        self.progress_bar.setValue(int(progress * 1000))
        self.progress_label.setText(
            tr('playing_page.export_progress_percent', value=int(progress * 100))
        )

    def setStatus(self, status: LyricVideoExportProgress) -> None:
        self.setProgress(status.progress)
        self.frame_label.setText(
            tr(
                'playing_page.export_frame_status',
                current=status.current_frame,
                total=status.frame_count,
            )
        )
        self.fps_label.setText(
            tr('playing_page.export_fps_status', value=f'{status.fps:.1f}')
        )
        self.eta_datas.append(status.fps)
        if len(self.eta_datas) >= (self.eta_datas.maxlen or 1):
            average_fps = sum(list(self.eta_datas)) / len(self.eta_datas)
            if average_fps > 0:
                value = max(
                    0, (status.frame_count - status.current_frame) / average_fps
                )
                self.eta_label.setText(
                    tr('playing_page.export_eta_status', value=f'{value:.0f}')
                )
        if not self._show_preview or status.preview_image is None:
            return
        pixmap = QPixmap.fromImage(status.preview_image)
        self.preview_label.setPixmap(
            pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def finish(self, success: bool) -> None:
        if success:
            self.setProgress(1.0)
            self.progress_label.setText(tr('playing_page.export_complete'))
        self.yesButton.setEnabled(True)


class PlayingPage(QWidget):
    def __init__(
        self,
        ctx: AppContext,
    ) -> None:
        super().__init__()
        self._logger = logging.getLogger(__name__)
        if ctx.launch_window:
            ctx.launch_window.subtitle('Initializing playing page...')
            self._lw = ctx.launch_window
        else:
            self._lw = None
        self.ctx = ctx
        self._app = ctx.app
        self._mgr = ctx.mgr
        self._transmgr = ctx.transmgr
        self._ymgr = ctx.ymgr
        self._ws_handler = ctx.ws_handler

        self.playing_manager = ctx.playing_manager

        self.setObjectName('studio_page')
        self.cur: DummyCard | None = None

        lw = self._lw
        if lw:
            lw.subtitle('  Building player UI...')
        global_layout = QHBoxLayout()

        contents_layout = QVBoxLayout()

        ali = Qt.AlignmentFlag

        top_layout = QVBoxLayout()
        topleft_layout = QVBoxLayout()
        topleft_widget = QWidget()
        topleft_widget.setLayout(topleft_layout)
        self.img_label = QLabel()
        self.img_label.hide()
        self.img_label.setFixedSize(200, 200)
        self.ring = IndeterminateProgressRing()
        self.ring.setFixedSize(195, 195)
        self.ring.hide()
        top_layout.addWidget(self.ring)
        top_layout.addWidget(self.img_label)
        self.title_label = SubtitleLabel()
        self.artists_label = QLabel()
        topleft_layout.addWidget(
            self.title_label, alignment=ali.AlignLeft | ali.AlignTop
        )
        topleft_layout.addWidget(
            self.artists_label, alignment=ali.AlignLeft | ali.AlignTop
        )
        topleft_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        )
        self.artists_label.setWordWrap(True)
        self.title_label.setWordWrap(True)
        top_layout.addWidget(topleft_widget)

        contents_widget = QWidget()
        contents_layout.addLayout(top_layout)

        contents_widget.setLayout(contents_layout)
        global_layout.addWidget(contents_widget, stretch=-1)
        if lw:
            lw.subtitle('  Creating lyrics viewer...')
        self.viewer = LyricsViewer(ctx)
        global_layout.addWidget(self.viewer, stretch=2)

        self.setLayout(global_layout)

        self.bg_color = QColor(0, 0, 0) if isDark() else QColor(255, 255, 255)

        self.translation_button = PillToolButton(self)
        self.translation_button.hide()
        self.translation_button.toggled.connect(self.translationToggled)
        self.translation_button.setChecked(cfg.show_translation)
        self.translation_button.setFixedSize(32, 32)
        bindIcon(self.translation_button, 'translation')

        self.lyric_video_export_button = PillToolButton(self)
        self.lyric_video_export_button.hide()
        self.lyric_video_export_button.setFixedSize(32, 32)
        bindIcon(self.lyric_video_export_button, 'export')
        self.lyric_video_export_button.clicked.connect(self.exportLyricVideo)

        self.lyric_editor_button = PillToolButton(self)
        self.lyric_editor_button.hide()
        self.lyric_editor_button.setFixedSize(32, 32)
        bindIcon(self.lyric_editor_button, 'edit')
        self.lyric_editor_button.clicked.connect(self.editLyrics)

        self.comments_button = PillToolButton(self)
        self.comments_button.setFixedSize(32, 32)
        bindIcon(self.comments_button, 'comment')
        self.comments_button.clicked.connect(self.viewComments)

        event_bus.subscribe(PLAYBACK_SONG_LOADING, self._onPlaybackSongLoading)
        event_bus.subscribe(PLAYBACK_IMAGE_LOADED, self._onPlaybackImageLoaded)
        event_bus.subscribe(PLAYBACK_LYRICS_UPDATED, self._onPlaybackLyricsUpdated)
        event_bus.subscribe(PLAYBACK_ERROR, self._onPlaybackError)
        event_bus.subscribe(POST_PLAY_STORABLE, self._onPostPlayStorable)
        event_bus.subscribe(SONG_CHANGED, self._updateDatas)
        event_bus.subscribe(POST_THEME_CHANGED, self._updateDatas)
        event_bus.subscribe(BACKGROUND_RATIO_CHANGED, self._updateDatas)

    @property
    def _mwindow_obj(self):
        return self.ctx.main_window

    @property
    def playlist(self) -> list[SongStorable]:
        return self.playing_manager.playlist

    @playlist.setter
    def playlist(self, value: list[SongStorable]) -> None:
        self.playing_manager.setPlaylist(value)

    @property
    def current_index(self) -> int:
        return self.playing_manager.current_index

    @current_index.setter
    def current_index(self, value: int) -> None:
        self.playing_manager.setCurrentIndex(value)

    @property
    def play_mode(self) -> PlayMode:
        return self.playing_manager.play_mode

    @property
    def total_length(self) -> float:
        return self.playing_manager.total_length

    @property
    def preloaded(self) -> bool:
        return self.playing_manager.preloaded

    @property
    def _preload_triggered(self) -> bool:
        return self.playing_manager._preload_triggered

    @_preload_triggered.setter
    def _preload_triggered(self, value: bool) -> None:
        self.playing_manager._preload_triggered = value

    def translationToggled(self, state: bool):
        self.ctx.config.show_translation = state

    def _updateDatas(self, song: SongStorable | None = None) -> None:
        self.bg_color = mixColor(
            QColor(40, 40, 40) if theme.isDark() else QColor(230, 230, 230),
            self._mwindow_obj.song_theme
            if self._mwindow_obj.song_theme
            else QColor(0, 0, 0),
            1 - cfg.background_ratio * 0.5,
        )
        if song is not None:
            self.cur = DummyCard(song)
            self.title_label.setText(song.name)
            self.artists_label.setText(_artists_text(song))
        self.translation_button.setVisible(bool(song and song.translated_lyric))
        self.lyric_video_export_button.setVisible(self.cur is not None)
        self.lyric_editor_button.setVisible(self.cur is not None)

        self.update()

    def _onSwitchPage(self, interface: QWidget) -> None:
        if interface is not self:
            return

        event_bus.emit(
            UPDATE_COVER,
            self.img_label.pixmap(),
            self.cur.info.name if self.cur else '',
        )

    def onNosoundSkipChanged(self, state: Qt.CheckState) -> None:
        cfg.skip_nosound = state == Qt.CheckState.Checked

    def onPlayButtonClicked(self) -> None:
        if self.cur is None:
            event_bus.emit(PLAY_START_PLAYLIST)

    def exportLyricVideo(self) -> None:
        if self.cur is None:
            InfoBar.warning(
                tr('playing_page.export_lyric_video'),
                tr('playing_page.no_song_to_export'),
                parent=self._mwindow_obj,
                duration=3000,
            )
            return

        self.lyric_video_export_button.setChecked(False)

        song = self.cur.storable
        has_translation = bool(song.translated_lyric or self._transmgr.parsed)
        dialog = LyricVideoExportDialog(self._mwindow_obj, has_translation)
        if not dialog.exec():
            return

        options = dialog.options()
        dialog.saveOptionsToConfig()
        export_path, _fmt = QFileDialog.getSaveFileName(
            self._mwindow_obj,
            tr('playing_page.export_lyric_video'),
            _lyric_video_default_path(song, options.video_ext),
            tr('playing_page.lyric_video_files'),
        )
        if not export_path:
            return

        if not self.playing_manager.ensureAssets(
            song,
            lambda song=song, options=options, export_path=export_path: (
                self._startLyricVideoExport(song, options, export_path)
            ),
        ):
            return

        self._startLyricVideoExport(song, options, export_path)

    def _startLyricVideoExport(
        self,
        song: SongStorable,
        options: LyricVideoExportOptions,
        export_path: str,
    ) -> None:
        result: dict[str, str] = {}
        if self.ctx.player.isPlaying():
            self.ctx.player.pause()
            event_bus.emit(PLAY_STATE_CHANGED, False)

        progress_dialog = LyricVideoExportProgressDialog(self._mwindow_obj)
        self._lyric_video_progress_dialog = progress_dialog
        progress_dialog.show()
        dialog_state = {
            'stage': 'render',
            'render': progress_dialog,
            'merge': None,
        }
        progress_state = {'render': -1.0, 'merge': -1.0}

        def _update_progress(status: LyricVideoExportProgress) -> None:
            progress = max(0.0, min(1.0, status.progress))
            has_preview = status.preview_image is not None
            stage = status.stage
            if (
                progress < 1.0
                and not has_preview
                and progress - progress_state.get(stage, -1.0) < 0.005
            ):
                return
            progress_state[stage] = progress

            def _apply() -> None:
                if status.stage == 'merge' and dialog_state['merge'] is None:
                    render_dialog = dialog_state['render']
                    if isinstance(render_dialog, LyricVideoExportProgressDialog):
                        render_dialog.finish(True)
                        render_dialog.hide()
                    merge_dialog = LyricVideoExportProgressDialog(
                        self._mwindow_obj,
                        'playing_page.merging_lyric_video',
                        show_preview=False,
                    )
                    dialog_state['merge'] = merge_dialog
                    dialog_state['stage'] = 'merge'
                    self._lyric_video_progress_dialog = merge_dialog
                    merge_dialog.show()

                current_dialog = dialog_state.get(status.stage)
                if not isinstance(current_dialog, LyricVideoExportProgressDialog):
                    current_dialog = progress_dialog
                global_progress = (
                    0.95 + progress * 0.05
                    if status.stage == 'merge'
                    else min(progress, 0.95)
                )
                event_bus.emit(UPDATE_LOADING_PROGRESS, global_progress)
                current_dialog.setStatus(status)

            self.ctx.addScheduledTask(_apply)

        def _export() -> None:
            try:
                sources = self._lyricVideoSources(song)
                exportLyricVideo(
                    sources,
                    options,
                    export_path,
                    _update_progress,
                )
                result['path'] = export_path
            except Exception as e:
                self._logger.exception(e)
                result['error'] = str(e)

        def _final() -> None:
            event_bus.emit(STOP_PROGRESS_LOADING)
            active_dialog = dialog_state.get(str(dialog_state['stage']))
            if not isinstance(active_dialog, LyricVideoExportProgressDialog):
                active_dialog = progress_dialog
            active_dialog.finish(not result.get('error'))
            if result.get('error'):
                InfoBar.error(
                    tr('playing_page.export_failed'),
                    result['error'],
                    parent=self._mwindow_obj,
                    duration=8000,
                )
                return
            InfoBar.success(
                tr('playing_page.export_lyric_video'),
                tr(
                    'playing_page.exported_lyric_video_song_name',
                    song_name=song.name,
                ),
                parent=self._mwindow_obj,
                duration=5000,
            )

        event_bus.emit(UPDATE_LOADING_PROGRESS, 0)
        event_bus.emit(START_PROGRESS_LOADING)
        asyncTask(_export, (), self._mwindow_obj, _final)

    def editLyrics(self) -> None:
        if self.cur is None:
            InfoBar.warning(
                tr('lyric_editor.edit_lyrics'),
                tr('lyric_editor.no_editable_song'),
                parent=self._mwindow_obj,
                duration=3000,
            )
            return

        self.lyric_editor_button.setChecked(False)
        editor_page = self.ctx.lyric_editor_page
        if not editor_page.openForCurrentSong():
            return
        if self._mwindow_obj.dp_expanded:
            self._mwindow_obj.togglePlayingPageExpand()
        self._mwindow_obj.contents_widget.setCurrentWidget(editor_page)

    def viewComments(self):
        self.comments_button.setChecked(False)

        if self._mwindow_obj.dp_expanded:
            self._mwindow_obj.togglePlayingPageExpand()
        self._mwindow_obj.contents_widget.setCurrentWidget(self.ctx.comments_page)
        self.ctx.comments_page.page = 1
        self.ctx.comments_page.cursor = '-1'
        self.ctx.comments_page.loadComments()

    def _lyricVideoSources(self, song: SongStorable) -> LyricVideoSources:
        lyrics = song.getLyrics()
        lyric = self._mgr.cur or lyrics['lyric'] or '[00:00.000]'
        translated_lyric = self._transmgr.cur or lyrics['translated_lyric']
        yrc_lyric = self._ymgr.cur or lyrics['yrc_lyric']
        audio_path = os.path.join(MUSIC_DATA_DIR, song.content_cache_hash)
        duration = max(
            self.playing_manager.getDisplayLength(),
            song.duration / 1000 if song.duration > 0 else 0.0,
            1.0,
        )
        return LyricVideoSources(
            lyric=lyric,
            translated_lyric=translated_lyric,
            yrc_lyric=yrc_lyric,
            audio_path=audio_path,
            duration=duration,
            font_family=self.ctx.harmony_font_family,
            theme_color=self._mwindow_obj.song_theme,
            is_dark=theme.isDark(),
            refresh_rate=float(getattr(self.viewer, 'refresh_rate', 60.0)),
            lyrics_smooth_factor=cfg.lyrics_smooth_factor,
            acceleration_smooth_factor=cfg.acceleration_smooth_factor,
            background_ratio=cfg.background_ratio,
        )

    @staticmethod
    def patchedPaintEvent(card: CardWidget, e) -> None:
        from PySide6.QtGui import QPainter, QPainterPath
        from qfluentwidgets import isDarkTheme

        painter = QPainter(card)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)

        w, h = card.width(), card.height()
        r = card.getBorderRadius()
        d = 2 * r

        isDark = isDarkTheme()

        path = QPainterPath()
        path.arcMoveTo(1, h - d - 1, d, d, 240)
        path.arcTo(1, h - d - 1, d, d, 225, -60)
        path.lineTo(1, r)
        path.arcTo(1, 1, d, d, -180, -90)
        path.lineTo(w - r, 1)
        path.arcTo(w - d - 1, 1, d, d, 90, -90)
        path.lineTo(w - 1, h - r)
        path.arcTo(w - d - 1, h - d - 1, d, d, 0, -60)

        topBorderColor = QColor(0, 0, 0, 0)
        if isDark:
            topBorderColor = QColor(255, 255, 255, 11)
            if card.isPressed:
                topBorderColor = QColor(255, 255, 255, 34)
            elif card.isHover:
                topBorderColor = QColor(255, 255, 255, 30)
        else:
            topBorderColor = QColor(0, 0, 0, 28)

        painter.strokePath(path, topBorderColor)

        path = QPainterPath()
        path.arcMoveTo(1, h - d - 1, d, d, 240)
        path.arcTo(1, h - d - 1, d, d, 240, 30)
        path.lineTo(w - r - 1, h - 1)
        path.arcTo(w - d - 1, h - d - 1, d, d, 270, 30)

        bottomBorderColor = topBorderColor
        if not isDark and card.isHover and not card.isPressed:
            bottomBorderColor = QColor(0, 0, 0, 27)

        painter.strokePath(path, bottomBorderColor)

        painter.setPen(Qt.PenStyle.NoPen)
        rect = card.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(card.backgroundColor)
        painter.drawRoundedRect(rect, r, r)
        painter.end()

    def _onPlaybackSongLoading(self, song: SongStorable) -> None:
        for label in self.findChildren(QLabel):
            label.setWordWrap(True)

        self.cur = DummyCard(song)
        self.title_label.setText(song.name)
        self.artists_label.setText(_artists_text(song))
        self.lyric_video_export_button.setVisible(True)
        self.lyric_editor_button.setVisible(True)

        self._mgr.cur = ''
        self._transmgr.cur = ''
        self._ymgr.cur = ''
        self._mgr.parse()
        self._transmgr.parse()
        self._ymgr.parse()
        self.viewer.prewarmFontMetrics()

        self.img_label.hide()
        self.ring.show()
        self._app.processEvents()

    def _onPlaybackImageLoaded(
        self,
        song: SongStorable,
        image_bytes: bytes,
        avg_color: list[int] | tuple[int, int, int] | None = None,
    ) -> None:
        qimg = QImage()
        qimg.loadFromData(image_bytes)
        if qimg.isNull():
            return

        pixmap = QPixmap.fromImage(qimg)
        scaled = pixmap.scaled(
            self.img_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.img_label.setPixmap(scaled)
        self.img_label.show()
        self.ring.hide()

        if avg_color is None:
            avg_color = [128, 128, 128]
        self._mwindow_obj.song_theme = QColor(
            int(avg_color[0]), int(avg_color[1]), int(avg_color[2])
        )
        self._mwindow_obj.update()
        event_bus.emit(POST_THEME_CHANGED)

    def _onPostPlayStorable(self, song: SongStorable) -> None:
        if self.cur is not None and self.cur.storable.id != song.id:
            return
        self.sendSongCoverAndInfo()

    def _onPlaybackLyricsUpdated(self, song: SongStorable) -> None:
        if self.cur is not None and self.cur.storable.id != song.id:
            return
        self.translation_button.setVisible(bool(song.translated_lyric))
        self.lyric_video_export_button.setVisible(True)
        self.lyric_editor_button.setVisible(True)
        self.viewer.prewarmFontMetrics()

    def _onPlaybackError(self, title: str, message: str) -> None:
        self.ring.hide()
        if title == tr('playing_manager.warning'):
            InfoBar.warning(title, message, parent=self._mwindow_obj)
        else:
            InfoBar.error(title, message, parent=self._mwindow_obj)

    def sendSongCoverAndInfo(self) -> None:
        if not self._ws_handler.is_open:
            return
        if self.cur is None:
            return
        if not isinstance(self.cur, DummyCard):
            return

        pixmap = self.img_label.pixmap()
        if pixmap is None or pixmap.isNull():
            return

        pixmap = pixmap.scaled(pixmap.size(), Qt.AspectRatioMode.KeepAspectRatio)

        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, 'PNG')
        img_bytes = buffer.data().data()
        buffer.close()

        song_name = self.cur.storable.name
        position = self.playing_manager.getDisplayPosition()
        duration = self.playing_manager.getDisplayLength()
        translation_enabled = bool(cfg.show_translation)
        use_yrc = self._ymgr.hasYrcTiming()
        artists = _artists_text(self.cur.storable)
        is_playing = self.ctx.player.isPlaying()
        self._ws_handler.sendJsonFactory(
            lambda img_bytes=img_bytes, song_name=song_name, position=position, duration=duration, translation_enabled=translation_enabled, use_yrc=use_yrc, artists=artists, is_playing=is_playing: {
                'option': 'cover',
                'image': jsonBase64Bytes(img_bytes), # type: ignore
                'song_name': song_name,
                'position': position,
                'duration': duration,
                'translation_enabled': translation_enabled,
                'use_yrc': use_yrc,
                'artists': artists,
                'is_playing': is_playing,
            },
            coalesce_key='cover',
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        self.update()
        button_x = 15
        translation_y = self.height() - 15 - self.translation_button.height()
        self.translation_button.move(button_x, translation_y)
        self.lyric_video_export_button.move(
            button_x,
            translation_y - 7 - self.lyric_video_export_button.height(),
        )
        self.lyric_editor_button.move(
            button_x,
            translation_y
            - 14
            - self.lyric_video_export_button.height()
            - self.lyric_editor_button.height(),
        )
        self.comments_button.move(
            button_x,
            translation_y
            - 24
            - self.lyric_video_export_button.height()
            - self.lyric_editor_button.height()
            - self.comments_button.height(),
        )
        return super().resizeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.bg_color)
        r = self.rect()
        painter.drawRoundedRect(r, 10, 10)
        painter.drawRect(QRect(r.x() + 10, r.y(), r.width() - 10, r.height()))
        painter.drawRect(QRect(r.x(), r.y() + 10, r.width(), r.height() - 10))
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._mwindow_obj.dp_animating and not self.viewer.hovering:
            self._mwindow_obj.togglePlayingPageExpand()
        return super().mousePressEvent(event)
