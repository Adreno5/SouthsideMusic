from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.lyric_formats import (  # noqa: E402
    alignTranslation,
    contentLines,
    hasWordTiming,
    isInfoLine,
    krcTranslations,
    parseAny,
    parseKrc,
    parseLrc,
    parseQrc,
    parseRichsync,
    parseYrc,
    toLrc,
    translationTexts,
    toYrc,
)

KRC = (
    '[offset:0]\n'
    '[1524,3610]<0,330,0>还<330,480,0>记<810,310,0>得\n'
    '[5154,2990]<0,390,0>随<390,170,0>着\n'
)

YRC = (
    '{"t":0,"c":[{"tx":"作词: "},{"tx":"周杰伦"}]}\n'
    '[10000,2000](10000,500,0)还(10500,500,0)记(11000,1000,0)得\n'
    '[20000,2000](20000,500,0)随(20500,500,0)着\n'
)


def test_parseKrc_word_timing_is_relative_to_line_start() -> None:
    lines = parseKrc(KRC)
    assert len(lines) == 2
    assert lines[0].start == 1524
    assert lines[0].end == 5134
    assert [word.start for word in lines[0].words] == [1524, 1854, 2334]
    assert lines[0].text == '还记得'
    assert hasWordTiming(lines)


def test_generate_yrc_and_lrc_from_krc() -> None:
    lines = parseKrc(KRC)
    assert (
        toYrc(lines).splitlines()[0]
        == '[1524,3610](1524,330,0)还(1854,480,0)记(2334,310,0)得'
    )
    assert toLrc(lines).splitlines() == ['[00:01.524]还记得', '[00:05.154]随着']


def test_krcTranslations_keeps_positions_and_blanks_placeholders() -> None:
    payload = base64.b64encode(
        json.dumps(
            {'content': [{'type': 1, 'lyricContent': [['一'], ['//'], ['三']]}]}
        ).encode()
    ).decode()
    assert krcTranslations(f'[language:{payload}]\n{KRC}') == ['一', '', '三']
    assert krcTranslations(KRC) == []


def test_parseQrc_word_timing_is_absolute() -> None:
    lines = parseQrc('[10268,4799]风(10268,332)吹(10600,300)过(10900,331)\n')
    assert len(lines) == 1
    assert lines[0].start == 10268
    assert [word.start for word in lines[0].words] == [10268, 10600, 10900]
    assert lines[0].text == '风吹过'


def test_parseYrc_reads_credits_and_absolute_word_times() -> None:
    lines = parseYrc(YRC)
    assert len(lines) == 3

    credit = lines[0]
    assert credit.is_metadata
    assert credit.start == 0
    assert credit.text == '作词：周杰伦'

    lyric = lines[1]
    assert not lyric.is_metadata
    assert lyric.start == 10000
    assert lyric.end == 12000
    assert [word.start for word in lyric.words] == [10000, 10500, 11000]
    assert lyric.text == '还记得'

    assert lines[2].start == 20000
    assert lines[2].text == '随着'


def test_contentLines_drops_metadata_and_blank_text() -> None:
    lines = parseYrc(YRC)
    content = contentLines(lines)
    assert [line.text for line in content] == ['还记得', '随着']
    assert all(not line.is_metadata for line in content)


def test_parseLrc_skips_metadata_and_fills_line_end() -> None:
    lines = parseLrc('[ar:artist]\n[00:30.85] 對這個世界\n[00:34.17]跌倒了\n')
    assert len(lines) == 2
    assert lines[0].start == 30850
    assert lines[0].text == '對這個世界'
    assert lines[0].end == 34170
    assert not hasWordTiming(lines)
    assert toYrc(lines) == ''


def test_alignTranslation_restamps_by_index_not_by_time() -> None:
    original = parseLrc(
        '[00:10.00]line one\n[00:20.00]line two\n[00:30.00]line three\n'
    )
    translated = ['一', '二', '三']
    assert alignTranslation(original, translated).splitlines() == [
        '[00:10.000]一',
        '[00:20.000]二',
        '[00:30.000]三',
    ]


def test_alignTranslation_ignores_metadata_lines_when_counting() -> None:
    lines = parseYrc(YRC)
    assert alignTranslation(lines, ['one', 'two']).splitlines() == [
        '[00:10.000]one',
        '[00:20.000]two',
    ]


def test_alignTranslation_keeps_blank_placeholder_slots() -> None:
    original = parseLrc('[00:10.00]a\n[00:20.00]b\n[00:30.00]c\n')
    assert alignTranslation(original, ['一', '', '三']).splitlines() == [
        '[00:10.000]一',
        '[00:30.000]三',
    ]


def test_alignTranslation_stops_at_the_shorter_side() -> None:
    original = parseLrc('[00:10.00]a\n[00:20.00]b\n')
    assert alignTranslation(original, ['一']).splitlines() == ['[00:10.000]一']
    assert alignTranslation(original, []) == ''


