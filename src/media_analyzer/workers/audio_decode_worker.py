"""Audio decode worker — runs FFmpeg to decode audio to PCM."""

import logging
import subprocess
import shutil
import numpy as np
from typing import Optional

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class AudioDecodeWorker(QThread):
    """
    Background thread that decodes audio from any format to PCM float32
    using FFmpeg subprocess.

    Emits finished(numpy_array, sample_rate, num_channels, channel_layout) on success.
    """

    finished = Signal(object, int, int, str)  # (np.ndarray, sample_rate, channels, channel_layout)
    error = Signal(str)
    progress = Signal(str)  # Status message

    def __init__(self, file_path: str, sample_rate: int = 44100,
                 max_duration: Optional[float] = None, parent=None):
        super().__init__(parent)
        self._file_path = file_path
        self._sample_rate = sample_rate
        self._max_duration = max_duration  # Limit decode length (seconds)
        self._running = True

    def run(self):
        # Find ffmpeg
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.error.emit("FFmpeg not found. Install FFmpeg and add to PATH.")
            return

        try:
            # Step 1: Probe channel count and layout
            channels, channel_layout = self._probe_audio_info(ffmpeg)
            if not self._running:
                return
            if channels <= 0:
                channels = 2  # Default to stereo

            self.progress.emit(f"Decoding audio ({channels}ch, {self._sample_rate}Hz)...")

            # Step 2: Decode to raw PCM float32
            cmd = [
                ffmpeg, "-i", self._file_path,
                "-vn",  # No video
                "-f", "f32le",  # Raw float32 little-endian
                "-acodec", "pcm_f32le",
                "-ar", str(self._sample_rate),
                "-ac", str(channels),
            ]
            if self._max_duration:
                cmd.extend(["-t", str(self._max_duration)])
            cmd.extend(["-loglevel", "error", "pipe:1"])

            logger.info(f"FFmpeg decode: {' '.join(cmd)}")

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )

            stdout_data, stderr_data = proc.communicate()

            if not self._running:
                return

            if proc.returncode != 0:
                err_msg = stderr_data.decode("utf-8", errors="replace").strip()
                self.error.emit(f"FFmpeg decode failed: {err_msg[:200]}")
                return

            if len(stdout_data) == 0:
                self.error.emit("No audio data decoded (file may have no audio track)")
                return

            # Step 3: Convert to numpy array
            pcm = np.frombuffer(stdout_data, dtype=np.float32)
            pcm = pcm.reshape(-1, channels)

            duration_s = len(pcm) / self._sample_rate
            logger.info(f"Audio decoded: {len(pcm)} samples, {channels}ch, "
                        f"{duration_s:.1f}s, layout={channel_layout}")

            self.finished.emit(pcm, self._sample_rate, channels, channel_layout)

        except Exception as e:
            logger.error(f"Audio decode error: {e}", exc_info=True)
            self.error.emit(f"Decode error: {str(e)}")

    def _probe_audio_info(self, ffmpeg: str) -> tuple:
        """Probe audio channel count and channel_layout using ffprobe.
        Returns (channels: int, channel_layout: str)."""
        ffprobe = ffmpeg.replace("ffmpeg", "ffprobe")
        if not shutil.which(ffprobe):
            ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return 2, ""

        channels = 2
        channel_layout = ""

        try:
            cmd = [
                ffprobe, "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=channels,channel_layout",
                "-of", "csv=p=0",
                self._file_path,
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if result.returncode == 0 and result.stdout.strip():
                # Output format: "channels,channel_layout" e.g. "2,stereo" or "6,5.1(side)"
                parts = result.stdout.strip().split(",", 1)
                if parts[0].isdigit():
                    channels = int(parts[0])
                if len(parts) > 1:
                    channel_layout = parts[1].strip()
        except Exception:
            pass

        return channels, channel_layout

    def stop(self):
        self._running = False
