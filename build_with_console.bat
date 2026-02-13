@echo off
title Building

nuitka src/main.py --standalone --enable-plugin=pyside6,numpy --windows-console-mode=force --output-filename=SouthsideMusic --include-data-dir="./icons=./icons" --include-data-dir="./fonts=./fonts"

echo Done!
title Done
pause