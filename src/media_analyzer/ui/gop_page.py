"""GOP visualization page — frame size chart with GOP boundaries and statistics."""

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QLabel,
    QCheckBox, QScrollBar,
)
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QPen, QColor, QBrush, QPaintEvent, QWheelEvent,
    QMouseEvent, QFont, QFontMetrics,
)

from media_analyzer.core.models import PacketInfo, TagType, FrameType, StreamInfo

logger = logging.getLogger(__name__)

# Frame type colors
COLOR_I = QColor(224, 80, 80)    # Red
COLOR_P = QColor(80, 144, 224)   # Blue
COLOR_B = QColor(80, 192, 112)   # Green


@dataclass
class FrameInfo:
    """Single video frame for GOP display."""
    index: int = 0
    frame_type: str = "P"    # "I", "P", "B"
    size_bytes: int = 0
    dts_ms: int = 0
    pts_ms: int = 0
    is_idr: bool = False


# ---------------------------------------------------------------------------
# Frame extraction functions (from PacketInfo, all formats)
# ---------------------------------------------------------------------------

def _extract_video_frames(packets: List[PacketInfo],
                          stream_info: Optional[StreamInfo] = None) -> List[FrameInfo]:
    """Extract video frame info from packets (all formats)."""
    if not packets:
        return []

    # Detect format
    for p in packets[:20]:
        if p.script_data and "box_type" in p.script_data:
            if p.script_data.get("ebml_id") is not None:
                return _extract_flv_frames(packets)
            return _extract_mp4_frames(packets, stream_info)
        if p.script_data and "pid" in p.script_data:
            return _extract_ts_frames(packets)

    return _extract_flv_frames(packets)


def _extract_flv_frames(packets: List[PacketInfo]) -> List[FrameInfo]:
    """Extract from FLV/RTMP/WebM/MKV — each video packet is a frame."""
    from media_analyzer.core.models import AVCPacketType

    frames = []
    idx = 0
    for pkt in packets:
        if pkt.tag_type != TagType.VIDEO:
            continue
        if pkt.data_size <= 0:
            continue
        # Skip AVC/HEVC sequence headers (codec config, not real video frames)
        if pkt.avc_packet_type is not None and pkt.avc_packet_type != AVCPacketType.NALU:
            continue

        ft = "I" if pkt.frame_type == FrameType.KEY else "P"
        if pkt.frame_type == FrameType.DISPOSABLE_INTER:
            ft = "B"
        elif (pkt.frame_type == FrameType.INTER
              and pkt.composition_time is not None
              and pkt.composition_time != 0):
            ft = "B"

        frames.append(FrameInfo(
            index=idx,
            frame_type=ft,
            size_bytes=pkt.data_size,
            dts_ms=pkt.timestamp,
            pts_ms=pkt.pts,
            is_idr=(pkt.frame_type == FrameType.KEY),
        ))
        idx += 1
    return frames


def _extract_ts_frames(packets: List[PacketInfo]) -> List[FrameInfo]:
    """Extract from TS — only PUSI video packets."""
    frames = []
    idx = 0
    for pkt in packets:
        if pkt.tag_type != TagType.VIDEO:
            continue
        if not pkt.script_data or not pkt.script_data.get("pusi"):
            continue
        if pkt.timestamp <= 0 and idx > 0:
            continue

        frame_size = pkt.script_data.get("_pes_size", pkt.data_size)
        ft = "I" if pkt.frame_type == FrameType.KEY else "P"
        if pkt.frame_type == FrameType.DISPOSABLE_INTER:
            ft = "B"
        elif (pkt.frame_type == FrameType.INTER
              and pkt.composition_time is not None
              and pkt.composition_time != 0):
            ft = "B"

        frames.append(FrameInfo(
            index=idx,
            frame_type=ft,
            size_bytes=frame_size,
            dts_ms=pkt.timestamp,
            pts_ms=pkt.pts,
            is_idr=(pkt.frame_type == FrameType.KEY),
        ))
        idx += 1
    return frames


