#!/bin/bash
# Build MediaInsight for macOS
# Requires: pip install pyinstaller pymediainfo PySide6

echo "=== Building MediaInsight for macOS ==="
echo

# Clean previous build
rm -rf dist/MediaInsight dist/MediaInsight.app build/MediaInsight

# Run PyInstaller
pyinstaller MediaInsight.spec --noconfirm

echo
if [ -d "dist/MediaInsight.app" ]; then
    echo "=== Build successful! ==="
    echo "Output: dist/MediaInsight.app"
    echo
    echo "To install: drag MediaInsight.app to /Applications"
elif [ -d "dist/MediaInsight" ]; then
    echo "=== Build successful! ==="
    echo "Output: dist/MediaInsight/"
    echo "Run: ./dist/MediaInsight/MediaInsight"
else
    echo "=== Build FAILED ==="
fi
