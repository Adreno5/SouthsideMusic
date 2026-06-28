from __future__ import annotations

from threading import Lock
from functools import wraps
from os import listdir, path
import datetime
import logging

"""helper utilities for working with api responses."""

truncate_length = 64
logger = logging.getLogger('pyncm.helper')


def _substitute_with_fullwidth(string, sub=set('\x00\\/:<>|?*".')):
    return ''.join([c if c not in sub else chr(ord(c) + 0xFEE0) for c in string])


def _default(default=None):
    def preWrapper(func):
        @property
        @wraps(func)
        def wrapper(*a, **k):
            try:
                return func(*a, **k)
            except Exception as e:
                logger.warn('failed to get attribute %s : %s' % (func.__name__, e))
                return default

        return wrapper

    return preWrapper


class IDCahceHelper:
    """generic cache for id-based dicts."""

    _cache: dict = {}

    def __new__(cls, item_id, *args):
        if item_id not in IDCahceHelper._cache:
            IDCahceHelper._cache[item_id] = super().__new__(cls)
        return IDCahceHelper._cache[item_id]

    def __init__(self, item_id, factory_func=None) -> None:
        if hasattr(self, '_lock'):
            with self._lock:
                return
        self._lock: Lock
        self._item_id = item_id
        if factory_func:
            self._factory_func = factory_func
        self._lock = Lock()
        return self.refresh()

    def refresh(self):
        with self._lock:
            self.data = self._factory_func(self._item_id)


class AlbumHelper(IDCahceHelper):
    def __init__(self, item_id):
        from pyncm.apis.album import getAlbumInfo

        super().__init__(item_id, getAlbumInfo)

    def refresh(self):
        logger.debug('caching album info %s' % self._item_id)
        return super().refresh()

    @_default()
    def albumName(self):
        return self.data['album']['name']

    @_default()
    def albumAliases(self):
        return self.data['album']['alias']

    @_default()
    def albumCompany(self):
        return self.data['album']['company']

    @_default()
    def albumBriefDescription(self):
        return self.data['album']['breifDesc']

    @_default()
    def albumDescription(self):
        return self.data['album']['description']

    @_default()
    def albumPublishTime(self):
        return (
            datetime.datetime(1970, 1, 1)
            + datetime.timedelta(milliseconds=self.data['album']['publishTime'])
        ).year

    @_default()
    def albumSongCount(self):
        return self.data['album']['size']

    @_default()
    def albumArtists(self):
        return [_ar['name'] for _ar in self.data['album']['artists']]


class ArtistHelper(IDCahceHelper):
    def __init__(self, item_id):
        from pyncm.apis.artist import getArtistDetails

        super().__init__(item_id, getArtistDetails)

    def refresh(self):
        logger.debug('caching artist info %s' % self._item_id)
        return super().refresh()

    @_default()
    def ID(self):
        return self.data['data']['artist']['id']

    @_default()
    def artistName(self):
        return self.data['data']['artist']['name']

    @_default()
    def artistTranslatedName(self):
        return self.data['data']['artist']['transNames']

    @_default()
    def artistBrief(self):
        return self.data['data']['artist']['briefDesc']


class UserHelper(IDCahceHelper):
    def __init__(self, item_id):
        from pyncm.apis.user import getUserDetail

        super().__init__(item_id, getUserDetail)

    def refresh(self):
        logger.debug('caching user info %s' % self._item_id)
        return super().refresh()

    @_default()
    def ID(self):
        return self.data['userPoint']['userId']

    @_default()
    def userName(self):
        return self.data['profile']['nickname']

    @_default()
    def avatar(self):
        return self.data['profile']['avatarUrl']

    @_default()
    def avatarBackground(self):
        return self.data['profile']['backgroundUrl']


class TrackHelper:
    """helper for handling generic track objects."""

    def __init__(self, track_dict) -> None:
        self.data: dict = track_dict

    @property
    def duration(self) -> int:
        """track duration in milliseconds."""
        return int(self.data['dt'])

    @property
    def album(self) -> AlbumHelper:
        """album object with more metadata."""
        return AlbumHelper(self.data['al']['id'])

    @_default()
    def ID(self):
        return self.data['id']

    @_default()
    def trackPublishTime(self):
        return (
            datetime.datetime(1970, 1, 1)
            + datetime.timedelta(milliseconds=self.data['publishTime'])
        ).year

    @_default()
    def trackNumber(self):
        return self.data['no']

    @_default(default='Unknown')
    def trackName(self):
        assert self.data['name'] is not None
        return self.data['name']

    @_default(default=[])
    def trackAliases(self):
        return self.data['alia']

    @_default(default='Unknown')
    def albumName(self):
        if self.data['al']['id']:
            return self.data['al']['name']
        else:
            return self.data['pc']['alb']

    @_default(
        default='https://p1.music.126.net/UeTuwE7pvjBpypWLudqukA==/3132508627578625.jpg'
    )
    def albumCover(self):
        al = self.data['al'] if 'al' in self.data else self.data['album']
        if al['id']:
            return al['picUrl']
        else:
            return 'https://music.163.com/api/img/blur/' + self.data['pc']['cid']

    @_default(default=['Various artists'])
    def artists(self):
        ar = self.data['ar'] if 'ar' in self.data else self.data['artists']
        ret = [_ar['name'] for _ar in ar]
        if not ret.count(None):
            return ret
        else:
            return [self.data['pc']['ar']]

    @_default(default='null')
    def cd(self):
        return self.data['cd']

    @_default()
    def title(self):
        return f'{self.trackName} - {",".join(self.artists)}'  # type: ignore

    @property
    def template(self):
        return {
            'id': str(self.ID),
            'year': str(self.trackPublishTime),
            'no': str(self.trackNumber),
            'track': self.trackName,
            'album': self.albumName,
            'title': self.title,
            'artists': ' / '.join(self.artists),  # type: ignore
        }


class FuzzyPathHelper(IDCahceHelper):
    tbl_basenames = None
    tbl_basenames_noext = None

    @property
    def base_path(self):
        return self._item_id

    def __init__(self, basepath, limit_exts={'.flac', '.mp3', '.m4a'}) -> None:
        self.limit_exts = limit_exts
        super().__init__(basepath)

    def _factory_func(self, _item_id):
        files = filter(
            lambda file: path.isfile(path.join(self.base_path, file)),
            listdir(self.base_path) if path.exists(self.base_path) else [],
        )
        self.tbl_basenames = {path.basename(file): file for file in files}

        def split(file):
            return path.splitext(path.basename(file))

        self.tbl_basenames_noext = {
            (
                split(file)[0] if (split(file)[1].lower() in self.limit_exts) else None
            ): file
            for file in self.tbl_basenames
        }

    def exists(self, name, partial_extension_check=True):
        """check if a file exists in O(1) time.

        Args:
            name: file basename inside basepath.
            partial_extension_check: only check basename w/o extension.

        Returns:
            bool
        """
        if partial_extension_check:
            return name in self.tbl_basenames_noext
        else:
            return name in self.tbl_basenames

    def fullpath(self, name):
        if name in self.tbl_basenames:
            return path.join(self.base_path, self.tbl_basenames[name])  # type: ignore
        if name in self.tbl_basenames_noext:
            return path.join(self.base_path, self.tbl_basenames_noext[name])  # type: ignore

    def get_extension(self, name):
        p = self.fullpath(name)
        return path.splitext(p)[1][1:]  # type: ignore
