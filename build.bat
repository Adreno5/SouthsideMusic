@echo off
echo Building

RD /S /Q build.result >nul
RD /S /Q *.dist >nul

echo Building - Nuitka
call build_venv\Scripts\python.exe -m nuitka launcher.py --windows-console-mode=hide --output-filename=Launch --standalone --windows-icon-from-ico=icons\app.ico

mkdir build.result >nul
echo Building - Copy launcher
rem Launch.exe and its runtime must stay together (Nuitka standalone cannot load
rem python314.dll from another folder), and they cannot sit in the app root:
rem the app's free-threaded Python would load that GIL-build DLL through
rem scipy -> ctypes and crash. So: its own folder plus a shortcut in the root.
xcopy .\launcher.dist .\build.result\raw\launcher /E /I /Y /Q /J >nul
echo Building - Link Launch.exe into the root
rem Relative working directory: run the script from the folder it must fill in.
pushd "%~dp0build.result\raw"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\create_shortcut.ps1"
if errorlevel 1 echo [WARN] Could not create the Launch.lnk shortcut.
popd
echo Building - Copy embed_python
xcopy .\embed_python .\build.result\raw\python /E /I /Y /Q /J >nul
echo Building - Copy free-threaded Python
if exist freethreaded_python (
    xcopy freethreaded_python .\build.result\raw\freethreaded_python /E /I /Y /Q /J >nul
)
echo Building - Copy src
xcopy .\src .\build.result\raw\src /E /I /Y /Q /J >nul
echo Building - Copy fonts
xcopy .\fonts .\build.result\raw\fonts /E /I /Y /Q /J >nul
echo Building - Copy icons
xcopy .\icons .\build.result\raw\icons /E /I /Y /Q /J >nul
echo Building - Copy images
xcopy .\images .\build.result\raw\images /E /I /Y /Q /J >nul

copy .\pyproject.toml .\build.result\raw\pyproject.toml
copy .\bootstrap.py .\build.result\raw\bootstrap.py
copy .\full_requirements.txt .\build.result\raw\full_requirements.txt

echo Building - Verify runtime imports
rem bootstrap.py imports PySide6 before it can repair anything, so a shipped
rem python that lost it would die on startup with no way back. Only the
rem bootstrap-capable modules are checked here; the full PySide6 is installed
rem by bootstrap on first launch.
"build.result\raw\python\python.exe" -c "import PySide6, shiboken6"
if errorlevel 1 (
    echo [ERROR] build.result\raw\python cannot import PySide6/shiboken6.
    echo         Fix embed_python\Lib\site-packages and build again.
    pause
    exit /b 1
)

echo Building - Remove unneeded files
RD /S /Q "build.result\raw\python\Lib\site-packages\__pycache__" >nul
RD /S /Q "build.result\raw\python\Lib\site-packages\*.dist-info" >nul
RD /S /Q "build.result\raw\python\Lib\site-packages\*.egg-info" >nul
RD /S /Q "build.result\raw\python\Lib\site-packages\tests" >nul
RD /S /Q "build.result\raw\python\Lib\site-packages\test" >nul
RD /S /Q "build.result\raw\python\Lib\site-packages\docs" >nul
RD /S /Q "build.result\raw\python\Lib\site-packages\examples" >nul
RD /S /Q "build.result\raw\python\Lib\site-packages\PySide6\*.pdb" >nul
RD /S /Q "build.result\raw\python\Lib\__pycache__" >nul
del /S /Q "build.result\raw\python\Lib\site-packages\*.pyc" >nul
if exist "build.result\raw\freethreaded_python" (
    RD /S /Q "build.result\raw\freethreaded_python\include" >nul
    RD /S /Q "build.result\raw\freethreaded_python\libs" >nul
    RD /S /Q "build.result\raw\freethreaded_python\tcl" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\test" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\idlelib" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\tkinter" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\site-packages\__pycache__" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\site-packages\audioop" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\site-packages\bin" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\site-packages\numpy" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\site-packages\numpy.libs" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\site-packages\PIL" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\site-packages\pydub" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\site-packages\scipy" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\site-packages\scipy.libs" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\site-packages\psutil" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\site-packages\PySide6" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\site-packages\qfluentwidgets" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\site-packages\qframelesswindow" >nul
    RD /S /Q "build.result\raw\freethreaded_python\Lib\site-packages\shiboken6" >nul
    for /D %%d in ("build.result\raw\freethreaded_python\Lib\site-packages\audioop_lts-*.dist-info") do RD /S /Q "%%~fd" >nul
    for /D %%d in ("build.result\raw\freethreaded_python\Lib\site-packages\numpy-*.dist-info") do RD /S /Q "%%~fd" >nul
    for /D %%d in ("build.result\raw\freethreaded_python\Lib\site-packages\pillow-*.dist-info") do RD /S /Q "%%~fd" >nul
    for /D %%d in ("build.result\raw\freethreaded_python\Lib\site-packages\pydub-*.dist-info") do RD /S /Q "%%~fd" >nul
    for /D %%d in ("build.result\raw\freethreaded_python\Lib\site-packages\scipy-*.dist-info") do RD /S /Q "%%~fd" >nul
    for /D %%d in ("build.result\raw\freethreaded_python\Lib\site-packages\psutil-*.dist-info") do RD /S /Q "%%~fd" >nul
    for /D %%d in ("build.result\raw\freethreaded_python\Lib\site-packages\pyside6-*.dist-info") do RD /S /Q "%%~fd" >nul
    for /D %%d in ("build.result\raw\freethreaded_python\Lib\site-packages\pyside6_addons-*.dist-info") do RD /S /Q "%%~fd" >nul
    for /D %%d in ("build.result\raw\freethreaded_python\Lib\site-packages\pyside6_essentials-*.dist-info") do RD /S /Q "%%~fd" >nul
    for /D %%d in ("build.result\raw\freethreaded_python\Lib\site-packages\pyside6_fluent_widgets-*.dist-info") do RD /S /Q "%%~fd" >nul
    for /D %%d in ("build.result\raw\freethreaded_python\Lib\site-packages\pysidesix_frameless_window-*.dist-info") do RD /S /Q "%%~fd" >nul
    for /D %%d in ("build.result\raw\freethreaded_python\Lib\site-packages\shiboken6-*.dist-info") do RD /S /Q "%%~fd" >nul
    del /S /Q "build.result\raw\freethreaded_python\*.pdb" >nul
    del /S /Q "build.result\raw\freethreaded_python\Lib\site-packages\*.pyc" >nul
    del /Q "build.result\raw\freethreaded_python\Lib\site-packages\*.whl" >nul
    del /Q "build.result\raw\freethreaded_python\Lib\site-packages\README.txt" >nul
)

echo Building - Generate icon
call python scripts\create_icon.py

echo Building - Locate Inno Setup
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

echo Building - Inno Setup installer
if defined ISCC (
    call "%ISCC%" installer.iss
) else (
    echo [WARN] Inno Setup compiler not found.
    echo        Run 'python setup_workspace.py --innosetup' to auto-install.
    echo        Or download from: https://jrsoftware.org/isdl.php
    echo        Skipping installer build. Raw files are in build.result\raw\
)

echo Building - Cleanup
RD /S /Q launcher.dist >nul
RD /S /Q launcher.build >nul
RD /S /Q launcher.onefile-build >nul

echo Done!
echo Done
pause
