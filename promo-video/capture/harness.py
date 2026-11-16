from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PUB = ROOT / 'promo-video' / 'public' / 'textures'
OUT = HERE / 'out'
PUB.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / f'harness_{os.getpid()}.log'
LOG.write_text('', encoding='utf-8')
LATEST = OUT / 'harness.log'
LATEST.write_text('', encoding='utf-8')
STACK_FH = open(OUT / 'stack.log', 'w', encoding='utf-8')


def log(msg: object) -> None:
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(f'[{time.strftime("%H:%M:%S")}] {msg}\n')
    except Exception:
        pass


os.environ['QT_SCALE_FACTOR'] = os.environ.get('QT_SCALE_FACTOR', '2')
sys.path.insert(0, str(ROOT / 'src'))
sys.path.append(str(ROOT / 'src' / 'views'))
sys.path.append(str(ROOT / 'src' / 'services'))
sys.path.append(str(ROOT / 'src' / 'utils'))

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtTest import QTest

import views.error_popup as error_popup_module
import views.dependences_window as depmod
from views.main_window import MainWindow
from core.favorites import favorites_manager

HOLDER: dict[str, object] = {}
_real_exec = QApplication.exec

import threading


def _watchdog() -> None:
    time.sleep(360)
    log('WATCHDOG TIMEOUT - hard exit')
    os._exit(3)


def _heartbeat() -> None:
    while True:
        time.sleep(3)
        log('heartbeat')


threading.Thread(target=_watchdog, daemon=True).start()
threading.Thread(target=_heartbeat, daemon=True).start()

import faulthandler

faulthandler.dump_traceback_later(30, repeat=True, file=STACK_FH)

_orig_popup_init = error_popup_module.ErrorPopupWindow.__init__
_orig_popup_exec = error_popup_module.ErrorPopupWindow.exec


def _popup_init(self, txt, *args, **kwargs):
    log('APP ERROR POPUP:\n' + str(txt))
    _orig_popup_init(self, txt, *args, **kwargs)


def _popup_exec(self):
    try:
        self.close()
    except Exception:
        pass
    return 0


error_popup_module.ErrorPopupWindow.__init__ = _popup_init
error_popup_module.ErrorPopupWindow.exec = _popup_exec


def _quick_start_check(self):
    try:
        base = Path(depmod.__file__).resolve().parents[2]
        self._addFfmpegToPath(str(base / 'ffmpeg'))
    except Exception:
        log('ffmpeg path setup failed\n' + traceback.format_exc())
    self.ctx.dependences_available = True
    self.hide()
    log('dependency check skipped (fast path)')


depmod.DependencesWindow.startCheck = _quick_start_check

LAYOUT: dict = {'pages': {}, 'elements': []}
INTERESTING = {
    'CardWidget',
    'SImageLabel',
    'TitleLabel',
    'SubtitleLabel',
    'CaptionLabel',
    'PushButton',
    'TransparentPushButton',
    'PillToolButton',
    'LineEdit',
    'SearchLineEdit',
    'ComboBox',
    'ProgressBar',
    'Slider',
    'CheckBox',
    'EQEditor',
    'LyricsViewer',
    'QListWidget',
    'SScrollArea',
    'SListWidget',
    'NumberViewer',
    'SettableNumberViewer',
    'SongCard',
    'SearchSongCard',
    'LocalFolderCard',
    'CloudFolderCard',
    'DummyCard',
    'AccountWidget',
    'RediscoveryCard',
    'HeartModeCard',
    'PrivateRoamCard',
    'PrivateRadarCard',
    'SimilarSongsCard',
    'SectionContainer',
    'SectionSeparator',
    'FlowLayout',
    'QLineEdit',
    'QLabel',
}
INTERESTING_NAMES = {'studio_page', 'desktop_lyrics_page', 'titleLabel', 'controller'}


def dpr() -> float:
    mw = HOLDER.get('mw')
    if mw is None:
        return 1.0
    return mw.devicePixelRatio()


