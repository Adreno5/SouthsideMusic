from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core import lyric_sources as sources
from core.lyric_sources import _qqBody, _tagText

QQ_XML = (
    '<command-lable-xwl78-qq-music><cmd value="1031"><result>0</result>'
    '<lyric musicid="2047277348"><content type="file">'
    '<![CDATA[4E6F74206120686578207061796C6F6164]]></content>'
    '<contentts><![CDATA[[00:00.000]QQ音乐享有本翻译作品的著作权\n'
    '[00:01.490]如果你看见我打来电话\n'
    '[00:03.164]请不要接]]></contentts>'
    '</lyric></cmd></command-lable-xwl78-qq-music>'
)


def test_qqBody_decodes_utf8_regardless_of_http_charset() -> None:
    text = _qqBody(QQ_XML.encode('utf-8'))
    assert 'QQ音乐享有本翻译作品的著作权' in text
    assert '\ufffd' not in text


def test_qqBody_guards_against_requests_latin1_fallback() -> None:
    data = QQ_XML.encode('utf-8')
    assert 'QQ音乐' not in data.decode('latin-1')
    assert 'QQ音乐' in _qqBody(data)


def test_tagText_extracts_plain_qrc_translation() -> None:
    text = _qqBody(QQ_XML.encode('utf-8'))
    lines = _tagText(text, 'contentts').splitlines()
    assert lines[0] == '[00:00.000]QQ音乐享有本翻译作品的著作权'
    assert lines[1] == '[00:01.490]如果你看见我打来电话'
    assert lines[2] == '[00:03.164]请不要接'


def test_tagText_ignores_undecryptable_payload() -> None:
    text = _qqBody(QQ_XML.encode('utf-8'))
    assert _tagText(text, 'content') == ''


def test_tagText_returns_empty_when_tag_missing() -> None:
    assert _tagText(_qqBody(QQ_XML.encode('utf-8')), 'contentroma') == ''


def _response(data: Any, text: str = '') -> Mock:
    response = Mock()
    response.json.return_value = data
    response.content = text.encode('utf-8')
    return response


def _cancel() -> Mock:
    cancel = Mock(spec=threading.Event)
    cancel.is_set.return_value = False
    cancel.wait.return_value = False
    return cancel


def test_matching_preserves_unicode_and_version_names() -> None:
    assert sources._nameScore('Đừng Nhấc Máy', 'Xin Đừng Nhấc Máy') == 4
    assert sources._nameScore('愛你', '爱你') == 7
    assert sources._nameScore('Song（feat. Guest）', 'song') == 6
    assert sources._nameScore('Song (live)', 'Song') == 4
    assert sources._nameScore('Song (acoustic version)', 'Song (acoustic)') == 6
    assert sources._nameScore('Song!', 'Song') == 4
    assert sources._nameScore('Ocean \U0001f30a', 'Ocean \U0001f4a7') == 4
    assert sources._artistScore(['Singer', 'Guest'], ['Guest', 'Singer']) == 7
    assert sources._artistScore(['Singer'], ['Other']) == 0


def test_match_duration_thresholds_and_missing_duration() -> None:
    track = sources._Track('Song', ('Singer',), 100000)
    matches = [
        sources._matchType(
            track, sources._Pick('Song', 100000 + delta, '', ('Singer',))
        )
        for delta in (0, 299, 300, 699, 700, 1499, 1500, 3499, 3500)
    ]
    assert matches == [100, 100, 100, 100, 100, 100, 99, 99, 90]
    assert sources._matchType(track, sources._Pick('Song', 0, '', ('Singer',))) == 100


def test_search_ranks_artists_instead_of_first_equal_title() -> None:
    track = sources._Track('Song', ('Singer', 'Guest'), 100000)
    wrong = sources._Pick('Song', 100000, 'wrong', ('Other',))
    correct = sources._Pick('Song', 100500, 'correct', ('Guest', 'Singer'))
    search = Mock(return_value=[wrong, correct])
    assert sources._searchTrack(search, track, _cancel()) == correct
    search.assert_called_once_with('Song Singer Guest')


def test_search_retries_full_queries_and_enforces_medium_match() -> None:
    track = sources._Track('Song (feat. Guest)', ('Singer',), 100000)
    wrong = sources._Pick('Unrelated', 200000, 'wrong', ('Other',))
    correct = sources._Pick('Song', 100000, 'correct', ('Singer',))
    search = Mock(side_effect=[[wrong], [wrong], [], [correct]])
    assert sources._searchTrack(search, track, _cancel()) == correct
    assert [call.args[0] for call in search.call_args_list] == [
        'Song (feat. Guest) Singer',
        'Song (feat. Guest) Singer',
        'Song Singer',
        'Song',
    ]
    assert sources._searchTrack(Mock(return_value=[wrong]), track, _cancel()) is None


