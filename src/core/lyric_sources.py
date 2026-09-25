from __future__ import annotations

import base64
import html
import json
import logging
import re
import threading
import time
import zlib
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, NamedTuple

import requests

from core.lyric_formats import (
    LyricLine,
    alignTranslation,
    contentLines,
    hasWordTiming,
    krcDecrypt,
    krcTranslations,
    parseAny,
    parseKrc,
    parseLrc,
    parseRichsync,
    qrcDecrypt,
    toLrc,
    toYrc,
    translationTexts,
    yrcToLrc,
)

_logger = logging.getLogger(__name__)

_TIMEOUT = 6
_UA = (
    'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/63.0.3239.132 Safari/537.36'
)
_WEB_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
)
_QQ_HEADERS = {'User-Agent': _UA, 'Referer': 'https://y.qq.com/'}
_NETEASE_PUBLIC_HEADERS = {'User-Agent': _UA, 'Referer': 'https://music.163.com/'}
_KUGOU_HEADERS = {'User-Agent': _UA}
_SODA_HEADERS = {
    'Accept': '*/*',
    'User-Agent': (
        'com.luna.music/100198030 (Linux; U; Android 15; zh_CN_#Hans; '
        'ABR-AL80; Build/V417IR;tt-ok/3.12.13.19)'
    ),
}
_LRCLIB_HEADERS = {'User-Agent': 'SouthsideMusic (https://github.com/)'}
_MUSIXMATCH_HEADERS = {
    'User-Agent': 'Dalvik/2.1.0 (Linux; U; Android 13)',
    'Cookie': 'AWSELB=0; AWSELBCORS=0',
}
_MUSIXMATCH_BASE = 'https://apic.musixmatch.com/ws/1.1/'
_MUSIXMATCH_APP_ID = 'android-player-v1.0'
_MATCH_TOLERANCE_MS = 10000

_SODA_QUERY = {
    'device_platform': 'android',
    'os': 'android',
    'ssmix': 'a',
    'cdid': '46556f98-1720-4248-83da-62b74b60b46a',
    'channel': 'xiaomi_8478_64',
    'aid': '386088',
    'app_name': 'luna',
    'version_code': '100198030',
    'version_name': '19.8.0',
    'manifest_version_code': '100198030',
    'update_version_code': '100198030',
    'resolution': '1080*1920',
    'dpi': '480',
    'device_type': 'ABR-AL80',
    'device_brand': 'HUAWEI',
    'language': 'zh',
    'os_api': '35',
    'os_version': '15',
    'ac': 'wifi',
    'device_model': 'ABR-AL80',
    'tz_name': 'Asia/Shanghai',
    'tz_offset': '28800',
    'package': 'com.luna.music',
    'sim_region': 'cn',
    'iid': '3729104815266374981',
    'device_id': '3729104815266374981',
    'cursor': '0',
    'count': '20',
}

_NORMALIZE_RE = re.compile(r'[^0-9a-z\u4e00-\u9fff]+')


@dataclass
class LyricCandidate:
    source: str
    lyric: str
    yrc_lyric: str
    translated_lyric: str
    has_word: bool
    translation_source: str = ''


class _Raw(NamedTuple):
    source: str
    lyric: str
    yrc_lyric: str
    has_word: bool
    translations: list[str]


class _Pick(NamedTuple):
    name: str
    duration: int
    payload: Any


def _toInt(value: object) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub('', text.lower())


def _near(duration: int, duration_ms: int) -> bool:
    return bool(duration) and abs(duration - duration_ms) <= _MATCH_TOLERANCE_MS


def _pickTrack(items: Sequence[_Pick], title: str, duration_ms: int) -> Any | None:
    if not items:
        return None
    target = _normalize(title)
    matched = [item for item in items if target and _normalize(item.name) == target]
    pool = matched or list(items)
    if duration_ms <= 0:
        return pool[0].payload
    within = [item for item in pool if _near(item.duration, duration_ms)]
    if not within:
        within = [item for item in items if _near(item.duration, duration_ms)]
    if not within:
        return None
    return min(within, key=lambda item: abs(item.duration - duration_ms)).payload


