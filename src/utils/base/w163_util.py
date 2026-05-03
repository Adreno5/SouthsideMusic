import requests

from functools import lru_cache
from utils.base.base_util import SongInfo
from .base_util import BaseLyricUtil
from MusicLibrary.neteaseCloudMusicApi import NeteaseCloudMusicApi


class CloudMusicUtil(BaseLyricUtil):
    def init(self) -> None:
        pass

    def search(self, keyword: str) -> list[SongInfo]:
        api = NeteaseCloudMusicApi()
        response = api.search(keyword).data
        assert isinstance(response, dict), "Invalid response"

        result = response.get("result", {})
        songs = result.get("songs", []) if isinstance(result, dict) else []
        return [
            SongInfo(
                name=songdict["name"],
                artists="、".join(art["name"] for art in songdict["artists"]),
                id=songdict["id"],
                privilege=songdict["fee"],
            )
            for songdict in songs
        ]
