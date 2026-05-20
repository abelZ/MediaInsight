"""Timestamp chart page — shows frame number vs timestamp progression."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QButtonGroup, QLabel,
)
from PySide6.QtCharts import (
    QChart, QChartView, QLineSeries, QScatterSeries, QValueAxis,
)
from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import QColor, QPen, QPainter
from typing import List, Dict, Optional
from collections import defaultdict

from media_analyzer.core.models import PacketInfo, TagType, FrameType, StreamInfo


class TimestampPage(QWidget):
    """
    Timestamp progression chart view.

    Shows frame sequence number (X) vs timestamp in ms (Y).
    Useful for detecting:
    - Timestamp jumps / resets
    - Non-monotonic timestamps
    - DTS/PTS gaps
    - Audio/video sync drift

    Bottom toggle: Video / Audio / Both
    Supports: mouse wheel zoom (X-axis), rubber band zoom, double-click reset
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loaded = False
        self._live_mode = False
        self._live_timer: Optional[QTimer] = None
        self._live_packets_fn = None
        self._live_stream_info_fn = None
        self._x_min = 0.0
        self._x_max = 1.0
        self._y_min = 0.0
        self._y_max = 1.0
        # Current filter mode: "video", "audio", "both"
        self._filter_mode = "both"
        # Cached frame data for re-rendering on filter change
        self._video_frames: List[tuple] = []  # [(frame_no, timestamp_ms), ...]
        self._audio_frames: List[tuple] = []
        self._video_idr_frames: List[tuple] = []  # IDR subset
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setMouseTracking(True)

        # Chart
        self._chart = QChart()
        self._chart.setTitle("Timestamp Progression")
        self._chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        self._chart.legend().setVisible(True)
        self._chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)

        # Dark theme
        self._chart.setBackgroundBrush(QColor(30, 30, 36))
        self._chart.setTitleBrush(QColor(200, 200, 210))
        self._chart.legend().setLabelColor(QColor(180, 180, 190))

        # Chart view
        self._chart_view = QChartView(self._chart)
        self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._chart_view.setRubberBand(QChartView.RubberBand.RectangleRubberBand)
        self._chart_view.setMouseTracking(True)
        self._chart_view.viewport().setMouseTracking(True)
        self._chart_view.installEventFilter(self)
        self._chart_view.viewport().installEventFilter(self)
        layout.addWidget(self._chart_view)

        # Tooltip overlay
        self._tooltip = QLabel(self._chart_view)
        self._tooltip.setStyleSheet(
            "background-color: rgba(40, 40, 50, 220); color: #e0e0e0; "
            "padding: 4px 8px; border-radius: 4px; font-size: 11px;"
        )
        self._tooltip.hide()
        self._tooltip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # Bottom filter bar
        filter_bar = QHBoxLayout()
        filter_bar.setContentsMargins(8, 4, 8, 4)
        filter_bar.setSpacing(4)

        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)

        self._btn_both = QPushButton("Both")
        self._btn_video = QPushButton("Video")
        self._btn_audio = QPushButton("Audio")

        for btn in (self._btn_both, self._btn_video, self._btn_audio):
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setMinimumWidth(60)
            btn.setStyleSheet("""
                QPushButton {
                    background: #2a2a32; color: #b0b0b8; border: 1px solid #444;
                    border-radius: 3px; padding: 2px 12px; font-size: 12px;
                }
                QPushButton:checked {
                    background: #3a5070; color: #e0e8f0; border-color: #5080b0;
                }
                QPushButton:hover { background: #363640; }
            """)
            self._btn_group.addButton(btn)

        self._btn_both.setChecked(True)
        self._btn_both.clicked.connect(lambda: self._set_filter("both"))
        self._btn_video.clicked.connect(lambda: self._set_filter("video"))
        self._btn_audio.clicked.connect(lambda: self._set_filter("audio"))

        filter_bar.addStretch()
        filter_bar.addWidget(self._btn_both)
        filter_bar.addWidget(self._btn_video)
        filter_bar.addWidget(self._btn_audio)
        filter_bar.addStretch()

        layout.addLayout(filter_bar)

    def _set_filter(self, mode: str):
        """Switch between video/audio/both display."""
        if mode == self._filter_mode:
            return
        self._filter_mode = mode
        self._render_chart()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_R or event.key() == Qt.Key.Key_Escape:
            self._reset_zoom()
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if obj is self._chart_view or obj is self._chart_view.viewport():
            if event.type() == event.Type.MouseMove:
                pos = event.position() if hasattr(event, 'position') else event.pos()
                self._show_tooltip(pos)
                return False
            elif event.type() == event.Type.Leave:
                self._tooltip.hide()
                return False
        return super().eventFilter(obj, event)

    def _show_tooltip(self, pos):
        """Show tooltip with frame info at cursor position."""
        if not self._loaded:
            self._tooltip.hide()
            return

        if hasattr(pos, 'toPointF'):
            pos_f = pos.toPointF()
        elif hasattr(pos, 'x'):
            pos_f = QPointF(pos.x(), pos.y())
        else:
            self._tooltip.hide()
            return

        plot_area = self._chart.plotArea()
        if not plot_area.contains(pos_f):
            self._tooltip.hide()
            return

        chart_point = self._chart.mapToValue(pos_f)
        frame_no = int(round(chart_point.x()))
        ts_ms = chart_point.y()

        # Format tooltip
        lines = [f"Frame: #{frame_no}"]
        lines.append(f"Timestamp: {ts_ms:.0f} ms")
        # Convert to human-readable time
        total_sec = int(ts_ms) // 1000
        ms = int(ts_ms) % 1000
        mins = total_sec // 60
        secs = total_sec % 60
        hours = mins // 60
        mins = mins % 60
        if hours > 0:
            lines.append(f"Time: {hours}:{mins:02d}:{secs:02d}.{ms:03d}")
        else:
            lines.append(f"Time: {mins:02d}:{secs:02d}.{ms:03d}")

        self._tooltip.setText("\n".join(lines))
        self._tooltip.adjustSize()

        tip_x = int(pos_f.x()) + 15
        tip_y = int(pos_f.y()) - self._tooltip.height() - 5
        if tip_x + self._tooltip.width() > self._chart_view.width():
            tip_x = int(pos_f.x()) - self._tooltip.width() - 10
        if tip_y < 0:
            tip_y = int(pos_f.y()) + 15
        self._tooltip.move(tip_x, tip_y)
        self._tooltip.show()

    def wheelEvent(self, event):
        """Mouse wheel zoom — X axis only."""
        factor = 1.25
        if event.angleDelta().y() > 0:
            for axis in self._chart.axes(Qt.Orientation.Horizontal):
                center = (axis.min() + axis.max()) / 2
                half_range = (axis.max() - axis.min()) / 2 / factor
                axis.setRange(center - half_range, center + half_range)
        else:
            for axis in self._chart.axes(Qt.Orientation.Horizontal):
                center = (axis.min() + axis.max()) / 2
                half_range = (axis.max() - axis.min()) / 2 * factor
                axis.setRange(center - half_range, center + half_range)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        """Double-click to reset zoom."""
        self._reset_zoom()
        event.accept()

    def _reset_zoom(self):
        """Reset axes to original full range."""
        for axis in self._chart.axes(Qt.Orientation.Horizontal):
            axis.setRange(self._x_min, self._x_max)
        for axis in self._chart.axes(Qt.Orientation.Vertical):
            axis.setRange(self._y_min, self._y_max)

    def load_packets(self, packets: List[PacketInfo],
                     stream_info: Optional[StreamInfo] = None) -> None:
        """Extract frame timestamps and render chart."""
        if not packets:
            return

        self._loaded = True
        self._extract_frames(packets, stream_info)
        self._render_chart()

    def _extract_frames(self, packets: List[PacketInfo],
                        stream_info: Optional[StreamInfo] = None) -> None:
        """Extract per-frame (index, timestamp) data from packets."""
        self._video_frames = []
        self._audio_frames = []
        self._video_idr_frames = []

        # Detect format
        is_ts = False
        is_mp4 = False
        for p in packets[:20]:
            if p.script_data and "box_type" in p.script_data:
                is_mp4 = True
                break
            if p.script_data and "pid" in p.script_data:
                is_ts = True
                break

        if is_mp4:
            self._extract_mp4(packets, stream_info)
        elif is_ts:
            self._extract_ts(packets)
        else:
            self._extract_flv(packets)

    def _extract_flv(self, packets: List[PacketInfo]) -> None:
        """Extract from FLV/RTMP — each tag has a timestamp."""
        video_idx = 0
        audio_idx = 0
        for pkt in packets:
            if pkt.tag_type == TagType.VIDEO:
                self._video_frames.append((video_idx, pkt.timestamp))
                if pkt.frame_type == FrameType.KEY:
                    self._video_idr_frames.append((video_idx, pkt.timestamp))
                video_idx += 1
            elif pkt.tag_type == TagType.AUDIO:
                self._audio_frames.append((audio_idx, pkt.timestamp))
                audio_idx += 1

    def _extract_ts(self, packets: List[PacketInfo]) -> None:
        """Extract from TS — PUSI packets with PTS."""
        video_idx = 0
        audio_idx = 0
        for pkt in packets:
            if not pkt.script_data:
                continue
            if not pkt.script_data.get("pusi"):
                continue
            if pkt.timestamp <= 0:
                continue

            if pkt.tag_type == TagType.VIDEO:
                self._video_frames.append((video_idx, pkt.timestamp))
                if pkt.frame_type == FrameType.KEY:
                    self._video_idr_frames.append((video_idx, pkt.timestamp))
                video_idx += 1
            elif pkt.tag_type == TagType.AUDIO:
                self._audio_frames.append((audio_idx, pkt.timestamp))
                audio_idx += 1

    def _extract_mp4(self, packets: List[PacketInfo],
                     stream_info: Optional[StreamInfo] = None) -> None:
        """Extract from MP4 — compute per-sample DTS from track metadata."""
        if not stream_info or not stream_info.metadata:
            return

        tracks = stream_info.metadata.get("tracks", [])
        if not tracks:
            return

        for track in tracks:
            handler = track.get("handler", "")
            is_video = handler in ("vide", "video")
            is_audio = handler in ("soun", "sound")
            if not is_video and not is_audio:
                continue

            timescale = track.get("timescale", 0)
            if timescale <= 0:
                continue

            stts_entries = track.get("stts", [])
            sample_sizes = track.get("stsz", [])
            sync_set = track.get("stss", set())

            if not sample_sizes:
                continue

            # Compute DTS for each sample
            sample_dts_list = []
            dts = 0
            sample_idx = 0
            for entry in stts_entries:
                if isinstance(entry, (list, tuple)):
                    count, delta = entry[0], entry[1]
                else:
                    count = entry.get("count", 0)
                    delta = entry.get("delta", 0)
                for _ in range(count):
                    if sample_idx >= len(sample_sizes):
                        break
                    dts_ms = int(dts * 1000 / timescale)
                    sample_dts_list.append(dts_ms)
                    dts += delta
                    sample_idx += 1

            # Extrapolate if needed
            if len(sample_dts_list) < len(sample_sizes) and stts_entries:
                last_entry = stts_entries[-1]
                if isinstance(last_entry, (list, tuple)):
                    last_delta = last_entry[1]
                else:
                    last_delta = last_entry.get("delta", 1)
                while len(sample_dts_list) < len(sample_sizes):
                    dts_ms = int(dts * 1000 / timescale)
                    sample_dts_list.append(dts_ms)
                    dts += last_delta

            # Build frame list
            for i in range(len(sample_dts_list)):
                if is_video:
                    self._video_frames.append((i, sample_dts_list[i]))
                    if (i + 1) in sync_set:
                        self._video_idr_frames.append((i, sample_dts_list[i]))
                elif is_audio:
                    self._audio_frames.append((i, sample_dts_list[i]))

    def _render_chart(self) -> None:
        """Render the timestamp chart from cached frame data."""
        self._chart.removeAllSeries()
        for axis in self._chart.axes():
            self._chart.removeAxis(axis)

        show_video = self._filter_mode in ("both", "video")
        show_audio = self._filter_mode in ("both", "audio")

        if not self._video_frames and not self._audio_frames:
            self._chart.setTitle("Timestamp Progression  |  No frame data")
            return

        max_frame = 0
        max_ts = 0
        min_ts = float('inf')

        # Video series
        if show_video and self._video_frames:
            video_series = QLineSeries()
            video_series.setName("Video DTS")
            video_pen = QPen(QColor(80, 150, 230))
            video_pen.setWidth(2)
            video_series.setPen(video_pen)

            for frame_no, ts in self._video_frames:
                video_series.append(float(frame_no), float(ts))
                max_frame = max(max_frame, frame_no)
                max_ts = max(max_ts, ts)
                min_ts = min(min_ts, ts)

            self._chart.addSeries(video_series)

            # IDR markers
            if self._video_idr_frames:
                idr_series = QScatterSeries()
                idr_series.setName("IDR")
                idr_series.setMarkerSize(7)
                idr_series.setColor(QColor(230, 70, 70))
                idr_series.setBorderColor(QColor(230, 70, 70))

                for frame_no, ts in self._video_idr_frames:
                    idr_series.append(float(frame_no), float(ts))

                self._chart.addSeries(idr_series)

        # Audio series
        if show_audio and self._audio_frames:
            audio_series = QLineSeries()
            audio_series.setName("Audio DTS")
            audio_pen = QPen(QColor(100, 200, 130))
            audio_pen.setWidth(2)
            audio_series.setPen(audio_pen)

            for frame_no, ts in self._audio_frames:
                audio_series.append(float(frame_no), float(ts))
                max_frame = max(max_frame, frame_no)
                max_ts = max(max_ts, ts)
                min_ts = min(min_ts, ts)

            self._chart.addSeries(audio_series)

        if max_frame == 0:
            self._chart.setTitle("Timestamp Progression  |  No data for filter")
            return

        if min_ts == float('inf'):
            min_ts = 0

        # X axis: frame number
        axis_x = QValueAxis()
        axis_x.setTitleText("Frame #")
        axis_x.setRange(0, float(max_frame))
        axis_x.setLabelFormat("%.0f")
        axis_x.setGridLineColor(QColor(60, 60, 70))
        axis_x.setLabelsColor(QColor(160, 160, 170))
        axis_x.setTitleBrush(QColor(180, 180, 190))

        self._x_min = 0
        self._x_max = float(max_frame)

        # Y axis: timestamp in ms
        axis_y = QValueAxis()
        axis_y.setTitleText("Timestamp (ms)")
        y_min = max(0, min_ts - (max_ts - min_ts) * 0.05)
        y_max = max_ts * 1.05
        axis_y.setRange(y_min, y_max)
        axis_y.setLabelFormat("%.0f")
        axis_y.setGridLineColor(QColor(60, 60, 70))
        axis_y.setLabelsColor(QColor(160, 160, 170))
        axis_y.setTitleBrush(QColor(180, 180, 190))

        self._y_min = y_min
        self._y_max = y_max

        self._chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        self._chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)

        # Attach all series to axes
        for series in self._chart.series():
            series.attachAxis(axis_x)
            series.attachAxis(axis_y)

        # Title with stats
        v_count = len(self._video_frames)
        a_count = len(self._audio_frames)
        dur_s = max_ts / 1000.0 if max_ts > 0 else 0
        title = f"Timestamp Progression  |  Video: {v_count} frames  |  Audio: {a_count} frames"
        if dur_s > 0:
            title += f"  |  Duration: {dur_s:.1f}s"
        self._chart.setTitle(title)

    # --- Live mode (RTMP) ---

    def start_live_mode(self, packets_fn, stream_info_fn=None):
        """Start live update mode for RTMP streams."""
        self._live_mode = True
        self._live_packets_fn = packets_fn
        self._live_stream_info_fn = stream_info_fn

        if self._live_timer is None:
            self._live_timer = QTimer(self)
            self._live_timer.timeout.connect(self._live_update)
        self._live_timer.start(2000)

    def stop_live_mode(self):
        """Stop live update."""
        self._live_mode = False
        if self._live_timer:
            self._live_timer.stop()

    def _live_update(self):
        """Timer callback: refresh chart with latest data."""
        if not self._live_mode or not self._live_packets_fn:
            return
        packets = self._live_packets_fn()
        if packets:
            packets_snapshot = list(packets)
            stream_info = self._live_stream_info_fn() if self._live_stream_info_fn else None
            self.load_packets(packets_snapshot, stream_info)

    def clear(self) -> None:
        """Clear the chart and stop live mode."""
        self.stop_live_mode()
        self._chart.removeAllSeries()
        for axis in self._chart.axes():
            self._chart.removeAxis(axis)
        self._chart.setTitle("Timestamp Progression")
        self._loaded = False
        self._video_frames = []
        self._audio_frames = []
        self._video_idr_frames = []
