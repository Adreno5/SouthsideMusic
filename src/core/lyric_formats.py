from __future__ import annotations

import base64
import json
import re
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field

_QRC_KEY = b'!@#)(*$%123ZXC!@!@#)(NHL'

_KRC_KEY = bytes(
    (
        0x40,
        0x47,
        0x61,
        0x77,
        0x5E,
        0x32,
        0x74,
        0x47,
        0x51,
        0x36,
        0x31,
        0x2D,
        0xCE,
        0xD2,
        0x6E,
        0x69,
    )
)

_SBOX1 = (
    14,
    4,
    13,
    1,
    2,
    15,
    11,
    8,
    3,
    10,
    6,
    12,
    5,
    9,
    0,
    7,
    0,
    15,
    7,
    4,
    14,
    2,
    13,
    1,
    10,
    6,
    12,
    11,
    9,
    5,
    3,
    8,
    4,
    1,
    14,
    8,
    13,
    6,
    2,
    11,
    15,
    12,
    9,
    7,
    3,
    10,
    5,
    0,
    15,
    12,
    8,
    2,
    4,
    9,
    1,
    7,
    5,
    11,
    3,
    14,
    10,
    0,
    6,
    13,
)

_SBOX2 = (
    15,
    1,
    8,
    14,
    6,
    11,
    3,
    4,
    9,
    7,
    2,
    13,
    12,
    0,
    5,
    10,
    3,
    13,
    4,
    7,
    15,
    2,
    8,
    15,
    12,
    0,
    1,
    10,
    6,
    9,
    11,
    5,
    0,
    14,
    7,
    11,
    10,
    4,
    13,
    1,
    5,
    8,
    12,
    6,
    9,
    3,
    2,
    15,
    13,
    8,
    10,
    1,
    3,
    15,
    4,
    2,
    11,
    6,
    7,
    12,
    0,
    5,
    14,
    9,
)

_SBOX3 = (
    10,
    0,
    9,
    14,
    6,
    3,
    15,
    5,
    1,
    13,
    12,
    7,
    11,
    4,
    2,
    8,
    13,
    7,
    0,
    9,
    3,
    4,
    6,
    10,
    2,
    8,
    5,
    14,
    12,
    11,
    15,
    1,
    13,
    6,
    4,
    9,
    8,
    15,
    3,
    0,
    11,
    1,
    2,
    12,
    5,
    10,
    14,
    7,
    1,
    10,
    13,
    0,
    6,
    9,
    8,
    7,
    4,
    15,
    14,
    3,
    11,
    5,
    2,
    12,
)

_SBOX4 = (
    7,
    13,
    14,
    3,
    0,
    6,
    9,
    10,
    1,
    2,
    8,
    5,
    11,
    12,
    4,
    15,
    13,
    8,
    11,
    5,
    6,
    15,
    0,
    3,
    4,
    7,
    2,
    12,
    1,
    10,
    14,
    9,
    10,
    6,
    9,
    0,
    12,
    11,
    7,
    13,
    15,
    1,
    3,
    14,
    5,
    2,
    8,
    4,
    3,
    15,
    0,
    6,
    10,
    10,
    13,
    8,
    9,
    4,
    5,
    11,
    12,
    7,
    2,
    14,
)

_SBOX5 = (
    2,
    12,
    4,
    1,
    7,
    10,
    11,
    6,
    8,
    5,
    3,
    15,
    13,
    0,
    14,
    9,
    14,
    11,
    2,
    12,
    4,
    7,
    13,
    1,
    5,
    0,
    15,
    10,
    3,
    9,
    8,
    6,
    4,
    2,
    1,
    11,
    10,
    13,
    7,
    8,
    15,
    9,
    12,
    5,
    6,
    3,
    0,
    14,
    11,
    8,
    12,
    7,
    1,
    14,
    2,
    13,
    6,
    15,
    0,
    9,
    10,
    4,
    5,
    3,
)

