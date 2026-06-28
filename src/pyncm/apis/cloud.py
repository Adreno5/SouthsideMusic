from __future__ import annotations

import json

from . import eapi, weapi, getCurrentSession

BUCKET = 'jd-musicrep-privatecloud-audio-public'


def getCloudDriveInfo(limit=30, offset=0) -> dict:
    """get cloud drive contents (pc client api).

    Args:
        limit: page size. defaults to 30.
        offset: offset. defaults to 0.

    Returns:
        dict
    """
    return weapi('/weapi/v1/cloud/get', {'limit': str(limit), 'offset': str(offset)})


def getCloudDriveItemInfo(song_ids: list) -> dict:
    """get cloud drive item detail (pc client api).

    Args:
        song_ids: cloud item ids.

    Returns:
        dict
    """
    ids = song_ids if isinstance(song_ids, list) else [song_ids]
    return weapi('/weapi/v1/cloud/get/byids', {'songIds': ids})


def getNosToken(
    filename,
    md5,
    fileSize,
    ext,
    ftype='audio',
    nos_product=3,
    bucket=BUCKET,
    local=False,
) -> dict:
    """allocate nos token for cloud upload (mobile api).

    Args:
        filename: file name.
        md5: file md5 hash.
        fileSize: file size.
        ext: file extension.
        ftype: upload type. defaults to 'audio'.
        nos_product: app type. defaults to 3.
        bucket: target bucket.
        local: unknown. defaults to false.

    Returns:
        dict
    """
    return eapi(
        '/eapi/nos/token/alloc',
        {
            'type': str(ftype),
            'nos_product': str(nos_product),
            'md5': str(md5),
            'local': str(local).lower(),
            'filename': str(filename),
            'fileSize': str(fileSize),
            'ext': str(ext),
            'bucket': str(bucket),
        },
    )


def setUploadObject(
    stream,
    md5,
    fileSize,
    objectKey,
    token,
    offset=0,
    compete=True,
    bucket=BUCKET,
    session=None,
) -> dict:
    """upload file to cloud drive.

    Args:
        stream: bytes or file-like object.
        md5: data md5 hash.
        objectKey: from getNosToken.
        token: from getNosToken.
        offset: resume offset. defaults to 0.
        compete: whether file is fully uploaded. defaults to true.

    Returns:
        dict
    """
    r = (session or getCurrentSession()).post(
        'http://45.127.129.8/%s/' % bucket + objectKey.replace('/', '%2F'),
        data=stream,
        params={'version': '1.0', 'offset': offset, 'complete': str(compete).lower()},
        headers={
            'x-nos-token': token,
            'Content-MD5': md5,
            'Content-Type': 'cloudmusic',
            'Content-Length': str(fileSize),
        },
    )
    return json.loads(r.text)


def getCheckCloudUpload(md5, ext='', length=0, bitrate=0, songId=0, version=1) -> dict:
    """check cloud upload status (mobile api).

    Args:
        md5: file md5 hash.
        ext: file extension.
        length: file size.
        bitrate: audio bitrate.
        songId: cloud resource id. 0 for new resource.
        version: upload version. defaults to 1.

    Returns:
        dict
    """
    return eapi(
        '/eapi/cloud/upload/check',
        {
            'songId': str(songId),
            'version': str(version),
            'md5': str(md5),
            'length': str(length),
            'ext': str(ext),
            'bitrate': str(bitrate),
        },
    )


def setUploadCloudInfo(
    resourceId, songid, md5, filename, song='.', artist='.', album='.', bitrate=128
) -> dict:
    """submit cloud upload info (mobile api).

    note: file corresponding to md5 must already be uploaded via setUploadObject.
    song must not contain '.' or '/'.

    Args:
        resourceId: from getNosToken.
        songid: from getCheckCloudUpload.
        md5: file md5 hash.
        filename: file name.
        song: song title. defaults to '.'.
        artist: artist name. defaults to '.'.
        album: album name. defaults to '.'.
        bitrate: audio bitrate. defaults to 128.

    Returns:
        dict
    """
    return eapi(
        '/eapi/upload/cloud/info/v2',
        {
            'resourceId': str(resourceId),
            'songid': str(songid),
            'md5': str(md5),
            'filename': str(filename),
            'song': str(song),
            'artist': str(artist),
            'album': str(album),
            'bitrate': bitrate,
        },
    )


def setPublishCloudResource(songid) -> dict:
    """publish cloud resource (mobile api).

    Args:
        songid: from setUploadCloudInfo.

    Returns:
        dict
    """
    return eapi('/eapi/cloud/pub/v2', {'songid': str(songid)})


def setRectifySongId(oldSongId, newSongId, session=None) -> dict:
    """rectify song id in cloud drive.

    Args:
        oldSongId: source song id.
        newSongId: target song id.

    Returns:
        dict
    """
    return (
        (session or getCurrentSession())
        .get(
            '/api/cloud/user/song/match',
            params={'songId': str(oldSongId), 'adjustSongId': str(newSongId)},
        )
        .json()
    )
