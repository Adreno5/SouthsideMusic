from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.models import QualityLevelInfo, QualityPrivilegeInfo
from core.netease_backend import NeteaseCloudMusicBackend


class NeteaseQualityPrivilegeTests(unittest.TestCase):
    def test_null_charge_list_preserves_maximum_quality(self) -> None:
        for max_br, display_text in ((0, '未知音质'), (999000, '无损')):
            with self.subTest(max_br=max_br):
                response = {
                    'code': 200,
                    'data': [{'downloadMaxbr': max_br, 'chargeInfoList': None}],
                }
                with patch(
                    'core.netease_backend.apis.track.getTrackPrivilege',
                    return_value=response,
                ):
                    privilege = NeteaseCloudMusicBackend().getSongQualityPrivilege('1')

                self.assertEqual(
                    privilege, QualityPrivilegeInfo(max_br, display_text, [])
                )

    def test_empty_charge_list_preserves_maximum_quality(self) -> None:
        response = {
            'code': 200,
            'data': [{'downloadMaxbr': 999000, 'chargeInfoList': []}],
        }
        with patch(
            'core.netease_backend.apis.track.getTrackPrivilege',
            return_value=response,
        ):
            privilege = NeteaseCloudMusicBackend().getSongQualityPrivilege('1')

        self.assertEqual(privilege, QualityPrivilegeInfo(999000, '无损', []))

    def test_charge_list_preserves_quality_levels(self) -> None:
        response = {
            'code': 200,
            'data': [
                {
                    'downloadMaxbr': 999000,
                    'chargeInfoList': [
                        {'rate': 128000, 'chargeType': 0},
                        {'rate': 320000, 'chargeType': 1},
                        {'rate': 999000, 'chargeType': 2},
                    ],
                }
            ],
        }
        with patch(
            'core.netease_backend.apis.track.getTrackPrivilege',
            return_value=response,
        ):
            privilege = NeteaseCloudMusicBackend().getSongQualityPrivilege('1')

        self.assertEqual(
            privilege,
            QualityPrivilegeInfo(
                999000,
                '无损',
                [
                    QualityLevelInfo(128000, False, '标准'),
                    QualityLevelInfo(320000, True, '极高'),
                    QualityLevelInfo(999000, True, '无损'),
                ],
            ),
        )


if __name__ == '__main__':
    unittest.main()
