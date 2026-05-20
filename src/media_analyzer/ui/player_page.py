"""Player page — video player (VLC) + MediaInfo display."""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QLabel, QPushButton,
    QSlider, QStyle, QComboBox, QGroupBox, QFormLayout,
)
from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtGui import QFont
from typing import Optional, List
import sys
import os

try:
    # Try to use bundled VLC first (vendor/vlc/<platform>/ directory)
    import os as _os
    _project_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

    # Platform-specific subdirectory
    if sys.platform == "win32":
        _vlc_platform = "win64"
    elif sys.platform == "darwin":
        _vlc_platform = "macos"
    else:
        _vlc_platform = "linux"

    _vlc_vendor_path = _os.path.join(_project_root, "vendor", "vlc", _vlc_platform)

    # Frozen (PyInstaller) path — VLC is bundled differently
    if hasattr(sys, '_MEIPASS'):
        _meipass = sys._MEIPASS
        # Check if VLC libs are at the bundle root (Windows: libvlc.dll next to exe)
        if sys.platform == "win32":
            _frozen_libvlc = _os.path.join(_meipass, "libvlc.dll")
            if _os.path.isfile(_frozen_libvlc):
                _os.environ['PYTHON_VLC_LIB_PATH'] = _frozen_libvlc
                _plugins = _os.path.join(_meipass, "plugins")
                if _os.path.isdir(_plugins):
                    _os.environ['VLC_PLUGIN_PATH'] = _plugins
                _os.add_dll_directory(_meipass)
        elif sys.platform == "darwin":
            _frozen_libvlc = _os.path.join(_meipass, "vlc", "lib", "libvlc.dylib")
            if _os.path.isfile(_frozen_libvlc):
                _os.environ['PYTHON_VLC_LIB_PATH'] = _frozen_libvlc
                _plugins = _os.path.join(_meipass, "vlc", "plugins")
                if _os.path.isdir(_plugins):
                    _os.environ['VLC_PLUGIN_PATH'] = _plugins
    elif _os.path.isdir(_vlc_vendor_path):
        # Development mode: use vendor/vlc/<platform>/
        if sys.platform == "win32":
            _libvlc_path = _os.path.join(_vlc_vendor_path, "libvlc.dll")
        elif sys.platform == "darwin":
            _libvlc_path = _os.path.join(_vlc_vendor_path, "lib", "libvlc.dylib")
        else:
            _libvlc_path = _os.path.join(_vlc_vendor_path, "lib", "libvlc.so")

        # Set env vars for python-vlc to find bundled libraries
        if _os.path.isfile(_libvlc_path):
            _os.environ['PYTHON_VLC_LIB_PATH'] = _libvlc_path
            _os.environ['PYTHON_VLC_MODULE_PATH'] = _os.path.join(
                _vlc_vendor_path, "plugins")
            # Windows: add to DLL search path
            if sys.platform == "win32":
                _os.add_dll_directory(_vlc_vendor_path)

    import vlc
    HAS_VLC = True
except (ImportError, FileNotFoundError, OSError):
    HAS_VLC = False


class _MediaInfoWorker(QThread):
    """Background thread for pymediainfo parsing (avoids blocking UI)."""
    finished = Signal(object)  # Emits MediaInfo result or None

    def __init__(self, file_path: str, parent=None):
        super().__init__(parent)
        self._file_path = file_path

    def run(self):
        try:
            from pymediainfo import MediaInfo
            media_info = MediaInfo.parse(self._file_path)
            self.finished.emit(media_info)
        except Exception:
            self.finished.emit(None)


