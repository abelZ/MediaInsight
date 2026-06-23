# MediaInsight

A cross-platform, high-performance media analysis tool built with Python and PySide6 (Qt6).

Parses media containers at the **raw byte level** and displays packet/box/element structure with detailed field-level decoding. Audio waveform and spectrogram analysis via FFmpeg decoding.

![MediaInsight](resources/icons/app_icon_128.png)

## Features

### Supported Formats

| Format | View | Capabilities |
|--------|------|-------------|
| **FLV** | Table (packet list) | Tag parsing, H.264/H.265/AV1 NALU extraction, SPS/PPS decoding, AMF0 script data, Enhanced RTMP |
| **MPEG-TS** | Table (packet/PES view) | PAT/PMT parsing, PES reassembly, frame type detection (I/P/B), H.264/H.265 Annex B NALU parsing |
| **MP4/MOV** | Tree (box hierarchy) | Full box parsing (60+ box types), mdat chunk/sample listing, avcC/hvcC/esds codec config decoding |
| **WebM/MKV** | Tree (element hierarchy) | Full EBML element parsing, Cluster/Block decoding, VP8/VP9/AV1/H.264/H.265/Opus/Vorbis/AAC support |
| **WAV** | Tree (chunk hierarchy) | RIFF chunk parsing, fmt/data/LIST/bext/fact/cue chunks, format details, byte-level hex highlighting |
| **AAC (ADTS)** | Tree (frame list) | ADTS sync detection, per-frame header decode (profile/AOT, sample rate, channels, frame length, CRC), ID3v2 prefix skip, byte-level field highlighting |
| **RTMP/RTMPS** | Dual tab (RTMP packets + FLV tags) | Pure Python protocol implementation, handshake capture, live stream analysis, Save As FLV |
| **HLS/M3U8** | Segment list + analysis | M3U8 parsing, segment download, per-segment TS/fMP4 analysis, raw M3U8 text view |
| **HTTP/HTTPS** | Auto-detect (FLV/TS/MP4) | Progressive download with progress display |

### UI Pages

| Tab | Description |
|-----|-------------|
| **Analyzer** | Main analysis view — table/tree + detail panel + hex view |
| **Bitrate** | Per-second video/audio bitrate chart with IDR markers |
| **Timestamp** | Frame number vs. timestamp progression (detect jumps/drift) |
| **GOP** | Per-frame size chart with GOP boundaries, I/P/B color coding, GOP statistics |
| **Audio** | PCM waveform display + spectrogram (log/linear scale), multi-channel, playback with cursor sync |
| **Player** | Built-in VLC-based media player + MediaInfo metadata |
| **Log** | Real-time application log with level filtering and auto-scroll |

### UI Features

- **Hex View**: Split hex + ASCII display with byte-level highlighting
- **Detail Panel**: Tree-based field display with collapsible groups and value interpretation
- **Bitrate Chart**: Per-second video/audio bitrate with IDR markers, zoomable X-axis
- **Timestamp Chart**: Frame-by-frame timestamp progression, audio/video toggle
- **Audio Page**: Multi-channel PCM waveform (peak envelope) + spectrogram (log/linear freq scale), Adobe Audition-style colormap, sounddevice playback with cursor sync, channel mute/toggle, zoom & seek
- **Player Page**: Built-in VLC player (RTMP/HLS/HTTP/local) + MediaInfo metadata
- **Log Page**: Color-coded real-time log, level filter, clear, auto-scroll
- **Theme System**: 8 built-in color themes (Catppuccin, One Dark Pro, Dracula, Tokyo Night, Monokai Pro, GitHub Dark, Nord, Solarized Dark)
- **Background Parsing**: Non-blocking file loading with progress indication
- **Filter System**: Filter by type (Video/Audio/Script), IDR frames, SEI presence

### RTMP Live Stream Analysis

- Pure Python socket implementation (no librtmp dependency)
- Captures full handshake (C0/C1/S0/S1/S2/C2)
- Real-time RTMP protocol packet view (commands, control messages, media)
- Auto-extracted FLV tags with full codec/NALU parsing
- Control bar: Pause/Resume, Disconnect, live statistics with recording indicator
- Save As FLV: export captured stream as playable FLV file
- Bitrate chart with real-time dynamic updates
- Supports RTMPS (RTMP over TLS)

### HLS (M3U8) Analysis

