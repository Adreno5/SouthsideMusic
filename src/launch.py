import os
from pathlib import Path
import subprocess
import sys
import requests
import toml
from concurrent.futures import ThreadPoolExecutor, as_completed

if __name__ == '__main__':
    print('[UPDATOR] detecting updates')
    with open('pyproject.toml', 'r+', encoding='utf-8') as f:
        parsed = toml.load(f)
        ver: int = int(parsed['project']['version'].removeprefix('v'))
        print(f'[UPDATOR] current version: v{ver}')
        data = requests.get('https://api.github.com/repos/Adreno5/SouthsideMusic/releases/latest').json()
        newest: int = int(data['tag_name'].removeprefix('v'))
        
        if newest > ver:
            print(f'[UPDATOR] update available: v{newest}')

            file_list = []
            def collect_files(api_url):
                items = requests.get(api_url).json()
                for item in items:
                    if item['type'] == 'file':
                        file_list.append((item['path'], item['download_url']))
                    elif item['type'] == 'dir':
                        collect_files(item['url'])

            print('[UPDATOR] collecting file list...')
            collect_files('https://api.github.com/repos/Adreno5/SouthsideMusic/contents/src')
            print(f'[UPDATOR] {len(file_list)} files to download')

            def download_file(path, url):
                try:
                    print(f'[UPDATOR] downloading {path}')
                    Path(path).parent.mkdir(parents=True, exist_ok=True)
                    txt = requests.get(url).text
                    with open(path, 'w', encoding='utf-8') as fp:
                        fp.write(txt)
                    print(f'[UPDATOR] downloaded {path} ({len(txt)} chars)')
                except Exception as e:
                    print(f'[UPDATOR] failed to download {path}: {e}')

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(download_file, path, url) for path, url in file_list]
                for future in as_completed(futures):
                    future.result()

            f.seek(0)

            parsed['project']['version'] = f'v{newest}'
            toml.dump(parsed, f)

        else:
            print('[UPDATOR] no update available')

    print('[LAUNCH] launching')

    cwd = Path(os.getcwd()).resolve()
    python = cwd / 'python' / 'python.exe'
    mainpy = cwd / 'src' / 'main.py'

    if not python.exists():
        print('[ERROR] python.exe not found')
        sys.exit(1)
    if not mainpy.exists():
        print('[ERROR] main.py not found')
        sys.exit(1)

    print(f'[LAUNCH] cwd={cwd.as_posix()}')
    print(f'[LAUNCH] python={python.as_posix()}')
    print(f'[LAUNCH] main.py={mainpy.as_posix()}')

    powershell_cmd = [
        'powershell.exe',
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-Command',
        f'cd \'{cwd.as_posix()}\'; & \'{python.as_posix()}\' \'{mainpy.as_posix()}\''
    ]

    print(f'[LAUNCH] excution command: {' '.join(powershell_cmd)}')

    subprocess.run([python.as_posix(), '--version'], text=True, shell=True)

    print('[LAUNCH] run launch script')
    process = subprocess.Popen(
        powershell_cmd,
        stdout=sys.stdout,
        stderr=sys.stderr,
        stdin=sys.stdin,
        text=True,
        cwd=cwd.as_posix()
    )

    process.wait()

    print(f'[EXIT] exited: {process.returncode}')
    sys.exit(process.returncode)