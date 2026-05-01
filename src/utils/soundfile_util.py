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
from mutagen.id3._frames import APIC, TIT2, TPE1

_EXT_MAP: dict[type, str] = {
    MP3: '.mp3',
    FLAC: '.flac',
    MP4: '.m4a',
    OggOpus: '.opus',
    OggVorbis: '.ogg',
    WAVE: '.wav',
}

def _detect_format(song_bytes: bytes):
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(song_bytes)
        tmp_path = tmp.name
    try:
        audio = mutagen.File(tmp_path) # type: ignore
        if audio is None:
            raise ValueError(f"Invalid audio file format(magic bytes: {song_bytes[:8].hex()})")
        return tmp_path, type(audio)
    except Exception:
        os.unlink(tmp_path)
        raise


def _set_tags_mp3(audio: MP3, song_name: str, song_artists: str, song_image: bytes) -> None:
    try:
        audio.add_tags()
    except ID3NoHeaderError:
        audio = MP3(audio.filename)
        audio.add_tags()

    tags: mutagen.id3.ID3 = audio.tags # type: ignore
    if tags is None:
        audio.add_tags()
        tags = audio.tags

    tags.delall('TIT2')
    tags.delall('TPE1')
    tags.add(TIT2(encoding=3, text=song_name))
    tags.add(TPE1(encoding=3, text=song_artists))

    if song_image:
        tags.delall('APIC')
        tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=song_image))


def _set_tags_vorbis(audio: FLAC | OggOpus | OggVorbis, song_name: str, song_artists: str, song_image: bytes) -> None:
    audio['title'] = song_name
    audio['artist'] = song_artists

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


def _set_tags_mp4(audio: MP4, song_name: str, song_artists: str, song_image: bytes) -> None:
    audio['\xa9nam'] = song_name
    audio['\xa9ART'] = song_artists

    if song_image:
        audio['covr'] = [MP4Cover(song_image, imageformat=MP4Cover.FORMAT_JPEG)]


def _set_tags_wave(audio: WAVE, song_name: str, song_artists: str, song_image: bytes) -> None:
    if audio.tags is None:
        audio.add_tags()
    tags: mutagen.id3.ID3 = audio.tags  # type: ignore[assignment]
    tags.add(TIT2(encoding=3, text=song_name))
    tags.add(TPE1(encoding=3, text=song_artists))

    if song_image:
        tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=song_image))


def saveSongWithMetaInformations(
    song_bytes: bytes,
    song_image: bytes,
    song_name: str,
    song_artists: str,
    output_path: str = ''
) -> str:
    tmp_path, fmt = _detect_format(song_bytes)

    try:
        audio = mutagen.File(tmp_path) # type: ignore
        if audio is None:
            raise ValueError("Cannot detect audio format")

        if isinstance(audio, MP3):
            _set_tags_mp3(audio, song_name, song_artists, song_image)
        elif isinstance(audio, (FLAC, OggOpus, OggVorbis)):
            _set_tags_vorbis(audio, song_name, song_artists, song_image)
        elif isinstance(audio, MP4):
            _set_tags_mp4(audio, song_name, song_artists, song_image)
        elif isinstance(audio, WAVE):
            _set_tags_wave(audio, song_name, song_artists, song_image)
        else:
            raise ValueError(f"Unsupported audio format: {fmt.__name__}")

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