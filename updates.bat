@echo off
setlocal

set /p "VER=Enter version number (e.g. 32): "

echo.
echo Updating version to v%VER%...

python -c "import re; c=open('pyproject.toml','r',encoding='utf-8').read(); open('pyproject.toml','w',encoding='utf-8').write(re.sub(r'version = \"v\d+\"', 'version = \"v%VER%\"', c))"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to update pyproject.toml.
    pause
    exit /b 1
)

python -c "import re; c=open('installer.iss','r',encoding='utf-8').read(); open('installer.iss','w',encoding='utf-8').write(re.sub(r'#define AppVersion \"v\d+\"', '#define AppVersion \"v%VER%\"', c))"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to update installer.iss.
    pause
    exit /b 1
)

echo Done! pyproject.toml and installer.iss updated to v%VER%.
pause
