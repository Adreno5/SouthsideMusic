@echo off
title Building

RD /S /Q build.result 2>nul
RD /S /Q *.dist 2>nul

call .venv\Scripts\activate
title Building - Nuitka
call nuitka launcher.py --windows-console-mode=hide --output-filename=Launch --standalone --windows-icon-from-ico=icons\app.ico

mkdir build.result 2>nul
title Building - Copy launcher.dist
xcopy .\launcher.dist .\build.result /E /I /Y
title Building - Copy embed_python
xcopy .\embed_python .\build.result\python /E /I /Y
title Building - Copy src
xcopy .\src .\build.result\src /E /I /Y
title Building - Copy fonts
xcopy .\fonts .\build.result\fonts /E /I /Y
title Building - Copy icons
xcopy .\icons .\build.result\icons /E /I /Y
title Building - Copy images
xcopy .\images .\build.result\images /E /I /Y

copy .\pyproject.toml .\build.result\pyproject.toml

title Building - Remove unneeded files
RD /S /Q "build.result\.venv\Lib\site-packages\__pycache__" 2>nul
RD /S /Q "build.result\.venv\Lib\site-packages\*.dist-info" 2>nul
RD /S /Q "build.result\.venv\Lib\site-packages\*.egg-info" 2>nul
RD /S /Q "build.result\.venv\Lib\site-packages\tests" 2>nul
RD /S /Q "build.result\.venv\Lib\site-packages\test" 2>nul
RD /S /Q "build.result\.venv\Lib\site-packages\docs" 2>nul
RD /S /Q "build.result\.venv\Lib\site-packages\examples" 2>nul
RD /S /Q "build.result\.venv\Lib\site-packages\PySide6\*.pdb" 2>nul
RD /S /Q "build.result\.venv\Lib\__pycache__" 2>nul
del /S /Q "build.result\.venv\Lib\site-packages\*.pyc" 2>nul

title Building - Reorganize into raw
mkdir build.result\raw 2>nul
move build.result\python build.result\raw\python >nul
move build.result\src build.result\raw\src >nul
move build.result\fonts build.result\raw\fonts >nul
move build.result\icons build.result\raw\icons >nul
move build.result\images build.result\raw\images >nul
for %%f in (build.result\*) do move "%%f" "build.result\raw\" >nul 2>nul

title Building - Generate icon
call python scripts\create_icon.py

title Building - Locate Inno Setup
set ISCC=
for /f "skip=1 tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 7_is1" /v "InstallLocation" 2^>nul ^| findstr /i "REG_"') do set "ISCC=%%b\ISCC.exe"
if not defined ISCC (
    for /f "skip=1 tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 7_is1" /v "InstallLocation" 2^>nul ^| findstr /i "REG_"') do set "ISCC=%%b\ISCC.exe"
)
if not defined ISCC (
    for /f "skip=1 tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1" /v "InstallLocation" 2^>nul ^| findstr /i "REG_"') do set "ISCC=%%b\ISCC.exe"
)
if not defined ISCC (
    for /f "skip=1 tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1" /v "InstallLocation" 2^>nul ^| findstr /i "REG_"') do set "ISCC=%%b\ISCC.exe"
)
if not defined ISCC (
    for /f "delims=" %%a in ('where iscc 2^>nul') do set "ISCC=%%a"
)
if not defined ISCC (
    for %%p in (
        "C:\Program Files\Inno Setup 7\ISCC.exe"
        "C:\Program Files (x86)\Inno Setup 7\ISCC.exe"
        "D:\Program Files\Inno Setup 7\ISCC.exe"
        "C:\Program Files\Inno Setup 6\ISCC.exe"
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
        "D:\Program Files\Inno Setup 6\ISCC.exe"
    ) do if exist %%p set "ISCC=%%~p"
)

title Building - Inno Setup installer
if defined ISCC (
    call "%ISCC%" installer.iss
) else (
    echo [WARN] Inno Setup compiler not found.
    echo        Run 'python setup_workspace.py --innosetup' to auto-install.
    echo        Or download from: https://jrsoftware.org/isdl.php
    echo        Skipping installer build. Raw files are in build.result\raw\
)

title Building - Cleanup
RD /S /Q launcher.dist 2>nul
RD /S /Q launcher.build 2>nul
RD /S /Q launcher.onefile-build 2>nul

echo Done!
title Done
pause