def test_parseAny_detects_each_format() -> None:
    assert hasWordTiming(parseAny(KRC))
    assert hasWordTiming(parseAny(YRC))
    assert hasWordTiming(parseAny('[10268,4799]风(10268,332)吹(10600,300)\n'))
    assert not hasWordTiming(parseAny('[00:30.85]text\n'))
    assert parseAny('') == []


def test_musixmatch_richsync_word_positions() -> None:
    raw = json.dumps(
        [
            {
                'ts': 1.0,
                'te': 2.0,
                'x': 'Hello you',
                'l': [{'c': 'Hello', 'o': 0}, {'c': ' you', 'o': 0.5}],
            }
        ]
    )
    lines = parseRichsync(raw)
    assert len(lines) == 1
    assert lines[0].start == 1000
    assert [word.start for word in lines[0].words] == [1000, 1500]
    assert lines[0].words[-1].end == 2000


def test_translationTexts_rejects_timestamp_only_placeholder() -> None:
    placeholder = '[00:00.950]\n[00:01.950]\n[00:02.950]\n'
    assert translationTexts(placeholder) == []


def test_translationTexts_reads_lrc_qrc_and_plain_text() -> None:
    assert translationTexts('[00:10.00]一\n[00:20.00]二\n') == ['一', '二']
    assert translationTexts('[0,1000]一(0,500)\n') == ['一']
    assert translationTexts('[00:10.00]//\n[00:20.00]二\n') == ['', '二']
    assert translationTexts('一\n二\n') == ['一', '二']
    assert translationTexts('') == []
    assert translationTexts('   ') == []


def test_isInfoLine_detects_section_markers() -> None:
    assert isInfoLine('[Pre-Hook]')
    assert isInfoLine('[Hook]')
    assert isInfoLine('[Verse 1]')
    assert isInfoLine('  [Bridge]  ')
    assert isInfoLine('【副歌】')


def test_isInfoLine_detects_credit_lines() -> None:
    assert isInfoLine('作曲 : Frums')
    assert isInfoLine('编曲 : Frums')
    assert isInfoLine('作词: Arizona Zervas/Ethan Walker')
    assert isInfoLine('Composed by: Dark/Rain')
    assert isInfoLine('Produced by: Someone')
    assert isInfoLine('QQ音乐享有本翻译作品的著作权')


def test_isInfoLine_keeps_real_lyric_text() -> None:
    assert not isInfoLine('I see you got that new Mercedes')
    assert not isInfoLine('Tonight:Mostly cloudy with isolated showers')
    assert not isInfoLine('including Boston,issued at 7:21 PM,October 22nd.')
    assert not isInfoLine('戏曲: 人生')
    assert not isInfoLine('')
    assert not isInfoLine('   ')


def test_alignTranslation_skips_section_markers_and_credits() -> None:
    original = parseLrc(
        '[00:05.00]作曲 : Frums\n'
        '[00:10.00][Pre-Hook]\n'
        '[00:20.00]I see you got that new Mercedes\n'
        '[00:30.00][Hook]\n'
        '[00:40.00]I might let you drive me crazy\n'
    )
    translation = ['我看到你开着新的奔驰', '我想你已经让我发疯']
    assert alignTranslation(original, translation).splitlines() == [
        '[00:20.000]我看到你开着新的奔驰',
        '[00:40.000]我想你已经让我发疯',
    ]


def test_alignTranslation_drops_info_lines_present_on_both_sides() -> None:
    original = parseLrc('[00:10.00][Chorus]\n[00:20.00]line A\n[00:30.00]line B\n')
    translation = ['[副歌]', '甲', '乙']
    assert alignTranslation(original, translation).splitlines() == [
        '[00:20.000]甲',
        '[00:30.000]乙',
    ]


def test_alignTranslation_drops_qq_claim_line_only_in_translation() -> None:
    original = parseLrc('[00:10.00]line A\n[00:20.00]line B\n')
    translation = ['QQ音乐享有本翻译作品的著作权', '甲', '乙']
    assert alignTranslation(original, translation).splitlines() == [
        '[00:10.000]甲',
        '[00:20.000]乙',
    ]


def test_malformed_input_returns_empty() -> None:
    assert parseKrc('[99999999999999999999,x]oops') == []
    assert parseQrc('[x,y]oops(1,2)') == []
    assert parseLrc('[99:99.99]') == []
    assert parseRichsync('not json') == []
    assert parseRichsync('{"a": 1}') == []
    assert parseYrc('{"t":0,"c":"nope"}') == []


def main() -> None:
    checks = sorted(name for name in globals() if name.startswith('test_'))
    for name in checks:
        globals()[name]()
    print(f'test_lyric_formats: {len(checks)} checks passed')


if __name__ == '__main__':
    main()