- SBR (single bitrate) M3U8 playlist parsing
- Segment list with duration, filename, download status indicators
- Click-to-download: per-segment TS/fMP4 analysis using existing parsers
- Raw M3U8 text view (bottom tab)
- M3U8 metadata display: version, target duration, media sequence, playlist type
- Supports EXT-X-BYTERANGE, EXT-X-DISCONTINUITY, EXT-X-PROGRAM-DATE-TIME

### WebM/MKV (Matroska) Analysis

- Full EBML element hierarchy displayed as expandable tree
- All standard Matroska elements recognized (90+ element IDs)
- Cluster/SimpleBlock/BlockGroup parsing with absolute timestamp computation
- Video codecs: VP8, VP9, AV1, H.264, H.265, MPEG-2, Theora
- Audio codecs: Opus, Vorbis, AAC, AC-3, DTS, FLAC, MP3
- Track info: codec, resolution, sample rate, channels, default duration
- Keyframe detection from SimpleBlock flags
- Detail panel shows element definition, value, and contextual interpretation

### AAC (ADTS) Analysis

- Raw `.aac` ADTS streams parsed frame-by-frame
- Optional ID3v2 prefix automatically skipped
- 12-bit sync word detection with multi-frame chain validation (avoids false positives in foreign containers)
- Per-frame header decoding: MPEG version, profile (AOT — Main/LC/SSR/LTP), sample rate (16-entry index table), channel configuration, frame length, buffer fullness, raw-data-block count, CRC presence
- Synthetic HEADER row summarises detected stream parameters (`MPEG-x AAC-LC, 44100 Hz, Stereo`, etc.)
- Per-frame timestamp computed from cumulative sample count (1024 × (num_raw_blocks + 1) samples per frame)
- Byte-level hex highlight for every header field; HLS .aac segments also recognised
- Audio page decodes via PyAV with manual ADTS fallback when the demuxer/decoder disagree on channel layout

### Bitrate Analysis

- Frame-level bitrate calculation (not packet-level)
- FLV/RTMP/WebM/MKV: per-frame bitrate with DTS timestamps
- MPEG-TS: PES-level bitrate from PTS/DTS
- MP4: stts-derived per-sample DTS with stsz sample sizes
- IDR frame positions marked on chart
- Zoomable X-axis (scroll wheel, drag, double-click to reset)
- Fixed Y-axis tick intervals (500, 1000, 2000 kbps etc.)
- RTMP live mode: auto-refresh every 2s

### Timestamp Analysis

- Frame number (X) vs. timestamp (Y) chart
- Useful for detecting: timestamp jumps, resets, non-monotonic DTS, audio/video sync drift
- Toggle between Video / Audio / Both
- IDR positions marked as red scatter points
- Zoomable, double-click to reset
- RTMP live mode support

### GOP Analysis

- Per-frame size chart with I/P/B color coding (I red, P blue, B green)
- GOP boundaries derived from IDR positions
- GOP statistics: count, min/avg/max GOP length, total frames
- Supports FLV / RTMP / TS / MP4 / WebM/MKV via shared frame extraction
- Filter to video frames only; horizontal scroll for long streams
- RTMP live mode: chart updates as new GOPs arrive

### Analysis Capabilities

- H.264 SPS/PPS full bitstream parsing (profile, level, resolution, chroma, etc.)
- H.265 VPS/SPS/PPS parsing
- MPEG-2 picture type detection
- Frame type identification: I/P/B via actual slice header parsing (not just random_access_indicator)
- MP4 sample table cross-referencing (stco + stsc + stsz + stss)
- AAC AudioSpecificConfig decoding (profile, sample rate, channels)
- AAC ADTS frame header decoding (sync word, AOT, sample rate index, channel config, frame length, CRC)
- EBML VInt parsing, element ID/size decoding, container recursion

## Installation

### Requirements

- Python 3.10+
- PySide6
- numpy (audio waveform/spectrogram computation)
- sounddevice (audio playback; optional — waveform display still works without it)
- pymediainfo (for Player page MediaInfo display)
- python-vlc (for Player page video playback; optional — requires VLC installed or bundled)
- FFmpeg (for Audio page decoding; must be on PATH or bundled)

### From Source

```bash
git clone https://github.com/AbelPPhil662/MediaInsight.git
cd MediaInsight
pip install -r requirements.txt
python run.py
```

### Dependencies

