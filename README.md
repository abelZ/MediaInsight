# MediaInsight

A cross-platform, high-performance media analysis tool built with Python and PySide6 (Qt6).

Parses media files at the **raw byte level** (no FFmpeg dependency) and displays packet/box structure with detailed field-level decoding.

![MediaInsight](resources/icons/app_icon_128.png)

## Features

### Supported Formats

| Format | View | Capabilities |
|--------|------|-------------|
| **FLV** | Table (packet list) | Tag parsing, H.264/H.265/AV1 NALU extraction, SPS/PPS decoding, AMF0 script data, Enhanced RTMP |
| **MPEG-TS** | Table (packet/PES view) | PAT/PMT parsing, PES reassembly, frame type detection (I/P/B), H.264/H.265 Annex B NALU parsing |
| **MP4/MOV** | Tree (box hierarchy) | Full box parsing (60+ box types), mdat chunk/sample listing, avcC/hvcC/esds codec config decoding |

### UI Features

- **Hex View**: Split hex + ASCII display with byte-level highlighting
- **Detail Panel**: Tree-based field display with collapsible groups
- **Player Page**: Built-in media player + MediaInfo metadata display
- **Theme System**: 8 built-in color themes (Catppuccin, One Dark Pro, Dracula, Tokyo Night, Monokai Pro, GitHub Dark, Nord, Solarized Dark)
- **Background Parsing**: Non-blocking file loading with progress indication
- **Filter System**: Filter by type (Video/Audio/Script), IDR frames, SEI presence

### Analysis Capabilities

- H.264 SPS/PPS full bitstream parsing (profile, level, resolution, chroma, etc.)
- H.265 VPS/SPS/PPS parsing
- MPEG-2 picture type detection
- Frame type identification: I/P/B via actual slice header parsing (not just random_access_indicator)
- MP4 sample table cross-referencing (stco + stsc + stsz + stss)
- AAC AudioSpecificConfig decoding (profile, sample rate, channels)

## Installation

### Requirements

- Python 3.10+
- PySide6
- pymediainfo (for Player page MediaInfo display)

### From Source

```bash
git clone https://github.com/your-repo/MediaInsight.git
cd MediaInsight
pip install -r requirements.txt
python run.py
```

### Dependencies

```bash
pip install PySide6 pymediainfo
```

#### Optional: MediaInfo library

`pymediainfo` requires the MediaInfo library to be installed on your system:

- **Windows**: Download from https://mediaarea.net/en/MediaInfo/Download/Windows — the DLL is bundled with pymediainfo on Windows in most cases.
- **macOS**: `brew install mediainfo`
- **Linux**: `sudo apt install libmediainfo0v5` or `sudo yum install libmediainfo`

## Building Executables

### Windows

```bash
pip install pyinstaller
build_windows.bat
```

Output: `dist/MediaInsight/MediaInsight.exe`

### macOS

```bash
pip install pyinstaller
chmod +x build_macos.sh
./build_macos.sh
```

Output: `dist/MediaInsight.app`

## Usage

### Open a File

- **File → Open File** (Ctrl+O): Open a local media file
- **File → Open URL** (Ctrl+U): Open an HTTP/HTTPS stream

### Navigation

- **Analyzer** tab: Main analysis view (table/tree + detail + hex)
- **Player** tab: Video playback + MediaInfo display

### View Modes (TS files)

- **TS Packet View** (Ctrl+Shift+1): Every 188-byte packet as a row with PID/CC columns
- **TS PES View** (Ctrl+Shift+2): Only frame-start packets (PUSI=1)

### Interaction

- Click a packet/box → Detail panel shows parsed fields, Hex view shows raw bytes
- Click a NALU in detail → Hex view shows that NALU's bytes
- Click a field in detail → Hex view highlights the corresponding bytes

## Project Structure

```
src/media_analyzer/
├── __main__.py          # Entry point
├── app.py               # QApplication setup, theme application
├── core/
│   ├── models.py        # Data models (PacketInfo, NALUInfo, StreamInfo, etc.)
│   └── source.py        # Data sources (FileSource with mmap, HTTP streaming)
├── parsers/
│   ├── base.py          # BaseParser abstract class
│   ├── flv/             # FLV parser (tags, video/audio/script, Enhanced RTMP)
│   ├── ts/              # MPEG-TS parser (PES reassembly, frame detection)
│   ├── mp4/             # MP4/MOV parser (box hierarchy, sample tables)
│   ├── h264/            # H.264 bitstream (SPS/PPS, BitReader, exp-Golomb)
│   └── h265/            # H.265 bitstream (VPS/SPS/PPS)
├── ui/
│   ├── main_window.py   # Main window with nav bar, pages, menus
│   ├── packet_table/    # Table model + view (FLV/TS packet display)
│   ├── box_tree_view.py # MP4 box tree widget
│   ├── detail_panel.py  # Field tree display (all formats)
│   ├── hex_view.py      # Hex + ASCII split view
│   ├── player_page.py   # Player + MediaInfo page
│   └── themes.py        # Color theme definitions
└── workers/
    └── parse_worker.py  # Background parsing thread
```

## Developer

- **Author**: Abel
- **Email**: fylaotou@gmail.com

## License

MIT
