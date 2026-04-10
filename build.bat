@echo off
title Building

RD /S /Q build.result 2>nul
call .venv\Scripts\activate
call nuitka src/launch.py --standalone --windows-console-mode=hide --output-filename=Launch
mkdir build.result 2>nul
xcopy .\launch.dist .\build.result /E /I /Y
xcopy .\.venv .\build.result\.venv /E /I /Y
xcopy .\src .\build.result\src /E /I /Y
xcopy .\fonts .\build.result\fonts /E /I /Y
xcopy .\icons .\build.result\icons /E /I /Y

echo Done!
title Done
pause