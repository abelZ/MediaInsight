@echo off
REM Build MediaInsight for Windows
REM Requires: pip install pyinstaller PySide6
REM Optional: pip install pymediainfo python-vlc

echo === Building MediaInsight for Windows ===
echo.

REM Check required dependencies
python -c "import PySide6" 2>nul
if %errorlevel% neq 0 (
    echo ERROR: PySide6 not installed. Please run:
    echo   pip install PySide6
    pause
    exit /b 1
)

python -c "import PyInstaller" 2>nul
if %errorlevel% neq 0 (
    echo ERROR: PyInstaller not installed. Please run:
    echo   pip install pyinstaller
    pause
    exit /b 1
)

REM Check optional dependencies
python -c "import pymediainfo" 2>nul
if %errorlevel% neq 0 (
    echo WARNING: pymediainfo not installed. MediaInfo panel will be disabled.
    echo   pip install pymediainfo
    echo.
)

python -c "import vlc" 2>nul
if %errorlevel% neq 0 (
    echo NOTE: python-vlc not installed or VLC not found.
    echo   Player will use bundled VLC libraries from vendor\vlc\win64\
    echo   pip install python-vlc
    echo.
)

REM Check VLC vendor files
if not exist vendor\vlc\win64\libvlc.dll (
    echo WARNING: vendor\vlc\win64\libvlc.dll not found
    echo Player will only work if system VLC is installed at runtime.
    echo.
    echo To bundle VLC, copy these files from your VLC installation:
    echo   C:\Program Files\VideoLAN\VLC\libvlc.dll     -^> vendor\vlc\win64\
    echo   C:\Program Files\VideoLAN\VLC\libvlccore.dll -^> vendor\vlc\win64\
    echo   C:\Program Files\VideoLAN\VLC\plugins\       -^> vendor\vlc\win64\plugins\
    echo.
)

REM Clean previous build
if exist dist\MediaInsight rmdir /s /q dist\MediaInsight
if exist build\MediaInsight rmdir /s /q build\MediaInsight

REM Run PyInstaller
echo Running PyInstaller...
python -m PyInstaller MediaInsight.spec --noconfirm

echo.
if exist dist\MediaInsight\MediaInsight.exe (
    echo === Build successful! ===
    echo Output: dist\MediaInsight\MediaInsight.exe
    echo.
    REM Show VLC status
    if exist dist\MediaInsight\libvlc.dll (
        echo VLC: bundled ^(embedded player will work^)
    ) else (
        echo VLC: NOT bundled ^(player requires system VLC^)
    )
    echo.
    echo Run: dist\MediaInsight\MediaInsight.exe
) else (
    echo === Build FAILED ===
    echo Check the output above for errors.
)
echo.
pause