def test_search_stops_before_request_when_cancelled() -> None:
    search = Mock()
    cancel = threading.Event()
    cancel.set()
    assert (
        sources._searchTrack(search, sources._Track('Song', ('Singer',), 0), cancel)
        is None
    )
    search.assert_not_called()


def test_qq_search_includes_groups_and_uses_reference_request() -> None:
    child = {
        'title': 'Song',
        'interval': 100,
        'id': 2,
        'mid': 'child',
        'singer': [{'name': 'Singer'}],
    }
    parent = {
        'title': 'Song (live)',
        'interval': 200,
        'id': 1,
        'mid': 'parent',
        'singer': [{'name': 'Singer'}],
        'group': [child],
    }
    response = _response({'req_1': {'data': {'body': {'song': {'list': [parent]}}}}})
    with patch.object(sources.requests, 'post', return_value=response) as post:
        tracks = sources._qqSearch('Song Singer')
    assert [track.payload['mid'] for track in tracks] == ['parent', 'child']
    assert tracks[1].artists == ('Singer',)
    assert tracks[1].duration == 100000
    assert post.call_args.kwargs['json']['req_1']['param']['num_per_page'] == '20'
    assert post.call_args.kwargs['headers']['Referer'] == 'https://c.y.qq.com/'


def test_qq_http_failure_falls_back_to_lrc() -> None:
    matched = sources._Pick('Song', 100000, {'id': '2', 'mid': 'child'}, ('Singer',))
    with (
        patch.object(sources, '_searchTrack', return_value=matched),
        patch.object(sources, '_qqQrcLyrics', side_effect=requests.HTTPError()),
        patch.object(
            sources,
            '_qqFallbackLyrics',
            return_value=('[00:01.000]hello', '[00:01.000]你好'),
        ) as fallback,
    ):
        raw = sources._fetchQq(sources._Track('Song', ('Singer',), 100000), _cancel())
    assert raw is not None and not raw.has_word
    assert raw.translations == ['你好']
    fallback.assert_called_once_with('child')


def test_qq_fallback_posts_jsonp_and_decodes_base64() -> None:
    response = _response(
        {}, 'MusicJsonCallback_lrc({"lyric":"WzAwOjAxLjAwMF1oZWxsbw==","trans":""});'
    )
    with patch.object(sources.requests, 'post', return_value=response) as post:
        assert sources._qqFallbackLyrics('mid') == ('[00:01.000]hello', '')
    assert post.call_args.kwargs['data']['songmid'] == 'mid'
    assert post.call_args.kwargs['data']['format'] == 'jsonp'


def test_kugou_search_retains_hash_and_grouped_tracks() -> None:
    parent = {
        'songname': 'Song',
        'singername': 'Singer、Guest',
        'duration': 100,
        'hash': 'parent',
        'group': [
            {
                'songname': 'Song',
                'singername': 'Singer',
                'duration': 101,
                'hash': 'child',
            }
        ],
    }
    with patch.object(
        sources.requests, 'get', return_value=_response({'data': {'info': [parent]}})
    ):
        tracks = sources._kugouSearch('Song Singer')
    assert [track.payload for track in tracks] == ['parent', 'child']
    assert tracks[0].artists == ('Singer', 'Guest')


def test_kugou_lyric_search_uses_matched_hash_and_duration() -> None:
    matched = sources._Pick('Song', 100000, 'hash', ('Singer',))
    with (
        patch.object(sources, '_searchTrack', return_value=matched),
        patch.object(
            sources.requests,
            'get',
            side_effect=[
                _response({'candidates': [{'id': '2', 'accesskey': 'key'}]}),
                _response({'content': 'encoded'}),
            ],
        ) as get,
        patch.object(sources, 'krcDecrypt', return_value='[1000,500]<0,500,0>hello'),
    ):
        raw = sources._fetchKugou(
            sources._Track('Song', ('Singer',), 100000), _cancel()
        )
    assert raw is not None and raw.has_word
    assert get.call_args_list[0].kwargs['params']['hash'] == 'hash'
    assert get.call_args_list[0].kwargs['params']['duration'] == 100000
    assert get.call_args_list[1].kwargs['params']['accesskey'] == 'key'


