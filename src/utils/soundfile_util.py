import os
import shutil
import tempfile

import mutagen
import mutagen.id3
from mutagen.mp3 import MP3
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE
from mutagen.id3._util import ID3NoHeaderError
from mutagen.id3._frames import (
    APIC, TIT2, TPE1, TALB, TPE2, TDRC, TRCK, TCON, TCOM, USLT
)

from utils.lyric_util import LRCLyricParser

_EXT_MAP: dict[type, str] = {
    MP3: '.mp3',
    FLAC: '.flac',
    MP4: '.m4a',
    OggOpus: '.opus',
    OggVorbis: '.ogg',
    WAVE: '.wav',
}

def _clean_lrc(raw_lyric: str) -> str:
    mgr = LRCLyricParser()
    mgr.cur = raw_lyric
    mgr.parse()
    lines = []
    for info in mgr.parsed:
        minutes = int(info['time'] // 60)
        seconds = info['time'] % 60
        timestamp = f'[{minutes:02d}:{seconds:05.2f}]'
        lines.append(f'{timestamp}{info['content']}')
    return '\n'.join(lines)

def _detect_format(song_bytes: bytes):
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(song_bytes)
        tmp_path = tmp.name
    try:
        audio = mutagen.File(tmp_path)  # type: ignore
        if audio is None:
            raise ValueError(f'Invalid audio file format (magic bytes: {song_bytes[:8].hex()})')
        return tmp_path, type(audio)
    except Exception:
        os.unlink(tmp_path)
        raise

def _set_id3_tags(
    audio: MP3 | WAVE,
    song_name: str,
    song_artists: str,
    song_image: bytes,
    lyrics: str,
    album: str,
    album_artist: str,
    year: str,
    track_number: str,
    genre: str,
    composer: str,
) -> None:
    # Ensure tags exist
    if isinstance(audio, MP3):
        try:
            audio.add_tags()
        except ID3NoHeaderError:
            audio = MP3(audio.filename)
            audio.add_tags()
    else:  # WAVE
        if audio.tags is None:
            audio.add_tags()

    tags: mutagen.id3.ID3 = audio.tags  # type: ignore
    if tags is None:
        # fallback
        audio.add_tags()
        tags = audio.tags

    # Basic fields
    tags.delall('TIT2')
    tags.delall('TPE1')
    tags.add(TIT2(encoding=3, text=song_name))
    tags.add(TPE1(encoding=3, text=song_artists))

    # Lyrics
    if lyrics:
        tags.delall('USLT')
        tags.add(USLT(encoding=3, lang='eng', desc='', text=lyrics))

    # Album
    if album:
        tags.delall('TALB')
        tags.add(TALB(encoding=3, text=album))

    # Album artist
    if album_artist:
        tags.delall('TPE2')
        tags.add(TPE2(encoding=3, text=album_artist))

    # Year / Date
    if year:
        tags.delall('TDRC')
        tags.add(TDRC(encoding=3, text=year))

    # Track number ('1' or '1/10')
    if track_number:
        tags.delall('TRCK')
        tags.add(TRCK(encoding=3, text=track_number))

    # Genre
    if genre:
        tags.delall('TCON')
        tags.add(TCON(encoding=3, text=genre))

    # Composer
    if composer:
        tags.delall('TCOM')
        tags.add(TCOM(encoding=3, text=composer))

    # Cover image
    if song_image:
        tags.delall('APIC')
        tags.add(
            APIC(
                encoding=3,
                mime='image/jpeg',
                type=3,
                desc='Cover',
                data=song_image,
            )
        )

def _set_tags_vorbis(
    audio: FLAC | OggOpus | OggVorbis,
    song_name: str,
    song_artists: str,
    song_image: bytes,
    lyrics: str,
    album: str,
    album_artist: str,
    year: str,
    track_number: str,
    genre: str,
    composer: str,
) -> None:
    audio['title'] = song_name
    audio['artist'] = song_artists

    if lyrics:
        audio['lyrics'] = lyrics
    if album:
        audio['album'] = album
    if album_artist:
        audio['albumartist'] = album_artist
    if year:
        audio['date'] = year
    if track_number:
        audio['tracknumber'] = track_number
    if genre:
        audio['genre'] = genre
    if composer:
        audio['composer'] = composer

    # Cover image
    if song_image:
        if isinstance(audio, FLAC):
            audio.clear_pictures()
        elif 'METADATA_BLOCK_PICTURE' in audio:
            del audio['METADATA_BLOCK_PICTURE']

        pic = Picture()
        pic.type = 3
        pic.desc = 'Cover'
        pic.mime = 'image/jpeg'
        pic.data = song_image

        if isinstance(audio, FLAC):
            audio.add_picture(pic)
        else:
            audio['METADATA_BLOCK_PICTURE'] = [pic.write()]

def _set_tags_mp4(
    audio: MP4,
    song_name: str,
    song_artists: str,
    song_image: bytes,
    lyrics: str,
    album: str,
    album_artist: str,
    year: str,
    track_number: str,
    genre: str,
    composer: str,
) -> None:
    audio['\xa9nam'] = song_name
    audio['\xa9ART'] = song_artists

    if lyrics:
        audio['\xa9lyr'] = lyrics
    if album:
        audio['\xa9alb'] = album
    if album_artist:
        audio['aART'] = album_artist
    if year:
        audio['\xa9day'] = year
    if track_number:
        # 'trkn' expects a tuple (track, total)
        parts = track_number.split('/')
        try:
            tr = int(parts[0])
            total = int(parts[1]) if len(parts) > 1 else 0
            audio['trkn'] = [(tr, total)]
        except (ValueError, IndexError):
            # fallback
            pass
    if genre:
        audio['\xa9gen'] = genre
    if composer:
        audio['\xa9wrt'] = composer

    if song_image:
        audio['covr'] = [MP4Cover(song_image, imageformat=MP4Cover.FORMAT_JPEG)]

def saveSongWithInformations(
    song_bytes: bytes,
    song_image: bytes,
    song_name: str,
    song_artists: str,
    output_path: str,
    lyrics: str = '',
    album: str = '',
    album_artist: str = '',
    year: str = '',
    track_number: str = '',
    genre: str = '',
    composer: str = '',
) -> str:
    tmp_path, fmt = _detect_format(song_bytes)

    try:
        audio = mutagen.File(tmp_path)  # type: ignore
        if audio is None:
            raise ValueError('Cannot detect audio format')

        if isinstance(audio, MP3):
            _set_id3_tags(
                audio, song_name, song_artists, song_image,
                _clean_lrc(lyrics), album, album_artist, year, track_number, genre, composer
            )
        elif isinstance(audio, WAVE):
            _set_id3_tags(
                audio, song_name, song_artists, song_image,
                _clean_lrc(lyrics), album, album_artist, year, track_number, genre, composer
            )
        elif isinstance(audio, (FLAC, OggOpus, OggVorbis)):
            _set_tags_vorbis(
                audio, song_name, song_artists, song_image,
                _clean_lrc(lyrics), album, album_artist, year, track_number, genre, composer
            )
        elif isinstance(audio, MP4):
            _set_tags_mp4(
                audio, song_name, song_artists, song_image,
                _clean_lrc(lyrics), album, album_artist, year, track_number, genre, composer
            )
        else:
            raise ValueError(f'Unsupported audio format: {fmt.__name__}')

        audio.save(tmp_path)

        ext = _EXT_MAP.get(fmt, '.bin')
        final_path = tmp_path.rsplit('.', 1)[0] + ext
        if final_path != tmp_path:
            os.rename(tmp_path, final_path)

        if output_path and os.path.isabs(os.path.dirname(output_path) or '.'):
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            if os.path.exists(output_path):
                os.remove(output_path)
            shutil.move(final_path, output_path)
            return output_path

        return final_path

    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def getSongFormat(song_bytes: bytes):
    tmp_path, fmt = _detect_format(song_bytes)
    return _EXT_MAP.get(fmt, '.bin')