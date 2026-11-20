from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESULT = ROOT / 'build.result'
RAW = RESULT / 'raw'


def _run(cmd: list[str], cwd: Path = ROOT, check: bool = True) -> None:
    print(f'  > {" ".join(cmd)}')
    subprocess.run(cmd, cwd=cwd, check=check)


def _reset() -> None:
    print('Building')
    shutil.rmtree(RESULT, ignore_errors=True)
    for name in ('launcher.dist', 'launcher.build', 'launcher.onefile-build'):
        shutil.rmtree(ROOT / name, ignore_errors=True)
    RAW.mkdir(parents=True)


def _cleanup_launcher() -> None:
    for name in ('launcher.dist', 'launcher.build', 'launcher.onefile-build'):
        shutil.rmtree(ROOT / name, ignore_errors=True)


def _copy_tree(src: Path, dst: Path, required: bool = True) -> None:
    if not src.exists():
        if required:
            raise FileNotFoundError(src)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        result = subprocess.run(
            ['xcopy', str(src), str(dst), '/E', '/I', '/Y', '/Q', '/J'],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
        )
        if result.returncode > 1:
            raise RuntimeError(f'xcopy failed for {src}')
        return
    shutil.copytree(
        src,
        dst,
        symlinks=True,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'),
    )


def _tag() -> str:
    with (ROOT / 'pyproject.toml').open('rb') as handle:
        return tomllib.load(handle)['project']['version']


def _arch_tag() -> str:
    machine = platform.machine().lower()
    if machine in ('amd64', 'x86_64', 'x64'):
        return 'x86_64'
    if machine in ('arm64', 'aarch64'):
        return 'arm64'
    return machine


def _debian_arch() -> str:
    return 'amd64' if _arch_tag() == 'x86_64' else 'arm64'


