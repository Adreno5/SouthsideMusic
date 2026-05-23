from __future__ import annotations

import io
import json
import logging
import time
import zipfile
import threading
from pathlib import Path
from typing import TypedDict

import requests as raw_requests
import toml  # type: ignore[import-untyped]
from imports import (
    QMessageBox,
    event_bus,
    START_PROGRESS_LOADING,
    STOP_PROGRESS_LOADING,
    UPDATE_LOADING_PROGRESS,
)


_logger = logging.getLogger(__name__)

excludes = ['ffmpeg']
_CHECK_INTERVAL_SECONDS = 24 * 3600
_LAST_CHECK_FILE = Path('update_check_time.json')
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36'
)


class UpdateInfo(TypedDict):
    newest: int
    current: int
    download_url: str


def _should_check_updates() -> bool:
    now = time.time()
    try:
        data = json.loads(_LAST_CHECK_FILE.read_text(encoding='utf-8'))
        last_check = data.get('last_check', 0.0)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return True
    return now - last_check > _CHECK_INTERVAL_SECONDS


def _record_check_time() -> None:
    try:
        _LAST_CHECK_FILE.write_text(
            json.dumps({'last_check': time.time()}), encoding='utf-8'
        )
    except OSError:
        pass


def _read_current_version() -> int:
    parsed = toml.load(Path('pyproject.toml').read_text(encoding='utf-8'))
    return int(parsed['project']['version'].removeprefix('v'))


def _fetch_latest_release() -> dict | None:
    try:
        resp = raw_requests.get(
            'https://api.github.com/repos/Adreno5/SouthsideMusic/releases/latest',
            headers={'User-Agent': USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or 'tag_name' not in data:
            _logger.warning('Unexpected release API response: %s', data)
            return None
        return data
    except Exception as e:
        _logger.warning('Failed to fetch latest release: %s', e)
        return None


def checkForUpdates() -> UpdateInfo | None:
    if not _should_check_updates():
        return None

    current = _read_current_version()

    latest = _fetch_latest_release()
    if latest is None:
        return None

    tag_name = latest.get('tag_name', '')
    newest = int(tag_name.removeprefix('v'))
    if newest <= current:
        _record_check_time()
        return None

    zipball_url = latest.get('zipball_url')
    if not isinstance(zipball_url, str) or not zipball_url:
        _record_check_time()
        return None

    _record_check_time()
    return UpdateInfo(current=current, newest=newest, download_url=zipball_url)


def applyUpdate(update_info: UpdateInfo) -> bool:
    try:
        event_bus.emit(START_PROGRESS_LOADING)
        event_bus.emit(UPDATE_LOADING_PROGRESS, 0.0)

        response = raw_requests.get(
            update_info['download_url'],
            headers={'User-Agent': USER_AGENT, 'Accept': 'application/zip,*/*'},
            stream=True,
            timeout=180,
        )
        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        raw_data = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            raw_data.extend(chunk)
            downloaded += len(chunk)
            if total_size > 0:
                event_bus.emit(UPDATE_LOADING_PROGRESS, 0.5 * downloaded / total_size)

        event_bus.emit(UPDATE_LOADING_PROGRESS, 0.5)

        with zipfile.ZipFile(io.BytesIO(bytes(raw_data))) as zf:
            namelist = zf.namelist()
            if not namelist:
                raise ValueError('Empty archive')

            first_entry = namelist[0]
            if '/' not in first_entry:
                raise ValueError(f'Unexpected archive structure: {first_entry}')
            root_prefix = first_entry.split('/')[0] + '/'
            file_names = [n for n in namelist if not n.endswith('/')]
            total_files = max(1, len(file_names))

            for i, name in enumerate(file_names):
                relative = name[len(root_prefix) :]
                parts = Path(relative).parts
                if parts and parts[0] in excludes:
                    continue
                target = Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))
                event_bus.emit(
                    UPDATE_LOADING_PROGRESS, 0.5 + 0.5 * (i + 1) / total_files
                )

        _record_check_time()
        event_bus.emit(UPDATE_LOADING_PROGRESS, 1.0)
        event_bus.emit(STOP_PROGRESS_LOADING)
        return True

    except Exception as e:
        event_bus.emit(STOP_PROGRESS_LOADING)
        _logger.exception(e)
        return False


def startUpdateCheck(mwindow=None) -> None:
    def _check():
        try:
            update_result = checkForUpdates()
        except Exception as e:
            _logger.exception(e)
            return
        if update_result is None:
            return
        applyUpdateImmediately(update_result, mwindow)

    threading.Thread(target=_check, daemon=True).start()


def applyUpdateImmediately(update_info: UpdateInfo, mwindow=None) -> None:
    success = applyUpdate(update_info)

    def _show_result():
        if success:
            QMessageBox.information(
                mwindow,
                'Update Complete',
                'Update completed. Please restart the app.',
                QMessageBox.StandardButton.Ok,
            )
            if mwindow:
                mwindow.close()
        else:
            QMessageBox.warning(
                mwindow,
                'Update Failed',
                'Failed to update. Please try again later.',
                QMessageBox.StandardButton.Ok,
            )

    if mwindow:
        mwindow.addScheduledTask(_show_result)