def _extract_mp4_frames(packets: List[PacketInfo],
                        stream_info: Optional[StreamInfo]) -> List[FrameInfo]:
    """Extract from MP4 using stts/stsz/stss/frame_types from stream_info."""
    if not stream_info or not stream_info.metadata:
        return []

    tracks = stream_info.metadata.get("tracks", [])
    frames = []

    for track in tracks:
        handler = track.get("handler", "")
        if handler not in ("vide", "video"):
            continue

        timescale = track.get("timescale", 0)
        if timescale <= 0:
            continue

        stts_entries = track.get("stts", [])
        sample_sizes = track.get("stsz", [])
        sync_set = track.get("stss", set())
        frame_types_list = track.get("frame_types", [])

        if not sample_sizes:
            continue

        # Compute DTS for each sample
        dts_list = []
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
                dts_list.append(int(dts * 1000 / timescale))
                dts += delta
                sample_idx += 1

        # Build frames
        for i in range(len(sample_sizes)):
            is_idr = (i + 1) in sync_set
            dts_ms = dts_list[i] if i < len(dts_list) else 0

            if i < len(frame_types_list) and frame_types_list[i]:
                ft = frame_types_list[i]
            elif is_idr:
                ft = "I"
            else:
                ft = "P"

            frames.append(FrameInfo(
                index=i,
                frame_type=ft,
                size_bytes=sample_sizes[i],
                dts_ms=dts_ms,
                pts_ms=dts_ms,
                is_idr=is_idr,
            ))
        break  # Only first video track

    return frames


def _group_into_gops(frames: List[FrameInfo]) -> List[List[FrameInfo]]:
    """Split frame list into GOPs (each starts with an I-frame)."""
    if not frames:
        return []

    gops: List[List[FrameInfo]] = []
    current_gop: List[FrameInfo] = []

    for f in frames:
        if f.frame_type == "I" and current_gop:
            gops.append(current_gop)
            current_gop = []
        current_gop.append(f)

    if current_gop:
        gops.append(current_gop)

    return gops


# ---------------------------------------------------------------------------
# GOPChartWidget — bar chart with fixed bar width
# ---------------------------------------------------------------------------