_SBOX6 = (
    12,
    1,
    10,
    15,
    9,
    2,
    6,
    8,
    0,
    13,
    3,
    4,
    14,
    7,
    5,
    11,
    10,
    15,
    4,
    2,
    7,
    12,
    9,
    5,
    6,
    1,
    13,
    14,
    0,
    11,
    3,
    8,
    9,
    14,
    15,
    5,
    2,
    8,
    12,
    3,
    7,
    0,
    4,
    10,
    1,
    13,
    11,
    6,
    4,
    3,
    2,
    12,
    9,
    5,
    15,
    10,
    11,
    14,
    1,
    7,
    6,
    0,
    8,
    13,
)

_SBOX7 = (
    4,
    11,
    2,
    14,
    15,
    0,
    8,
    13,
    3,
    12,
    9,
    7,
    5,
    10,
    6,
    1,
    13,
    0,
    11,
    7,
    4,
    9,
    1,
    10,
    14,
    3,
    5,
    12,
    2,
    15,
    8,
    6,
    1,
    4,
    11,
    13,
    12,
    3,
    7,
    14,
    10,
    15,
    6,
    8,
    0,
    5,
    9,
    2,
    6,
    11,
    13,
    8,
    1,
    4,
    10,
    7,
    9,
    5,
    0,
    15,
    14,
    2,
    3,
    12,
)

_SBOX8 = (
    13,
    2,
    8,
    4,
    6,
    15,
    11,
    1,
    10,
    9,
    3,
    14,
    5,
    0,
    12,
    7,
    1,
    15,
    13,
    8,
    10,
    3,
    7,
    4,
    12,
    5,
    6,
    11,
    0,
    14,
    9,
    2,
    7,
    11,
    4,
    1,
    9,
    12,
    14,
    2,
    0,
    6,
    10,
    13,
    15,
    3,
    5,
    8,
    2,
    1,
    14,
    7,
    4,
    10,
    8,
    13,
    15,
    12,
    9,
    0,
    3,
    5,
    6,
    11,
)

_KEY_ROUND_SHIFT = (1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1)

_KEY_PERM_C = (
    56,
    48,
    40,
    32,
    24,
    16,
    8,
    0,
    57,
    49,
    41,
    33,
    25,
    17,
    9,
    1,
    58,
    50,
    42,
    34,
    26,
    18,
    10,
    2,
    59,
    51,
    43,
    35,
)

_KEY_PERM_D = (
    62,
    54,
    46,
    38,
    30,
    22,
    14,
    6,
    61,
    53,
    45,
    37,
    29,
    21,
    13,
    5,
    60,
    52,
    44,
    36,
    28,
    20,
    12,
    4,
    27,
    19,
    11,
    3,
)

_KEY_COMPRESSION = (
    13,
    16,
    10,
    23,
    0,
    4,
    2,
    27,
    14,
    5,
    20,
    9,
    22,
    18,
    11,
    3,
    25,
    7,
    15,
    6,
    26,
    19,
    12,
    1,
    40,
    51,
    30,
    36,
    46,
    54,
    29,
    39,
    50,
    44,
    32,
    47,
    43,
    48,
    38,
    55,
    33,
    52,
    45,
    41,
    49,
    35,
    28,
    31,
)

_IP_LEFT = (
    57,
    49,
    41,
    33,
    25,
    17,
    9,
    1,
    59,
    51,
    43,
    35,
    27,
    19,
    11,
    3,
    61,
    53,
    45,
    37,
    29,
    21,
    13,
    5,
    63,
    55,
    47,
    39,
    31,
    23,
    15,
    7,
)

_IP_RIGHT = (
    56,
    48,
    40,
    32,
    24,
    16,
    8,
    0,
    58,
    50,
    42,
    34,
    26,
    18,
    10,
    2,
    60,
    52,
    44,
    36,
    28,
    20,
    12,
    4,
    62,
    54,
    46,
    38,
    30,
    22,
    14,
    6,
)

