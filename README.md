# Southside Music
### Advanced Modern Player

## Install
Just download the latest release and unzip it, then launch `Launch.exe`.

## Usage
Do not use an anonymous account. Instead, go to the `Session` page to log into your own account.

## Contributing
Clone this repository and run `uv sync` to set up your environment.
And download embeddable Python 3.12.7 from https://www.python.org/downloads/release/python-3127/, then use get-pip.py to install pip for Python, and you can install the required libraries from requirements.txt
Download uv from https://uv.doczh.com/getting-started/installation/

## Tech Stack
- PySide: GUI base
- Fluent Widgets: Modern widgets
- Nuitka: Package
- pydub, sounddevice: play music
- numpy, scipy: Fast math support & FFT computing
- pyncm: NeteaseCloudMusic api requesting and account management

**Not for commercial use.**