def test_soda_fetch_uses_matched_id_and_chinese_translation() -> None:
    matched = sources._Pick('Song', 100000, 'track-id', ('Singer',))
    with (
        patch.object(sources, '_searchTrack', return_value=matched),
        patch.object(
            sources.requests,
            'get',
            return_value=_response({
                'lyric': {
                    'content': '[00:01.000]hello',
                    'translations': {'cn': '[00:01.000]你好'},
                }
            }),
        ) as get,
    ):
        raw = sources._fetchSoda(sources._Track('Song', ('Singer',), 100000), _cancel())
    assert raw is not None and raw.translations == ['你好']
    assert get.call_args.kwargs['params']['track_id'] == 'track-id'


def test_lrclib_search_preserves_fractional_duration_and_splits_artists() -> None:
    entry = {
        'trackName': 'Song',
        'duration': 100.75,
        'artistName': 'Singer & Guest',
        'id': 3,
    }
    with patch.object(
        sources.requests, 'get', side_effect=[_response([]), _response([entry])]
    ) as get:
        tracks = sources._lrclibSearch('Song Singer')
    assert tracks[0].duration == 100750
    assert tracks[0].artists == ('Singer', 'Guest')
    assert get.call_args_list[0].kwargs['params'] == {
        'track_name': 'Song',
        'artist_name': 'Singer',
    }
    assert get.call_args_list[1].kwargs['params'] == {'track_name': 'Song Singer'}


def test_lrclib_fetch_uses_selected_id() -> None:
    with (
        patch.object(
            sources,
            '_searchTrack',
            return_value=sources._Pick('Song', 100000, 3, ('Singer',)),
        ),
        patch.object(
            sources.requests,
            'get',
            return_value=_response({'syncedLyrics': '[00:01.000]hello'}),
        ) as get,
    ):
        raw = sources._fetchLrclib(
            sources._Track('Song', ('Singer',), 100000), _cancel()
        )
    assert raw is not None and raw.lyric == '[00:01.000]hello'
    assert get.call_args.args[0] == 'https://lrclib.net/api/get/3'


def test_netease_public_uses_encrypted_word_lyric_endpoint() -> None:
    response = _response({
        'lrc': {'lyric': '[00:01.000]hello'},
        'yrc': {'lyric': '[1000,500](1000,500,0)hello'},
        'tlyric': {'lyric': '[00:01.000]旧'},
        'ytlrc': {'lyric': '[00:01.000]新'},
    })
    with patch.object(sources.requests, 'post', return_value=response) as post:
        raw = sources._fetchNeteasePublic('42', _cancel())
    assert raw is not None and raw.has_word and raw.translations == ['新']
    assert (
        post.call_args.args[0] == 'https://interface3.music.163.com/eapi/song/lyric/v1'
    )
    ciphertext = bytes.fromhex(post.call_args.kwargs['data']['params'])
    plaintext = unpad(
        AES.new(b'e82ckenh8dichen8', AES.MODE_ECB).decrypt(ciphertext), 16
    ).decode()
    path, payload, digest = plaintext.split('-36cd479b6b5-')
    assert path == '/api/song/lyric/v1' and len(digest) == 32
    data = json.loads(payload)
    assert data['id'] == '42' and data['yv'] == '0' and data['cp'] == 'false'
    assert json.loads(data['header'])['MUSIC_U'] == ''


def test_musixmatch_renews_token_then_retries() -> None:
    renew = {'message': {'header': {'status_code': 401, 'hint': 'renew'}}}
    success = {'message': {'header': {'status_code': 200}, 'body': {'track_list': []}}}
    token = {'message': {'header': {'status_code': 200}, 'body': {'user_token': 'new'}}}
    with (
        patch.object(sources, '_musixmatch_token', 'old'),
        patch.object(
            sources, '_musixmatchRequest', side_effect=[renew, token, success]
        ) as request,
    ):
        assert sources._musixmatchCall('track.search', {}, _cancel()) == success
    assert request.call_args_list[0].args[1]['usertoken'] == 'old'
    assert request.call_args_list[1].args[0] == 'token.get'
    assert request.call_args_list[2].args[1]['usertoken'] == 'new'


def test_musixmatch_captcha_does_not_retry() -> None:
    with (
        patch.object(sources, '_musixmatchToken', return_value='token'),
        patch.object(
            sources, '_musixmatchRequest', side_effect=PermissionError('captcha')
        ) as request,
    ):
        try:
            sources._musixmatchCall('track.search', {}, _cancel())
        except PermissionError:
            pass
        else:
            raise AssertionError('Captcha must stop requests')
    assert request.call_count == 1