_INV_IP = (
    (
        (1, 4, 7),
        (0, 4, 6),
        (1, 12, 5),
        (0, 12, 4),
        (1, 20, 3),
        (0, 20, 2),
        (1, 28, 1),
        (0, 28, 0),
    ),
    (
        (1, 5, 7),
        (0, 5, 6),
        (1, 13, 5),
        (0, 13, 4),
        (1, 21, 3),
        (0, 21, 2),
        (1, 29, 1),
        (0, 29, 0),
    ),
    (
        (1, 6, 7),
        (0, 6, 6),
        (1, 14, 5),
        (0, 14, 4),
        (1, 22, 3),
        (0, 22, 2),
        (1, 30, 1),
        (0, 30, 0),
    ),
    (
        (1, 7, 7),
        (0, 7, 6),
        (1, 15, 5),
        (0, 15, 4),
        (1, 23, 3),
        (0, 23, 2),
        (1, 31, 1),
        (0, 31, 0),
    ),
    (
        (1, 0, 7),
        (0, 0, 6),
        (1, 8, 5),
        (0, 8, 4),
        (1, 16, 3),
        (0, 16, 2),
        (1, 24, 1),
        (0, 24, 0),
    ),
    (
        (1, 1, 7),
        (0, 1, 6),
        (1, 9, 5),
        (0, 9, 4),
        (1, 17, 3),
        (0, 17, 2),
        (1, 25, 1),
        (0, 25, 0),
    ),
    (
        (1, 2, 7),
        (0, 2, 6),
        (1, 10, 5),
        (0, 10, 4),
        (1, 18, 3),
        (0, 18, 2),
        (1, 26, 1),
        (0, 26, 0),
    ),
    (
        (1, 3, 7),
        (0, 3, 6),
        (1, 11, 5),
        (0, 11, 4),
        (1, 19, 3),
        (0, 19, 2),
        (1, 27, 1),
        (0, 27, 0),
    ),
)


@dataclass
class LyricWord:
    text: str
    start: int
    end: int


@dataclass
class LyricLine:
    start: int
    end: int
    text: str
    words: list[LyricWord] = field(default_factory=list)
    is_metadata: bool = False