def save_pixmap(pm: QPixmap, rel: str) -> str:
    path = PUB / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    pm.save(str(path), 'PNG')
    log(f'saved {rel} {pm.width()}x{pm.height()} dpr={pm.devicePixelRatio()}')
    return str(path)


def composite_gl(pm: QPixmap, mw) -> QPixmap:
    try:
        gls = [
            w for w in mw.findChildren(QOpenGLWidget) if w.isVisible() and w.width() > 8
        ]
    except Exception:
        return pm
    if not gls:
        return pm
    painter = QPainter(pm)
    for widget in gls:
        try:
            fb = widget.grabFramebuffer()
        except Exception:
            log('grabFramebuffer failed\n' + traceback.format_exc())
            continue
        pos = widget.mapTo(mw, QPoint(0, 0))
        target = QRect(pos.x(), pos.y(), widget.width(), widget.height())
        painter.drawImage(target, fb)
        log(
            f'composited GL {type(widget).__name__} at {target} fb={fb.width()}x{fb.height()}'
        )
    painter.end()
    return pm


def composite_playing(pm: QPixmap, mw) -> QPixmap:
    return composite_gl(pm, mw)


def element_crop(pm: QPixmap, page_widget: QWidget, mw) -> None:
    try:
        widgets = page_widget.findChildren(QWidget)
    except Exception:
        return
    d = pm.devicePixelRatio()
    page_name = page_widget.property('_capture_name') or type(page_widget).__name__
    entries = []
    for w in widgets:
        try:
            if not w.isVisible():
                continue
            width, height = w.width(), w.height()
            if width < 12 or height < 12:
                continue
            cls = type(w).__name__
            obj = w.objectName()
            if cls not in INTERESTING and obj not in INTERESTING_NAMES:
                continue
            if width * height < 4000:
                continue
            pos = w.mapTo(mw, QPoint(0, 0))
            rect = QRect(
                int(pos.x() * d), int(pos.y() * d), int(width * d), int(height * d)
            )
            if not pm.rect().contains(rect):
                continue
            crop = pm.copy(rect)
            safe_obj = ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in obj)[
                :32
            ]
            rel = f'elements/{page_name}/{len(entries):02d}_{cls}_{safe_obj}.png'
            save_pixmap(crop, rel)
            entries.append({
                'class': cls,
                'objectName': obj,
                'x': pos.x(),
                'y': pos.y(),
                'w': width,
                'h': height,
                'file': rel,
            })
            if len(entries) >= 70:
                break
        except Exception:
            continue
    LAYOUT['elements'].extend([{**e, 'page': page_name} for e in entries])
    log(f'elements {page_name}: {len(entries)}')
    # dump widget text tree for reference
    try:
        lines = []
        for w in widgets:
            if not w.isVisible():
                continue
            cls = type(w).__name__
            if cls not in INTERESTING and w.objectName() not in INTERESTING_NAMES:
                continue
            txt = ''
            for prop in ('text', 'title', 'placeholderText'):
                try:
                    val = getattr(w, prop)()
                    if isinstance(val, str) and val:
                        txt = val
                        break
                except Exception:
                    pass
            if txt or w.objectName():
                pos = w.mapTo(mw, QPoint(0, 0))
                lines.append(
                    f'{cls}|{w.objectName()}|{pos.x()},{pos.y()},{w.width()},{w.height()}|{txt[:80]}'
                )
        (OUT / f'tree_{page_name}.txt').write_text('\n'.join(lines), encoding='utf-8')
    except Exception:
        log('tree dump failed\n' + traceback.format_exc())


def capture_page(name: str, page_widget: QWidget, playing: bool = False) -> QPixmap:
    mw = HOLDER['mw']
    page_widget.setProperty('_capture_name', name)
    pm = mw.grab()
    pm = composite_gl(pm, mw)
    save_pixmap(pm, f'pages/{name}.png')
    LAYOUT['pages'][name] = {
        'width': mw.width(),
        'height': mw.height(),
        'dpr': pm.devicePixelRatio(),
        'file': f'pages/{name}.png',
    }
    element_crop(pm, page_widget, mw)
    return pm