class GOPChartWidget(QWidget):
    """Custom widget that draws frame size bars with GOP boundaries."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames: List[FrameInfo] = []
        self._gops: List[List[FrameInfo]] = []
        self._max_size = 1

        # Fixed bar width
        self.BAR_WIDTH = 8
        self.BAR_GAP = 2

        # Scroll offset (frame index of left edge)
        self._scroll_offset = 0
        self._total_frames = 0

        # Hover state
        self._hover_idx = -1

        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #1a1a22;")

    def set_frames(self, frames: List[FrameInfo], gops: List[List[FrameInfo]]):
        """Set frame data for display."""
        self._frames = frames
        self._gops = gops
        self._total_frames = len(frames)
        self._scroll_offset = 0
        self._max_size = max((f.size_bytes for f in frames), default=1)
        self._hover_idx = -1
        self.update()

    def set_scroll_offset(self, offset: int):
        """Set scroll position (frame index of left edge)."""
        self._scroll_offset = max(0, min(offset, self._total_frames - 1))
        self.update()

    @property
    def scroll_offset(self) -> int:
        return self._scroll_offset

    @property
    def total_frames(self) -> int:
        return self._total_frames

    @property
    def visible_frames(self) -> int:
        """Number of frames visible in current widget width."""
        MARGIN_LEFT = 50
        draw_w = max(self.width() - MARGIN_LEFT, 1)
        return draw_w // (self.BAR_WIDTH + self.BAR_GAP)

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        MARGIN_LEFT = 50
        MARGIN_BOTTOM = 20
        MARGIN_TOP = 10

        if not self._frames or w <= MARGIN_LEFT:
            painter.setPen(QColor(80, 80, 90))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No video frame data")
            return

        draw_w = w - MARGIN_LEFT
        draw_h = h - MARGIN_TOP - MARGIN_BOTTOM
        bar_area_h = draw_h * 0.9

        # Fixed bar width layout
        bar_w = self.BAR_WIDTH
        step = bar_w + self.BAR_GAP
        n_visible = draw_w // step + 1
        view_start = self._scroll_offset
        view_end = min(view_start + n_visible, self._total_frames)
        n_visible = view_end - view_start

        if n_visible <= 0:
            return

        # Draw Y-axis labels
        painter.setPen(QColor(100, 100, 110))
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        max_kb = self._max_size / 1024
        y_ticks = 5
        for i in range(y_ticks + 1):
            y = MARGIN_TOP + bar_area_h - (i / y_ticks) * bar_area_h
            val = (i / y_ticks) * max_kb
            painter.setPen(QColor(100, 100, 110))
            if val >= 1024:
                label = f"{val/1024:.1f}MB"
            else:
                label = f"{val:.0f}KB"
            painter.drawText(2, int(y) + 4, label)
            painter.setPen(QPen(QColor(40, 40, 50), 1, Qt.PenStyle.DotLine))
            painter.drawLine(MARGIN_LEFT, int(y), w, int(y))

        # Draw bars
        gop_x_positions: List[float] = []

        for i in range(n_visible):
            fi = view_start + i
            if fi >= len(self._frames):
                break
            frame = self._frames[fi]

            x = MARGIN_LEFT + i * step
            bar_h = (frame.size_bytes / self._max_size) * bar_area_h
            y = MARGIN_TOP + bar_area_h - bar_h

            # Color by type
            if frame.frame_type == "I":
                color = COLOR_I
            elif frame.frame_type == "B":
                color = COLOR_B
            else:
                color = COLOR_P

            # Highlight on hover
            if fi == self._hover_idx:
                color = color.lighter(140)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawRect(QRectF(x, y, max(bar_w - 1, 1.5), bar_h))

            # GOP boundary
            if frame.frame_type == "I" and i > 0:
                gop_x_positions.append(x)

        # Draw GOP boundary lines
        painter.setPen(QPen(QColor(200, 200, 60, 120), 1, Qt.PenStyle.DashLine))
        for gx in gop_x_positions:
            painter.drawLine(int(gx) - 1, MARGIN_TOP, int(gx) - 1, h - 5)

        # Draw X-axis labels (frame indices)
        painter.setPen(QColor(100, 100, 110))
        label_step = max(1, n_visible // 10)
        for i in range(0, n_visible, label_step):
            fi = view_start + i
            x = MARGIN_LEFT + i * step
            painter.drawText(int(x), h - 3, str(fi))

        # Draw frame type legend
        legend_x = MARGIN_LEFT + 5
        legend_y = MARGIN_TOP + 3
        for label, color in [("I", COLOR_I), ("P", COLOR_P), ("B", COLOR_B)]:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRect(int(legend_x), legend_y, 10, 10)
            painter.setPen(QColor(180, 180, 190))
            painter.drawText(int(legend_x) + 13, legend_y + 9, label)
            legend_x += 30

        # Draw hover tooltip
        if 0 <= self._hover_idx < len(self._frames):
            self._draw_tooltip(painter, w, MARGIN_TOP)

    def _draw_tooltip(self, painter: QPainter, w: int, margin_top: int):
        """Draw tooltip for hovered frame."""
        frame = self._frames[self._hover_idx]
        lines = [
            f"Frame #{frame.index}",
            f"Type: {frame.frame_type}-frame{'  (IDR)' if frame.is_idr else ''}",
            f"Size: {frame.size_bytes:,} bytes ({frame.size_bytes/1024:.1f} KB)",
            f"DTS: {frame.dts_ms} ms",
            f"PTS: {frame.pts_ms} ms",
        ]

        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)
        fm = QFontMetrics(font)
        text_w = max(fm.horizontalAdvance(l) for l in lines) + 16
        text_h = fm.height() * len(lines) + 12

        tip_x = min(w - text_w - 10, w // 2)
        tip_y = margin_top + 5

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(30, 30, 40, 230))
        painter.drawRoundedRect(QRectF(tip_x, tip_y, text_w, text_h), 4, 4)

        painter.setPen(QColor(220, 220, 230))
        y = tip_y + fm.ascent() + 4
        for line in lines:
            painter.drawText(int(tip_x + 8), int(y), line)
            y += fm.height()

    def mouseMoveEvent(self, event: QMouseEvent):
        """Track hover position for tooltip."""
        if self._total_frames == 0:
            return
        MARGIN_LEFT = 50
        x = event.position().x() - MARGIN_LEFT
        if x < 0:
            self._hover_idx = -1
            self.update()
            return
        step = self.BAR_WIDTH + self.BAR_GAP
        idx = self._scroll_offset + int(x / step)
        idx = max(0, min(idx, self._total_frames - 1))
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.update()

    def leaveEvent(self, event):
        self._hover_idx = -1
        self.update()

    def wheelEvent(self, event: QWheelEvent):
        """Scroll wheel to pan horizontally."""
        if self._total_frames == 0:
            return
        delta = -5 if event.angleDelta().y() > 0 else 5
        new_offset = self._scroll_offset + delta
        new_offset = max(0, min(new_offset, self._total_frames - 1))
        self._scroll_offset = new_offset
        self.update()
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """Double-click to reset scroll to beginning."""
        self._scroll_offset = 0
        self.update()


# ---------------------------------------------------------------------------
# StatsPanel — frame size distribution statistics and histogram
# ---------------------------------------------------------------------------

class StatsPanel(QWidget):
    """Side panel showing frame size distribution statistics and histogram."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames: List[FrameInfo] = []
        self._stats: dict = {}
        self.setFixedWidth(220)
        self.setStyleSheet("background-color: #1e1e28;")

    def set_frames(self, frames: List[FrameInfo]):
        self._frames = frames
        self._compute_stats()
        self.update()

    def _compute_stats(self):
        """Compute per-type statistics."""
        by_type: dict = {"I": [], "P": [], "B": []}
        for f in self._frames:
            if f.frame_type in by_type:
                by_type[f.frame_type].append(f.size_bytes)

        self._stats = {}
        for ft, sizes in by_type.items():
            if sizes:
                self._stats[ft] = {
                    "count": len(sizes),
                    "avg": sum(sizes) / len(sizes),
                    "max": max(sizes),
                    "min": min(sizes),
                    "total": sum(sizes),
                }
            else:
                self._stats[ft] = {"count": 0, "avg": 0, "max": 0, "min": 0, "total": 0}

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()

        if not self._stats:
            painter.setPen(QColor(80, 80, 90))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No data")
            return

        painter.setPen(QColor(180, 180, 190))
        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)
        fm = QFontMetrics(font)

        y = 15
        font_bold = QFont(font)
        font_bold.setBold(True)
        painter.setFont(font_bold)
        painter.drawText(10, y, "Frame Statistics")
        y += 25
        painter.setFont(font)

        # Table header
        painter.setPen(QColor(140, 140, 150))
        painter.drawText(10, y, "Type")
        painter.drawText(50, y, "Count")
        painter.drawText(100, y, "Avg")
        painter.drawText(160, y, "Max")
        y += 5
        painter.setPen(QPen(QColor(60, 60, 70)))
        painter.drawLine(10, y, w - 10, y)
        y += 15

        colors = {"I": COLOR_I, "P": COLOR_P, "B": COLOR_B}
        for ft in ("I", "P", "B"):
            s = self._stats.get(ft, {})
            if s.get("count", 0) == 0:
                continue
            color = colors[ft]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRect(10, y - 8, 8, 8)
            painter.setPen(QColor(200, 200, 210))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawText(22, y, f"{ft}")
            painter.drawText(50, y, f"{s['count']}")
            painter.drawText(100, y, f"{s['avg']/1024:.1f}K")
            painter.drawText(160, y, f"{s['max']/1024:.1f}K")
            y += 18

        y += 15

        # GOP summary
        total_frames = sum(self._stats[ft]["count"] for ft in ("I", "P", "B"))
        i_count = self._stats["I"]["count"]
        if i_count > 0 and total_frames > 0:
            painter.setPen(QColor(140, 140, 150))
            painter.setFont(font_bold)
            painter.drawText(10, y, "GOP Summary")
            y += 18
            painter.setFont(font)
            painter.setPen(QColor(180, 180, 190))

            avg_gop_len = total_frames / i_count
            i_pct = (i_count / total_frames) * 100
            total_size = sum(self._stats[ft]["total"] for ft in ("I", "P", "B"))
            i_size_pct = (self._stats["I"]["total"] / total_size * 100) if total_size > 0 else 0

            painter.drawText(10, y, f"Avg GOP length: {avg_gop_len:.1f} frames")
            y += 16
            painter.drawText(10, y, f"I-frame ratio: {i_pct:.1f}%")
            y += 16
            painter.drawText(10, y, f"I-frame size ratio: {i_size_pct:.1f}%")
            y += 16
            painter.drawText(10, y, f"Total frames: {total_frames}")
            y += 16
            painter.drawText(10, y, f"Total GOPs: {i_count}")
            y += 25

        # Mini histogram
        if y + 100 < h:
            self._draw_histogram(painter, 10, y, w - 20, min(120, h - y - 10))

    def _draw_histogram(self, painter: QPainter, x: int, y: int, w: int, h: int):
        """Draw a mini histogram of frame sizes by type."""
        painter.setPen(QColor(140, 140, 150))
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(x, y + 10, "Size Distribution (KB)")
        y += 18
        h -= 18

        if h < 30:
            return

        all_sizes = [f.size_bytes / 1024 for f in self._frames]
        if not all_sizes:
            return
        max_val = max(all_sizes)
        if max_val == 0:
            return

        n_bins = min(20, w // 8)
        bin_width = max_val / n_bins

        bins_i = [0] * n_bins
        bins_p = [0] * n_bins
        bins_b = [0] * n_bins

        for f in self._frames:
            kb = f.size_bytes / 1024
            bi = min(int(kb / bin_width), n_bins - 1)
            if f.frame_type == "I":
                bins_i[bi] += 1
            elif f.frame_type == "P":
                bins_p[bi] += 1
            else:
                bins_b[bi] += 1

        max_count = max(max(bins_i, default=0), max(bins_p, default=0),
                        max(bins_b, default=0), 1)

        bw = w / n_bins
        for i in range(n_bins):
            bx = x + i * bw
            by = y + h
            total_h_b = (bins_b[i] / max_count) * h
            total_h_p = (bins_p[i] / max_count) * h
            total_h_i = (bins_i[i] / max_count) * h

            if bins_b[i] > 0:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(COLOR_B)
                painter.drawRect(QRectF(bx, by - total_h_b, bw - 1, total_h_b))
                by -= total_h_b
            if bins_p[i] > 0:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(COLOR_P)
                painter.drawRect(QRectF(bx, by - total_h_p, bw - 1, total_h_p))
                by -= total_h_p
            if bins_i[i] > 0:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(COLOR_I)
                painter.drawRect(QRectF(bx, by - total_h_i, bw - 1, total_h_i))

        painter.setPen(QColor(100, 100, 110))
        painter.drawText(x, y + h + 12, "0")
        painter.drawText(int(x + w - 30), y + h + 12, f"{max_val:.0f}KB")


# ---------------------------------------------------------------------------
# GOPPage — main page widget
# ---------------------------------------------------------------------------

class GOPPage(QWidget):
    """
    GOP visualization page.

    Shows frame size bar chart with GOP boundaries and frame size
    distribution statistics. Uses our own parser's frame_types from
    slice header analysis (MP4) or container-level frame type (FLV/TS/MKV).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames: List[FrameInfo] = []
        self._gops: List[List[FrameInfo]] = []
        self._loaded = False

        # Live mode
        self._live_mode = False
        self._live_timer: Optional[QTimer] = None
        self._live_packets_fn = None
        self._live_stream_info_fn = None

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 4, 8, 4)
        toolbar.setSpacing(8)

        self._cb_stats = QCheckBox("Stats Panel")
        self._cb_stats.setChecked(True)
        self._cb_stats.setStyleSheet("font-size: 11px; color: #b0b0b8;")
        self._cb_stats.toggled.connect(self._on_stats_toggled)
        toolbar.addWidget(self._cb_stats)

        toolbar.addSpacing(16)

        self._info_label = QLabel("")
        self._info_label.setStyleSheet("font-size: 11px; color: #a0a0a8;")
        toolbar.addWidget(self._info_label)

        toolbar.addStretch()

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("font-size: 11px; color: #808088;")
        toolbar.addWidget(self._status_label)

        layout.addLayout(toolbar)

        # Main content: chart + stats panel in splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._chart = GOPChartWidget()
        splitter.addWidget(self._chart)

        self._stats_panel = StatsPanel()
        splitter.addWidget(self._stats_panel)

        splitter.setSizes([600, 220])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        layout.addWidget(splitter, 1)

        # Scrollbar
        self._scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self._scrollbar.setMaximum(0)
        self._scrollbar.valueChanged.connect(self._on_scroll)
        layout.addWidget(self._scrollbar)

    def load_packets(self, packets: List[PacketInfo],
                     stream_info: Optional[StreamInfo] = None) -> None:
        """Extract video frames and display GOP visualization."""
        if not packets:
            return

        self._frames = _extract_video_frames(packets, stream_info)
        if not self._frames:
            self._status_label.setText("No video frames found")
            return

        self._gops = _group_into_gops(self._frames)
        self._loaded = True

        # Update chart
        self._chart.set_frames(self._frames, self._gops)

        # Update stats
        self._stats_panel.set_frames(self._frames)

        # Update scrollbar
        visible = self._chart.visible_frames
        self._scrollbar.setMaximum(max(0, len(self._frames) - visible))
        self._scrollbar.setPageStep(visible)

        # Update info
        n_gops = len(self._gops)
        n_frames = len(self._frames)
        self._info_label.setText(f"{n_frames} frames, {n_gops} GOPs")
        self._status_label.setText(
            f"I:{sum(1 for f in self._frames if f.frame_type=='I')} "
            f"P:{sum(1 for f in self._frames if f.frame_type=='P')} "
            f"B:{sum(1 for f in self._frames if f.frame_type=='B')}")

    def start_live_mode(self, packets_fn, stream_info_fn=None):
        """Start RTMP live mode — refresh every 2s."""
        self._live_mode = True
        self._live_packets_fn = packets_fn
        self._live_stream_info_fn = stream_info_fn
        if not self._live_timer:
            self._live_timer = QTimer(self)
            self._live_timer.setInterval(2000)
            self._live_timer.timeout.connect(self._live_update)
        self._live_timer.start()

    def stop_live_mode(self):
        if self._live_timer:
            self._live_timer.stop()
        self._live_mode = False

    def _live_update(self):
        if self._live_packets_fn:
            packets = self._live_packets_fn()
            si = self._live_stream_info_fn() if self._live_stream_info_fn else None
            self.load_packets(packets, si)

    def _on_stats_toggled(self, checked: bool):
        self._stats_panel.setVisible(checked)

    def _on_scroll(self, value: int):
        if not self._frames:
            return
        self._chart.set_scroll_offset(value)

    def clear(self):
        """Reset page."""
        self._frames = []
        self._gops = []
        self._loaded = False
        self._chart.set_frames([], [])
        self._stats_panel.set_frames([])
        self._info_label.setText("")
        self._status_label.setText("")
        self.stop_live_mode()