class PlayerPage(QWidget):
    """
    Player page with:
    - Left: Video player (VLC via python-vlc) with controls
    - Right: MediaInfo tree (parsed via pymediainfo)

    VLC supports RTMP, RTMPS, HLS, HTTP, and all local formats natively.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_path: Optional[str] = None
        self._loaded = False
        self._is_playing = False
        self._vlc_ready = False  # VLC initialized lazily on first play
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Left: Player ---
        player_widget = QWidget()
        player_layout = QVBoxLayout(player_widget)
        player_layout.setContentsMargins(4, 4, 4, 4)
        player_layout.setSpacing(4)

        # Video display frame (VLC renders into this widget)
        self._video_frame = QWidget()
        self._video_frame.setMinimumSize(320, 240)
        self._video_frame.setStyleSheet("background-color: black;")
        self._video_frame.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        player_layout.addWidget(self._video_frame, 1)

        # Controls bar
        controls = QHBoxLayout()
        controls.setSpacing(6)

        self._btn_play = QPushButton()
        self._btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self._btn_play.setFixedSize(32, 32)
        self._btn_play.clicked.connect(self._toggle_play)
        controls.addWidget(self._btn_play)

        self._btn_stop = QPushButton()
        self._btn_stop.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self._btn_stop.setFixedSize(32, 32)
        self._btn_stop.clicked.connect(self._stop)
        controls.addWidget(self._btn_stop)

        self._time_label = QLabel("00:00 / 00:00")
        self._time_label.setFixedWidth(120)
        controls.addWidget(self._time_label)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.sliderMoved.connect(self._seek)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        controls.addWidget(self._slider, 1)

        player_layout.addLayout(controls)

        # Settings bar (below controls)
        settings = QHBoxLayout()
        settings.setSpacing(12)

        # Decode mode
        decode_label = QLabel("Decode:")
        decode_label.setStyleSheet("font-size: 11px;")
        settings.addWidget(decode_label)
        self._decode_combo = QComboBox()
        self._decode_combo.addItems(["Auto (Hardware)", "Software (avcodec)"])
        self._decode_combo.setFixedWidth(150)
        self._decode_combo.setStyleSheet("font-size: 11px;")
        self._decode_combo.setMaxVisibleItems(5)
        self._decode_combo.currentIndexChanged.connect(self._on_decode_changed)
        settings.addWidget(self._decode_combo)

        settings.addSpacing(16)

        # Audio track
        audio_label = QLabel("Audio:")
        audio_label.setStyleSheet("font-size: 11px;")
        settings.addWidget(audio_label)
        self._audio_combo = QComboBox()
        self._audio_combo.setFixedWidth(180)
        self._audio_combo.setStyleSheet("font-size: 11px;")
        self._audio_combo.setMaxVisibleItems(10)
        self._audio_combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._audio_combo.currentIndexChanged.connect(self._on_audio_track_changed)
        settings.addWidget(self._audio_combo)

        settings.addSpacing(16)

        # Subtitle track
        sub_label = QLabel("Subtitle:")
        sub_label.setStyleSheet("font-size: 11px;")
        settings.addWidget(sub_label)
        self._sub_combo = QComboBox()
        self._sub_combo.setFixedWidth(180)
        self._sub_combo.setStyleSheet("font-size: 11px;")
        self._sub_combo.setMaxVisibleItems(10)
        self._sub_combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._sub_combo.currentIndexChanged.connect(self._on_subtitle_changed)
        settings.addWidget(self._sub_combo)

        settings.addSpacing(16)

        # Volume control
        vol_label = QLabel("Vol:")
        vol_label.setStyleSheet("font-size: 11px;")
        settings.addWidget(vol_label)
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(100)
        self._volume_slider.setFixedWidth(80)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        settings.addWidget(self._volume_slider)
        self._volume_label = QLabel("100%")
        self._volume_label.setFixedWidth(35)
        self._volume_label.setStyleSheet("font-size: 11px;")
        settings.addWidget(self._volume_label)

        settings.addStretch(1)

        player_layout.addLayout(settings)
        splitter.addWidget(player_widget)

        # --- Right: MediaInfo ---
        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(4, 4, 4, 4)
        info_layout.setSpacing(4)

        info_title = QLabel("Media Info")
        info_title.setStyleSheet("font-weight: bold; font-size: 12px; padding: 4px;")
        info_layout.addWidget(info_title)

        self._info_tree = QTreeWidget()
        self._info_tree.setHeaderLabels(["Property", "Value"])
        self._info_tree.setColumnWidth(0, 200)
        self._info_tree.setAlternatingRowColors(True)
        self._info_tree.setRootIsDecorated(True)
        info_layout.addWidget(self._info_tree)

        splitter.addWidget(info_widget)
        splitter.setSizes([600, 400])

        layout.addWidget(splitter)

        # Poll timer for position/duration updates
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)
        self._poll_timer.timeout.connect(self._poll_status)

        # Slider drag state
        self._slider_dragging = False

    def _setup_vlc(self):
        """Initialize VLC player instance (called lazily on first play)."""
        if not HAS_VLC or self._vlc_ready:
            return

        # Build VLC arguments based on settings
        args = ["--quiet", "--no-video-title-show", "--no-stats"]

        # Decode mode
        if self._decode_combo.currentIndex() == 1:
            # Software decode: disable hardware acceleration
            args.append("--avcodec-hw=none")

        # Create VLC instance
        self._vlc_instance = vlc.Instance(*args)
        self._vlc_player = self._vlc_instance.media_player_new()

        # Bind VLC output to the video frame widget
        if sys.platform == "win32":
            self._vlc_player.set_hwnd(int(self._video_frame.winId()))
        elif sys.platform == "darwin":
            self._vlc_player.set_nsobject(int(self._video_frame.winId()))
        else:
            self._vlc_player.set_xwindow(int(self._video_frame.winId()))

        # Set volume from slider
        self._vlc_player.audio_set_volume(self._volume_slider.value())
        self._vlc_ready = True

    def load_file(self, file_path: str, source=None, mediainfo_path: Optional[str] = None) -> None:
        """Load a media file or URL for playback and info display.

        Args:
            file_path: Local path or URL (RTMP/RTMPS/HLS/HTTP all supported by VLC)
            source: Unused (kept for API compat)
            mediainfo_path: Local file path for MediaInfo parsing (temp file for streams)
        """
        # Skip if already loaded the same file
        if file_path == self._file_path and self._loaded:
            return

        # Stop current playback if any
        if self._vlc_ready and self._is_playing:
            self._vlc_player.stop()
            self._is_playing = False
            self._poll_timer.stop()
            self._btn_play.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

        # Clear previous media so next play uses new file
        if self._vlc_ready:
            self._vlc_player.set_media(None)

        self._file_path = file_path
        self._loaded = True

        # Load MediaInfo: use mediainfo_path (local temp file) if provided
        mi_path = mediainfo_path or file_path
        self._load_mediainfo_async(mi_path)

    def _load_mediainfo_async(self, file_path: str) -> None:
        """Parse MediaInfo in background thread."""
        self._info_tree.clear()
        QTreeWidgetItem(self._info_tree, ["Loading...", ""])

        # For live streams, MediaInfo can't parse — show placeholder
        if (file_path.startswith("rtmp://") or file_path.startswith("rtmps://")
                or ".m3u8" in file_path):
            self._info_tree.clear()
            QTreeWidgetItem(self._info_tree,
                ["Note", "MediaInfo not available for live streams"])
            return

        self._mediainfo_worker = _MediaInfoWorker(file_path, self)
        self._mediainfo_worker.finished.connect(self._on_mediainfo_ready)
        self._mediainfo_worker.start()

    def _on_mediainfo_ready(self, media_info) -> None:
        """Handle MediaInfo result from background thread."""
        self._info_tree.clear()

        if media_info is None:
            QTreeWidgetItem(self._info_tree,
                ["Error", "Failed to parse media info"])
            return

        # Display each track
        for track in media_info.tracks:
            track_type = track.track_type
            track_data = track.to_data()

            # Create track node
            summary = self._get_track_summary(track)
            track_item = QTreeWidgetItem(self._info_tree,
                [track_type, summary])
            track_item.setExpanded(track_type in ("General", "Video", "Audio"))

            # Add fields grouped by importance
            important_fields = self._get_important_fields(track_type)

            # Important fields first
            for field_name in important_fields:
                value = track_data.get(field_name)
                if value is not None and value != "":
                    display_name = field_name.replace("_", " ").title()
                    self._add_info_field(track_item, display_name, str(value))

            # Other fields (collapsed)
            other_item = QTreeWidgetItem(track_item, ["Other Properties", ""])
            other_item.setExpanded(False)
            for key, value in sorted(track_data.items()):
                if key in important_fields or key == "track_type":
                    continue
                if value is None or value == "" or key.startswith("other_"):
                    continue
                display_name = key.replace("_", " ").title()
                self._add_info_field(other_item, display_name, str(value))

    @staticmethod
    def _get_track_summary(track) -> str:
        """Get a one-line summary for a track."""
        t = track.track_type
        if t == "General":
            return f"{track.format or ''} | {track.other_duration[0] if track.other_duration else ''} | {track.other_file_size[0] if track.other_file_size else ''}"
        elif t == "Video":
            return f"{track.format or ''} {track.width}x{track.height} @ {track.frame_rate}fps"
        elif t == "Audio":
            ch = track.other_channel_s[0] if track.other_channel_s else f"{track.channel_s}ch"
            return f"{track.format or ''} {track.other_sampling_rate[0] if track.other_sampling_rate else ''} {ch}"
        return ""

    @staticmethod
    def _get_important_fields(track_type: str) -> list:
        """Get ordered list of important fields for each track type."""
        if track_type == "General":
            return [
                "format", "format_profile", "codec_id", "file_size",
                "duration", "overall_bit_rate", "frame_rate", "frame_count",
                "writing_application", "file_creation_date",
            ]
        elif track_type == "Video":
            return [
                "format", "format_profile", "format_settings", "codec_id",
                "duration", "bit_rate", "width", "height",
                "display_aspect_ratio", "frame_rate_mode", "frame_rate",
                "frame_count", "color_space", "chroma_subsampling",
                "bit_depth", "scan_type", "stream_size",
            ]
        elif track_type == "Audio":
            return [
                "format", "format_additionalfeatures", "codec_id",
                "duration", "bit_rate_mode", "bit_rate",
                "channel_s", "channel_layout", "sampling_rate",
                "frame_count", "compression_mode", "stream_size",
            ]
        return ["format", "codec_id", "duration", "bit_rate"]

    def _add_info_field(self, parent: QTreeWidgetItem, name: str, value: str) -> None:
        """Add a field to the info tree."""
        QTreeWidgetItem(parent, [name, value])

    # --- Player controls ---

    def _toggle_play(self):
        if not HAS_VLC:
            return

        # Lazy init VLC on first play (avoids blocking UI on tab switch)
        if not self._vlc_ready:
            self._setup_vlc()
            if not self._vlc_ready:
                return

        if self._is_playing:
            self._vlc_player.pause()
            self._is_playing = False
            self._poll_timer.stop()
            self._btn_play.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        else:
            # If no media set yet, set it now
            if self._vlc_player.get_media() is None and self._file_path:
                media = self._vlc_instance.media_new(self._file_path)
                self._vlc_player.set_media(media)

            self._vlc_player.play()
            self._is_playing = True
            self._poll_timer.start()
            self._btn_play.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            # Populate track combos after a short delay (tracks available after play starts)
            QTimer.singleShot(1000, self._populate_tracks)

    def _stop(self):
        if not HAS_VLC or not self._vlc_ready:
            return
        self._vlc_player.stop()
        self._is_playing = False
        self._poll_timer.stop()
        self._slider.setValue(0)
        self._update_time_label(0, 0)
        self._btn_play.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def _seek(self, position: int):
        if not HAS_VLC or not self._vlc_ready:
            return
        self._vlc_player.set_time(position)

    def _on_slider_pressed(self):
        self._slider_dragging = True

    def _on_slider_released(self):
        self._slider_dragging = False
        self._seek(self._slider.value())

    def _poll_status(self):
        """Poll VLC player for position and duration updates."""
        if not HAS_VLC or not self._vlc_ready or not self._is_playing:
            return

        state = self._vlc_player.get_state()

        # Check if playback ended
        if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error):
            self._is_playing = False
            self._poll_timer.stop()
            self._btn_play.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            return

        pos = self._vlc_player.get_time()  # ms (-1 if not available)
        dur = self._vlc_player.get_length()  # ms (-1 if not available)

        if pos < 0:
            pos = 0
        if dur < 0:
            dur = 0

        # Update slider (only if user is not dragging)
        if not self._slider_dragging:
            if dur > 0:
                self._slider.setRange(0, dur)
            self._slider.setValue(pos)

        self._update_time_label(pos, dur)

    def _update_time_label(self, pos_ms: int, dur_ms: int):
        pos_str = self._format_time(pos_ms)
        dur_str = self._format_time(dur_ms)
        self._time_label.setText(f"{pos_str} / {dur_str}")

    @staticmethod
    def _format_time(ms: int) -> str:
        if ms <= 0:
            return "00:00"
        secs = ms // 1000
        mins = secs // 60
        secs = secs % 60
        if mins >= 60:
            hours = mins // 60
            mins = mins % 60
            return f"{hours}:{mins:02d}:{secs:02d}"
        return f"{mins:02d}:{secs:02d}"

    # --- Settings handlers ---

    def _on_decode_changed(self, index: int):
        """Handle decode mode change. Requires restart of VLC instance."""
        if not self._vlc_ready:
            return  # Will use new setting on next _setup_vlc() call
        # Need to recreate VLC instance with new settings
        was_playing = self._is_playing
        file_path = self._file_path
        self._stop()
        self._vlc_player.release()
        self._vlc_instance.release()
        self._vlc_ready = False
        # Re-init will pick up new decode setting
        if was_playing and file_path:
            self._setup_vlc()
            media = self._vlc_instance.media_new(file_path)
            self._vlc_player.set_media(media)
            self._vlc_player.play()
            self._is_playing = True
            self._poll_timer.start()
            self._btn_play.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))

    def _on_volume_changed(self, value: int):
        """Handle volume slider change."""
        self._volume_label.setText(f"{value}%")
        if self._vlc_ready:
            self._vlc_player.audio_set_volume(value)

    def _on_audio_track_changed(self, index: int):
        """Handle audio track selection."""
        if not self._vlc_ready or not self._is_playing:
            return
        # Index 0 = "Disabled", subsequent = track IDs
        track_ids = self._get_audio_track_ids()
        if index == 0:
            self._vlc_player.audio_set_track(-1)
        elif index - 1 < len(track_ids):
            self._vlc_player.audio_set_track(track_ids[index - 1])

    def _on_subtitle_changed(self, index: int):
        """Handle subtitle track selection."""
        if not self._vlc_ready or not self._is_playing:
            return
        # Index 0 = "Disabled", subsequent = track IDs
        track_ids = self._get_subtitle_track_ids()
        if index == 0:
            self._vlc_player.video_set_spu(-1)
        elif index - 1 < len(track_ids):
            self._vlc_player.video_set_spu(track_ids[index - 1])

    def _populate_tracks(self):
        """Populate audio and subtitle track combos after playback starts."""
        if not self._vlc_ready:
            return

        # Audio tracks
        self._audio_combo.blockSignals(True)
        self._audio_combo.clear()
        self._audio_combo.addItem("Disabled")
        try:
            tracks = self._vlc_player.audio_get_track_description()
            if tracks:
                for track_id, name in tracks:
                    if track_id == -1:
                        continue  # Skip "Disable" entry from VLC
                    label = name.decode("utf-8", errors="replace") if isinstance(name, bytes) else str(name)
                    self._audio_combo.addItem(f"#{track_id}: {label}")
            # Select current track
            current = self._vlc_player.audio_get_track()
            idx = self._find_track_combo_index(self._audio_combo, current)
            if idx >= 0:
                self._audio_combo.setCurrentIndex(idx)
        except Exception:
            pass
        self._audio_combo.blockSignals(False)

        # Subtitle tracks
        self._sub_combo.blockSignals(True)
        self._sub_combo.clear()
        self._sub_combo.addItem("Disabled")
        try:
            tracks = self._vlc_player.video_get_spu_description()
            if tracks:
                for track_id, name in tracks:
                    if track_id == -1:
                        continue
                    label = name.decode("utf-8", errors="replace") if isinstance(name, bytes) else str(name)
                    self._sub_combo.addItem(f"#{track_id}: {label}")
            current = self._vlc_player.video_get_spu()
            idx = self._find_track_combo_index(self._sub_combo, current)
            if idx >= 0:
                self._sub_combo.setCurrentIndex(idx)
        except Exception:
            pass
        self._sub_combo.blockSignals(False)

    def _get_audio_track_ids(self) -> List[int]:
        """Get list of audio track IDs (excluding -1)."""
        ids = []
        try:
            tracks = self._vlc_player.audio_get_track_description()
            if tracks:
                for track_id, _ in tracks:
                    if track_id != -1:
                        ids.append(track_id)
        except Exception:
            pass
        return ids

    def _get_subtitle_track_ids(self) -> List[int]:
        """Get list of subtitle track IDs (excluding -1)."""
        ids = []
        try:
            tracks = self._vlc_player.video_get_spu_description()
            if tracks:
                for track_id, _ in tracks:
                    if track_id != -1:
                        ids.append(track_id)
        except Exception:
            pass
        return ids

    @staticmethod
    def _find_track_combo_index(combo: QComboBox, track_id: int) -> int:
        """Find combo box index for a given track ID."""
        for i in range(combo.count()):
            text = combo.itemText(i)
            if text.startswith(f"#{track_id}:"):
                return i
        return 0  # Default to "Disabled"

    def stop_and_reset(self):
        """Stop playback and reset state for new file (keeps VLC alive)."""
        self._stop()
        self._file_path = None
        self._loaded = False
        # Clear media info tree
        self._info_tree.clear()

    def cleanup(self):
        """Stop playback and release resources (for app exit)."""
        self._poll_timer.stop()
        if HAS_VLC and self._vlc_ready:
            self._vlc_player.stop()
            self._vlc_player.release()
            self._vlc_instance.release()
            self._vlc_ready = False