```bash
pip install PySide6 pymediainfo python-vlc numpy sounddevice
```

#### FFmpeg

The Audio page uses FFmpeg to decode audio from any container format to PCM:

- **Windows**: Download from https://ffmpeg.org/download.html — add `ffmpeg.exe` directory to PATH
- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt install ffmpeg` or `sudo yum install ffmpeg`

If FFmpeg is not available, all analysis features still work — only the Audio tab's waveform/spectrogram/playback is disabled.

#### VLC Player

The built-in player uses [python-vlc](https://pypi.org/project/python-vlc/) which requires VLC libraries:

- **Windows**: Install [VLC](https://www.videolan.org/vlc/), or copy `libvlc.dll` + `libvlccore.dll` + `plugins/` to `vendor/vlc/win64/`
- **macOS**: Install [VLC.app](https://www.videolan.org/vlc/download-macosx.html), or copy dylibs + plugins to `vendor/vlc/macos/lib/` and `vendor/vlc/macos/plugins/`

If VLC is not available, all analysis features still work — only the Player tab is disabled.

#### MediaInfo library

`pymediainfo` requires the MediaInfo library:

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

VLC libraries from `vendor/vlc/win64/` are automatically bundled if present.

#### VLC plugin cache (first-Play startup)

`libvlc` scans every plugin DLL on first call to `vlc.Instance(...)` to discover demuxers, decoders, etc. With ~360 plugins (~150 MB), a cold scan takes **~4 s** — the user sees a multi-second freeze the first time they click Play. Subsequent Play clicks within the same process are instant because the plugins are already loaded.

VLC supports a precomputed cache file (`plugins/plugins.dat`) that skips this scan. We ship one with the project to make first Play near-instant (~70 ms). Two things to know:

1. **`plugins.dat` validates each entry by `(relative path, size, mtime)`.** Paths are relative to `plugins/`, so the cache is location-independent — zip → unzip → move → rename all work, **as long as mtimes are preserved**. Standard ZIP (Explorer, 7-Zip, `tar -a`) preserves them; plain `cp -r` and `git clone` do not.

2. **PyInstaller stamps fresh mtimes on every copied DLL during `COLLECT`.** This invalidates the pre-built cache silently — VLC sees the mismatch, treats every entry as stale, and falls back to a full scan. So the bundled `dist/` build needs `plugins.dat` regenerated **after** PyInstaller finishes copying. `build_windows.bat` does this automatically by invoking `vendor\vlc\win64\vlc-cache-gen.exe` against `dist\MediaInsight\_internal\plugins\` after the PyInstaller step.

If the build script reports `WARNING: plugin cache rebuild failed`, the bundle still works — just expect a ~4 s freeze on first Play.

##### Regenerating `plugins.dat` for a new VLC version

If you upgrade `vendor/vlc/win64/`, regenerate the cache once:

```cmd
cd vendor\vlc\win64
vlc-cache-gen.exe "%CD%\plugins"
```

Important: `vlc-cache-gen.exe` only resolves plugins when given an **absolute Windows path**. A relative argument like `plugins` produces a 26-byte empty cache (header only, zero plugins) — which is silently accepted by VLC and gives no speedup at all.

`vlc-cache-gen.exe` is shipped at `vendor/vlc/win64/vlc-cache-gen.exe` for the build script to use; it is a build-time tool only and is **not** included in the runtime distribution (the spec only bundles `libvlc.dll`, `libvlccore.dll`, and the `plugins/` folder).

### macOS

```bash
pip install pyinstaller
chmod +x build_macos.sh
./build_macos.sh
```

Output: `dist/MediaInsight.app`

## Usage

### Open a File

- **File → Open File** (Ctrl+O): Open a local media file (FLV, TS, MP4, MOV, WebM, MKV, WAV, AAC)
- **File → Open URL** (Ctrl+U): Open HTTP/HTTPS/RTMP/RTMPS/HLS(M3U8) stream

### Navigation

| Tab | Shortcut | Content |
|-----|----------|---------|
| Analyzer | — | Main analysis view (table/tree + detail + hex) |
| Bitrate | — | Per-second bitrate chart with IDR markers |
| Timestamp | — | Frame sequence vs timestamp chart |
| GOP | — | Per-frame size chart with GOP boundaries and statistics |
| Audio | — | PCM waveform + spectrogram, multi-channel playback |
| Player | — | Video playback + MediaInfo display |
| Log | — | Real-time application log |

### View Modes (TS files)

- **TS Packet View** (Ctrl+Shift+1): Every 188-byte packet as a row with PID/CC columns
- **TS PES View** (Ctrl+Shift+2): Only frame-start packets (PUSI=1)

### RTMP Stream

1. File → Open URL → enter `rtmp://` or `rtmps://` address
2. Control bar appears with Pause/Resume/Disconnect and live statistics
3. RTMP Packets tab: protocol-level view (handshake, commands, media chunks)
4. FLV Tags tab: extracted audio/video/script tags with full parsing
5. Switch to Bitrate/Timestamp tab for real-time monitoring
6. File → Save As to export captured stream as FLV file

