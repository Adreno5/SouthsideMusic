from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING

import requests
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpacerItem,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    InfoBar,
    MessageBoxBase,
    ProgressBar,
    SubtitleLabel,
)

from core.audio_effect_export import AudioEffectExportProgress, renderSongWithEffects
from core.backend import getBackend
from core.cache_cleanup import touchCacheFile
from core.downloader import asyncTask
from core.i18n import tr
from core.models import SongStorable
from core.soundfile import getSongFormat, saveSongWithInformation

if TYPE_CHECKING:
    from views.main_window import MainWindow

_logger = logging.getLogger(__name__)


class SongEffectExportDialog(MessageBoxBase):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._with_effects = False

        self.title_label = SubtitleLabel(tr('song_export.title'))
        self.content_label = QLabel(tr('song_export.question'))
        self.content_label.setWordWrap(True)
        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.content_label)

        self.yesButton.setText(tr('song_export.with_effects'))
        self.cancelButton.setText(tr('song_export.original'))
        self.yesButton.clicked.connect(self._onYesClicked)

    def _onYesClicked(self) -> None:
        self._with_effects = True

    def withEffects(self) -> bool:
        return self._with_effects


class SongEffectExportProgressDialog(MessageBoxBase):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)

        self.title_label = SubtitleLabel(tr('song_export.exporting'))
        self.progress_label = QLabel(
            tr('playing_page.export_progress_percent', value=0)
        )
        self.progress_bar = ProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.time_label = CaptionLabel(
            tr('song_export.time_status', current='0', total='0')
        )
        self.eta_label = CaptionLabel(tr('playing_page.export_eta_status', value='---'))

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.progress_label)
        self.viewLayout.addWidget(self.progress_bar)
        labels_layout = QHBoxLayout()
        labels_layout.addWidget(self.time_label)
        labels_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        )
        labels_layout.addWidget(self.eta_label)
        self.viewLayout.addLayout(labels_layout)

        self.cancelButton.hide()
        self.yesButton.setEnabled(False)
        self.yesButton.setText(tr('dependences_window.ok'))

    def setProgress(self, progress: float) -> None:
        progress = max(0.0, min(1.0, progress))
        self.progress_bar.setValue(int(progress * 1000))
        self.progress_label.setText(
            tr('playing_page.export_progress_percent', value=int(progress * 100))
        )

    def setStatus(self, status: AudioEffectExportProgress) -> None:
        self.setProgress(status.progress)
        self.time_label.setText(
            tr(
                'song_export.time_status',
                current=f'{status.processed_seconds:.0f}',
                total=f'{status.total_seconds:.0f}',
            )
        )
        if status.eta_seconds is not None:
            self.eta_label.setText(
                tr('playing_page.export_eta_status', value=f'{status.eta_seconds:.0f}')
            )

    def finish(self, success: bool) -> None:
        if success:
            self.setProgress(1.0)
            self.progress_label.setText(tr('playing_page.export_complete'))
        self.yesButton.setEnabled(True)


def exportSong(
    storable: SongStorable,
    cache_path: str,
    export_path: str,
    mwindow: MainWindow,
) -> None:
    dialog = SongEffectExportDialog(mwindow)
    dialog.exec()
    with_effects = dialog.withEffects()

    progress_dialog: SongEffectExportProgressDialog | None = None
    if with_effects:
        progress_dialog = SongEffectExportProgressDialog(mwindow)
        progress_dialog.show()

    result: dict[str, str] = {}
    progress_state = {'value': -1.0}

    def _report(status: AudioEffectExportProgress) -> None:
        if progress_dialog is None:
            return
        if status.progress < 1.0 and status.progress - progress_state['value'] < 0.005:
            return
        progress_state['value'] = status.progress

        def _apply() -> None:
            progress_dialog.setStatus(status)

        mwindow.addScheduledTask(_apply)

    def _export() -> None:
        try:
            detail = getBackend().getTrackDetail(storable.id)
            image_bytes = requests.get(detail.cover_url).content

            with open(cache_path, 'rb') as song_file:
                original_bytes = song_file.read()
            touchCacheFile(cache_path)

            if with_effects:
                music_bytes = renderSongWithEffects(
                    cache_path,
                    getSongFormat(original_bytes),
                    _report,
                )
            else:
                music_bytes = original_bytes

            year = ''
            if detail.publish_time:
                year = str(
                    datetime.datetime.fromtimestamp(detail.publish_time / 1000).year
                )

            saveSongWithInformation(
                music_bytes,
                image_bytes,
                storable.name,
                storable.artists,
                export_path,
                storable.lyric,
                detail.album_name,
                '',
                year,
                f'{detail.cd}/{detail.track_no}',
                '',
                '',
            )
        except Exception as e:
            _logger.exception(e)
            result['error'] = str(e)

    def _final() -> None:
        if progress_dialog is not None:
            progress_dialog.finish(not result.get('error'))
        if result.get('error'):
            InfoBar.error(
                tr('playing_page.export_failed'),
                result['error'],
                parent=mwindow,
                duration=8000,
            )
            return
        InfoBar.success(
            tr('song_card.export'),
            tr('song_card.exported_song_song_name', song_name=storable.name),
            parent=mwindow,
            duration=5000,
        )

    asyncTask(_export, (), mwindow, _final)