def _textRaw(
    source: str, lines: Sequence[LyricLine], translations: Sequence[str]
) -> _Raw | None:
    content = contentLines(lines)
    if not content:
        return None
    word = hasWordTiming(content)
    return _Raw(
        source=source,
        lyric=toLrc(content),
        yrc_lyric=toYrc(content) if word else '',
        has_word=word,
        translations=list(translations),
    )


def _buildRaw(lyrics: Mapping[str, str]) -> _Raw | None:
    lyric = (lyrics.get('lyric') or '').strip()
    yrc = (lyrics.get('yrc_lyric') or '').strip()
    if not lyric and not yrc:
        return None
    return _Raw(
        source='cache',
        lyric=lyric or yrcToLrc(yrc),
        yrc_lyric=yrc,
        has_word=bool(yrc),
        translations=translationTexts(lyrics.get('translated_lyric') or ''),
    )


def _neteaseRaw(source: str, lyric: str, yrc: str, translated: str) -> _Raw | None:
    lyric = lyric.strip()
    yrc = yrc.strip()
    if not lyric and not yrc:
        return None
    return _Raw(
        source=source,
        lyric=lyric or yrcToLrc(yrc),
        yrc_lyric=yrc,
        has_word=bool(yrc),
        translations=translationTexts(translated),
    )


def _assemble(raw: _Raw, translation: _Raw | None) -> LyricCandidate:
    display = raw.yrc_lyric if raw.has_word and raw.yrc_lyric else raw.lyric
    translated = ''
    translation_source = ''
    if translation is not None:
        translated = alignTranslation(parseAny(display), translation.translations)
        translation_source = translation.source
    return LyricCandidate(
        source=raw.source,
        lyric=raw.lyric,
        yrc_lyric=raw.yrc_lyric,
        translated_lyric=translated,
        has_word=raw.has_word,
        translation_source=translation_source,
    )


def _fetchNeteaseNcm(netease_id: str, cancel: threading.Event) -> _Raw | None:
    if not netease_id or cancel.is_set():
        return None
    from core.backend import getBackend

    info = getBackend().getTrackLyrics(netease_id)
    return _neteaseRaw(
        'netease-ncm',
        info.lyric or '',
        info.yrc_lyric or '',
        info.translated_lyric or '',
    )


def _fetchNeteasePublic(netease_id: str, cancel: threading.Event) -> _Raw | None:
    if not netease_id or cancel.is_set():
        return None
    response = requests.get(
        'https://music.163.com/api/song/lyric',
        params={'id': netease_id, 'lv': '-1', 'kv': '-1', 'tv': '-1', 'rv': '-1'},
        headers=_NETEASE_PUBLIC_HEADERS,
        timeout=_TIMEOUT,
    )
    data = response.json()
    if not isinstance(data, dict):
        return None

    def block(name: str) -> str:
        value = data.get(name)
        return str(value.get('lyric') or '') if isinstance(value, dict) else ''

    return _neteaseRaw('netease-public', block('lrc'), block('yrc'), block('tlyric'))


def _qqSearch(query: str) -> list[_Pick]:
    response = requests.post(
        'https://u.y.qq.com/cgi-bin/musicu.fcg',
        data=json.dumps(
            {
                'music.search.SearchCgiService': {
                    'method': 'DoSearchForQQMusicDesktop',
                    'module': 'music.search.SearchCgiService',
                    'param': {
                        'num_per_page': 10,
                        'page_num': 1,
                        'query': query,
                        'search_type': 0,
                    },
                }
            }
        ),
        headers=_QQ_HEADERS,
        timeout=_TIMEOUT,
    )
    songs = (
        response.json()
        .get('music.search.SearchCgiService', {})
        .get('data', {})
        .get('body', {})
        .get('song', {})
        .get('list')
        or []
    )
    return [
        _Pick(
            name=str(song.get('name') or ''),
            duration=_toInt(song.get('interval')) * 1000,
            payload={
                'id': str(song.get('id') or ''),
                'mid': str(song.get('mid') or ''),
            },
        )
        for song in songs
        if isinstance(song, dict)
    ]


