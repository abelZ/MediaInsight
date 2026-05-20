#!/bin/bash
# Build MediaInsight for macOS
# Requires: pip install pyinstaller pymediainfo PySide6 python-vlc

set -e

echo "=== Building MediaInsight for macOS ==="
echo

# Check dependencies
python3 -c "import PySide6; import pymediainfo; import vlc" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "ERROR: Missing dependencies. Please run:"
    echo "  pip install PySide6 pymediainfo python-vlc pyinstaller"
    exit 1
fi

# Check VLC vendor files
if [ ! -f "vendor/vlc/macos/lib/libvlc.dylib" ]; then
    echo "WARNING: vendor/vlc/macos/lib/libvlc.dylib not found"
    echo "Player will only work if system VLC is installed."
    echo ""
    echo "To bundle VLC, copy from /Applications/VLC.app/Contents/MacOS/lib/:"
    echo "  libvlc.5.dylib       -> vendor/vlc/macos/lib/libvlc.dylib"
    echo "  libvlccore.9.dylib   -> vendor/vlc/macos/lib/libvlccore.dylib"
    echo "  /Applications/VLC.app/Contents/MacOS/plugins/ -> vendor/vlc/macos/plugins/"
    echo ""
fi

# Clean previous build
rm -rf dist/MediaInsight dist/MediaInsight.app build/MediaInsight

# Run PyInstaller
echo "Running PyInstaller..."
pyinstaller MediaInsight.spec --noconfirm

echo
if [ -d "dist/MediaInsight.app" ]; then
    echo "=== Build successful! ==="
    echo "Output: dist/MediaInsight.app"
    echo ""
    # Check VLC bundled
    if [ -f "dist/MediaInsight.app/Contents/MacOS/vlc/lib/libvlc.dylib" ] || \
       [ -f "dist/MediaInsight.app/Contents/Frameworks/libvlc.dylib" ]; then
        echo "VLC: bundled (embedded player will work)"
    else
        echo "VLC: NOT bundled (player requires system VLC or VLC.app installed)"
    fi
    echo ""
    echo "To install: drag MediaInsight.app to /Applications"
elif [ -d "dist/MediaInsight" ]; then
    echo "=== Build successful! ==="
    echo "Output: dist/MediaInsight/"
    echo "Run: ./dist/MediaInsight/MediaInsight"
else
    echo "=== Build FAILED ==="
    echo "Check the output above for errors."
    exit 1
fi