def snapshot_cover(name: str) -> None:
    mw = HOLDER['mw']
    try:
        label = mw.ctx.playing_page.img_label
        pm = label.pixmap()
        if pm is not None and not pm.isNull():
            save_pixmap(pm, f'covers/{name}.png')
    except Exception:
        log('cover snapshot failed\n' + traceback.format_exc())


def restore_and_quit() -> None:
    app = QApplication.instance()
    mw = HOLDER.get('mw')
    try:
        if mw is not None:
            try:
                ctx = mw.ctx
                if getattr(mw, 'dp_expanded', False):
                    mw.togglePlayingPageExpand()
                if getattr(mw, 'pl_expanded', False):
                    mw.togglePlaylistExpand()
                try:
                    ctx.playing_manager.stopPlaybackForShutdown()
                except Exception:
                    pass
                mw.resize(QSize(997, 686))
                mw.move(315, 152)
                (OUT / 'layout.json').write_text(
                    json.dumps(LAYOUT, ensure_ascii=False, indent=1), encoding='utf-8'
                )
            except Exception:
                log('window restore failed\n' + traceback.format_exc())
        log('finishing, quitting app')
    finally:
        try:
            app.quit()
        except Exception:
            log('quit failed\n' + traceback.format_exc())


def ready_debug() -> str:
    mw = HOLDER.get('mw')
    if mw is None:
        return 'no-mw'
    parts = []
    try:
        parts.append(f'visible={mw.isVisible()}')
        ctx = mw.ctx
        parts.append(f'home={ctx.home_page is not None}')
        parts.append(f'current={type(mw.contents_widget.currentWidget()).__name__}')
        launch = getattr(ctx, 'launch_window', None)
        if launch is not None:
            try:
                parts.append(f'launch_visible={launch.isVisible()}')
            except Exception:
                parts.append('launch_dead')
    except Exception as exc:
        parts.append(f'err={exc!r}')
    return ' '.join(parts)


def window_ready() -> bool:
    mw = HOLDER.get('mw')
    if mw is None:
        return False
    try:
        if not mw.isVisible():
            return False
        ctx = mw.ctx
        if getattr(ctx, 'home_page', None) is None:
            return False
        if mw.contents_widget.currentWidget() is not ctx.home_page:
            return False
        launch = getattr(ctx, 'launch_window', None)
        if launch is not None:
            try:
                if launch.isVisible():
                    return False
            except RuntimeError:
                pass
        return True
    except Exception:
        return False


def poll(pred, then, label: str, timeout_ms: int = 20000, interval: int = 200) -> None:
    started = time.time()

    def check() -> None:
        ok = False
        try:
            ok = bool(pred())
        except Exception:
            ok = False
        if ok:
            log(f'poll ok: {label} ({time.time() - started:.1f}s)')
            return then()
        if (time.time() - started) * 1000 > timeout_ms:
            log(f'poll TIMEOUT: {label}')
            return then()
        QTimer.singleShot(interval, check)

    check()


def delay(ms: int, then) -> None:
    QTimer.singleShot(ms, then)


# ---------- tasks ----------
SONGS = [
    '405599119',  # 7 Years
    '515269424',  # Wolves
    '515453363',  # All Falls Down
    '26060065',  # Counting Stars
    '1992859765',  # Alone
]
HERO_ID = os.environ.get('HERO_ID', '515269424')


def all_library_songs():
    songs = []
    for folder in favorites_manager.folders:
        songs.extend(folder.songs)
    seen = set()
    uniq = []
    for s in songs:
        key = str(getattr(s, 'id', ''))
        if key and key not in seen:
            seen.add(key)
            uniq.append(s)
    return uniq


def find_song(song_id: str):
    for s in all_library_songs():
        if str(getattr(s, 'id', '')) == song_id:
            return s
    return None


