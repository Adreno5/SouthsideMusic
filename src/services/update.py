from __future__ import annotations

import hashlib
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TypedDict

import toml  # type: ignore[import-untyped]
from imports import QMessageBox

from utils import requests_util as requests
from utils.config_util import cfg

UPDATE_SRC_URL = "https://api.github.com/repos/Adreno5/SouthsideMusic/contents/src"
UPDATE_PYPROJECT_URL = (
    "https://api.github.com/repos/Adreno5/SouthsideMusic/contents/pyproject.toml"
)


class UpdateFileInfo(TypedDict):
    path: str
    download_url: str
    sha: str


class UpdateInfo(TypedDict):
    newest: int
    current: int
    files: list[UpdateFileInfo]


def _git_blob_sha(path: str) -> str | None:
    file = Path(path)
    if not file.exists() or not file.is_file():
        return None
    data = file.read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _collect_update_files(api_url: str, file_list: list[UpdateFileInfo]) -> None:
    items = requests.get(api_url).json()
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "file":
            download_url = item.get("download_url")
            path = item.get("path")
            sha = item.get("sha")
            if (
                isinstance(download_url, str)
                and isinstance(path, str)
                and isinstance(sha, str)
            ):
                file_list.append(
                    UpdateFileInfo(path=path, download_url=download_url, sha=sha)
                )
        elif item.get("type") == "dir" and isinstance(item.get("url"), str):
            _collect_update_files(item["url"], file_list)


def checkForUpdates() -> UpdateInfo | None:
    with open("pyproject.toml", "r", encoding="utf-8") as f:
        parsed = toml.load(f)
    current = int(parsed["project"]["version"].removeprefix("v"))
    latest = requests.get(
        "https://api.github.com/repos/Adreno5/SouthsideMusic/releases/latest"
    ).json()
    newest = int(latest["tag_name"].removeprefix("v"))
    if newest <= current:
        return None

    file_list: list[UpdateFileInfo] = []
    _collect_update_files(UPDATE_SRC_URL, file_list)
    pyproject_info = requests.get(UPDATE_PYPROJECT_URL).json()
    if isinstance(pyproject_info, dict):
        path = pyproject_info.get("path")
        download_url = pyproject_info.get("download_url")
        sha = pyproject_info.get("sha")
        if (
            isinstance(path, str)
            and isinstance(download_url, str)
            and isinstance(sha, str)
        ):
            file_list.append(
                UpdateFileInfo(path=path, download_url=download_url, sha=sha)
            )

    changed_files = [
        item for item in file_list if _git_blob_sha(item["path"]) != item["sha"]
    ]
    if not changed_files:
        return None
    return UpdateInfo(current=current, newest=newest, files=changed_files)


def applyUpdate(update_info: UpdateInfo) -> bool:
    try:
        total = max(1, len(update_info["files"]))
        completed = 0
        progress_lock = threading.Lock()
        cfg.progress = 0
        cfg.progress_inter = False
        cfg.show_progress = True

        def _download_file(item: UpdateFileInfo) -> None:
            nonlocal completed
            if _git_blob_sha(item["path"]) == item["sha"]:
                with progress_lock:
                    completed += 1
                    cfg.progress = completed / total
                return
            data = requests.get(item["download_url"]).content
            path = Path(item["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            with progress_lock:
                completed += 1
                cfg.progress = completed / total

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(_download_file, item) for item in update_info["files"]
            ]
            for future in as_completed(futures):
                future.result()

        cfg.progress = 1
        cfg.show_progress = False
        return True
    except Exception as e:
        cfg.show_progress = False
        logging.exception(e)
        return False


def startUpdateCheck(mwindow=None) -> None:
    def _check():
        try:
            update_result = checkForUpdates()
        except Exception as e:
            logging.exception(e)
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
                "Update Complete",
                "Update completed. Please restart the app.",
                QMessageBox.StandardButton.Ok,
            )
            if mwindow:
                mwindow.close()
        else:
            QMessageBox.warning(
                mwindow,
                "Update Failed",
                "Failed to update. Please try again later.",
                QMessageBox.StandardButton.Ok,
            )

    if mwindow:
        mwindow.addScheduledTask(_show_result)