def _remove_paths(root: Path, names: tuple[str, ...]) -> None:
    for name in names:
        for path in sorted(
            root.rglob(name),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            shutil.rmtree(path, ignore_errors=True)


def _remove_files(root: Path, pattern: str) -> None:
    for path in root.rglob(pattern):
        path.unlink(missing_ok=True)


def _prune_windows(raw: Path) -> None:
    _remove_paths(
        raw,
        ('__pycache__', '.mypy_cache', '.ruff_cache', '.pytest_cache', '.git'),
    )
    _remove_files(raw, '*.pyc')

    site = raw / 'python' / 'Lib' / 'site-packages'
    for pattern in ('*.dist-info', '*.egg-info', 'tests', 'test', 'docs', 'examples'):
        for path in site.glob(pattern):
            shutil.rmtree(path, ignore_errors=True)
    for path in (site / 'PySide6').glob('*.pdb'):
        path.unlink(missing_ok=True)
    shutil.rmtree(raw / 'python' / 'Lib' / '__pycache__', ignore_errors=True)

    worker = raw / 'freethreaded_python'
    if not worker.exists():
        return
    for name in ('include', 'libs', 'tcl'):
        shutil.rmtree(worker / name, ignore_errors=True)
    for name in ('test', 'idlelib', 'tkinter'):
        shutil.rmtree(worker / 'Lib' / name, ignore_errors=True)
    worker_site = worker / 'Lib' / 'site-packages'
    for name in (
        '__pycache__',
        'audioop',
        'bin',
        'numpy',
        'numpy.libs',
        'PIL',
        'pydub',
        'scipy',
        'scipy.libs',
        'psutil',
        'PySide6',
        'qfluentwidgets',
        'qframelesswindow',
        'shiboken6',
    ):
        shutil.rmtree(worker_site / name, ignore_errors=True)
    prefixes = (
        'audioop_lts-',
        'numpy-',
        'pillow-',
        'pydub-',
        'scipy-',
        'psutil-',
        'pyside6-',
        'pyside6_addons-',
        'pyside6_essentials-',
        'pyside6_fluent_widgets-',
        'pysidesix_frameless_window-',
        'shiboken6-',
    )
    for path in worker_site.glob('*.dist-info'):
        if path.name.lower().startswith(prefixes):
            shutil.rmtree(path, ignore_errors=True)
    _remove_files(worker, '*.pdb')
    _remove_files(worker, '*.whl')
    (worker_site / 'README.txt').unlink(missing_ok=True)


def _find_iscc() -> Path | None:
    import winreg

    keys = (
        (
            winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
            r'\Inno Setup 7_is1',
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
            r'\Inno Setup 7_is1',
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
            r'\Inno Setup 6_is1',
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
            r'\Inno Setup 6_is1',
        ),
    )
    for hive, subkey in keys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                location, _ = winreg.QueryValueEx(key, 'InstallLocation')
        except OSError:
            continue
        candidate = Path(location) / 'ISCC.exe'
        if candidate.is_file():
            return candidate

    found = shutil.which('iscc')
    if found:
        return Path(found)

    for candidate in (
        Path(r'C:\Program Files\Inno Setup 7\ISCC.exe'),
        Path(r'C:\Program Files (x86)\Inno Setup 7\ISCC.exe'),
        Path(r'D:\Program Files\Inno Setup 7\ISCC.exe'),
        Path(r'C:\Program Files\Inno Setup 6\ISCC.exe'),
        Path(r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'),
        Path(r'D:\Program Files\Inno Setup 6\ISCC.exe'),
    ):
        if candidate.is_file():
            return candidate
    return None


def _run_inno(iscc: Path) -> None:
    try:
        _run([str(iscc), 'installer.iss'])
        return
    except subprocess.CalledProcessError:
        print('[WARN] Inno Setup failed. Retrying once in case the output was locked.')
    time.sleep(10)
    try:
        _run([str(iscc), 'installer.iss'])
    except subprocess.CalledProcessError:
        print(
            '[ERROR] Inno Setup failed again. If it reported EndUpdateResource, '
            f'add {RESULT} to the antivirus trust list.'
        )


def _build_windows() -> None:
    _reset()
    build_python = ROOT / 'build_venv' / 'Scripts' / 'python.exe'
    if not build_python.is_file():
        raise SystemExit(
            'Missing build_venv. Run python setup_workspace.py before building.'
        )

    print('Building - Nuitka')
    _run([
        str(build_python),
        '-m',
        'nuitka',
        'launcher.py',
        '--windows-console-mode=disable',
        '--output-filename=Launch',
        '--standalone',
        '--windows-icon-from-ico=icons\\app.ico',
        '--experimental=force-dependencies-pefile',
    ])

    print('Building - Copy launcher')
    _copy_tree(ROOT / 'launcher.dist', RAW / 'launcher')
    print('Building - Link Launch.exe into the root')
    try:
        _run(
            [
                'powershell',
                '-NoProfile',
                '-ExecutionPolicy',
                'Bypass',
                '-File',
                str(ROOT / 'scripts' / 'create_shortcut.ps1'),
            ],
            cwd=RAW,
        )
    except subprocess.CalledProcessError:
        print('[WARN] Could not create the Launch.lnk shortcut.')

    print('Building - Copy embed_python')
    _copy_tree(ROOT / 'embed_python', RAW / 'python')
    print('Building - Copy free-threaded Python')
    _copy_tree(ROOT / 'freethreaded_python', RAW / 'freethreaded_python', False)
    print('Building - Copy src')
    _copy_tree(ROOT / 'src', RAW / 'src')
    print('Building - Copy fonts')
    _copy_tree(ROOT / 'fonts', RAW / 'fonts')
    print('Building - Generate icon')
    _run([sys.executable, str(ROOT / 'scripts' / 'create_icon.py')])
    print('Building - Copy icons')
    _copy_tree(ROOT / 'icons', RAW / 'icons')
    print('Building - Copy images')
    _copy_tree(ROOT / 'images', RAW / 'images')
    for name in ('pyproject.toml', 'bootstrap.py', 'full_requirements.txt'):
        shutil.copy2(ROOT / name, RAW / name)

    print('Building - Verify runtime imports')
    _run([str(RAW / 'python' / 'python.exe'), '-c', 'import PySide6, shiboken6'])

    print('Building - Remove unneeded files')
    _prune_windows(RAW)

    print('Building - Inno Setup installer')
    iscc = _find_iscc()
    if iscc is None:
        print('[WARN] Inno Setup compiler not found.')
        print("       Run 'python setup_workspace.py --innosetup' to auto-install.")
        print('       Raw files are in build.result\\raw\\')
    else:
        _run_inno(iscc)
    _cleanup_launcher()


def _install_uv_runtime(request: str, dest: Path) -> Path:
    install_dir = RESULT / 'runtime' / request.replace('.', '_')
    _run(
        [
            'uv',
            'python',
            'install',
            '--no-progress',
            '--install-dir',
            str(install_dir),
            request,
        ],
    )
    roots = [
        path
        for path in install_dir.iterdir()
        if path.is_dir() and not path.name.startswith('.')
    ]
    if not roots:
        raise RuntimeError(f'uv did not install Python {request} into {install_dir}')
    shutil.move(str(roots[0]), dest)
    return _posix_python(dest)


def _posix_python(runtime: Path) -> Path:
    bindir = runtime / 'bin'
    for name in ('python3', 'python3.14'):
        candidate = bindir / name
        if candidate.is_file():
            return candidate
    matches = sorted(bindir.glob('python3*'))
    if not matches:
        raise RuntimeError(f'no interpreter in {bindir}')
    return matches[0]


def _site_packages(python: Path) -> Path:
    result = subprocess.run(
        [
            str(python),
            '-c',
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip())


def _uv_install(python: Path, site: Path, requirements: list[str]) -> None:
    site.mkdir(parents=True, exist_ok=True)
    _run(
        [
            'uv',
            'pip',
            'install',
            '--no-progress',
            '--python',
            str(python),
            '--target',
            str(site),
            *requirements,
        ],
    )


def _worker_pins() -> list[str]:
    wanted = {'numpy', 'scipy', 'pillow', 'pydub', 'audioop-lts'}
    pins: list[str] = []
    for line in (
        (ROOT / 'full_requirements.txt').read_text(encoding='utf-8').splitlines()
    ):
        line = line.strip()
        if '==' not in line:
            continue
        if line.split('==', 1)[0].strip().lower() in wanted:
            pins.append(line)
    return pins


def _install_posix_launcher() -> None:
    launcher = RAW / 'launcher'
    launcher.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'launcher.py', launcher / 'launcher.py')
    script = launcher / 'Launch'
    script.write_text(
        '#!/bin/sh\n'
        'HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)\n'
        'PY="$HERE/../python/bin/python3"\n'
        '[ -x "$PY" ] || PY="$HERE/../python/bin/python3.14"\n'
        'exec "$PY" "$HERE/launcher.py" "$@"\n',
        encoding='utf-8',
    )
    script.chmod(0o755)


def _prune_posix(raw: Path) -> None:
    _remove_paths(
        raw,
        ('__pycache__', '.git', '.mypy_cache', '.ruff_cache', '.pytest_cache'),
    )
    _remove_files(raw, '*.pyc')
    for runtime in (raw / 'python', raw / 'freethreaded_python'):
        if not runtime.exists():
            continue
        for name in ('include', 'share'):
            shutil.rmtree(runtime / name, ignore_errors=True)
        for lib in runtime.glob('lib/python3*'):
            for name in ('test', 'idlelib', 'turtledemo', 'tkinter', 'ensurepip'):
                shutil.rmtree(lib / name, ignore_errors=True)


def _png_icon(dest: Path, size: int) -> None:
    from PIL import Image

    image = Image.open(ROOT / 'icon.png').convert('RGBA')
    image.resize((size, size), Image.Resampling.LANCZOS).save(dest)


def _mac_icon(dest: Path) -> None:
    iconset = RESULT / 'AppIcon.iconset'
    iconset.mkdir()
    try:
        for size, name in (
            (16, '16x16'),
            (32, '16x16@2x'),
            (32, '32x32'),
            (64, '32x32@2x'),
            (128, '128x128'),
            (256, '128x128@2x'),
            (256, '256x256'),
            (512, '256x256@2x'),
            (512, '512x512'),
            (1024, '512x512@2x'),
        ):
            _png_icon(iconset / f'icon_{name}.png', size)
        _run(['iconutil', '-c', 'icns', str(iconset), '-o', str(dest)])
    finally:
        shutil.rmtree(iconset, ignore_errors=True)


def _package_mac(tag: str) -> None:
    print('Building - Application bundle')
    app = RESULT / 'SouthsideMusic.app'
    contents = app / 'Contents'
    macos = contents / 'MacOS'
    resources = contents / 'Resources'
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    shutil.copytree(RAW, resources / 'app', symlinks=True, copy_function=os.link)

    executable = macos / 'SouthsideMusic'
    executable.write_text(
        '#!/bin/sh\n'
        'HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)\n'
        'exec "$HERE/../Resources/app/launcher/Launch" "$@"\n',
        encoding='utf-8',
    )
    executable.chmod(0o755)

    _mac_icon(resources / 'AppIcon.icns')
    version = tag.removeprefix('v')
    info = {
        'CFBundleName': 'Southside Music',
        'CFBundleDisplayName': 'Southside Music',
        'CFBundleIdentifier': 'top.southside.music',
        'CFBundleExecutable': 'SouthsideMusic',
        'CFBundleIconFile': 'AppIcon',
        'CFBundlePackageType': 'APPL',
        'CFBundleShortVersionString': version,
        'CFBundleVersion': version,
        'LSMinimumSystemVersion': '11.0',
        'NSHighResolutionCapable': True,
    }
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))

    _run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=False)

    print('Building - Disk image')
    dmg = RESULT / f'SouthsideMusic_{tag}_macos_{_arch_tag()}.dmg'
    _run([
        'hdiutil',
        'create',
        '-volname',
        'Southside Music',
        '-srcfolder',
        str(app),
        '-ov',
        '-format',
        'UDZO',
        str(dmg),
    ])


