from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen
import tqdm

PACKAGE = 'NeteaseCloudMusicApi'
DEFAULT_VERSION = '4.32.0'
DEFAULT_TARGET = Path('api-reference') / 'NeteaseCloudMusicApi'
RETRY_COUNT = 3
TIMEOUT_SECONDS = 30
DEFAULT_WORKERS = 15


def fetch_bytes(url: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            with urlopen(url, timeout=TIMEOUT_SECONDS) as response:
                return response.read()
        except URLError as exc:
            last_error = exc
            if attempt == RETRY_COUNT:
                break
            time.sleep(attempt)
    if last_error is None:
        raise RuntimeError(f'failed to fetch {url}')
    raise last_error


def safe_clean(target: Path, repo_root: Path) -> None:
    expected = (repo_root / DEFAULT_TARGET).resolve()
    resolved = target.resolve()
    if resolved != expected:
        raise RuntimeError(f'refusing to clean unexpected path: {resolved}')
    if target.exists():
        shutil.rmtree(target)


def normalize_remote_path(item: dict) -> str | None:
    raw_name = str(item.get('name', '')).strip()
    if not raw_name:
        return None

    remote_path = raw_name.lstrip('/')
    if not remote_path:
        return None
    return remote_path


def split_chunks(items: list[str], count: int) -> list[list[str]]:
    chunks: list[list[str]] = [[] for _ in range(count)]
    for index, item in enumerate(items):
        chunks[index % count].append(item)
    return chunks


def download_file(file_base_url: str, target: Path, remote_path: str) -> None:
    local_path = target / Path(*remote_path.split('/'))
    local_path.parent.mkdir(parents=True, exist_ok=True)

    escaped_path = '/'.join(quote(part) for part in remote_path.split('/'))
    local_path.write_bytes(fetch_bytes(f'{file_base_url}/{escaped_path}'))


def download_worker(
    worker_index: int,
    file_base_url: str,
    target: Path,
    paths: list[str],
) -> int:
    bar = tqdm.tqdm(
        total=len(paths),
        position=worker_index,
        desc=f'worker {worker_index + 1}',
        leave=True,
    )
    try:
        for remote_path in paths:
            bar.set_postfix_str(remote_path[-40:])
            download_file(file_base_url, target, remote_path)
            bar.update(1)
    finally:
        bar.close()
    return len(paths)


def download(version: str, target: Path, clean: bool, workers: int) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    target = (repo_root / target).resolve()
    workers = max(1, workers)

    if clean:
        safe_clean(target, repo_root)

    target.mkdir(parents=True, exist_ok=True)

    list_url = f'https://data.jsdelivr.com/v1/package/npm/{PACKAGE}@{version}/flat'
    file_base_url = f'https://cdn.jsdelivr.net/npm/{PACKAGE}@{version}'

    print(f'Fetching file list: {list_url}')
    payload = json.loads(fetch_bytes(list_url).decode('utf-8'))
    files = payload.get('files', [])
    paths = [
        remote_path
        for item in files
        if (remote_path := normalize_remote_path(item)) is not None
    ]
    chunks = split_chunks(paths, workers)

    print(f'Downloading {len(paths)} files with {workers} workers...')
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                download_worker,
                worker_index,
                file_base_url,
                target,
                chunk,
            )
            for worker_index, chunk in enumerate(chunks)
        ]
        count = sum(future.result() for future in futures)

    meta = {
        'package': PACKAGE,
        'version': version,
        'source': f'{file_base_url}/',
        'listSource': list_url,
        'fileCount': count,
        'workers': workers,
        'downloadedAt': datetime.now().isoformat(timespec='seconds'),
    }
    (target / '_download-meta.json').write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding='utf-8',
    )

    print(f'Downloaded {count} files to {target}')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Download NeteaseCloudMusicApi source from jsDelivr for local search.'
    )
    parser.add_argument('--version', default=DEFAULT_VERSION)
    parser.add_argument('--target', type=Path, default=DEFAULT_TARGET)
    parser.add_argument('--clean', action='store_true')
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    download(args.version, args.target, args.clean, args.workers)


if __name__ == '__main__':
    main()
