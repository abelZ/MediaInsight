"""Bitrate analysis chart page — shows per-second bitrate with IDR markers."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCharts import (
    QChart, QChartView, QLineSeries, QScatterSeries, QValueAxis,
)
from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import QColor, QPen, QPainter, QMouseEvent
from typing import List, Dict, Optional
from dataclasses import dataclass
from collections import defaultdict

from media_analyzer.core.models import PacketInfo, TagType, FrameType, StreamInfo


@dataclass
class FrameSample:
    """A single frame/sample for bitrate calculation."""
    timestamp_ms: int   # DTS in milliseconds
    size_bytes: int     # Frame data size
    is_video: bool      # True=video, False=audio
    is_idr: bool        # True if IDR/sync frame


class BitrateExtractor:
    """
    Extracts frame-level bitrate data from parsed packets.
    Handles FLV, TS, MP4, and RTMP formats transparently.
    """

    @staticmethod
    def extract(packets: List[PacketInfo],
                stream_info: Optional['StreamInfo'] = None) -> List[FrameSample]:
        """Auto-detect format and extract frame samples."""
        if not packets:
            return []

        # Detect format from first few packets
        for p in packets[:20]:
            if p.script_data and "box_type" in p.script_data:
                if p.script_data.get("ebml_id") is not None:
                    # WebM/MKV — use FLV-style extraction (direct timestamp + size)
                    return BitrateExtractor._extract_flv(packets)
                return BitrateExtractor._extract_mp4(packets, stream_info)
            if p.script_data and "pid" in p.script_data:
                return BitrateExtractor._extract_ts(packets)

        # FLV / RTMP (packets have direct timestamp)
        return BitrateExtractor._extract_flv(packets)

    @staticmethod
    def _extract_flv(packets: List[PacketInfo]) -> List[FrameSample]:
        """Extract from FLV/RTMP — each tag is a frame with timestamp."""
        samples = []
        for pkt in packets:
            if pkt.tag_type == TagType.VIDEO:
                samples.append(FrameSample(
                    timestamp_ms=pkt.timestamp,
                    size_bytes=pkt.data_size,
                    is_video=True,
                    is_idr=(pkt.frame_type == FrameType.KEY),
                ))
            elif pkt.tag_type == TagType.AUDIO:
                samples.append(FrameSample(
                    timestamp_ms=pkt.timestamp,
                    size_bytes=pkt.data_size,
                    is_video=False,
                    is_idr=False,
                ))
        return samples

    @staticmethod
    def _extract_ts(packets: List[PacketInfo]) -> List[FrameSample]:
        """
        Extract from TS — use PUSI packets (frame starts) with PTS timestamp.
        The TS parser already sets packet.timestamp = PTS in ms for PUSI packets.
        Use _pes_size from script_data for frame size (full PES payload size).
        """
        samples = []
        for pkt in packets:
            if not pkt.script_data:
                continue
            # Only use frame-start (PUSI) packets
            if not pkt.script_data.get("pusi"):
                continue
            # Must have a valid timestamp
            if pkt.timestamp <= 0:
                continue

            # Use _pes_size as frame size (full PES payload), fallback to data_size
            frame_size = pkt.script_data.get("_pes_size", pkt.data_size)

            if pkt.tag_type == TagType.VIDEO:
                samples.append(FrameSample(
                    timestamp_ms=pkt.timestamp,
                    size_bytes=frame_size,
                    is_video=True,
                    is_idr=(pkt.frame_type == FrameType.KEY),
                ))
            elif pkt.tag_type == TagType.AUDIO:
                samples.append(FrameSample(
                    timestamp_ms=pkt.timestamp,
                    size_bytes=frame_size,
                    is_video=False,
                    is_idr=False,
                ))
        return samples

    @staticmethod
    def _extract_mp4(packets: List[PacketInfo],
                     stream_info: Optional['StreamInfo'] = None) -> List[FrameSample]:
        """
        Extract from MP4 — compute per-sample DTS from stts entries,
        sample sizes from stsz, and sync flags from stss.
        Uses track data stored in StreamInfo.metadata["tracks"].
        """
        if not stream_info or not stream_info.metadata:
            return []

        tracks = stream_info.metadata.get("tracks", [])
        if not tracks:
            return []

        samples = []
        for track in tracks:
            handler = track.get("handler", "")
            is_video = handler in ("vide", "video")
            is_audio = handler in ("soun", "sound")
            if not is_video and not is_audio:
                continue

            timescale = track.get("timescale", 0)
            if timescale <= 0:
                continue

            stts_entries = track.get("stts", [])  # [(count, delta), ...]
            sample_sizes = track.get("stsz", [])  # [size, size, ...]
            sync_set = track.get("stss", set())   # {1-based sample numbers}

            if not sample_sizes:
                continue

            # Compute DTS for each sample from stts
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

            # Extrapolate if stts didn't cover all samples
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

            # Build FrameSamples
            for i in range(min(len(sample_sizes), len(sample_dts_list))):
                is_idr = (i + 1) in sync_set  # stss uses 1-based indices
                samples.append(FrameSample(
                    timestamp_ms=sample_dts_list[i],
                    size_bytes=sample_sizes[i],
                    is_video=is_video,
                    is_idr=is_idr if is_video else False,
                ))

        return samples


class BitratePage(QWidget):
    """
    Bitrate analysis chart view.

    Shows per-second bitrate as line charts:
    - Video bitrate (blue line)
    - Audio bitrate (green line)
    - IDR frame positions (red scatter markers on video line)

    X-axis: time in seconds
    Y-axis: bitrate in kbps
    Supports: mouse wheel zoom, rubber band zoom, right-click reset, RTMP live update
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loaded = False
        self._live_mode = False  # RTMP real-time update mode
        self._live_timer: Optional[QTimer] = None
        self._live_packets_fn = None  # Callable to get latest packets
        self._live_stream_info_fn = None  # Callable to get stream info
        self._x_min = 0.0  # Original X axis range for reset
        self._x_max = 1.0
        # Stored bucket data for tooltip display
        self._video_buckets: Dict[int, int] = {}
        self._audio_buckets: Dict[int, int] = {}
        self._idr_seconds: set = set()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setMouseTracking(True)

        # Chart
        self._chart = QChart()
        self._chart.setTitle("Bitrate Analysis")
        self._chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        self._chart.legend().setVisible(True)
        self._chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)

        # Dark theme for chart
        self._chart.setBackgroundBrush(QColor(30, 30, 36))
        self._chart.setTitleBrush(QColor(200, 200, 210))
        self._chart.legend().setLabelColor(QColor(180, 180, 190))

        # Chart view with zoom support
        self._chart_view = QChartView(self._chart)
        self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Enable horizontal rubber band zoom (drag to zoom X axis only)
        self._chart_view.setRubberBand(QChartView.RubberBand.HorizontalRubberBand)
        self._chart_view.setMouseTracking(True)
        self._chart_view.viewport().setMouseTracking(True)
        self._chart_view.installEventFilter(self)
        self._chart_view.viewport().installEventFilter(self)
        layout.addWidget(self._chart_view)

        # Tooltip overlay label
        self._tooltip = QLabel(self._chart_view)
        self._tooltip.setStyleSheet(
            "background-color: rgba(40, 40, 50, 220); color: #e0e0e0; "
            "padding: 4px 8px; border-radius: 4px; font-size: 11px;"
        )
        self._tooltip.hide()
        self._tooltip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def keyPressEvent(self, event):
        """Handle key events for zoom reset."""
        if event.key() == Qt.Key.Key_R or event.key() == Qt.Key.Key_Escape:
            self._reset_zoom()
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        """Intercept mouse events on chart view for tooltip display."""
        if obj is self._chart_view or obj is self._chart_view.viewport():
            if event.type() == event.Type.MouseMove:
                pos = event.position() if hasattr(event, 'position') else event.pos()
                self._show_tooltip(pos)
                return False  # Don't consume — let rubber band zoom still work
            elif event.type() == event.Type.Leave:
                self._tooltip.hide()
                return False
        return super().eventFilter(obj, event)

    def _show_tooltip(self, pos):
        """Show tooltip with bitrate info at given chart view position."""
        if not self._loaded or (not self._video_buckets and not self._audio_buckets):
            self._tooltip.hide()
            return

        # Convert to QPointF
        if hasattr(pos, 'toPointF'):
            pos_f = pos.toPointF()
        elif hasattr(pos, 'x'):
            from PySide6.QtCore import QPointF as _QPointF
            pos_f = _QPointF(pos.x(), pos.y())
        else:
            self._tooltip.hide()
            return

        # Check if position is within chart plot area
        plot_area = self._chart.plotArea()
        if not plot_area.contains(pos_f):
            self._tooltip.hide()
            return

        # Convert pixel position to chart value
        chart_point = self._chart.mapToValue(pos_f)
        sec = int(round(chart_point.x()))

        # Get bitrate values at this second
        video_kbps = self._video_buckets.get(sec, 0) / 1000.0
        audio_kbps = self._audio_buckets.get(sec, 0) / 1000.0
        is_idr = sec in self._idr_seconds

        # Format tooltip text
        lines = [f"Time: {sec}s"]
        lines.append(f"Video: {video_kbps:.0f} kbps")
        lines.append(f"Audio: {audio_kbps:.0f} kbps")
        lines.append(f"Total: {video_kbps + audio_kbps:.0f} kbps")
        if is_idr:
            lines.append("IDR Frame")

        self._tooltip.setText("\n".join(lines))
        self._tooltip.adjustSize()

        # Position tooltip near cursor
        tip_x = int(pos_f.x()) + 15
        tip_y = int(pos_f.y()) - self._tooltip.height() - 5
        # Keep within chart view bounds
        if tip_x + self._tooltip.width() > self._chart_view.width():
            tip_x = int(pos_f.x()) - self._tooltip.width() - 10
        if tip_y < 0:
            tip_y = int(pos_f.y()) + 15
        self._tooltip.move(tip_x, tip_y)
        self._tooltip.show()

    def wheelEvent(self, event):
        """Mouse wheel zoom — only X axis."""
        factor = 1.25
        if event.angleDelta().y() > 0:
            # Zoom in on X axis only
            for axis in self._chart.axes(Qt.Orientation.Horizontal):
                center = (axis.min() + axis.max()) / 2
                half_range = (axis.max() - axis.min()) / 2 / factor
                axis.setRange(center - half_range, center + half_range)
        else:
            # Zoom out on X axis only
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
        """Reset X axis to original full range."""
        for axis in self._chart.axes(Qt.Orientation.Horizontal):
            axis.setRange(self._x_min, self._x_max)

    def load_packets(self, packets: List[PacketInfo],
                     stream_info: Optional[StreamInfo] = None) -> None:
        """Extract frame-level bitrate and render chart."""
        if not packets:
            return

        self._loaded = True
        self._chart.removeAllSeries()
        for axis in self._chart.axes():
            self._chart.removeAxis(axis)

        # Extract frame samples using format-aware extractor
        samples = BitrateExtractor.extract(packets, stream_info)
        if not samples:
            self._chart.setTitle("Bitrate Analysis  |  No frame data available")
            return

        # Normalize timestamps: use relative time from first sample of each track
        # This handles RTMP streams where audio/video have different timestamp bases
        video_samples = [s for s in samples if s.is_video]
        audio_samples = [s for s in samples if not s.is_video]

        video_base = video_samples[0].timestamp_ms if video_samples else 0
        audio_base = audio_samples[0].timestamp_ms if audio_samples else 0

        # Compute per-second bitrate buckets using relative time
        video_buckets: Dict[int, int] = defaultdict(int)  # sec -> bits
        audio_buckets: Dict[int, int] = defaultdict(int)
        idr_seconds: set = set()

        for s in video_samples:
            sec = (s.timestamp_ms - video_base) // 1000
            video_buckets[sec] += s.size_bytes * 8
            if s.is_idr:
                idr_seconds.add(sec)

        for s in audio_samples:
            sec = (s.timestamp_ms - audio_base) // 1000
            audio_buckets[sec] += s.size_bytes * 8

        self._render_chart(video_buckets, audio_buckets, idr_seconds)

    def start_live_mode(self, packets_fn, stream_info_fn=None):
        """Start RTMP live update mode.

        Args:
            packets_fn: callable that returns current List[PacketInfo]
            stream_info_fn: callable that returns current StreamInfo (optional)
        """
        self._live_mode = True
        self._live_packets_fn = packets_fn
        self._live_stream_info_fn = stream_info_fn

        if self._live_timer is None:
            self._live_timer = QTimer(self)
            self._live_timer.timeout.connect(self._live_update)
        self._live_timer.start(2000)  # Update every 2 seconds

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
            # Take a snapshot to avoid race with worker thread appending
            packets_snapshot = list(packets)
            stream_info = self._live_stream_info_fn() if self._live_stream_info_fn else None
            self.load_packets(packets_snapshot, stream_info)

    def _render_chart(self, video_buckets: Dict[int, int],
                      audio_buckets: Dict[int, int], idr_seconds: set) -> None:
        """Render the bitrate chart from computed bucket data. Unit: kbps."""
        # Store for tooltip access
        self._video_buckets = video_buckets
        self._audio_buckets = audio_buckets
        self._idr_seconds = idr_seconds

        all_secs = set(video_buckets.keys()) | set(audio_buckets.keys())
        if not all_secs:
            self._chart.setTitle("Bitrate Analysis  |  No data")
            return
        min_sec = min(all_secs)
        max_sec = max(all_secs)

        # Convert to kbps
        divisor = 1000.0

        # Determine max bitrate for Y-axis
        max_kbps = 0
        for sec in range(min_sec, max_sec + 1):
            v = video_buckets.get(sec, 0) / divisor
            a = audio_buckets.get(sec, 0) / divisor
            max_kbps = max(max_kbps, v, a)

        # Build video series
        video_series = QLineSeries()
        video_series.setName("Video")
        video_pen = QPen(QColor(80, 150, 230))
        video_pen.setWidth(2)
        video_series.setPen(video_pen)

        for sec in range(min_sec, max_sec + 1):
            val = video_buckets.get(sec, 0) / divisor
            video_series.append(float(sec), val)

        # Build audio series
        audio_series = QLineSeries()
        audio_series.setName("Audio")
        audio_pen = QPen(QColor(100, 200, 130))
        audio_pen.setWidth(2)
        audio_series.setPen(audio_pen)

        for sec in range(min_sec, max_sec + 1):
            val = audio_buckets.get(sec, 0) / divisor
            audio_series.append(float(sec), val)

        # Build IDR marker series
        idr_series = QScatterSeries()
        idr_series.setName("IDR Frame")
        idr_series.setMarkerSize(8)
        idr_series.setColor(QColor(230, 70, 70))
        idr_series.setBorderColor(QColor(230, 70, 70))

        for sec in sorted(idr_seconds):
            val = video_buckets.get(sec, 0) / divisor
            idr_series.append(float(sec), val)

        # Add series to chart
        self._chart.addSeries(video_series)
        self._chart.addSeries(audio_series)
        self._chart.addSeries(idr_series)

        # Create axes
        axis_x = QValueAxis()
        axis_x.setTitleText("Time (s)")
        axis_x.setRange(float(min_sec), float(max_sec))
        axis_x.setLabelFormat("%.0f")
        axis_x.setGridLineColor(QColor(60, 60, 70))
        axis_x.setLabelsColor(QColor(160, 160, 170))
        axis_x.setTitleBrush(QColor(180, 180, 190))

        # Save original range for zoom reset
        self._x_min = float(min_sec)
        self._x_max = float(max_sec)

        axis_y = QValueAxis()
        axis_y.setTitleText("Bitrate (kbps)")
        # Use nice fixed tick intervals for easy reading
        max_y = max_kbps * 1.1
        if max_y <= 100:
            tick_interval = 20
        elif max_y <= 500:
            tick_interval = 100
        elif max_y <= 2000:
            tick_interval = 500
        elif max_y <= 5000:
            tick_interval = 1000
        elif max_y <= 20000:
            tick_interval = 2000
        else:
            tick_interval = 5000
        # Round max_y up to next tick
        max_y = ((int(max_y) // tick_interval) + 1) * tick_interval
        axis_y.setRange(0, max_y)
        axis_y.setTickInterval(tick_interval)
        axis_y.setTickType(QValueAxis.TickType.TicksFixed)
        axis_y.setLabelFormat("%.0f")
        axis_y.setGridLineColor(QColor(60, 60, 70))
        axis_y.setLabelsColor(QColor(160, 160, 170))
        axis_y.setTitleBrush(QColor(180, 180, 190))

        self._chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        self._chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)

        video_series.attachAxis(axis_x)
        video_series.attachAxis(axis_y)
        audio_series.attachAxis(axis_x)
        audio_series.attachAxis(axis_y)
        idr_series.attachAxis(axis_x)
        idr_series.attachAxis(axis_y)

        # Update title with summary
        dur_sec = max_sec - min_sec + 1
        avg_video = sum(video_buckets.values()) / dur_sec / divisor if dur_sec > 0 else 0
        avg_audio = sum(audio_buckets.values()) / dur_sec / divisor if dur_sec > 0 else 0
        idr_count = len(idr_seconds)
        self._chart.setTitle(
            f"Bitrate Analysis  |  Video avg: {avg_video:.0f} kbps  |  "
            f"Audio avg: {avg_audio:.0f} kbps  |  IDR frames: {idr_count}"
        )

    def clear(self) -> None:
        """Clear the chart and stop live mode."""
        self.stop_live_mode()
        self._chart.removeAllSeries()
        for axis in self._chart.axes():
            self._chart.removeAxis(axis)
        self._chart.setTitle("Bitrate Analysis")
        self._loaded = False