def _tagText(text: str, tag: str) -> str:
    match = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', text, re.S)
    if match is None:
        return ''
    body = match.group(1).strip()
    if body.startswith('<![CDATA['):
        body = body[len('<![CDATA[') :].rstrip()
        body = body[:-3] if body.endswith(']]>') else body
    if not body or body.startswith('['):
        return body
    try:
        decrypted = qrcDecrypt(body)
    except (TypeError, ValueError, OSError, zlib.error):
        return ''
    if 'LyricContent' not in decrypted:
        return decrypted
    inner = re.search(r'LyricContent="(.*?)"', decrypted, re.S)
    return html.unescape(inner.group(1)) if inner else ''


def _qqBody(content: bytes) -> str:
    return content.decode('utf-8', 'replace').replace('<!--', '').replace('-->', '')


def _qqQrcLyrics(song_id: str) -> tuple[str, str]:
    response = requests.post(
        'https://c.y.qq.com/qqmusic/fcgi-bin/lyric_download.fcg',
        data={'version': '15', 'miniversion': '82', 'lrctype': '4', 'musicid': song_id},
        headers=_QQ_HEADERS,
        timeout=_TIMEOUT,
    )
    text = _qqBody(response.content)
    return _tagText(text, 'content'), _tagText(text, 'contentts')


def _qqFallbackLyrics(song_mid: str) -> tuple[str, str]:
    response = requests.get(
        'https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg',
        params={
            'songmid': song_mid,
            'format': 'json',
            'g_tk': '5381',
            'loginUin': '0',
            'hostUin': '0',
            'inCharset': 'utf8',
            'outCharset': 'utf-8',
            'notice': '0',
            'platform': 'yqq',
            'needNewCode': '0',
        },
        headers=_QQ_HEADERS,
        timeout=_TIMEOUT,
    )
    data = response.json()
    if not isinstance(data, dict):
        return '', ''

    def decode(name: str) -> str:
        value = str(data.get(name) or '')
        try:
            return base64.b64decode(value).decode('utf-8', 'replace')
        except (TypeError, ValueError):
            return ''

    return decode('lyric'), decode('trans')


def _fetchQq(
    title: str, artist: str, duration_ms: int, cancel: threading.Event
) -> _Raw | None:
    song = _pickTrack(_qqSearch(f'{title} {artist}'.strip()), title, duration_ms)
    if not isinstance(song, dict) or cancel.is_set():
        return None
    lyrics, translated = _qqQrcLyrics(str(song.get('id') or ''))
    if not lyrics:
        lyrics, translated = _qqFallbackLyrics(str(song.get('mid') or ''))
    if not lyrics:
        return None
    return _textRaw('qq', parseAny(lyrics), translationTexts(translated))


def _fetchKugou(
    title: str, artist: str, duration_ms: int, cancel: threading.Event
) -> _Raw | None:
    response = requests.get(
        'https://lyrics.kugou.com/search',
        params={
            'ver': '1',
            'man': 'yes',
            'client': 'pc',
            'keyword': f'{artist} {title}'.strip(),
        },
        headers=_KUGOU_HEADERS,
        timeout=_TIMEOUT,
    )
    candidates = response.json().get('candidates') or []
    items = [
        _Pick(
            name=str(candidate.get('song') or ''),
            duration=_toInt(candidate.get('duration')),
            payload=candidate,
        )
        for candidate in candidates
        if isinstance(candidate, dict)
    ]
    candidate = _pickTrack(items, title, duration_ms)
    if not isinstance(candidate, dict) or cancel.is_set():
        return None
    response = requests.get(
        'https://lyrics.kugou.com/download',
        params={
            'ver': '1',
            'client': 'pc',
            'id': str(candidate.get('id') or ''),
            'accesskey': str(candidate.get('accesskey') or ''),
            'fmt': 'krc',
            'charset': 'utf8',
        },
        headers=_KUGOU_HEADERS,
        timeout=_TIMEOUT,
    )
    content = response.json().get('content')
    if not content:
        return None
    text = krcDecrypt(str(content))
    return _textRaw('kugou', parseKrc(text), krcTranslations(text))


