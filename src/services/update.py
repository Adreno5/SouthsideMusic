from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
import zipfile
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote as _url_quote

import requests
from imports import (
    MessageBox,
    event_bus,
    START_PROGRESS_LOADING,
    STOP_PROGRESS_LOADING,
    UPDATE_LOADING_PROGRESS,
    tr,
)

if TYPE_CHECKING:
    from views.main_window import MainWindow

_logger = logging.getLogger(__name__)

_FILE = Path('update.json')
_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36'
)
_REPO_OWNER = 'Adreno5'
_REPO_NAME = 'SouthsideMusic'


class UpdateInfo:
    tag_name: str
    published_at: str

    def __init__(self, tag_name: str, published_at: str) -> None:
        self.tag_name = tag_name
        self.published_at = published_at

    def __eq__(self, another: UpdateInfo):
        return (
            self.tag_name == another.tag_name
            and self.published_at == another.published_at
        )


def _read_installed_published_at() -> str | None:
    try:
        data = json.loads(_FILE.read_text(encoding='utf-8'))
        return data.get('published_at')
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return None


def _write_installed_release(published_at: str, tag_name: str) -> None:
    try:
        _FILE.write_text(
            json.dumps({'published_at': published_at, 'tag_name': tag_name}),
            encoding='utf-8',
        )
    except OSError:
        pass


def _parse_iso_timestamp(s: str) -> datetime | None:
    for suffix in ('Z', '+00:00', '-00:00'):
        if s.endswith(suffix):
            s = s[: -len(suffix)] + '+00:00'
            break
    else:
        s = s + '+00:00'
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _fetch_latest_release() -> dict | None:
    try:
        resp = requests.get(
            f'https://api.github.com/repos/{_REPO_OWNER}/{_REPO_NAME}/releases/latest',
            headers={'User-Agent': _USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if (
            not isinstance(data, dict)
            or 'tag_name' not in data
            or 'published_at' not in data
        ):
            _logger.warning('Unexpected release API response: %s', data)
            return None
        return data
    except Exception as e:
        _logger.warning('Failed to fetch latest release: %s', e)
        return None


def checkForUpdates() -> UpdateInfo | None:
    latest = _fetch_latest_release()
    if latest is None:
        return None

    published_at: str = latest.get('published_at', '')
    tag_name: str = latest.get('tag_name', '')

    latest_dt = _parse_iso_timestamp(published_at)
    if latest_dt is None:
        _logger.warning('Could not parse published_at: %s', published_at)
        return None

    installed_ts = _read_installed_published_at()
    if installed_ts is None:
        _write_installed_release(published_at, tag_name)
        return None

    installed_dt = _parse_iso_timestamp(installed_ts)
    if installed_dt is not None and latest_dt <= installed_dt:
        return None

    return UpdateInfo(tag_name=tag_name, published_at=published_at)


def _build_codeload_url(tag_name: str) -> str:
    encoded = _url_quote(tag_name, safe='')
    return (
        f'https://codeload.github.com/{_REPO_OWNER}/{_REPO_NAME}'
        f'/zip/refs/tags/{encoded}'
    )


def init_file():
    _FILE.write_text(json.dumps({}), encoding='utf-8')


def applyUpdate(update_info: UpdateInfo) -> bool:
    try:
        event_bus.emit(START_PROGRESS_LOADING)
        event_bus.emit(UPDATE_LOADING_PROGRESS, 0.0)

        download_url = _build_codeload_url(update_info.tag_name)

        response = requests.get(
            download_url,
            headers={'User-Agent': _USER_AGENT, 'Accept': 'application/zip,*/*'},
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
                target = Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))
                event_bus.emit(
                    UPDATE_LOADING_PROGRESS, 0.5 + 0.5 * (i + 1) / total_files
                )

        _write_installed_release(update_info.published_at, update_info.tag_name)
        event_bus.emit(UPDATE_LOADING_PROGRESS, 1.0)
        event_bus.emit(STOP_PROGRESS_LOADING)
        return True

    except Exception as e:
        event_bus.emit(STOP_PROGRESS_LOADING)
        _logger.exception(e)
        return False


def startUpdateCheck(mwindow: MainWindow) -> None:
    if not _FILE.is_file():
        init_file()

    def _check():
        try:
            update_result = checkForUpdates()
        except Exception as e:
            _logger.exception(e)
            return
        if update_result is None:
            return

        mwindow.ctx.addScheduledTask(lambda: _checked(update_result))

    def _checked(update_result: UpdateInfo):
        dialog = MessageBox(
            tr('update.update_available'),
            tr('update.version_available', tag_name=update_result.tag_name),
            mwindow,
        )
        dialog.cancelButton.setText(tr('update.skip'))
        dialog.yesButton.setText(tr('update.update'))
        if dialog.exec():
            applyUpdateImmediately(update_result, mwindow)
        else:
            _write_installed_release(update_result.published_at, update_result.tag_name)

    _logger.info('starting update check')
    threading.Thread(target=_check, daemon=True).start()


def applyUpdateImmediately(update_info: UpdateInfo, mwindow=None) -> None:
    success = applyUpdate(update_info)

    def _show_result():
        if success:
            dialog = MessageBox(
                tr('update.update_complete'),
                tr('update.update_completed_restart'),
                mwindow,
            )
            dialog.cancelButton.hide()
            dialog.yesButton.setText(tr('dependences_window.ok'))
            dialog.exec()
            _restart_app()
        else:
            dialog = MessageBox(
                tr('update.update_failed'),
                tr('update.failed_try_again_later'),
                mwindow,
            )
            dialog.cancelButton.hide()
            dialog.yesButton.setText(tr('dependences_window.ok'))
            dialog.exec()

    if mwindow:
        mwindow.ctx.addScheduledTask(_show_result)
    else:
        _show_result()


def _restart_app() -> None:
    args = [sys.executable] + sys.argv
    if sys.platform == 'win32':
        subprocess.Popen(
            args,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
            close_fds=True,
        )
    else:
        subprocess.Popen(args, start_new_session=True, close_fds=True)
    os._exit(0)
