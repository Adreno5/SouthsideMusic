@echo off
title Building

RD /S /Q build.result 2>nul
RD /S /Q *.dist 2>nul

call .venv\Scripts\activate
title Building - Nuitka
call nuitka src/launch.py --windows-console-mode=hide --output-filename=Launch --standalone

mkdir build.result 2>nul
title Building - Copy launch.dist
xcopy .\launch.dist .\build.result /E /I /Y
title Building - Copy .venv
xcopy .\embed_python .\build.result\python /E /I /Y
title Building - Copy src
xcopy .\src .\build.result\src /E /I /Y
title Building - Copy fonts
xcopy .\fonts .\build.result\fonts /E /I /Y
title Building - Copy icons
xcopy .\icons .\build.result\icons /E /I /Y
title Building - Copy ffmpeg
xcopy .\ffmpeg .\build.result\ffmpeg /E /I /Y
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

echo Done!
title Done
pause