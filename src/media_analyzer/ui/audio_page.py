"""Audio waveform analysis page — PCM waveform display with playback."""

import logging
import numpy as np
from typing import Optional, List

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QCheckBox, QScrollBar, QStyle, QSplitter, QComboBox,
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QPainter, QPen, QColor, QMouseEvent, QPaintEvent, QWheelEvent,
    QImage, QPixmap,
)

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except (ImportError, OSError):
    HAS_SOUNDDEVICE = False


# Waveform colors (per channel, dark theme)
CHANNEL_COLORS = [
    QColor(80, 160, 230),   # Blue (L / Ch1)
    QColor(100, 200, 130),  # Green (R / Ch2)
    QColor(220, 150, 80),   # Orange (Ch3)
    QColor(180, 100, 200),  # Purple (Ch4)
    QColor(200, 200, 80),   # Yellow (Ch5)
    QColor(80, 200, 200),   # Cyan (Ch6)
    QColor(200, 120, 120),  # Salmon (Ch7)
    QColor(120, 200, 200),  # Light Cyan (Ch8)
]

# Standard channel layout → per-channel names
# FFmpeg channel_layout strings: mono, stereo, 2.1, 3.0, 4.0, quad, 5.0, 5.1, 6.1, 7.1, etc.
CHANNEL_LAYOUT_NAMES = {
    "mono": ["C"],
    "stereo": ["FL", "FR"],
    "2.1": ["FL", "FR", "LFE"],
    "3.0": ["FL", "FR", "FC"],
    "3.0(back)": ["FL", "FR", "BC"],
    "4.0": ["FL", "FR", "FC", "BC"],
    "quad": ["FL", "FR", "BL", "BR"],
    "quad(side)": ["FL", "FR", "SL", "SR"],
    "5.0": ["FL", "FR", "FC", "BL", "BR"],
    "5.0(side)": ["FL", "FR", "FC", "SL", "SR"],
    "5.1": ["FL", "FR", "FC", "LFE", "BL", "BR"],
    "5.1(side)": ["FL", "FR", "FC", "LFE", "SL", "SR"],
    "6.0": ["FL", "FR", "FC", "BC", "SL", "SR"],
    "6.1": ["FL", "FR", "FC", "LFE", "BC", "SL", "SR"],
    "7.0": ["FL", "FR", "FC", "BL", "BR", "SL", "SR"],
    "7.1": ["FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"],
    "7.1(wide)": ["FL", "FR", "FC", "LFE", "BL", "BR", "FLC", "FRC"],
}

# Full channel name mapping
CHANNEL_FULL_NAMES = {
    "FL": "Front Left",
    "FR": "Front Right",
    "FC": "Front Center",
    "LFE": "LFE (Sub)",
    "BL": "Back Left",
    "BR": "Back Right",
    "SL": "Side Left",
    "SR": "Side Right",
    "BC": "Back Center",
    "FLC": "Front Left of Center",
    "FRC": "Front Right of Center",
    "C": "Center",
}


def get_channel_names(channels: int, layout: str) -> List[str]:
    """Get per-channel display names from channel count and layout string."""
    # Try exact match
    if layout in CHANNEL_LAYOUT_NAMES:
        names = CHANNEL_LAYOUT_NAMES[layout]
        if len(names) == channels:
            return names

    # Try layout without parenthetical suffix
    base_layout = layout.split("(")[0] if "(" in layout else layout
    if base_layout in CHANNEL_LAYOUT_NAMES:
        names = CHANNEL_LAYOUT_NAMES[base_layout]
        if len(names) == channels:
            return names

    # Fallback based on channel count
    if channels == 1:
        return ["Mono"]
    elif channels == 2:
        return ["L", "R"]
    else:
        return [f"Ch{i+1}" for i in range(channels)]


