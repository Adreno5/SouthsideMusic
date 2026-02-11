@echo off
title Building

nuitka src/main.py --standalone --enable-plugin=pyside6 --output-filename=SouthsideMusic --include-data-dir="./icons=./icons" --windows-console-mode=force

echo Done!
title Done
pause