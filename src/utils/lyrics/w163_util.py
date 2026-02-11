import base64
import json
from typing import Literal, override
import requests
import hashlib
import time

from functools import lru_cache

from utils.lyrics.base_util import SongInfo

from .base_util import BaseLyricUtil

class CloudMusicUtil(BaseLyricUtil):
    def init(self) -> None:
        pass
    
    def search(self, keyword: str) -> list[SongInfo]:
        return self._search_impl(keyword)
    
    @lru_cache(maxsize=128)
    def _search_impl(self, keyword: str) -> list[SongInfo]:
        response = requests.get(f'https://apis.netstart.cn/music/search?keywords={keyword}&type=1', headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7'
        })

        return [SongInfo(
            name=songdict['name'],
            artists='、'.join(art['name'] for art in songdict['artists']),
            id=songdict['id'],
            privilege=songdict['fee']
        ) for songdict in response.json()['result']['songs']]