class WaveformWidget(QWidget):
    """
    Custom widget that draws multi-channel PCM waveform with playback cursor.

    Uses peak envelope rendering: for each pixel column, draws a vertical line
    from min to max sample value — same technique as Audacity/Audition.
    """

    position_clicked = Signal(int)  # Emits sample index when user clicks
    view_changed = Signal(int, int)  # Emits (view_start, view_end) after zoom/scroll

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pcm: Optional[np.ndarray] = None  # [samples, channels]
        self._sample_rate: int = 44100
        self._channels: int = 0
        self._channel_visible: List[bool] = []
        self._channel_names: List[str] = []

        # View range (in samples)
        self._view_start: int = 0
        self._view_end: int = 0  # Total samples
        self._total_samples: int = 0

        # Playback cursor (sample index)
        self._cursor_pos: int = 0

        self.setMinimumHeight(100)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #1a1a22;")

    def set_pcm_data(self, pcm: np.ndarray, sample_rate: int, channels: int):
        """Set PCM data for display."""
        self._pcm = pcm
        self._sample_rate = sample_rate
        self._channels = channels
        self._total_samples = len(pcm)
        self._view_start = 0
        self._view_end = self._total_samples
        self._channel_visible = [True] * channels
        self._channel_names = [f"Ch{i+1}" for i in range(channels)]
        self._cursor_pos = 0
        self.update()

    def set_channel_names(self, names: List[str]):
        """Set display names for each channel."""
        self._channel_names = names
        self.update()

    def set_channel_visible(self, channel: int, visible: bool):
        """Show/hide a channel waveform."""
        if 0 <= channel < len(self._channel_visible):
            self._channel_visible[channel] = visible
            self.update()

    def set_cursor_position(self, sample_index: int):
        """Update playback cursor position."""
        self._cursor_pos = sample_index
        self.update()

    def set_view_range(self, start: int, end: int):
        """Set visible sample range (for zoom/scroll)."""
        self._view_start = max(0, start)
        self._view_end = min(self._total_samples, end)
        self.update()

    @property
    def view_start(self) -> int:
        return self._view_start

    @property
    def view_end(self) -> int:
        return self._view_end

    @property
    def total_samples(self) -> int:
        return self._total_samples

    def zoom(self, factor: float, center_pixel: Optional[int] = None):
        """Zoom in/out. factor > 1 = zoom in, < 1 = zoom out."""
        if self._total_samples == 0:
            return
        MARGIN_LEFT = 40
        draw_w = max(self.width() - MARGIN_LEFT, 1)
        view_len = self._view_end - self._view_start
        new_len = max(1000, int(view_len / factor))
        new_len = min(new_len, self._total_samples)

        # Zoom centered on mouse position or center of view
        if center_pixel is not None:
            ratio = center_pixel / draw_w
        else:
            ratio = 0.5
        center_sample = self._view_start + int(view_len * ratio)

        new_start = center_sample - int(new_len * ratio)
        new_start = max(0, new_start)
        new_end = new_start + new_len
        if new_end > self._total_samples:
            new_end = self._total_samples
            new_start = max(0, new_end - new_len)

        self._view_start = new_start
        self._view_end = new_end
        self.update()
        self.view_changed.emit(self._view_start, self._view_end)

    def paintEvent(self, event: QPaintEvent):
        """Draw waveform channels and cursor."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        w = self.width()
        h = self.height()
        # Reserve left margin for Y-axis scale labels
        MARGIN_LEFT = 40

        if self._pcm is None or self._channels == 0 or w <= MARGIN_LEFT:
            # Draw placeholder
            painter.setPen(QColor(80, 80, 90))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No audio data loaded")
            return

        num_channels = self._channels
        ch_height = h // num_channels
        draw_w = w - MARGIN_LEFT  # Waveform drawing width

        # Draw each channel (all channels shown; disabled ones are grayed out)
        view_len = self._view_end - self._view_start
        samples_per_pixel = max(1, view_len // max(draw_w, 1))

        for ch in range(num_channels):
            y_offset = ch * ch_height
            y_center = y_offset + ch_height // 2
            half_h = (ch_height - 6) // 2  # Leave gap between channels

            is_enabled = self._channel_visible[ch] if ch < len(self._channel_visible) else True

            # Choose color
            if is_enabled:
                color = CHANNEL_COLORS[ch % len(CHANNEL_COLORS)]
            else:
                color = QColor(60, 60, 70)  # Grayed out

            # Draw Y-axis scale (left margin)
            painter.setPen(QColor(90, 90, 100))
            painter.drawLine(MARGIN_LEFT - 1, y_offset + 2, MARGIN_LEFT - 1, y_offset + ch_height - 2)
            # Scale labels: +1.0, 0, -1.0 (float32 normalized range)
            painter.setFont(painter.font())
            scale_color = QColor(100, 100, 110)
            painter.setPen(scale_color)
            painter.drawText(2, y_offset + 12, "+1.0")
            painter.drawText(2, y_center + 4, " 0")
            painter.drawText(2, y_offset + ch_height - 4, "-1.0")
            # Tick marks
            painter.setPen(QPen(QColor(60, 60, 70), 1, Qt.PenStyle.DotLine))
            painter.drawLine(MARGIN_LEFT, y_offset + 2 + int(half_h * 0.5),
                             w, y_offset + 2 + int(half_h * 0.5))
            painter.drawLine(MARGIN_LEFT, y_offset + ch_height - 2 - int(half_h * 0.5),
                             w, y_offset + ch_height - 2 - int(half_h * 0.5))

            # Draw center line (zero axis)
            painter.setPen(QPen(QColor(50, 50, 60)))
            painter.drawLine(MARGIN_LEFT, y_center, w, y_center)

            # Draw channel label
            ch_name = self._channel_names[ch] if ch < len(self._channel_names) else f"Ch{ch+1}"
            label_color = QColor(140, 140, 150) if is_enabled else QColor(80, 80, 90)
            painter.setPen(label_color)
            painter.drawText(MARGIN_LEFT + 4, y_offset + 14, ch_name)
            if not is_enabled:
                painter.drawText(MARGIN_LEFT + 4, y_offset + 26, "(muted)")

            # Draw waveform (peak envelope)
            pen = QPen(color)
            pen.setWidth(1)
            painter.setPen(pen)

            pcm_ch = self._pcm[self._view_start:self._view_end, ch]
            for px in range(draw_w):
                s_start = px * view_len // draw_w
                s_end = min(s_start + samples_per_pixel, len(pcm_ch))
                if s_start >= s_end:
                    continue
                chunk = pcm_ch[s_start:s_end]
                mn = float(chunk.min())
                mx = float(chunk.max())
                y1 = y_center - int(mx * half_h)
                y2 = y_center - int(mn * half_h)
                if y1 == y2:
                    y2 = y1 + 1
                painter.drawLine(MARGIN_LEFT + px, y1, MARGIN_LEFT + px, y2)

            # Draw separator line between channels
            if ch < num_channels - 1:
                painter.setPen(QPen(QColor(50, 50, 60)))
                painter.drawLine(0, y_offset + ch_height, w, y_offset + ch_height)

        # Draw playback cursor
        if self._view_start <= self._cursor_pos <= self._view_end and view_len > 0:
            cursor_x = MARGIN_LEFT + int((self._cursor_pos - self._view_start) * draw_w / view_len)
            painter.setPen(QPen(QColor(255, 80, 80), 1))
            painter.drawLine(cursor_x, 0, cursor_x, h)

        # Draw time axis labels at bottom of each channel area
        painter.setPen(QColor(120, 120, 130))
        step = max(draw_w // 8, 80)
        for i in range(0, draw_w, step):
            sample = self._view_start + i * view_len // max(draw_w, 1)
            t = sample / self._sample_rate
            if t >= 60:
                label = f"{int(t)//60}:{int(t)%60:02d}"
            else:
                label = f"{t:.2f}s"
            painter.drawText(MARGIN_LEFT + i + 2, h - 4, label)

    def mousePressEvent(self, event: QMouseEvent):
        """Click to set playback position."""
        if event.button() == Qt.MouseButton.LeftButton and self._total_samples > 0:
            MARGIN_LEFT = 40
            x = event.position().x() - MARGIN_LEFT
            draw_w = self.width() - MARGIN_LEFT
            if x < 0 or draw_w <= 0:
                return
            view_len = self._view_end - self._view_start
            ratio = x / draw_w
            sample = self._view_start + int(view_len * ratio)
            sample = max(0, min(sample, self._total_samples - 1))
            self._cursor_pos = sample
            self.position_clicked.emit(sample)
            self.update()

    def wheelEvent(self, event: QWheelEvent):
        """Scroll wheel to zoom."""
        if self._total_samples == 0:
            return
        MARGIN_LEFT = 40
        factor = 1.3 if event.angleDelta().y() > 0 else 1 / 1.3
        center_px = int(event.position().x()) - MARGIN_LEFT
        draw_w = self.width() - MARGIN_LEFT
        if center_px < 0:
            center_px = 0
        # Convert pixel to ratio within draw area
        self.zoom(factor, center_px if draw_w > 0 else None)
        event.accept()


# Colormap for spectrogram — Adobe Audition style:
# Black → Deep Purple → Orange/Red → Bright Yellow → White
def _make_colormap() -> List[QColor]:
    """Generate a 256-entry colormap matching Adobe Audition spectral display.
    Emphasizes warm tones (orange/yellow) for mid-high energy."""
    cmap = []
    for i in range(256):
        t = i / 255.0
        if t < 0.1:
            # Black → very dark purple
            s = t / 0.1
            r, g, b = int(10 * s), 0, int(20 * s)
        elif t < 0.25:
            # Very dark purple → deep indigo/purple
            s = (t - 0.1) / 0.15
            r, g, b = int(10 + 40 * s), 0, int(20 + 80 * s)
        elif t < 0.4:
            # Deep purple → dark red/orange (warm transition, drop blue fast)
            s = (t - 0.25) / 0.15
            r, g, b = int(50 + 150 * s), int(20 * s), int(100 - 80 * s)
        elif t < 0.6:
            # Dark red/orange → bright orange
            s = (t - 0.4) / 0.2
            r, g, b = int(200 + 55 * s), int(20 + 100 * s), int(20 - 20 * s)
        elif t < 0.8:
            # Bright orange → yellow
            s = (t - 0.6) / 0.2
            r, g, b = 255, int(120 + 135 * s), 0
        else:
            # Yellow → bright yellow/white
            s = (t - 0.8) / 0.2
            r, g, b = 255, 255, int(200 * s)
        cmap.append(QColor(min(255, max(0, r)), min(255, max(0, g)), min(255, max(0, b))))
    return cmap


SPECTROGRAM_COLORMAP = _make_colormap()

# Pre-computed numpy RGB array for fast colormap lookup (256 × 3, uint8)
SPECTROGRAM_CMAP_ARRAY = np.array(
    [[c.red(), c.green(), c.blue()] for c in SPECTROGRAM_COLORMAP],
    dtype=np.uint8
)


def _apply_log_scale_func(spec_db: np.ndarray, sample_rate: int) -> np.ndarray:
    """Resample frequency axis to logarithmic scale (standalone function for threading).
    Low frequencies get more resolution, high frequencies compressed.
    spec_db shape: (n_freq, n_frames). Returns (n_log_bins, n_frames)."""
    n_freq, n_frames = spec_db.shape
    n_log_bins = 256  # Output frequency bins

    max_freq = sample_rate / 2
    min_freq = 20.0  # Start at 20Hz (avoid log(0))
    log_freqs = np.logspace(np.log10(min_freq), np.log10(max_freq),
                            n_log_bins + 1)

    # Map log bins to FFT bins
    fft_freqs = np.linspace(0, max_freq, n_freq)
    log_spec = np.zeros((n_log_bins, n_frames), dtype=np.float32)

    for m in range(n_log_bins):
        f_low = log_freqs[m]
        f_high = log_freqs[m + 1]
        idx_low = int(np.searchsorted(fft_freqs, f_low))
        idx_high = int(np.searchsorted(fft_freqs, f_high))
        if idx_high > idx_low:
            log_spec[m] = spec_db[idx_low:idx_high].max(axis=0)
        elif idx_low < n_freq:
            log_spec[m] = spec_db[min(idx_low, n_freq - 1)]

    return log_spec


def _compute_spectrogram_data(pcm: np.ndarray, sample_rate: int, channels: int,
                              fft_size: int, hop_size: int, mode: str) -> List[np.ndarray]:
    """Compute STFT magnitude spectrogram for each channel (module-level for threading).

    Returns a list of uint8 numpy arrays (one per channel), shape (n_freq_bins, n_frames),
    with values 0-255 representing normalized dB magnitude.
    """
    if pcm is None or len(pcm) == 0:
        return []

    n_fft = fft_size
    hop = hop_size
    window = np.hanning(n_fft).astype(np.float32)

    spec_data = []

    for ch in range(channels):
        signal = pcm[:, ch]
        n_frames = max(1, (len(signal) - n_fft) // hop + 1)

        # Vectorized STFT: create strided frame view (no copy)
        shape = (n_frames, n_fft)
        strides = (signal.strides[0] * hop, signal.strides[0])
        # Ensure we don't read past buffer
        if (n_frames - 1) * hop + n_fft > len(signal):
            pad_len = (n_frames - 1) * hop + n_fft - len(signal)
            signal = np.pad(signal, (0, pad_len))
        frames = np.lib.stride_tricks.as_strided(signal, shape=shape, strides=strides)

        # Apply window and compute FFT in batch
        windowed = frames * window[np.newaxis, :]
        fft_result = np.fft.rfft(windowed, axis=1)
        spec = np.abs(fft_result).T  # Shape: (n_fft//2+1, n_frames)

        # Convert to dB
        spec_db = 20 * np.log10(spec + 1e-10)

        # Apply frequency scale
        if mode == "log":
            spec_db = _apply_log_scale_func(spec_db, sample_rate)

        # Normalize to 0-255 for colormap (80dB dynamic range)
        vmin = spec_db.max() - 80
        vmax = spec_db.max()
        spec_norm = np.clip((spec_db - vmin) / (vmax - vmin), 0, 1)
        spec_data.append((spec_norm * 255).astype(np.uint8))

    return spec_data


class SpectrogramWidget(QWidget):
    """
    Spectrogram display widget.

    Computes STFT of PCM data and renders as a time-frequency heatmap.
    Supports Linear (log-frequency) and Mel frequency scales.
    """

    position_clicked = Signal(int)  # Emits sample index when clicked
    view_changed = Signal(int, int)  # Emits (view_start, view_end) after zoom

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pcm: Optional[np.ndarray] = None
        self._sample_rate: int = 44100
        self._channels: int = 0
        self._channel_visible: List[bool] = []
        self._total_samples: int = 0

        # View range (synced with waveform)
        self._view_start: int = 0
        self._view_end: int = 0

        # Spectrogram image cache (per channel)
        self._spec_image: Optional[QImage] = None
        self._spec_mode: str = "log"  # "log" or "linear"
        self._spec_data: Optional[List[np.ndarray]] = None  # Per-channel: [freq_bins, time_frames]

        # Playback cursor
        self._cursor_pos: int = 0

        # STFT params
        self._fft_size: int = 2048
        self._hop_size: int = 512

        self.setMinimumHeight(80)
        self.setStyleSheet("background-color: #0a0a12;")

    def set_pcm_data(self, pcm: np.ndarray, sample_rate: int, channels: int):
        """Set PCM data and start background spectrogram computation."""
        self._pcm = pcm
        self._sample_rate = sample_rate
        self._channels = channels
        self._channel_visible = [True] * channels
        self._total_samples = len(pcm)
        self._view_start = 0
        self._view_end = self._total_samples
        self._spec_image = None
        self._spec_data = None

        if self._total_samples > 0:
            self._start_compute()
        self.update()

    def _start_compute(self):
        """Start spectrogram computation in background thread."""
        if hasattr(self, '_compute_thread') and self._compute_thread is not None:
            if self._compute_thread.isRunning():
                # Disconnect to avoid stale signals, wait for completion
                try:
                    self._compute_thread.finished.disconnect(self._on_compute_done)
                except RuntimeError:
                    pass
                self._compute_thread.wait()

        from PySide6.QtCore import QThread

        class _SpecWorker(QThread):
            finished = Signal(list)

            def __init__(self, pcm, sr, channels, fft_size, hop_size, mode, parent=None):
                super().__init__(parent)
                self._pcm = pcm
                self._sr = sr
                self._channels = channels
                self._fft_size = fft_size
                self._hop_size = hop_size
                self._mode = mode

            def run(self):
                result = _compute_spectrogram_data(
                    self._pcm, self._sr, self._channels,
                    self._fft_size, self._hop_size, self._mode)
                self.finished.emit(result)

        self._compute_thread = _SpecWorker(
            self._pcm, self._sample_rate, self._channels,
            self._fft_size, self._hop_size, self._spec_mode, self)
        self._compute_thread.finished.connect(self._on_compute_done)
        self._compute_thread.start()

    def _on_compute_done(self, result: list):
        """Handle background spectrogram computation result."""
        self._spec_data = result
        self._spec_image = None
        self.update()

    def set_channel_visible(self, channel: int, visible: bool):
        """Show/hide a channel spectrogram (gray out when disabled)."""
        if 0 <= channel < len(self._channel_visible):
            self._channel_visible[channel] = visible
            self._spec_image = None  # Invalidate cache
            self.update()

    def set_mode(self, mode: str):
        """Set spectrogram mode: 'log' or 'linear'."""
        if mode != self._spec_mode:
            self._spec_mode = mode
            self._spec_image = None
            if self._pcm is not None and len(self._pcm) > 0:
                self._start_compute()
            self.update()

    def set_view_range(self, start: int, end: int):
        """Set visible sample range (synced with waveform)."""
        self._view_start = max(0, start)
        self._view_end = min(self._total_samples, end)
        self._spec_image = None  # Invalidate cached image
        self.update()

    def set_cursor_position(self, sample_index: int):
        """Update playback cursor."""
        self._cursor_pos = sample_index
        self.update()

    def resizeEvent(self, event):
        """Invalidate cached image on resize so it redraws at new size."""
        self._spec_image = None
        super().resizeEvent(event)

    def paintEvent(self, event: QPaintEvent):
        """Draw per-channel spectrogram images with cursor."""
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        MARGIN_LEFT = 40

        if not self._spec_data or w <= MARGIN_LEFT or self._channels == 0:
            painter.setPen(QColor(80, 80, 90))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No spectrogram data")
            return

        draw_w = w - MARGIN_LEFT
        num_channels = self._channels
        ch_height = h // num_channels

        # Get frame range for current view
        n_freq, n_frames = self._spec_data[0].shape
        frame_start = int(self._view_start / self._hop_size)
        frame_end = int(self._view_end / self._hop_size)
        frame_start = max(0, min(frame_start, n_frames - 1))
        frame_end = max(frame_start + 1, min(frame_end, n_frames))
        view_frames = frame_end - frame_start

        # Rebuild cached images if needed
        if self._spec_image is None:
            self._spec_image = []
            for ch in range(num_channels):
                spec_ch = self._spec_data[ch]
                n_f = spec_ch.shape[0]
                spec_view = spec_ch[:, frame_start:frame_end]

                is_enabled = self._channel_visible[ch] if ch < len(self._channel_visible) else True

                # Resample spec_view to draw_w columns using numpy indexing
                frame_indices = np.minimum(
                    np.arange(draw_w) * view_frames // draw_w,
                    spec_view.shape[1] - 1
                )
                # Shape: (n_f, draw_w) — resampled spectrogram for display
                resampled = spec_view[:, frame_indices]
                # Flip frequency axis (high freq at top row)
                resampled = resampled[::-1, :]

                # Map values to RGB using colormap lookup (fully vectorized)
                if is_enabled:
                    rgb = SPECTROGRAM_CMAP_ARRAY[resampled]  # (n_f, draw_w, 3)
                else:
                    gray = (resampled // 3).astype(np.uint8)
                    rgb = np.stack([gray, gray, gray], axis=2)  # (n_f, draw_w, 3)

                # Build BGRA buffer for QImage (Format_ARGB32 = BGRA in memory on little-endian)
                img_array = np.zeros((n_f, draw_w, 4), dtype=np.uint8)
                img_array[:, :, 0] = rgb[:, :, 2]  # B
                img_array[:, :, 1] = rgb[:, :, 1]  # G
                img_array[:, :, 2] = rgb[:, :, 0]  # R
                img_array[:, :, 3] = 255            # A

                # Ensure contiguous memory for QImage
                img_array = np.ascontiguousarray(img_array)

                # Create QImage from buffer
                img = QImage(img_array.data, draw_w, n_f,
                             draw_w * 4, QImage.Format.Format_ARGB32)
                img._numpy_data = img_array  # Keep reference

                # Scale to channel height
                scaled = img.scaled(draw_w, ch_height,
                                    Qt.AspectRatioMode.IgnoreAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
                self._spec_image.append(scaled)

        # Draw each channel's spectrogram
        for ch in range(num_channels):
            y_offset = ch * ch_height
            painter.drawImage(MARGIN_LEFT, y_offset, self._spec_image[ch])

            # Draw separator
            if ch < num_channels - 1:
                painter.setPen(QPen(QColor(60, 60, 70)))
                painter.drawLine(0, y_offset + ch_height, w, y_offset + ch_height)

        # Draw Y-axis frequency labels (on first channel area)
        import math
        painter.setPen(QColor(100, 100, 110))
        max_freq = self._sample_rate // 2

        if self._spec_mode == "log":
            min_freq = 20
            log_min = math.log10(min_freq)
            log_max = math.log10(max_freq)
            log_range = log_max - log_min
            nice_freqs = [50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]
            freq_labels = []
            for f in nice_freqs:
                if f < min_freq or f > max_freq:
                    continue
                ratio = 1.0 - (math.log10(f) - log_min) / log_range
                label = f"{f//1000}k" if f >= 1000 else str(f)
                freq_labels.append((label, ratio))
        else:
            freq_labels = []
            step = 5000 if max_freq >= 20000 else 2000 if max_freq >= 8000 else 1000
            for f in range(0, max_freq + 1, step):
                ratio = 1.0 - f / max_freq
                label = f"{f//1000}k" if f >= 1000 else str(f)
                freq_labels.append((label, ratio))

        # Draw labels for each channel
        for ch in range(num_channels):
            y_offset = ch * ch_height
            for label, ratio in freq_labels:
                y = y_offset + int(ratio * (ch_height - 1))
                painter.setPen(QColor(100, 100, 110))
                painter.drawText(2, max(y + 10, y_offset + 10), label)
                painter.setPen(QPen(QColor(40, 40, 50), 1, Qt.PenStyle.DotLine))
                painter.drawLine(MARGIN_LEFT, y, w, y)

        # Draw mode label
        mode_label = "Log" if self._spec_mode == "log" else "Linear"
        painter.setPen(QColor(120, 120, 130))
        painter.drawText(MARGIN_LEFT + 4, 14, f"Spectrogram ({mode_label})")

        # Draw playback cursor
        view_len = self._view_end - self._view_start
        if view_len > 0 and self._view_start <= self._cursor_pos <= self._view_end:
            cursor_x = MARGIN_LEFT + int((self._cursor_pos - self._view_start) * draw_w / view_len)
            painter.setPen(QPen(QColor(255, 80, 80), 1))
            painter.drawLine(cursor_x, 0, cursor_x, h)

    def mousePressEvent(self, event: QMouseEvent):
        """Click to set position."""
        if event.button() == Qt.MouseButton.LeftButton and self._total_samples > 0:
            MARGIN_LEFT = 40
            x = event.position().x() - MARGIN_LEFT
            draw_w = self.width() - MARGIN_LEFT
            if x < 0 or draw_w <= 0:
                return
            view_len = self._view_end - self._view_start
            sample = self._view_start + int(view_len * x / draw_w)
            sample = max(0, min(sample, self._total_samples - 1))
            self._cursor_pos = sample
            self.position_clicked.emit(sample)
            self.update()

    def wheelEvent(self, event: QWheelEvent):
        """Scroll wheel to zoom time axis."""
        if self._total_samples == 0:
            return
        MARGIN_LEFT = 40
        factor = 1.3 if event.angleDelta().y() > 0 else 1 / 1.3
        center_px = int(event.position().x()) - MARGIN_LEFT
        draw_w = max(self.width() - MARGIN_LEFT, 1)
        if center_px < 0:
            center_px = 0

        # Compute new view range
        view_len = self._view_end - self._view_start
        new_len = max(1000, int(view_len / factor))
        new_len = min(new_len, self._total_samples)

        ratio = center_px / draw_w if draw_w > 0 else 0.5
        center_sample = self._view_start + int(view_len * ratio)
        new_start = center_sample - int(new_len * ratio)
        new_start = max(0, new_start)
        new_end = new_start + new_len
        if new_end > self._total_samples:
            new_end = self._total_samples
            new_start = max(0, new_end - new_len)

        self._view_start = new_start
        self._view_end = new_end
        self._spec_image = None
        self.update()
        self.view_changed.emit(self._view_start, self._view_end)
        event.accept()


class AudioPage(QWidget):
    """
    Audio waveform analysis page.

    - Decodes audio via FFmpeg to PCM float32
    - Displays multi-channel waveform (peak envelope)
    - Playback via sounddevice with position cursor sync
    - Channel enable/disable toggles
    - Zoom (scroll wheel) and click-to-seek
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pcm: Optional[np.ndarray] = None
        self._sample_rate: int = 44100
        self._channels: int = 0
        self._file_path: Optional[str] = None
        self._loaded_path: Optional[str] = None

        # Playback state
        self._is_playing = False
        self._play_position: int = 0  # Current sample index
        self._stream = None  # sounddevice stream

        # Workers
        self._decode_worker = None

        self._setup_ui()

        # Timer for cursor sync during playback
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(30)  # ~33fps
        self._cursor_timer.timeout.connect(self._update_cursor)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Toolbar ---
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 4, 8, 4)
        toolbar.setSpacing(8)

        # Play/Stop buttons
        self._btn_play = QPushButton()
        self._btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self._btn_play.setFixedSize(28, 28)
        self._btn_play.setToolTip("Play / Pause")
        self._btn_play.clicked.connect(self._toggle_play)
        toolbar.addWidget(self._btn_play)

        self._btn_stop = QPushButton()
        self._btn_stop.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self._btn_stop.setFixedSize(28, 28)
        self._btn_stop.setToolTip("Stop")
        self._btn_stop.clicked.connect(self._stop)
        toolbar.addWidget(self._btn_stop)

        toolbar.addSpacing(8)

        # Time display
        self._time_label = QLabel("00:00.000 / 00:00.000")
        self._time_label.setStyleSheet("font-family: Consolas; font-size: 11px; color: #c0c0c8;")
        self._time_label.setFixedWidth(180)
        toolbar.addWidget(self._time_label)

        toolbar.addSpacing(16)

        # Channel toggles (populated when audio is loaded)
        self._ch_label = QLabel("Channels:")
        self._ch_label.setStyleSheet("font-size: 11px; color: #a0a0a8;")
        toolbar.addWidget(self._ch_label)
        self._ch_checkboxes: List[QCheckBox] = []
        self._ch_layout = QHBoxLayout()
        self._ch_layout.setSpacing(8)
        toolbar.addLayout(self._ch_layout)

        toolbar.addStretch()

        # Spectrogram mode selector
        spec_label = QLabel("Spectrogram:")
        spec_label.setStyleSheet("font-size: 11px; color: #a0a0a8;")
        toolbar.addWidget(spec_label)
        self._spec_mode_combo = QComboBox()
        self._spec_mode_combo.addItems(["Log Scale", "Linear Scale"])
        self._spec_mode_combo.setFixedWidth(110)
        self._spec_mode_combo.setStyleSheet("font-size: 11px;")
        self._spec_mode_combo.currentIndexChanged.connect(self._on_spec_mode_changed)
        toolbar.addWidget(self._spec_mode_combo)

        toolbar.addSpacing(12)

        # Status
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("font-size: 11px; color: #808088;")
        toolbar.addWidget(self._status_label)

        layout.addLayout(toolbar)

        # --- Main content: splitter with waveform (top) + spectrogram (bottom) ---
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Top: Waveform
        self._waveform = WaveformWidget()
        self._waveform.position_clicked.connect(self._on_position_clicked)
        self._waveform.view_changed.connect(self._on_waveform_view_changed)
        splitter.addWidget(self._waveform)

        # Bottom: Spectrogram
        self._spectrogram = SpectrogramWidget()
        self._spectrogram.position_clicked.connect(self._on_position_clicked)
        self._spectrogram.view_changed.connect(self._on_spectrogram_view_changed)
        splitter.addWidget(self._spectrogram)

        splitter.setSizes([150, 400])
        layout.addWidget(splitter, 1)

        # --- Scrollbar ---
        self._scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self._scrollbar.setMaximum(0)
        self._scrollbar.valueChanged.connect(self._on_scroll)
        layout.addWidget(self._scrollbar)

    def load_file(self, file_path: str):
        """Decode audio from file and display waveform."""
        if not file_path:
            return
        if file_path == self._loaded_path:
            return  # Already loaded

        self._stop()
        self._file_path = file_path
        self._status_label.setText("Decoding audio...")

        # Start background decode
        from media_analyzer.workers.audio_decode_worker import AudioDecodeWorker
        if self._decode_worker and self._decode_worker.isRunning():
            self._decode_worker.stop()
            self._decode_worker.wait(3000)

        self._decode_worker = AudioDecodeWorker(file_path, sample_rate=44100)
        self._decode_worker.finished.connect(self._on_decode_finished)
        self._decode_worker.error.connect(self._on_decode_error)
        self._decode_worker.progress.connect(self._on_decode_progress)
        self._decode_worker.start()

    def _on_decode_finished(self, pcm: np.ndarray, sample_rate: int, channels: int,
                            channel_layout: str = ""):
        """Handle decoded PCM data."""
        self._pcm = pcm
        self._sample_rate = sample_rate
        self._channels = channels
        self._play_position = 0
        self._loaded_path = self._file_path

        # Get channel names from layout
        self._channel_names = get_channel_names(channels, channel_layout)

        # Update waveform with channel names
        self._waveform.set_pcm_data(pcm, sample_rate, channels)
        self._waveform.set_channel_names(self._channel_names)

        # Update spectrogram
        self._spectrogram.set_pcm_data(pcm, sample_rate, channels)

        # Update channel checkboxes
        self._clear_channel_checkboxes()
        for ch in range(channels):
            name = self._channel_names[ch] if ch < len(self._channel_names) else f"Ch{ch+1}"
            cb = QCheckBox(name)
            cb.setChecked(True)
            cb.setStyleSheet("font-size: 11px; color: #b0b0b8;")
            cb.toggled.connect(lambda checked, c=ch: self._on_channel_toggled(c, checked))
            self._ch_checkboxes.append(cb)
            self._ch_layout.addWidget(cb)

        # Update time label
        duration = len(pcm) / sample_rate
        self._update_time_display(0, duration)

        # Update scrollbar
        self._scrollbar.setMaximum(len(pcm))
        self._scrollbar.setPageStep(len(pcm))

        self._status_label.setText(
            f"{channels}ch, {sample_rate}Hz, {duration:.1f}s, "
            f"{len(pcm):,} samples")

    def _on_decode_error(self, msg: str):
        """Handle decode error."""
        self._status_label.setText(f"Error: {msg}")
        logger.error(f"Audio decode error: {msg}")

    def _on_decode_progress(self, msg: str):
        self._status_label.setText(msg)

    def _toggle_play(self):
        """Toggle play/pause."""
        if self._is_playing:
            self._pause()
        else:
            self._play()

    def _play(self):
        """Start playback from current cursor position."""
        if not HAS_SOUNDDEVICE:
            self._status_label.setText("sounddevice not available — cannot play")
            return
        if self._pcm is None:
            return

        self._is_playing = True
        self._btn_play.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))

        # Create output stream
        try:
            self._stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype='float32',
                callback=self._audio_callback,
                finished_callback=self._on_playback_finished,
            )
            self._stream.start()
            self._cursor_timer.start()
        except Exception as e:
            self._status_label.setText(f"Playback error: {e}")
            self._is_playing = False
            self._btn_play.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def _pause(self):
        """Pause playback."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._is_playing = False
        self._cursor_timer.stop()
        self._btn_play.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def _stop(self):
        """Stop playback and reset position."""
        self._pause()
        self._play_position = 0
        self._waveform.set_cursor_position(0)
        if self._pcm is not None:
            self._update_time_display(0, len(self._pcm) / self._sample_rate)

    def _audio_callback(self, outdata, frames, time_info, status):
        """sounddevice callback — fill output buffer with PCM data."""
        if self._pcm is None:
            outdata.fill(0)
            raise sd.CallbackStop()

        end = self._play_position + frames
        total = len(self._pcm)

        if self._play_position >= total:
            outdata.fill(0)
            raise sd.CallbackStop()

        if end > total:
            valid = total - self._play_position
            outdata[:valid] = self._pcm[self._play_position:total]
            outdata[valid:] = 0
            self._play_position = total
            raise sd.CallbackStop()

        # Apply channel muting
        data = self._pcm[self._play_position:end].copy()
        for ch in range(self._channels):
            if ch < len(self._waveform._channel_visible) and not self._waveform._channel_visible[ch]:
                data[:, ch] = 0

        outdata[:] = data
        self._play_position = end

    def _on_playback_finished(self):
        """Called when playback reaches end."""
        # Schedule UI update in main thread
        QTimer.singleShot(0, self._playback_ended)

    def _playback_ended(self):
        self._is_playing = False
        self._cursor_timer.stop()
        self._btn_play.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def _update_cursor(self):
        """Timer callback — sync cursor position with playback."""
        self._waveform.set_cursor_position(self._play_position)
        self._spectrogram.set_cursor_position(self._play_position)
        if self._pcm is not None:
            pos_s = self._play_position / self._sample_rate
            dur_s = len(self._pcm) / self._sample_rate
            self._update_time_display(pos_s, dur_s)

    def _on_position_clicked(self, sample: int):
        """Handle click on waveform or spectrogram — seek to position."""
        self._play_position = sample
        self._waveform.set_cursor_position(sample)
        self._spectrogram.set_cursor_position(sample)
        if self._pcm is not None:
            pos_s = sample / self._sample_rate
            dur_s = len(self._pcm) / self._sample_rate
            self._update_time_display(pos_s, dur_s)

    def _on_channel_toggled(self, channel: int, visible: bool):
        """Handle channel checkbox toggle."""
        self._waveform.set_channel_visible(channel, visible)
        self._spectrogram.set_channel_visible(channel, visible)

    def _on_spec_mode_changed(self, index: int):
        """Handle spectrogram mode change."""
        mode = "log" if index == 0 else "linear"
        self._spectrogram.set_mode(mode)

    def _on_waveform_view_changed(self, start: int, end: int):
        """Sync spectrogram view range when waveform zooms."""
        self._spectrogram.set_view_range(start, end)

    def _on_spectrogram_view_changed(self, start: int, end: int):
        """Sync waveform view range when spectrogram zooms."""
        self._waveform.set_view_range(start, end)

    def _on_scroll(self, value: int):
        """Handle scrollbar movement."""
        if self._pcm is None:
            return
        view_len = self._waveform.view_end - self._waveform.view_start
        new_start = value
        new_end = new_start + view_len
        if new_end > self._waveform.total_samples:
            new_end = self._waveform.total_samples
            new_start = max(0, new_end - view_len)
        self._waveform.set_view_range(new_start, new_end)
        self._spectrogram.set_view_range(new_start, new_end)

    def _update_time_display(self, pos_s: float, dur_s: float):
        """Update time label."""
        def fmt(s):
            m = int(s) // 60
            sec = s - m * 60
            return f"{m:02d}:{sec:06.3f}"
        self._time_label.setText(f"{fmt(pos_s)} / {fmt(dur_s)}")

    def _clear_channel_checkboxes(self):
        """Remove all channel checkboxes."""
        for cb in self._ch_checkboxes:
            self._ch_layout.removeWidget(cb)
            cb.deleteLater()
        self._ch_checkboxes.clear()

    def clear(self):
        """Reset the page."""
        self._stop()
        self._pcm = None
        self._channels = 0
        self._loaded_path = None
        self._clear_channel_checkboxes()
        self._waveform.set_pcm_data(np.zeros((0, 1), dtype=np.float32), 44100, 0)
        self._status_label.setText("")
        self._time_label.setText("00:00.000 / 00:00.000")

    def cleanup(self):
        """Release resources (for app exit)."""
        self._stop()
        if self._decode_worker and self._decode_worker.isRunning():
            self._decode_worker.stop()
            self._decode_worker.wait(3000)
        # Wait for spectrogram background thread
        spec = self._spectrogram
        if hasattr(spec, '_compute_thread') and spec._compute_thread is not None:
            if spec._compute_thread.isRunning():
                spec._compute_thread.wait(5000)