def ensure_song_in_playlist(song_id: str) -> int:
    ctx = HOLDER['ctx']
    pm = ctx.playing_manager
    playlist = list(pm.playlist)
    for i, s in enumerate(playlist):
        if str(getattr(s, 'id', '')) == song_id:
            return i
    song = find_song(song_id)
    if song is None:
        return -1
    playlist.append(song)
    pm.setPlaylist(playlist)
    return len(playlist) - 1


def play_song(song_id: str) -> bool:
    ctx = HOLDER['ctx']
    pm = ctx.playing_manager
    idx = ensure_song_in_playlist(song_id)
    log(f'play song {song_id} idx={idx} playlist_len={len(pm.playlist)}')
    if idx < 0:
        return False
    try:
        ctx.player.setVolume(0.0)
    except Exception:
        log('setVolume failed\n' + traceback.format_exc())
    pm.playSongAtIndex(idx)
    return True


def current_title() -> str:
    try:
        return HOLDER['ctx'].playing_page.title_label.text()
    except Exception:
        return ''


def cover_ready() -> bool:
    try:
        label = HOLDER['ctx'].playing_page.img_label
        pm = label.pixmap()
        return pm is not None and not pm.isNull() and pm.width() > 10
    except Exception:
        return False


def lyrics_ready() -> bool:
    ctx = HOLDER['ctx']
    try:
        return ctx.ymgr.hasYrcTiming() or bool(getattr(ctx.mgr, 'parsed', None))
    except Exception:
        return False


def song_loaded(song_id: str) -> bool:
    try:
        cur = HOLDER['ctx'].playing_page.cur
        return cur is not None and str(cur.storable.id) == song_id and cover_ready()
    except Exception:
        return False


def task_prepare(done) -> None:
    ctx = HOLDER['ctx']
    mw = HOLDER['mw']
    try:
        mw.contents_widget.setCurrentWidget(ctx.home_page)
    except Exception:
        log('force home failed')
    try:
        mw.resize(QSize(1920, 1080))
    except Exception:
        log('resize failed')
    log(
        f'prepare: window {mw.width()}x{mw.height()} playlist={len(ctx.playing_manager.playlist)}'
    )

    def after_playlist() -> None:
        log(f'playlist restored len={len(ctx.playing_manager.playlist)}')
        try:
            ctx.player.setVolume(0.0)
        except Exception:
            pass
        try:
            capture_page('home', ctx.home_page)
        except Exception:
            log('home capture failed\n' + traceback.format_exc())
        done()

    def maybe_fill() -> None:
        if len(ctx.playing_manager.playlist) == 0:
            songs = all_library_songs()
            log(f'filling playlist from favorites: {len(songs)} songs')
            ctx.playing_manager.setPlaylist(list(songs))
        after_playlist()

    poll(
        lambda: len(HOLDER['ctx'].playing_manager.playlist) > 0,
        maybe_fill,
        'playlist',
        12000,
        250,
    )


def task_hero(done) -> None:
    if not play_song(HERO_ID):
        return done()

    def seek_and_expand() -> None:
        ctx = HOLDER['ctx']
        try:
            ctx.player.pause()
            length = float(ctx.player.getLength())
            seek = (
                min(max(12.0, length * 0.35), max(0.0, length - 8.0))
                if length > 0
                else 20.0
            )
            ctx.player.setPosition(seek)
            log(f'paused & seek to {seek:.1f}s / {length:.1f}s')
        except Exception:
            log('pause/seek failed\n' + traceback.format_exc())
        try:
            mw = HOLDER['mw']
            mw.togglePlayingPageExpand()
        except Exception:
            log('expand failed\n' + traceback.format_exc())
        delay(1600, capture)

    def capture() -> None:
        mw = HOLDER['mw']
        ctx = HOLDER['ctx']
        log(f'hero: title={current_title()!r} yrc={lyrics_ready()}')
        snapshot_cover(HERO_ID)
        capture_page('playing', ctx.playing_page, playing=True)
        # home page with cover-tinted background
        try:
            mw.togglePlayingPageExpand()
        except Exception:
            pass
        delay(600, lambda: (capture_page('home_song', ctx.home_page), done()))

    poll(
        lambda: song_loaded(HERO_ID),
        lambda: poll(lambda: lyrics_ready(), seek_and_expand, 'lyrics', 15000),
        'song+cover',
        25000,
    )


