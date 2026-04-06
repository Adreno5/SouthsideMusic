@echo off
title Building

call .venv\Scripts\activate
call nuitka src/main.py --standalone --enable-plugin=pyside6,numpy --windows-console-mode=hide --output-filename=SouthsideMusic --include-data-dir="./icons=./icons" --include-data-dir="./fonts=./fonts"

mkdir .\main.dist\ffmpeg 2>nul
xcopy .\ffmpeg .\main.dist\ffmpeg /E /I /Y

echo Done!
title Done
pause