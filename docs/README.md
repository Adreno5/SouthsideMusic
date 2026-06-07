[中文版本](README_zh.md)

# Southside Music

> In an age drowning in things that merely work, what's worth using is what earns the name of a real work. True craft lives in the hours no one sees.

v30 — A third-party NetEase CloudMusic desktop client. High-quality streaming, word-by-word lyrics, loudness normalization, and desktop lyrics overlay.

> For the project's history from the first line of code to v30, see [SouthsideMusic Story](SouthsideMusic_Story.md).

---

## Table of Contents

- [What It Is](#what-it-is)
- [Features](#features)
- [Installation](#installation)
- [Getting Started](#getting-started)
- [Interface Guide](#interface-guide)
- [Advanced Features](#advanced-features)
- [Tips](#tips)
- [Development](#development)

---

## What It Is

Southside Music is a Windows desktop music player for NetEase CloudMusic. Log in with your account to search, browse, and play from the full catalog. It has its own audio engine with loudness normalization, speed control, and spectrum visualization, plus word-by-word lyrics and a desktop overlay.

In short: it plays your NetEase music in a cleaner, more focused way.

---

## Features

**Playback**
- Full-catalog search and streaming from NetEase CloudMusic
- Loudness normalization — all songs play at the same perceived volume, no more sudden jumps
- Speed control from 0.1× to 3.0×
- Optional stereo widening for a cleaner headphone soundstage
- Auto-skip silent endings for seamless transitions
- Preloading of the next track for near-instant switching

**Visuals**
- Real-time spectrum visualization
- Word-by-word lyric highlighting
- Desktop lyrics — an always-on-top overlay that shows real-time lyrics
- Dark and light theme, auto-switching with your system

**Organization**
- Browse and play your NetEase cloud playlists
- Download cloud songs into local folders for offline listening
- Export songs as audio files with embedded cover art and metadata

**Auto-Update**
- Checks GitHub releases and applies updates automatically

---

## Installation

Download `SouthsideMusic_win64_setup.exe` from the latest version on the [Releases page](https://github.com/Adreno5/SouthsideMusic/releases) and run it.

> FFmpeg is required on first launch.

---

## Getting Started

### 1. Log In

Open the app and use **Account** in the bottom-left corner to log in:

- **Phone number** — Enter your number, then the verification code
- **QR code** — Scan with the NetEase CloudMusic app

Using the app without logging in is nearly impossible — anonymous accounts are limited to 30-second previews for most songs.

### 2. Search

Click the search box in the title bar, type a keyword, and hit Enter. Results appear directly below. Click the paper-plane icon to play, or the heart icon to save to a local folder.

### 3. Browse Playlists

Your NetEase cloud playlists appear in the left sidebar's favorites area. Click a playlist to load its songs, double-click to play. Right-click a cloud song and select "Add to Local Folder" to download it for offline listening.

### 4. Manage Local Favorites

Click "Add Folder" in the left sidebar's favorites area to create folders for organizing your songs.

---

## Interface Guide

### Search

Click the title bar search box, type a keyword, and press Enter. Results show song name, artist, and cover.

### Now Playing

Click the persistent progress bar at the bottom to expand the playing panel. The layout has two halves:

- **Left** — Album cover on top, song name and artist below
- **Right** — Scrolling lyrics with dual-language support and word-by-word highlighting

The spectrum animation and playback controls stay in the bottom bar — no need to expand the panel to use them.

### Favorites Area

The left sidebar combines local folders and cloud playlists. Click "Add Folder" to create a local folder; click a cloud playlist to load its songs. Right-click songs for export, add-to-folder, and other actions.

### Settings

Click **Setting** in the bottom-left corner. Adjustable options include:

| Setting | Description |
|---|---|
| Target LUFS | Volume normalization baseline. Lower = louder. Keep at -16 |
| Playback Speed | 0.1× to 3.0× |
| Play Mode | Repeat all / Repeat one / Shuffle / Sequential |
| Stereo Widening | Broaden the stereo soundstage |
| Skip Silent Endings | Auto-skip trailing silence |
| Silence Threshold | Volume level considered "silent" |
| FFT Spectrum | Toggle frequency visualization |
| Desktop Lyrics | Toggle always-on-top lyrics overlay |
| Output Device | Select audio output device |

---

## Advanced Features

### Word-by-Word Lyrics

When a song supports word-level timing data, the playing panel switches to word-by-word highlighting.

### Desktop Lyrics

Enable in Settings to show an always-on-top floating window with live lyrics. The background is fully opaque. Drag it anywhere — snap to the top edge of the screen by dragging upward.

### Song Export

Right-click any song and select "Export." The exported file is ready to drop onto an MP3 player or USB drive.

---

## Tips

- **Spacebar** toggles play/pause
- **Mouse wheel in the lyrics area** scrolls through lyrics; click to jump playback to that position
- Right-click cloud playlist songs to **save them to a local folder** for offline listening
- The app's theme color is **extracted from the current song's album cover**
- If the overall volume feels off, adjust the **loudness normalization target** in Settings

---

## Development

### Requirements

Python 3.12+ (3.12.7 recommended), Windows OS, package manager, internet connection (initial setup only).

### Setup

```bash
git clone https://github.com/Adreno5/SouthsideMusic.git
cd SouthsideMusic
python setup_workspace.py
```

`setup_workspace.py` handles all dependency setup automatically.

### Run from Source

```bash
uv run src/main.py
```

### Type Checking

```bash
uv run mypy src/
```

### Build

```bash
build.bat
```

`build.bat` produces:

```
build.result\
├── raw\          Runnable directory (portable)
└── installer\    Installer (SouthsideMusic_win64_setup.exe)
```

### Tech Stack

| Layer | Technology |
|---|---|
| GUI | PySide6 + PySide6-Fluent-Widgets |
| Audio | pydub + sounddevice |
| Math | NumPy + SciPy (FFT, signal processing) |
| Metadata | mutagen (ID3, Vorbis, MP4 tags) |
| API | pyncm (NetEase CloudMusic) |
| Networking | Tornado |
| Packaging | Nuitka + Inno Setup |
| Font | HarmonyOS Sans SC |

### Configuration

All settings are stored in `config.json`. The file checks for changes every second and auto-saves when modified.

### License

PolyForm Noncommercial License 1.0.0 — see [LICENSE](../LICENSE).

Commercial use is prohibited. The developer assumes no liability for any misuse, including but not limited to illegal redistribution of exported music. Users are solely responsible for their own actions.