def task_survey(done) -> None:
    ctx = HOLDER['ctx']
    mw = HOLDER['mw']
    others = [s for s in SONGS if s != HERO_ID]
    state = {'i': 0}

    def step() -> None:
        if state['i'] >= len(others):
            return done()
        sid = others[state['i']]
        state['i'] += 1
        if not play_song(sid):
            return step()

        def expand_capture() -> None:
            try:
                if not getattr(mw, 'dp_expanded', False):
                    mw.togglePlayingPageExpand()
            except Exception:
                pass
            delay(
                1300,
                lambda: (
                    capture_page('playing_' + sid, ctx.playing_page, playing=True),
                    snapshot_cover(sid),
                    collapse(),
                ),
            )

        def collapse() -> None:
            try:
                if getattr(mw, 'dp_expanded', False):
                    mw.togglePlayingPageExpand()
            except Exception:
                pass
            delay(700, step)

        poll(lambda: song_loaded(sid), expand_capture, 'survey ' + sid, 22000)

    step()


def task_favorites(done) -> None:
    ctx = HOLDER['ctx']
    mw = HOLDER['mw']
    try:
        folder = favorites_manager.folders[0]
        mw.onViewFolder(folder)
    except Exception:
        log('favorites nav failed\n' + traceback.format_exc())
    delay(1200, lambda: (capture_page('favorites', ctx.favorites_page), done()))


def task_rediscovery(done) -> None:
    ctx = HOLDER['ctx']
    mw = HOLDER['mw']
    try:
        mw.openRediscovery()
    except Exception:
        log('rediscovery nav failed\n' + traceback.format_exc())
    delay(1500, lambda: (capture_page('rediscovery', ctx.rediscovery_page), done()))


def task_settings(done) -> None:
    ctx = HOLDER['ctx']
    mw = HOLDER['mw']
    try:
        mw._onSettingsClicked()
    except Exception:
        log('settings nav failed\n' + traceback.format_exc())
    delay(900, expand_all_sections)
    delay(
        2100, lambda: (capture_page('settings_top', ctx.setting_page), scroll_to_eq())
    )

    def scroll_to_eq() -> None:
        try:
            stp = ctx.setting_page
            scroller = stp.scroller
            content = scroller.widget()
            pos = stp.eq_edit.mapTo(content, QPoint(0, 0))
            bar = scroller.verticalScrollBar()
            bar.setValue(max(0, pos.y() - 180))
            log(
                f'settings scroll to eq y={pos.y()} range={bar.minimum()}..{bar.maximum()}'
            )
        except Exception:
            log('scroll eq failed\n' + traceback.format_exc())

        def after_eq_capture() -> None:
            task_eq_seq(lambda: task_crossfade_capture(done))

        delay(
            900,
            lambda: (capture_page('settings_eq', ctx.setting_page), after_eq_capture()),
        )


def task_playlist(done) -> None:
    ctx = HOLDER['ctx']
    mw = HOLDER['mw']
    try:
        if not getattr(mw, 'pl_expanded', False):
            mw.togglePlaylistExpand()
    except Exception:
        log('playlist toggle failed\n' + traceback.format_exc())
    delay(900, lambda: (capture_page('playlist', ctx.playlist_page), collapse()))

    def collapse() -> None:
        try:
            if getattr(mw, 'pl_expanded', False):
                mw.togglePlaylistExpand()
        except Exception:
            pass
        delay(500, done)


def task_desktop_lyrics(done) -> None:
    ctx = HOLDER['ctx']
    try:
        seek = pick_lyric_seek(HERO_ID)
        try:
            ctx.player.setVolume(0.0)
            ctx.player.setPosition(seek)
            ctx.player.play()
            log(f'desktop lyrics: seek {seek:.1f}s playing={ctx.player.isPlaying()}')
        except Exception:
            log('desktop lyrics seek failed\n' + traceback.format_exc())
        ctx.desktop_lyrics_page.setLyricsVisible(True)
        viewer = ctx.desktop_lyrics_page.viewer
        delay(1800, lambda: capture_viewer(viewer, done))
    except Exception:
        log('desktop lyrics failed\n' + traceback.format_exc())
        done()


