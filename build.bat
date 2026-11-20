@echo off
cd /d "%~dp0"
python build.py %*
if errorlevel 1 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)
pause