### HLS Stream

1. File → Open URL → enter `.m3u8` URL
2. Left panel shows M3U8 info bar + segment list
3. Click "M3U8" tab at bottom to view raw M3U8 content
4. Click any segment → downloads and parses with TS/fMP4 analyzer
5. Right panel shows full packet analysis (same as local file)

### Interaction

- Click a packet/box/element → Detail panel shows parsed fields, Hex view shows raw bytes
- Click a NALU in detail → Hex view highlights that NALU's bytes
- Click a field in detail → Hex view highlights the corresponding bytes
- Bitrate/Timestamp chart: scroll wheel to zoom, double-click to reset

## Project Structure

```
src/media_analyzer/
├── __main__.py          # Entry point
├── app.py               # QApplication setup, theme application
├── core/
│   ├── models.py        # Data models (PacketInfo, NALUInfo, StreamInfo, etc.)
│   ├── source.py        # Data sources (FileSource with mmap, HTTP streaming, BufferSource)
│   ├── logging_config.py # Logging setup (Qt signal handler for Log view)
│   ├── rtmp/            # RTMP protocol (handshake, chunk, AMF0, client, FLV writer)
│   └── hls/             # HLS support (M3U8 parser)
├── parsers/
│   ├── base.py          # BaseParser abstract class
│   ├── flv/             # FLV parser (tags, video/audio/script, Enhanced RTMP)
│   ├── ts/              # MPEG-TS parser (PES reassembly, frame detection)
│   ├── mp4/             # MP4/MOV parser (box hierarchy, sample tables)
│   ├── ebml/            # WebM/MKV parser (EBML elements, Cluster/Block decoding)
│   ├── wav/             # WAV parser (RIFF chunks, fmt/data/LIST parsing)
│   ├── aac/             # AAC ADTS parser (sync detection, per-frame header decode)
│   ├── h264/            # H.264 bitstream (SPS/PPS, BitReader, exp-Golomb)
│   └── h265/            # H.265 bitstream (VPS/SPS/PPS)
├── ui/
│   ├── main_window.py   # Main window with nav bar, pages, menus
│   ├── packet_table/    # Table model + view (FLV/TS/RTMP packet display)
│   ├── box_tree_view.py # MP4/WebM box/element tree widget
│   ├── rtmp_view.py     # RTMP dual-tab view (protocol + FLV tags)
│   ├── rtmp_control_bar.py  # RTMP session control bar
│   ├── hls_view.py      # HLS segment list + raw M3U8 text view
│   ├── bitrate_page.py  # Bitrate analysis chart (QtCharts)
│   ├── timestamp_page.py # Timestamp progression chart (QtCharts)
│   ├── gop_page.py      # GOP page (frame-size chart + GOP statistics)
│   ├── audio_page.py    # Audio waveform + spectrogram (PyAV decode, sounddevice playback)
│   ├── log_page.py      # Real-time log view with filtering
│   ├── detail_panel.py  # Field tree display (all formats)
│   ├── hex_view.py      # Hex + ASCII split view
│   ├── player_page.py   # VLC Player + MediaInfo page
│   └── themes.py        # Color theme definitions
└── workers/
    ├── parse_worker.py  # Background parsing thread (FLV/TS/MP4/WebM/WAV/AAC)
    ├── rtmp_worker.py   # RTMP session worker thread
    ├── hls_worker.py    # HLS segment download + parse worker (TS / fMP4 / AAC)
    └── audio_decode_worker.py  # PyAV audio decode to PCM (background thread)
```

## Developer

- **Author**: Abel
- **Email**: fylaotou@gmail.com

## License

MIT