def capture_viewer(viewer, done) -> None:
    try:
        if not viewer.isVisible():
            log('desktop lyrics viewer not visible')
            return done()
        fb = viewer.grabFramebuffer()
        save_pixmap(fb, 'elements/desktop_lyrics/viewer.png')
        pos = viewer.mapToGlobal(QPoint(0, 0))
        LAYOUT['desktop_lyrics'] = {
            'x': pos.x(),
            'y': pos.y(),
            'w': viewer.width(),
            'h': viewer.height(),
        }
        log(f'desktop lyrics captured {fb.width()}x{fb.height()}')
    except Exception:
        log('desktop lyrics capture failed\n' + traceback.format_exc())
    try:
        HOLDER['ctx'].desktop_lyrics_page.setLyricsVisible(False)
    except Exception:
        pass
    done()


def capture_sequence(name: str, grab, duration_ms: int, interval_ms: int, done) -> None:
    seqdir = PUB / 'seq' / name
    seqdir.mkdir(parents=True, exist_ok=True)
    for old in seqdir.glob('*.png'):
        try:
            old.unlink()
        except OSError:
            pass
    meta = []
    started = time.time()

    def step() -> None:
        try:
            img = grab()
            if img is not None and img.width() > 4:
                idx = len(meta)
                img.save(str(seqdir / f'{idx:04d}.png'))
                meta.append({
                    'i': idx,
                    't': round(time.time() - started, 4),
                    'w': img.width(),
                    'h': img.height(),
                })
        except Exception:
            log('sequence grab failed\n' + traceback.format_exc())
        if (time.time() - started) * 1000 < duration_ms:
            QTimer.singleShot(interval_ms, step)
        else:
            (seqdir / 'times.json').write_text(
                json.dumps(meta, indent=0), encoding='utf-8'
            )
            log(f'sequence {name}: {len(meta)} frames in {time.time() - started:.2f}s')
            done()

    step()


def pick_lyric_seek(song_id: str) -> float:
    try:
        raw = (ROOT / 'data' / 'lyrics' / f'{song_id}.json').read_text(encoding='utf-8')
        data = json.loads(raw)
        yrc = data.get('yrc_lyric') or ''
        best = None
        for line in yrc.splitlines():
            line = line.strip()
            if not line.startswith('{'):
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            t = float(obj.get('t', 0)) / 1000.0
            text = ''.join(seg.get('tx', '') for seg in obj.get('c', []))
            if t < 35 or t > 150:
                continue
            letters = [ch for ch in text if ch.isalpha()]
            if len(letters) < 12:
                continue
            if any(ch in text for ch in '作词作曲制作人编曲'):
                continue
            best = t
            if 55 <= t <= 80:
                return t
        if best is not None:
            return best
    except Exception:
        log('pick lyric seek failed\n' + traceback.format_exc())
    return 62.0


def capture_image_sequence(
    name: str, grab, duration_ms: int, done, target_w: int = 1920
) -> None:
    seqdir = PUB / 'seq' / name
    seqdir.mkdir(parents=True, exist_ok=True)
    for old in seqdir.glob('*.jpg'):
        try:
            old.unlink()
        except OSError:
            pass
    for old in seqdir.glob('*.png'):
        try:
            old.unlink()
        except OSError:
            pass
    meta = []
    started = time.time()

    def step() -> None:
        try:
            img = grab()
            if img is not None and img.width() > 4:
                if img.width() > target_w:
                    img = img.scaledToWidth(
                        target_w, Qt.TransformationMode.SmoothTransformation
                    )
                img = img.convertToFormat(QImage.Format.Format_RGB32)
                idx = len(meta)
                img.save(str(seqdir / f'{idx:04d}.jpg'), 'JPEG', 90)
                meta.append({
                    'i': idx,
                    't': round(time.time() - started, 4),
                    'w': img.width(),
                    'h': img.height(),
                })
        except Exception:
            log('image seq grab failed\n' + traceback.format_exc())
        if (time.time() - started) * 1000 < duration_ms:
            QTimer.singleShot(12, step)
        else:
            (seqdir / 'times.json').write_text(json.dumps(meta), encoding='utf-8')
            log(f'sequence {name}: {len(meta)} frames in {time.time() - started:.2f}s')
            done()

    step()


