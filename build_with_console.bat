@echo off
title Building

nuitka src/main.py --standalone --enable-plugin=pyside6,numpy --windows-console-mode=force --output-filename=SouthsideMusic --include-data-dir="./icons=./icons" --include-data-dir="./fonts=./fonts" --include-data-dir="./.venv/Lib/site-packages/pyloudnorm=./pyloudnorm"

echo Done!
title Done
pause