def test_musixmatch_rejects_lyrics_for_other_track() -> None:
    search = {
        'message': {
            'body': {
                'track_list': [
                    {
                        'track': {
                            'track_name': 'Song',
                            'artist_name': 'Singer',
                            'track_id': 42,
                        }
                    }
                ]
            }
        }
    }
    wrong = {
        'message': {
            'body': {
                'macro_calls': {
                    'matcher.track.get': {
                        'message': {'body': {'track': {'track_id': 43}}}
                    },
                    'track.subtitles.get': {
                        'message': {
                            'header': {'status_code': 200},
                            'body': {
                                'subtitle_list': [
                                    {'subtitle': {'subtitle_body': '[00:01.000]wrong'}}
                                ]
                            },
                        }
                    },
                }
            }
        }
    }
    with patch.object(sources, '_musixmatchCall', side_effect=[search, *([wrong] * 5)]):
        assert (
            sources._fetchMusixmatch(
                sources._Track('Song', ('Singer',), 100000), _cancel()
            )
            is None
        )


def test_musixmatch_uses_reference_search_parameters_and_validated_lyrics() -> None:
    track = {
        'track_name': 'Song',
        'artist_name': 'Singer',
        'track_id': 42,
        'commontrack_vanity_id': 'Singer/Song',
    }
    search = {'message': {'body': {'track_list': [{'track': track}]}}}
    lyrics = {
        'message': {
            'body': {
                'macro_calls': {
                    'matcher.track.get': {'message': {'body': {'track': track}}},
                    'track.subtitles.get': {
                        'message': {
                            'header': {'status_code': 200},
                            'body': {
                                'subtitle_list': [
                                    {'subtitle': {'subtitle_body': '[00:01.000]hello'}}
                                ]
                            },
                        }
                    },
                }
            }
        }
    }
    with patch.object(sources, '_musixmatchCall', side_effect=[search, lyrics]) as call:
        raw = sources._fetchMusixmatch(
            sources._Track('Song', ('Singer',), 100750), _cancel()
        )
    assert raw is not None and raw.lyric == '[00:01.000]hello'
    assert call.call_args_list[0].args[1]['page_size'] == '10'
    assert call.call_args_list[0].args[1]['q_duration'] == 100


def test_pick_translation_prefers_source_in_use_over_closer_count() -> None:
    lyric = '[00:01.000]a\n[00:02.000]b\n[00:03.000]c'
    original = sources._Raw('kugou', lyric, '', False, [])
    paired = sources._Raw('kugou', lyric, '', False, ['甲', '乙'])
    closer = sources._Raw('qq', lyric, '', False, ['甲', '乙', '丙'])
    assert sources._pickTranslation(original, [closer, paired]) is paired


def test_pick_translation_falls_back_to_closest_line_count() -> None:
    original = sources._Raw(
        'musixmatch', '[00:01.000]a\n[00:02.000]b\n[00:03.000]c', '', False, []
    )
    far = sources._Raw('qq', 'x', '', False, ['甲'])
    near = sources._Raw('kugou', 'x', '', False, ['甲', '乙', '丙'])
    assert sources._pickTranslation(original, [far, near]) is near


def test_pick_translation_ignores_placeholder_and_credit_lines() -> None:
    original = sources._Raw('lrclib', '[00:01.000]a', '', False, [])
    hollow = sources._Raw(
        'netease-public',
        'x',
        '',
        False,
        ['', '', 'QQ音乐享有本翻译作品的著作权'],
    )
    assert sources._translationLines(hollow) == []
    assert sources._pickTranslation(original, [hollow]) is None


def test_iter_lyric_updates_keeps_in_use_source_translation() -> None:
    netease_release = threading.Event()
    qq_raw = sources._Raw('qq', '[00:01.000]a\n[00:02.000]b', '', False, ['甲', '乙'])
    netease_raw = sources._Raw(
        'netease-ncm',
        '[00:01.000]a\n[00:02.000]b',
        '[1000,500](1000,500,0)a\n[2000,500](2000,500,0)b',
        True,
        ['甲', '乙'],
    )

    def fetch_qq(track: Any, cancel: Any) -> sources._Raw:
        return qq_raw

    def fetch_netease(netease_id: Any, cancel: Any) -> sources._Raw:
        netease_release.wait(2)
        return netease_raw

    tasks = [
        ('qq', fetch_qq, (None,)),
        ('netease-ncm', fetch_netease, (None,)),
    ]
    with patch.object(sources, '_tasks', return_value=tasks):
        updates = sources.iterLyricUpdates('Song', 'Singer', '42', 100000)
        first = next(updates)
        netease_release.set()
        second = next(updates)
        rest = list(updates)
    assert first.source == 'qq' and first.translation_source == 'qq'
    assert second.source == 'netease-ncm'
    assert second.translation_source == 'netease-ncm'
    assert rest == []