def task_lyrics_seq(done) -> None:
    ctx = HOLDER['ctx']
    mw = HOLDER['mw']
    if not getattr(mw, 'dp_expanded', False):
        try:
            mw.togglePlayingPageExpand()
        except Exception:
            log('expand for seq failed\n' + traceback.format_exc())
    seek = pick_lyric_seek(HERO_ID)
    log(f'lyrics seq: seek {seek:.1f}s')

    def start() -> None:
        try:
            ctx.config.show_translation = True
            try:
                ctx.playing_page.translationToggled(True)
            except Exception:
                pass
            try:
                ctx.player.setVolume(0.0)
                ctx.player.setPosition(seek)
                ctx.player.play()
                log(
                    f'playing for seq, isPlaying={ctx.player.isPlaying()} pos={ctx.player.getPosition():.1f}'
                )
            except Exception:
                log('play for seq failed\n' + traceback.format_exc())
        except Exception:
            log('seq prep failed\n' + traceback.format_exc())
        delay(700, capture)

    def capture() -> None:
        viewer = ctx.playing_page.viewer
        capture_image_sequence(
            'lyrics', lambda: viewer.grabFramebuffer(), 2600, finish, 1920
        )

    def finish() -> None:
        try:
            ctx.player.pause()
        except Exception:
            pass
        done()

    delay(1300, start)


def task_eq_seq(done) -> None:
    ctx = HOLDER['ctx']
    stp = ctx.setting_page
    eq = stp.eq_edit
    original = eq.getBands() if hasattr(eq, 'getBands') else None
    rect = eq.rect()
    p1 = QPoint(int(rect.width() * 0.34), int(rect.height() * 0.62))
    p2 = QPoint(int(rect.width() * 0.52), int(rect.height() * 0.28))
    state = {'n': 0, 'meta': []}
    seqdir = PUB / 'seq' / 'eq_drag'
    seqdir.mkdir(parents=True, exist_ok=True)
    for old in seqdir.glob('*.png'):
        try:
            old.unlink()
        except OSError:
            pass
    started = time.time()

    def step() -> None:
        t = min(1.0, (time.time() - started) / 1.6)
        pos = QPoint(
            int(p1.x() + (p2.x() - p1.x()) * t), int(p1.y() + (p2.y() - p1.y()) * t)
        )
        try:
            QTest.mouseMove(eq, pos)
            img = eq.grab()
            idx = len(state['meta'])
            img.save(str(seqdir / f'{idx:04d}.png'))
            state['meta'].append({
                'i': idx,
                't': round(time.time() - started, 4),
                'x': pos.x(),
                'y': pos.y(),
            })
        except Exception:
            log('eq seq frame failed\n' + traceback.format_exc())
        if t < 1.0:
            QTimer.singleShot(33, step)
        else:
            try:
                QTest.mouseRelease(
                    eq, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p2
                )
            except Exception:
                log('eq release failed\n' + traceback.format_exc())
            (seqdir / 'times.json').write_text(
                json.dumps(state['meta']), encoding='utf-8'
            )
            log(f'sequence eq_drag: {len(state["meta"])} frames')
            if original is not None:
                try:
                    eq.setBands(original)
                    ctx.config.eq_bands = original
                except Exception:
                    log('eq restore failed\n' + traceback.format_exc())
            done()

    try:
        QTest.mousePress(
            eq, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p1
        )
    except Exception:
        log('eq press failed\n' + traceback.format_exc())
    delay(120, step)