def _tree_size_kb(root: Path) -> int:
    total = 0
    for path in root.rglob('*'):
        if path.is_file() and not path.is_symlink():
            total += path.stat().st_size
    return max(1, total // 1024)


def _package_linux(tag: str) -> None:
    arch = _arch_tag()
    print('Building - Portable archive')
    archive = RESULT / f'SouthsideMusic_{tag}_linux_{arch}.tar.gz'
    with tarfile.open(archive, 'w:gz', compresslevel=6) as handle:
        handle.add(RAW, arcname='SouthsideMusic')

    if shutil.which('dpkg-deb') is None:
        print('[WARN] dpkg-deb not found; skipping the Debian package.')
        return

    print('Building - Debian package')
    stage = RESULT / 'debian'
    app = stage / 'opt' / 'SouthsideMusic'
    shutil.copytree(RAW, app, symlinks=True, copy_function=os.link)

    applications = stage / 'usr' / 'share' / 'applications'
    applications.mkdir(parents=True)
    (applications / 'southside-music.desktop').write_text(
        '[Desktop Entry]\n'
        'Type=Application\n'
        'Name=Southside Music\n'
        'Comment=NetEase CloudMusic desktop client\n'
        'Exec=/opt/SouthsideMusic/launcher/Launch\n'
        'Icon=southside-music\n'
        'Terminal=false\n'
        'Categories=AudioVideo;Audio;Player;\n',
        encoding='utf-8',
    )
    icons = stage / 'usr' / 'share' / 'icons' / 'hicolor' / '256x256' / 'apps'
    icons.mkdir(parents=True)
    _png_icon(icons / 'southside-music.png', 256)

    debian = stage / 'DEBIAN'
    debian.mkdir()
    version = tag.removeprefix('v')
    (debian / 'control').write_text(
        'Package: southside-music\n'
        f'Version: {version}\n'
        'Section: sound\n'
        'Priority: optional\n'
        f'Architecture: {_debian_arch()}\n'
        'Maintainer: Adreno5\n'
        f'Installed-Size: {_tree_size_kb(app)}\n'
        'Depends: ffmpeg, libportaudio2, libegl1, libgl1, libxkbcommon-x11-0, '
        'libxcb-cursor0\n'
        'Description: Southside Music\n'
        ' A cross-platform NetEase CloudMusic desktop client.\n',
        encoding='utf-8',
    )
    postinst = debian / 'postinst'
    postinst.write_text(
        '#!/bin/sh\nset -e\nchmod -R a+rwX /opt/SouthsideMusic\n',
        encoding='utf-8',
    )
    postinst.chmod(0o755)

    _run([
        'dpkg-deb',
        '--root-owner-group',
        '--build',
        str(stage),
        str(RESULT / f'SouthsideMusic_{tag}_linux_{arch}.deb'),
    ])
    shutil.rmtree(stage, ignore_errors=True)


def _build_posix(tag: str, target: str) -> None:
    _reset()

    print('Building - Python runtime')
    python = _install_uv_runtime('3.14', RAW / 'python')
    requirements = RESULT / 'requirements.txt'
    with requirements.open('w', encoding='utf-8') as handle:
        subprocess.run(
            [
                'uv',
                'export',
                '--frozen',
                '--no-dev',
                '--no-hashes',
                '--format',
                'requirements-txt',
            ],
            cwd=ROOT,
            check=True,
            stdout=handle,
        )
    print('Building - Application dependencies')
    _uv_install(python, _site_packages(python), ['-r', str(requirements)])

    print('Building - Free-threaded worker')
    try:
        worker_python = _install_uv_runtime('3.14t', RAW / 'freethreaded_python')
        _uv_install(worker_python, _site_packages(worker_python), _worker_pins())
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f'[WARN] Free-threaded worker unavailable: {error}')
        shutil.rmtree(RAW / 'freethreaded_python', ignore_errors=True)

    print('Building - Copy src')
    _copy_tree(ROOT / 'src', RAW / 'src')
    print('Building - Copy fonts')
    _copy_tree(ROOT / 'fonts', RAW / 'fonts')
    print('Building - Copy icons')
    _copy_tree(ROOT / 'icons', RAW / 'icons')
    print('Building - Copy images')
    _copy_tree(ROOT / 'images', RAW / 'images')
    shutil.copy2(ROOT / 'pyproject.toml', RAW / 'pyproject.toml')
    shutil.copy2(ROOT / 'LICENSE', RAW / 'LICENSE')
    print('Building - Launcher')
    _install_posix_launcher()

    print('Building - Remove unneeded files')
    _prune_posix(RAW)

    if target == 'macos':
        _package_mac(tag)
    else:
        _package_linux(tag)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--platform',
        choices=('auto', 'windows', 'macos', 'linux'),
        default='auto',
    )
    options = parser.parse_args()

    target = options.platform
    if target == 'auto':
        if os.name == 'nt':
            target = 'windows'
        elif sys.platform == 'darwin':
            target = 'macos'
        else:
            target = 'linux'

    if (target == 'windows') != (os.name == 'nt'):
        raise SystemExit(f'Cannot build {target} packages on this host.')

    tag = _tag()
    if target == 'windows':
        _build_windows()
    else:
        _build_posix(tag, target)
    print('Done!')
    print('Done')
    return 0


if __name__ == '__main__':
    sys.exit(main())