def test_iter_lyric_updates_switches_to_closest_translation_source() -> None:
    release = {'qq': threading.Event(), 'kugou': threading.Event()}
    lrclib_raw = sources._Raw(
        'lrclib', '[00:01.000]a\n[00:02.000]b\n[00:03.000]c', '', False, []
    )
    qq_raw = sources._Raw('qq', 'x', '', False, ['甲'])
    kugou_raw = sources._Raw('kugou', 'x', '', False, ['甲', '乙', '丙'])

    def fetch_lrclib(track: Any, cancel: Any) -> sources._Raw:
        return lrclib_raw

    def fetch_qq(track: Any, cancel: Any) -> sources._Raw:
        release['qq'].wait(2)
        return qq_raw

    def fetch_kugou(track: Any, cancel: Any) -> sources._Raw:
        release['kugou'].wait(2)
        return kugou_raw

    tasks = [
        ('lrclib', fetch_lrclib, (None,)),
        ('qq', fetch_qq, (None,)),
        ('kugou', fetch_kugou, (None,)),
    ]
    with patch.object(sources, '_tasks', return_value=tasks):
        updates = sources.iterLyricUpdates('Song', 'Singer', '42', 100000)
        first = next(updates)
        release['qq'].set()
        second = next(updates)
        release['kugou'].set()
        third = next(updates)
        rest = list(updates)
    assert first.source == 'lrclib' and first.translation_source == ''
    assert second.source == 'lrclib' and second.translation_source == 'qq'
    assert third.source == 'lrclib' and third.translation_source == 'kugou'
    assert rest == []


def test_exceeds_duration_compares_last_line_against_song_length() -> None:
    raw = sources._Raw('kugou', '[00:10.000]a\n[01:00.000]b', '', False, [])
    assert sources._exceedsDuration(raw, 50000)
    assert not sources._exceedsDuration(raw, 60000)
    assert not sources._exceedsDuration(raw, 0)
    word = sources._Raw(
        'netease-ncm',
        '[00:10.000]a',
        '[10000,500](10000,500,0)a\n[300000,500](300000,500,0)b',
        True,
        [],
    )
    assert sources._exceedsDuration(word, 60000)
    assert not sources._exceedsDuration(
        sources._Raw('lrclib', '', '', False, []), 60000
    )


def test_exceeds_duration_ignores_trailing_credit_line() -> None:
    raw = sources._Raw('qq', '[00:10.000]a\n[09:00.000]作词: someone', '', False, [])
    assert not sources._exceedsDuration(raw, 50000)


def test_iter_lyric_updates_ignores_source_longer_than_song() -> None:
    qq_release = threading.Event()
    valid = sources._Raw('netease-ncm', '[00:10.000]a\n[00:20.000]b', '', False, [])
    too_long = sources._Raw('qq', '[00:10.000]a\n[05:00.000]b', '', False, ['甲', '乙'])

    def fetch_netease(netease_id: Any, cancel: Any) -> sources._Raw:
        return valid

    def fetch_qq(track: Any, cancel: Any) -> sources._Raw:
        qq_release.wait(2)
        return too_long

    tasks = [
        ('netease-ncm', fetch_netease, (None,)),
        ('qq', fetch_qq, (None,)),
    ]
    with patch.object(sources, '_tasks', return_value=tasks):
        updates = sources.iterLyricUpdates('Song', 'Singer', '42', 60000)
        first = next(updates)
        qq_release.set()
        rest = list(updates)
    assert first.source == 'netease-ncm' and first.translation_source == ''
    assert rest == []


def test_iter_lyric_updates_rejects_sole_source_longer_than_song() -> None:
    too_long = sources._Raw('qq', '[00:10.000]a\n[05:00.000]b', '', False, [])

    def fetch_qq(track: Any, cancel: Any) -> sources._Raw:
        return too_long

    tasks = [('qq', fetch_qq, (None,))]
    with patch.object(sources, '_tasks', return_value=tasks):
        assert list(sources.iterLyricUpdates('Song', 'Singer', '42', 60000)) == []


def main() -> None:
    checks = sorted(name for name in globals() if name.startswith('test_'))
    for name in checks:
        globals()[name]()
    print(f'test_lyric_sources: {len(checks)} checks passed')


if __name__ == '__main__':
    main()
