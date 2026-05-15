@echo off
REM Build MediaInsight for Windows
REM Requires: pip install pyinstaller pymediainfo PySide6

echo === Building MediaInsight for Windows ===
echo.

REM Clean previous build
if exist dist\MediaInsight rmdir /s /q dist\MediaInsight
if exist build\MediaInsight rmdir /s /q build\MediaInsight

REM Run PyInstaller via python -m
python -m PyInstaller MediaInsight.spec --noconfirm

echo.
if exist dist\MediaInsight\MediaInsight.exe (
    echo === Build successful! ===
    echo Output: dist\MediaInsight\MediaInsight.exe
    echo.
    echo To create a single-file exe (slower startup):
    echo   python -m PyInstaller MediaInsight.spec --noconfirm --onefile
) else (
    echo === Build FAILED ===
)
pause