def _bitNum(source: bytes, index: int, target: int) -> int:
    return (
        (source[index // 32 * 4 + 3 - index % 32 // 8] >> (7 - index % 8)) & 0x01
    ) << target


def _bitNumIntr(value: int, index: int, target: int) -> int:
    return ((value >> (31 - index)) & 0x00000001) << target


def _bitNumIntl(value: int, shift: int, target: int) -> int:
    return ((value << shift) & 0x80000000) >> target


def _sboxBit(value: int) -> int:
    return (value & 0x20) | ((value & 0x1F) >> 1) | ((value & 0x01) << 4)


def _keySchedule(key: bytes, schedule: list[list[int]], decrypt: bool) -> None:
    shift_c = 0
    shift_d = 0
    for i in range(28):
        shift_c |= _bitNum(key, _KEY_PERM_C[i], 31 - i)
        shift_d |= _bitNum(key, _KEY_PERM_D[i], 31 - i)

    for i in range(16):
        round_shift = _KEY_ROUND_SHIFT[i]
        shift_c = (
            (shift_c << round_shift) | (shift_c >> (28 - round_shift))
        ) & 0xFFFFFFF0
        shift_d = (
            (shift_d << round_shift) | (shift_d >> (28 - round_shift))
        ) & 0xFFFFFFF0

        target = 15 - i if decrypt else i
        for j in range(6):
            schedule[target][j] = 0
        for j in range(24):
            schedule[target][j // 8] |= _bitNumIntr(
                shift_c, _KEY_COMPRESSION[j], 7 - (j % 8)
            )
        for j in range(24, 48):
            schedule[target][j // 8] |= _bitNumIntr(
                shift_d, _KEY_COMPRESSION[j] - 27, 7 - (j % 8)
            )


def _initialPermutation(block: bytes) -> list[int]:
    state = [0, 0]
    for i in range(32):
        state[0] |= _bitNum(block, _IP_LEFT[i], 31 - i)
        state[1] |= _bitNum(block, _IP_RIGHT[i], 31 - i)
    return state


def _inversePermutation(state: list[int]) -> bytes:
    output = bytearray(8)
    for position, sources in enumerate(_INV_IP):
        value = 0
        for index, bit, target in sources:
            value |= _bitNumIntr(state[index], bit, target)
        output[position] = value
    return bytes(output)


def _f(state: int, key: list[int]) -> int:
    left = (
        _bitNumIntl(state, 31, 0)
        | ((state & 0xF0000000) >> 1)
        | _bitNumIntl(state, 4, 5)
        | _bitNumIntl(state, 3, 6)
        | ((state & 0x0F000000) >> 3)
        | _bitNumIntl(state, 8, 11)
        | _bitNumIntl(state, 7, 12)
        | ((state & 0x00F00000) >> 5)
        | _bitNumIntl(state, 12, 17)
        | _bitNumIntl(state, 11, 18)
        | ((state & 0x000F0000) >> 7)
        | _bitNumIntl(state, 16, 23)
    )
    right = (
        _bitNumIntl(state, 15, 0)
        | ((state & 0x0000F000) << 15)
        | _bitNumIntl(state, 20, 5)
        | _bitNumIntl(state, 19, 6)
        | ((state & 0x00000F00) << 13)
        | _bitNumIntl(state, 24, 11)
        | _bitNumIntl(state, 23, 12)
        | ((state & 0x000000F0) << 11)
        | _bitNumIntl(state, 28, 17)
        | _bitNumIntl(state, 27, 18)
        | ((state & 0x0000000F) << 9)
        | _bitNumIntl(state, 0, 23)
    )

    expanded = (
        ((left >> 24) & 0xFF) ^ key[0],
        ((left >> 16) & 0xFF) ^ key[1],
        ((left >> 8) & 0xFF) ^ key[2],
        ((right >> 24) & 0xFF) ^ key[3],
        ((right >> 16) & 0xFF) ^ key[4],
        ((right >> 8) & 0xFF) ^ key[5],
    )

    value = (
        (_SBOX1[_sboxBit(expanded[0] >> 2)] << 28)
        | (_SBOX2[_sboxBit(((expanded[0] & 0x03) << 4) | (expanded[1] >> 4))] << 24)
        | (_SBOX3[_sboxBit(((expanded[1] & 0x0F) << 2) | (expanded[2] >> 6))] << 20)
        | (_SBOX4[_sboxBit(expanded[2] & 0x3F)] << 16)
        | (_SBOX5[_sboxBit(expanded[3] >> 2)] << 12)
        | (_SBOX6[_sboxBit(((expanded[3] & 0x03) << 4) | (expanded[4] >> 4))] << 8)
        | (_SBOX7[_sboxBit(((expanded[4] & 0x0F) << 2) | (expanded[5] >> 6))] << 4)
        | _SBOX8[_sboxBit(expanded[5] & 0x3F)]
    )

    return (
        _bitNumIntl(value, 15, 0)
        | _bitNumIntl(value, 6, 1)
        | _bitNumIntl(value, 19, 2)
        | _bitNumIntl(value, 20, 3)
        | _bitNumIntl(value, 28, 4)
        | _bitNumIntl(value, 11, 5)
        | _bitNumIntl(value, 27, 6)
        | _bitNumIntl(value, 16, 7)
        | _bitNumIntl(value, 0, 8)
        | _bitNumIntl(value, 14, 9)
        | _bitNumIntl(value, 22, 10)
        | _bitNumIntl(value, 25, 11)
        | _bitNumIntl(value, 4, 12)
        | _bitNumIntl(value, 17, 13)
        | _bitNumIntl(value, 30, 14)
        | _bitNumIntl(value, 9, 15)
        | _bitNumIntl(value, 1, 16)
        | _bitNumIntl(value, 7, 17)
        | _bitNumIntl(value, 23, 18)
        | _bitNumIntl(value, 13, 19)
        | _bitNumIntl(value, 31, 20)
        | _bitNumIntl(value, 26, 21)
        | _bitNumIntl(value, 2, 22)
        | _bitNumIntl(value, 8, 23)
        | _bitNumIntl(value, 18, 24)
        | _bitNumIntl(value, 12, 25)
        | _bitNumIntl(value, 29, 26)
        | _bitNumIntl(value, 5, 27)
        | _bitNumIntl(value, 21, 28)
        | _bitNumIntl(value, 10, 29)
        | _bitNumIntl(value, 3, 30)
        | _bitNumIntl(value, 24, 31)
    )


def _crypt(block: bytes, key: list[list[int]]) -> bytes:
    state = _initialPermutation(block)
    for index in range(15):
        state[0], state[1] = state[1], _f(state[1], key[index]) ^ state[0]
    state[0] = _f(state[1], key[15]) ^ state[0]
    return _inversePermutation(state)


def _tripleDesSchedules(key: bytes, decrypt: bool) -> tuple[list[list[int]], ...]:
    schedules: list[list[list[int]]] = [[[0] * 6 for _ in range(16)] for _ in range(3)]
    if decrypt:
        _keySchedule(key[:8], schedules[2], True)
        _keySchedule(key[8:16], schedules[1], False)
        _keySchedule(key[16:24], schedules[0], True)
    else:
        _keySchedule(key[:8], schedules[0], False)
        _keySchedule(key[8:16], schedules[1], True)
        _keySchedule(key[16:24], schedules[2], False)
    return tuple(schedules)


def _tripleDesBlocks(blocks: bytes, key: bytes, decrypt: bool) -> bytes:
    schedules = _tripleDesSchedules(key, decrypt)
    order = tuple(reversed(schedules)) if not decrypt else schedules
    output = bytearray()
    for offset in range(0, len(blocks) - 7, 8):
        chunk = blocks[offset : offset + 8]
        for schedule in order:
            chunk = _crypt(chunk, schedule)
        output += chunk
    return bytes(output)


def qrcDecrypt(hexText: str) -> str:
    data = _tripleDesBlocks(bytes.fromhex(hexText), _QRC_KEY, True)
    return zlib.decompress(data).decode('utf-8', 'replace').lstrip('\ufeff')


def krcDecrypt(encoded: str) -> str:
    data = base64.b64decode(encoded)[4:]
    decoded = bytes(
        value ^ _KRC_KEY[index % len(_KRC_KEY)] for index, value in enumerate(data)
    )
    return zlib.decompress(decoded).decode('utf-8', 'replace')[1:]


def _formatTimestamp(milliseconds: int) -> str:
    minutes, remainder = divmod(max(0, milliseconds), 60000)
    seconds, millis = divmod(remainder, 1000)
    return f'{minutes:02d}:{seconds:02d}.{millis:03d}'


_KRC_LINE_RE = re.compile(r'^\[(\d+),(\d+)\](.*)$')
_KRC_WORD_RE = re.compile(r'<(-?\d+),(\d+),(-?\d+)>([^<]*)')
_QRC_WORD_RE = re.compile(r'(.*?)\((\d+),(\d+)\)', re.S)
_YRC_WORD_RE = re.compile(r'\((-?\d+),(-?\d+),(-?\d+)\)([^()]*)')
_STRUCTURED_RE = re.compile(r'\[\d+[:.,]\d+')
_SECTION_RE = re.compile(r'^\s*[\[【][^\[\]【】]{1,40}[\]】]\s*$')
_CREDIT_RE = re.compile(r'^\s*([^:：]{1,32}?)\s*[:：]')
_CREDIT_ROLES = (
    '作词',
    '作曲',
    '编曲',
    '词曲',
    '制作',
    '监制',
    '录音',
    '混音',
    '母带',
    '缩混',
    '和声',
    '和音',
    '原唱',
    '主唱',
    '伴唱',
    '合唱',
    '配唱',
    '演唱',
    '念白',
    '钢琴',
    '吉他',
    '吉它',
    '贝斯',
    '弦乐',
    '管乐',
    '管弦',
    '打击乐',
    '合成器',
    '键盘',
    '提琴',
    '小提琴',
    '中提琴',
    '大提琴',
    '低音提琴',
    '二胡',
    '古筝',
    '琵琶',
    '笛子',
    '唢呐',
    '尺八',
    '小号',
    '长号',
    '萨克斯',
    '单簧管',
    '双簧管',
    '巴松',
    '马头琴',
    '葫芦丝',
    '冬不拉',
    '热瓦普',
    '巴拉莱卡',
    '科布兹',
    '扬琴',
    '胡琴',
    '古琴',
    '曲绘',
    '调校',
    '调教',
    '校对',
    '翻译',
    '文案',
    '策划',
    '企划',
    '统筹',
    '出品',
    '发行',
    '推广',
    '营销',
    '宣传',
    '宣发',
    '经纪',
    '商务',
    '合作',
    '协作',
    '协力',
    '支持',
    '鸣谢',
    '感谢',
    '设计',
    '封面',
    '海报',
    '题字',
    '视觉',
    '影像',
    '导演',
    '造型',
    '妆发',
    '团队',
    '乐队',
    '单位',
    '平台',
    '厂牌',
    '工程',
    '分轨',
    '贴混',
    '助理',
    '顾问',
    '艺人',
    '运营',
    '管理',
    '行销',
    '媒介',
    '专辑介绍',
    'vocals',
    'vocal',
    'guitar',
    'bass',
    'drums',
    'piano',
    'strings',
    'synth',
    'violin',
    'violins',
    'cello',
    'viola',
    'choir',
    'chorus',
    'percussion',
    'keyboard',
    'programming',
    'production',
    'arrangement',
    'mixing',
    'mastering',
    'recording',
    'editing',
    'engineer',
    'mixer',
    'master',
    'director',
    'stylist',
    'artwork',
    'design',
    'translation',
    'proofreading',
    'composer',
    'lyricist',
    'harmony',
    'harmonica',
    'ocarina',
    'whistle',
    'winds',
    'brass',
    'horns',
    'pads',
    'isrc',
    'pgm',
    'record',
    'produce',
    'conduct',
)
_LRC_TIME_RE = re.compile(r'^\s*\[(\d+):(\d+)(?:[.:](\d{1,3}))?\]')
_LRC_META_RE = re.compile(
    r'^\s*\[(?:by|ar|al|ti|offset|length|re|ve|language|hash|sign|total):'
)


def parseKrc(text: str) -> list[LyricLine]:
    lines: list[LyricLine] = []
    try:
        for raw in text.splitlines():
            match = _KRC_LINE_RE.match(raw.strip())
            if match is None:
                continue
            start = int(match.group(1))
            duration = int(match.group(2))
            words = [
                LyricWord(
                    text=word.group(4),
                    start=start + int(word.group(1)),
                    end=start + int(word.group(1)) + int(word.group(2)),
                )
                for word in _KRC_WORD_RE.finditer(match.group(3))
            ]
            content = ''.join(word.text for word in words)
            if not content.strip():
                continue
            lines.append(
                LyricLine(start=start, end=start + duration, text=content, words=words)
            )
    except (TypeError, ValueError):
        return []
    return lines


def krcTranslations(text: str) -> list[str]:
    index = text.find('[language:')
    if index < 0:
        return []
    payload = text[index + len('[language:') :]
    payload = payload[: payload.find(']')]
    try:
        data = json.loads(base64.b64decode(payload).decode('utf-8', 'replace'))
    except (TypeError, ValueError):
        return []
    contents = data.get('content') if isinstance(data, dict) else None
    if not isinstance(contents, list):
        return []
    for block in contents:
        if not isinstance(block, dict) or block.get('type') != 1:
            continue
        entries = block.get('lyricContent')
        if not isinstance(entries, list):
            continue
        result: list[str] = []
        for entry in entries:
            value = (
                str(entry[0]) if isinstance(entry, list) and entry and entry[0] else ''
            )
            result.append('' if value == '//' else value)
        return result
    return []


def parseQrc(text: str) -> list[LyricLine]:
    lines: list[LyricLine] = []
    try:
        for raw in text.splitlines():
            line = raw.strip()
            if not line.startswith('['):
                continue
            close = line.find(']')
            if close < 0:
                continue
            head = line[1:close].split(',')
            if len(head) < 2 or not head[0].isdigit() or not head[1].isdigit():
                continue
            start = int(head[0])
            duration = int(head[1])
            words = [
                LyricWord(
                    text=word.group(1),
                    start=int(word.group(2)),
                    end=int(word.group(2)) + int(word.group(3)),
                )
                for word in _QRC_WORD_RE.finditer(line[close + 1 :])
                if word.group(1)
            ]
            content = ''.join(word.text for word in words)
            if not content.strip():
                continue
            lines.append(
                LyricLine(start=start, end=start + duration, text=content, words=words)
            )
    except (TypeError, ValueError):
        return []
    return lines


def parseLrc(text: str) -> list[LyricLine]:
    lines: list[LyricLine] = []
    try:
        for raw in text.splitlines():
            if _LRC_META_RE.match(raw):
                continue
            cursor = 0
            timestamps: list[int] = []
            while True:
                match = _LRC_TIME_RE.match(raw[cursor:])
                if match is None:
                    break
                minutes = int(match.group(1))
                seconds = int(match.group(2))
                millis = int((match.group(3) or '0').ljust(3, '0')[:3])
                timestamps.append(minutes * 60000 + seconds * 1000 + millis)
                cursor += match.end()
            if not timestamps:
                continue
            content = raw[cursor:].strip()
            if not content:
                continue
            for timestamp in timestamps:
                lines.append(LyricLine(start=timestamp, end=timestamp, text=content))
    except (TypeError, ValueError):
        return []
    lines.sort(key=lambda line: line.start)
    for index, line in enumerate(lines[:-1]):
        if line.end <= line.start:
            line.end = lines[index + 1].start
    return lines


def parseRichsync(raw: str) -> list[LyricLine]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    lines: list[LyricLine] = []
    try:
        for entry in data:
            if not isinstance(entry, dict):
                continue
            start = int(float(entry.get('ts') or 0) * 1000)
            end = int(float(entry.get('te') or 0) * 1000)
            raw_words = entry.get('l')
            words: list[LyricWord] = []
            if isinstance(raw_words, list):
                positions = [
                    int(float(word.get('o') or 0) * 1000) for word in raw_words
                ]
                for index, word in enumerate(raw_words):
                    if not isinstance(word, dict):
                        continue
                    word_start = start + positions[index]
                    word_end = (
                        start + positions[index + 1]
                        if index + 1 < len(positions)
                        else end
                    )
                    words.append(
                        LyricWord(
                            text=str(word.get('c') or ''),
                            start=word_start,
                            end=word_end,
                        )
                    )
            content = str(entry.get('x') or '').strip() or ''.join(
                word.text for word in words
            )
            if not content.strip():
                continue
            lines.append(LyricLine(start=start, end=end, text=content, words=words))
    except (TypeError, ValueError):
        return []
    return lines


def _jsonCredit(line: str) -> tuple[int, str] | None:
    stripped = line.strip()
    if not stripped.startswith('{'):
        return None
    try:
        obj = json.loads(stripped)
        cells = obj['c']
        start = int(float(obj.get('t') or 0))
    except (TypeError, ValueError, KeyError, IndexError):
        return None
    if not isinstance(cells, list):
        return None
    content = ''.join(
        str(cell.get('tx', '')) for cell in cells if isinstance(cell, dict)
    ).strip()
    if not content:
        return None
    return start, content.replace(': ', '：').replace(':', '：')


def parseYrc(text: str) -> list[LyricLine]:
    lines: list[LyricLine] = []
    try:
        for raw in text.splitlines():
            credit = _jsonCredit(raw)
            if credit is not None:
                start, content = credit
                lines.append(
                    LyricLine(start=start, end=start, text=content, is_metadata=True)
                )
                continue
            match = _KRC_LINE_RE.match(raw.strip())
            if match is None:
                continue
            start = int(match.group(1))
            duration = int(match.group(2))
            words = [
                LyricWord(
                    text=word.group(4),
                    start=int(word.group(1)),
                    end=int(word.group(1)) + int(word.group(2)),
                )
                for word in _YRC_WORD_RE.finditer(match.group(3))
            ]
            content = ''.join(word.text for word in words)
            if not content.strip():
                continue
            lines.append(
                LyricLine(start=start, end=start + duration, text=content, words=words)
            )
    except (TypeError, ValueError):
        return []
    lines.sort(key=lambda line: line.start)
    return lines


def contentLines(lines: Sequence[LyricLine]) -> list[LyricLine]:
    return [line for line in lines if not line.is_metadata and line.text.strip()]


def translationTexts(text: str) -> list[str]:
    if not text.strip():
        return []
    lines = parseAny(text)
    if lines:
        texts = [line.text.strip() for line in lines if not line.is_metadata]
    elif _STRUCTURED_RE.search(text):
        return []
    else:
        texts = [line.strip() for line in text.splitlines() if line.strip()]
    return ['' if value == '//' else value for value in texts]


def _isClaimingLine(text: str) -> bool:
    if '享有' in text and '翻译' in text and '权' in text:
        return True
    return (
        '版权' in text
        and ('未经' in text or '不得' in text or '请勿' in text)
        and ('许可' in text or '授权' in text)
    )


def isInfoLine(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if _SECTION_RE.match(stripped):
        return True
    if _isClaimingLine(stripped):
        return True
    match = _CREDIT_RE.match(stripped)
    if match is None:
        return False
    role = match.group(1).strip().lower()
    if role.endswith(' by'):
        return True
    compact = ''.join(role.split())
    return len(compact) <= 24 and any(compact.endswith(name) for name in _CREDIT_ROLES)


def alignTranslation(original: Sequence[LyricLine], translation: Sequence[str]) -> str:
    times = [line.start for line in contentLines(original) if not isInfoLine(line.text)]
    texts = [text for text in translation if not isInfoLine(text)]
    return toLrc(
        [
            LyricLine(start=start, end=start, text=text)
            for start, text in zip(times, texts)
            if text.strip()
        ]
    )


def yrcToLrc(text: str) -> str:
    return toLrc(contentLines(parseYrc(text)))


def parseAny(text: str) -> list[LyricLine]:
    for parse in (parseKrc, parseYrc, parseQrc, parseLrc):
        lines = parse(text)
        if lines:
            return lines
    return []


def toLrc(lines: Sequence[LyricLine]) -> str:
    return '\n'.join(
        f'[{_formatTimestamp(line.start)}]{line.text}'
        for line in lines
        if line.text.strip()
    )


def toYrc(lines: Sequence[LyricLine]) -> str:
    output: list[str] = []
    for line in lines:
        words = ''.join(
            f'({word.start},{max(0, word.end - word.start)},0){word.text}'
            for word in line.words
            if word.text
        )
        if not words:
            continue
        output.append(f'[{line.start},{max(0, line.end - line.start)}]{words}')
    return '\n'.join(output)


def hasWordTiming(lines: Sequence[LyricLine]) -> bool:
    return any(line.words for line in lines)
