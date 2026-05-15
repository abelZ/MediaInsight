"""Player page — video player + MediaInfo display."""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QLabel, QPushButton,
    QSlider, QHBoxLayout, QStyle,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QFont
from typing import Optional
import os


class PlayerPage(QWidget):
    """
    Player page with:
    - Left: Video player (QMediaPlayer + QVideoWidget) with controls
    - Right: MediaInfo tree (parsed via pymediainfo)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_path: Optional[str] = None
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

        # Video display
        self._video_widget = QVideoWidget()
        self._video_widget.setMinimumSize(320, 240)
        player_layout.addWidget(self._video_widget, 1)

        # Player engine
        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self._video_widget)

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
        controls.addWidget(self._slider, 1)

        player_layout.addLayout(controls)
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

        # Connect player signals
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)

    def load_file(self, file_path: str) -> None:
        """Load a media file for playback and info display."""
        self._file_path = file_path

        # Load into player
        url = QUrl.fromLocalFile(file_path)
        self._player.setSource(url)

        # Load MediaInfo
        self._load_mediainfo(file_path)

    def _load_mediainfo(self, file_path: str) -> None:
        """Parse and display MediaInfo for the file."""
        self._info_tree.clear()

        try:
            from pymediainfo import MediaInfo
            media_info = MediaInfo.parse(file_path)
        except ImportError:
            item = QTreeWidgetItem(self._info_tree,
                ["Error", "pymediainfo not installed (pip install pymediainfo)"])
            return
        except Exception as e:
            item = QTreeWidgetItem(self._info_tree,
                ["Error", str(e)])
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
        item = QTreeWidgetItem(parent, [name, value])

    # --- Player controls ---

    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _stop(self):
        self._player.stop()

    def _seek(self, position: int):
        self._player.setPosition(position)

    def _on_position_changed(self, position: int):
        self._slider.setValue(position)
        self._update_time_label(position, self._player.duration())

    def _on_duration_changed(self, duration: int):
        self._slider.setRange(0, duration)
        self._update_time_label(self._player.position(), duration)

    def _on_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._btn_play.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self._btn_play.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

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

    def cleanup(self):
        """Stop playback and release resources."""
        self._player.stop()
        self._player.setSource(QUrl())
