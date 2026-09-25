from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.lyric_sources import _qqBody, _tagText  # noqa: E402

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


def main() -> None:
    checks = sorted(name for name in globals() if name.startswith('test_'))
    for name in checks:
        globals()[name]()
    print(f'test_lyric_sources: {len(checks)} checks passed')


if __name__ == '__main__':
    main()