def _fetchSoda(
    title: str, artist: str, duration_ms: int, cancel: threading.Event
) -> _Raw | None:
    query = dict(_SODA_QUERY)
    query['q'] = f'{title} {artist}'.strip()
    query['_rticket'] = str(_toInt(time.time() * 1000))
    response = requests.get(
        'https://api.qishui.com/luna/search/track',
        params=query,
        headers=_SODA_HEADERS,
        timeout=_TIMEOUT,
    )
    tracks: list[_Pick] = []
    for group in response.json().get('result_groups') or []:
        for item in group.get('data') or []:
            if not isinstance(item, dict):
                continue
            if (item.get('meta') or {}).get('item_type') != 'track':
                continue
            track = (item.get('entity') or {}).get('track')
            if not isinstance(track, dict):
                continue
            tracks.append(
                _Pick(
                    name=str(track.get('name') or ''),
                    duration=_toInt(track.get('duration')),
                    payload=str(track.get('id') or ''),
                )
            )
    track_id = _pickTrack(tracks, title, duration_ms)
    if not isinstance(track_id, str) or not track_id or cancel.is_set():
        return None
    response = requests.get(
        'https://beta-luna.douyin.com/luna/h5/seo_track',
        params={'track_id': track_id, 'device_platform': 'web'},
        headers={'Accept': 'application/json', 'User-Agent': _WEB_UA},
        timeout=_TIMEOUT,
    )
    lyric = response.json().get('lyric') or {}
    if not isinstance(lyric, dict):
        return None
    translations = lyric.get('translations')
    chinese = translations.get('cn') if isinstance(translations, dict) else None
    return _textRaw(
        'soda',
        parseAny(str(lyric.get('content') or '')),
        translationTexts(chinese) if isinstance(chinese, str) else [],
    )


def _fetchLrclib(
    title: str, artist: str, duration_ms: int, cancel: threading.Event
) -> _Raw | None:
    response = requests.get(
        'https://lrclib.net/api/get',
        params={
            'track_name': title,
            'artist_name': artist,
            'duration': round(duration_ms / 1000) if duration_ms > 0 else None,
        },
        headers=_LRCLIB_HEADERS,
        timeout=_TIMEOUT,
    )
    if response.status_code == 404:
        response = requests.get(
            'https://lrclib.net/api/search',
            params={'track_name': title, 'artist_name': artist},
            headers=_LRCLIB_HEADERS,
            timeout=_TIMEOUT,
        )
        results = response.json() if response.status_code == 200 else []
        if not isinstance(results, list):
            return None
        items = [
            _Pick(
                name=str(item.get('trackName') or ''),
                duration=_toInt(item.get('duration')) * 1000,
                payload=item,
            )
            for item in results
            if isinstance(item, dict)
        ]
        entry = _pickTrack(items, title, duration_ms)
    else:
        entry = response.json() if response.status_code == 200 else None
    if not isinstance(entry, dict) or cancel.is_set():
        return None
    return _textRaw('lrclib', parseLrc(str(entry.get('syncedLyrics') or '')), [])


def _musixmatchToken(cancel: threading.Event) -> str:
    response = requests.get(
        _MUSIXMATCH_BASE + 'token.get',
        params={
            'user_language': 'en',
            'app_id': _MUSIXMATCH_APP_ID,
            't': str(time.time()),
        },
        headers=_MUSIXMATCH_HEADERS,
        timeout=_TIMEOUT,
    )
    token = ((response.json().get('message') or {}).get('body') or {}).get(
        'user_token'
    ) or ''
    return str(token) if token and not cancel.is_set() else ''


def _musixmatchCall(request: str, params: dict[str, Any], token: str) -> dict[str, Any]:
    merged = dict(params)
    merged.update(
        {
            'usertoken': token,
            'format': 'json',
            'app_id': _MUSIXMATCH_APP_ID,
            't': str(time.time()),
        }
    )
    response = requests.get(
        _MUSIXMATCH_BASE + request,
        params=merged,
        headers=_MUSIXMATCH_HEADERS,
        timeout=_TIMEOUT,
    )
    data = response.json()
    return data if isinstance(data, dict) else {}