def expand_all_sections() -> None:
    stp = HOLDER['ctx'].setting_page
    for section in stp._sections:
        try:
            if not section.isExpanded():
                section.setExpanded(True)
        except Exception:
            log('expand section failed\n' + traceback.format_exc())


def scroll_setting_to(needle: str) -> bool:
    stp = HOLDER['ctx'].setting_page
    try:
        content = stp.scroller.widget()
        for label in content.findChildren(QLabel):
            try:
                if needle in label.text():
                    pos = label.mapTo(content, QPoint(0, 0))
                    stp.scroller.verticalScrollBar().setValue(max(0, pos.y() - 150))
                    log(f'scrolled to {needle!r} y={pos.y()}')
                    return True
            except Exception:
                continue
    except Exception:
        log('scroll setting failed\n' + traceback.format_exc())
    return False


def task_crossfade_capture(done) -> None:
    scroll_setting_to('交叉')
    delay(
        900,
        lambda: (
            capture_page('settings_crossfade', HOLDER['ctx'].setting_page),
            done(),
        ),
    )


TASKS = [
    ('prepare', task_prepare),
    ('hero', task_hero),
    ('lyrics_seq', task_lyrics_seq),
    ('survey', task_survey),
    ('favorites', task_favorites),
    ('rediscovery', task_rediscovery),
    ('settings', task_settings),
    ('playlist', task_playlist),
    ('desktop_lyrics', task_desktop_lyrics),
]

ONLY = os.environ.get('ONLY', '')
if ONLY == 'desktop':
    TASKS = [
        ('prepare', task_prepare),
        ('hero', task_hero),
        ('desktop_lyrics', task_desktop_lyrics),
    ]
elif ONLY == 'lyrics':
    TASKS = [
        ('prepare', task_prepare),
        ('hero', task_hero),
        ('lyrics_seq', task_lyrics_seq),
    ]

STATE = {'task': 0, 'ready_since': None}


def run_task(index: int) -> None:
    if index >= len(TASKS):
        restore_and_quit()
        return
    name, fn = TASKS[index]
    log(f'task start: {name}')
    try:
        fn(lambda: (log(f'task done: {name}'), run_task(index + 1)))
    except Exception:
        log(f'task {name} crashed\n' + traceback.format_exc())
        run_task(index + 1)


def tick() -> None:
    try:
        if STATE['task'] != 0:
            return
        if not window_ready():
            STATE['ready_since'] = None
            STATE['fails'] = STATE.get('fails', 0) + 1
            if STATE['fails'] % 20 == 1:
                log(f'not ready yet (fail #{STATE["fails"]}): {ready_debug()}')
            return QTimer.singleShot(250, tick)
        if STATE['ready_since'] is None:
            STATE['ready_since'] = time.time()
            log('window ready, settling')
            return QTimer.singleShot(250, tick)
        if time.time() - STATE['ready_since'] < 2.5:
            return QTimer.singleShot(250, tick)
        log('settled, starting tasks')
        STATE['task'] = 1
        run_task(0)
    except Exception:
        log('TICK ERROR\n' + traceback.format_exc())
        restore_and_quit()


def patched_exec(self=None):
    QTimer.singleShot(200, tick)
    return _real_exec()


def patched_init(self, *args, **kwargs):
    _orig_init(self, *args, **kwargs)
    HOLDER['mw'] = self
    HOLDER['ctx'] = self.ctx
    log('MainWindow constructed')
    counter = {'n': 0}

    def _mt_tick():
        counter['n'] += 1
        log(f'mainthread tick {counter["n"]}')

    timer = QTimer(self)
    timer.setInterval(1000)
    timer.timeout.connect(_mt_tick)
    timer.start()
    self._capture_timer = timer


_orig_init = MainWindow.__init__
MainWindow.__init__ = patched_init
QApplication.exec = patched_exec

import services.update as update_module

update_module.startUpdateCheck = lambda *args, **kwargs: log('update check skipped')

log('running src/main.py')
import runpy

runpy.run_path(str(ROOT / 'src' / 'main.py'), run_name='__main__')
log('main.py returned')
