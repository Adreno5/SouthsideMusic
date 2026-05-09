# Southside Music

Advanced desktop music player — stream from NetEase CloudMusic with a custom audio engine.

![SouthsideMusic](images/showcase.png)

## Features

- **Search & stream** from NetEase CloudMusic's full catalog
- **Account login** — anonymous, cell phone, or QR code
- **Loudness normalization** — ITU-R BS.1770-4 (LUFS) for consistent volume across tracks
- **Stereo enhancement** — optional widening effect
- **Playback speed control** (0.1x – 3.0x)
- **Smart skip** — automatically skips silent endings
- **FFT spectrum visualization** — real-time frequency analysis
- **LRC & YRC lyrics** — word-by-word synced lyrics with translated support
- **Desktop lyrics** — floating always-on-top overlay for lyrics
- **WebSocket broadcast** — streams playback state, lyrics and FFT data to companion clients
- **Song export** — export with full metadata tags (MP3/FLAC/MP4/OGG/WAV)
- **Favorites** — folder-based organization
- **Four play modes** — repeat one, repeat list, shuffle, weighted-shuffle play in order
- **Dark / light theme** — auto-detection with system preference

## Install

Download the [latest release](https://github.com/Adreno5/SouthsideMusic/releases), unzip, and run `Launch.exe`.

> **Note**: `ffmpeg/bin/` is bundled with the release for audio decoding.

## Usage

Log in on the **Session** page with your NetEase CloudMusic account (cell phone or QR code). Anonymous accounts have limited access to high-quality streams.

## Development

```bash
# clone
git clone https://github.com/Adreno61/SouthsideMusic.git
cd SouthsideMusic

# Python 3.12 required
uv sync

# run
python src/main.py
```

### Build

```bash
# build.bat compiles the launcher with Nuitka and assembles the release folder
build.bat
```

Requires a Python 3.12.7 embeddable distribution placed at `embed_python/`. See `build.bat` for the full packaging pipeline (copies `src/`, `icons/`, `fonts/`, `ffmpeg/`, `images/` into `build.result/`).

## Tech Stack

| Layer     | Technology                                                                                                              |
| --------- | ----------------------------------------------------------------------------------------------------------------------- |
| GUI       | [PySide6](https://pypi.org/project/PySide6/) + [PySide6-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) |
| Audio     | [pydub](https://github.com/jiaaro/pydub) + [sounddevice](https://github.com/spatialaudio/python-sounddevice)            |
| Math      | [NumPy](https://numpy.org/) + [SciPy](https://scipy.org/) (FFT, signal processing)                                      |
| Metadata  | [mutagen](https://github.com/quodlibet/mutagen) (ID3, Vorbis, MP4 tags)                                                 |
| API       | [pyncm](https://github.com/greats3an/pyncm) (NetEase CloudMusic)                                                        |
| WebSocket | [Tornado](https://www.tornadoweb.org/)                                                                                  |
| Packaging | [Nuitka](https://nuitka.net/)                                                                                           |
| Font      | HarmonyOS Sans SC                                                                                                       |

## License

PolyForm Noncommercial License 1.0.0 — see [LICENSE](../LICENSE).