def _fetchMusixmatch(
    title: str, artist: str, duration_ms: int, cancel: threading.Event
) -> _Raw | None:
    token = _musixmatchToken(cancel)
    if not token:
        return None
    data = _musixmatchCall(
        'track.search',
        {
            'q_track': title,
            'q_artist': artist,
            'q_duration': str(round(duration_ms / 1000)) if duration_ms > 0 else None,
            'page_size': '5',
            'page': '1',
            's_track_rating': 'desc',
        },
        token,
    )
    track_list = ((data.get('message') or {}).get('body') or {}).get('track_list') or []
    items = [
        _Pick(
            name=str((item.get('track') or {}).get('track_name') or ''),
            duration=_toInt((item.get('track') or {}).get('track_length')) * 1000,
            payload=item.get('track'),
        )
        for item in track_list
        if isinstance(item, dict)
    ]
    track = _pickTrack(items, title, duration_ms)
    if not isinstance(track, dict) or cancel.is_set():
        return None
    track_id = str(track.get('track_id') or '')
    if not track_id:
        return None
    data = _musixmatchCall(
        'macro.subtitles.get',
        {
            'namespace': 'lyrics_richsynched',
            'optional_calls': 'track.richsync',
            'subtitle_format': 'lrc',
            'track_id': track_id,
            'f_subtitle_length_max_deviation': '40',
        },
        token,
    )
    calls = ((data.get('message') or {}).get('body') or {}).get('macro_calls') or {}
    if not isinstance(calls, dict):
        return None
    richsync = ((calls.get('track.richsync.get') or {}).get('message') or {}).get(
        'body'
    ) or {}
    richsync_body = str((richsync.get('richsync') or {}).get('richsync_body') or '')
    if richsync_body:
        raw = _textRaw('musixmatch', parseRichsync(richsync_body), [])
        if raw is not None:
            return raw
    subtitles = ((calls.get('track.subtitles.get') or {}).get('message') or {}).get(
        'body'
    ) or {}
    subtitle_list = subtitles.get('subtitle_list') or []
    if not subtitle_list:
        return None
    subtitle = (subtitle_list[0].get('subtitle') or {}).get('subtitle_body') or ''
    return _textRaw('musixmatch', parseLrc(str(subtitle)), [])


def _tasks(
    title: str, artist: str, netease_id: str, duration_ms: int
) -> list[tuple[str, Any, tuple[Any, ...]]]:
    return [
        ('netease-ncm', _fetchNeteaseNcm, (netease_id,)),
        ('netease-public', _fetchNeteasePublic, (netease_id,)),
        ('qq', _fetchQq, (title, artist, duration_ms)),
        ('kugou', _fetchKugou, (title, artist, duration_ms)),
        ('soda', _fetchSoda, (title, artist, duration_ms)),
        ('lrclib', _fetchLrclib, (title, artist, duration_ms)),
        ('musixmatch', _fetchMusixmatch, (title, artist, duration_ms)),
    ]


def iterLyricUpdates(
    title: str,
    artist: str,
    netease_id: str,
    duration_ms: int,
    cached: Mapping[str, str] | None = None,
) -> Iterator[LyricCandidate]:
    tasks = _tasks(title, artist, netease_id, duration_ms)
    cancel = threading.Event()
    executor = ThreadPoolExecutor(max_workers=len(tasks))
    original = _buildRaw(cached) if cached else None
    translation = original if original is not None and original.translations else None
    try:
        futures = {
            executor.submit(fetch, *args, cancel): name for name, fetch, args in tasks
        }
        pending = set(futures)
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                name = futures[future]
                try:
                    raw = future.result()
                except Exception as error:
                    _logger.info('lyric source %s failed: %r', name, error)
                    continue
                if raw is None:
                    _logger.info('lyric source %s returned nothing', name)
                    continue
                changed = False
                if raw.translations and translation is None:
                    translation = raw
                    changed = True
                if raw.lyric or raw.yrc_lyric:
                    if original is None or (raw.has_word and not original.has_word):
                        original = raw
                        changed = True
                        if raw.translations:
                            translation = raw
                if not changed or original is None:
                    continue
                _logger.info(
                    'lyric update from %s: words=%s translation=%s',
                    original.source,
                    original.has_word,
                    translation.source if translation else '',
                )
                yield _assemble(original, translation)
            if original is not None and original.has_word and translation is not None:
                return
    finally:
        cancel.set()
        executor.shutdown(wait=False, cancel_futures=True)
