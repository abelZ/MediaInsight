"""Audio decode worker — decodes audio to PCM using PyAV (FFmpeg binding)."""

import logging
import numpy as np
from typing import Optional

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class AudioDecodeWorker(QThread):
    """
    Background thread that decodes audio from any format to PCM float32
    using PyAV (python binding for FFmpeg libraries).

    Emits finished(numpy_array, sample_rate, num_channels, channel_layout) on success.
    """

    finished = Signal(object, int, int, str)  # (np.ndarray, sample_rate, channels, channel_layout)
    error = Signal(str)
    progress = Signal(str)  # Status message

    # Max decode duration (10 minutes)
    MAX_DURATION = 600.0

    def __init__(self, file_path: str, sample_rate: int = 44100,
                 parent=None):
        super().__init__(parent)
        self._file_path = file_path
        self._sample_rate = sample_rate
        self._running = True

    def run(self):
        try:
            import av
        except ImportError:
            self.error.emit("PyAV not installed. Run: pip install av")
            return

        try:
            container = av.open(self._file_path)
        except Exception as e:
            self.error.emit(f"Cannot open file: {e}")
            return

        try:
            # Find the first audio stream
            audio_streams = [s for s in container.streams if s.type == 'audio']
            if not audio_streams:
                self.error.emit("No audio track found in file")
                container.close()
                return

            stream = audio_streams[0]
            channels = stream.channels or 2
            channel_layout = str(stream.layout) if stream.layout else ""

            self.progress.emit(f"Decoding audio ({channels}ch, {self._sample_rate}Hz)...")

            # Set up resampler: convert to float32 planar at target sample rate
            resampler = av.AudioResampler(
                format='fltp',  # float32 planar
                layout=stream.layout or 'stereo',
                rate=self._sample_rate,
            )

            # Decode frames
            max_samples = int(self.MAX_DURATION * self._sample_rate)
            all_frames = []
            total_samples = 0

            for packet in container.demux(stream):
                if not self._running:
                    container.close()
                    return

                for frame in packet.decode():
                    # Resample
                    resampled = resampler.resample(frame)
                    for r_frame in resampled if isinstance(resampled, list) else [resampled]:
                        if r_frame is None:
                            continue
                        # Convert to numpy: shape (channels, samples)
                        arr = r_frame.to_ndarray()
                        all_frames.append(arr)
                        total_samples += arr.shape[1] if arr.ndim == 2 else arr.shape[0]

                        if total_samples >= max_samples:
                            break

                if total_samples >= max_samples:
                    break

            # Flush resampler
            if self._running:
                resampled = resampler.resample(None)
                for r_frame in resampled if isinstance(resampled, list) else [resampled]:
                    if r_frame is not None:
                        arr = r_frame.to_ndarray()
                        all_frames.append(arr)

            container.close()

            if not self._running:
                return

            if not all_frames:
                self.error.emit("No audio data decoded (file may have no audio track)")
                return

            # Concatenate all frames: (channels, total_samples)
            pcm_planar = np.concatenate(all_frames, axis=1 if all_frames[0].ndim == 2 else 0)

            # Transpose to interleaved: (total_samples, channels)
            if pcm_planar.ndim == 2:
                pcm = pcm_planar.T
            else:
                pcm = pcm_planar.reshape(-1, 1)

            # Trim to max duration
            if len(pcm) > max_samples:
                pcm = pcm[:max_samples]

            duration_s = len(pcm) / self._sample_rate
            logger.info(f"Audio decoded: {len(pcm)} samples, {channels}ch, "
                        f"{duration_s:.1f}s, layout={channel_layout}")

            self.finished.emit(pcm, self._sample_rate, channels, channel_layout)

        except Exception as e:
            logger.error(f"Audio decode error: {e}", exc_info=True)
            self.error.emit(f"Decode error: {str(e)}")
            try:
                container.close()
            except Exception:
                pass

    def stop(self):
        self._running = False
