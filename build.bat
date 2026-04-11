@echo off
chcp 65001
title Packaging SouthsideMusic

RD /S /Q build.result 2>nul
mkdir build.result

xcopy python-venv build.result\.venv /E /I /Y /Q
xcopy src build.result\src /E /I /Y /Q
xcopy fonts build.result\fonts /E /I /Y /Q
xcopy icons build.result\icons /E /I /Y /Q
xcopy ffmpeg build.result\ffmpeg /E /I /Y /Q

copy Launch.vbs build.result\ /Y

RD /S /Q "build.result\.venv\Lib\site-packages\__pycache__" 2>nul
del /s /q "build.result\.venv\Lib\site-packages\*.pyc" 2>nul

echo.
echo ✅ Build sucessful
echo.
pause