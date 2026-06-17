"""Audio decode worker — decodes audio to PCM using PyAV (FFmpeg binding)."""

import logging
import numpy as np
from typing import Optional

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


# AAC sample-rate index → Hz (ISO/IEC 14496-3, Table 1.16)
_AAC_SAMPLE_RATES = (
    96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050,
    16000, 12000, 11025, 8000, 7350, 0, 0, 0,
)

# AAC channel_configuration → channel count (Table 1.17)
_AAC_CHANNELS = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 8}


def _build_audio_specific_config(profile: int, sr_idx: int, ch_cfg: int) -> bytes:
    """Build the 2-byte AudioSpecificConfig for AAC-LC family.

    Layout: AOT(5) | sample_rate_idx(4) | channel_cfg(4) | GASpecificConfig(3 zero bits)
    profile in ADTS = AOT - 1, so AOT = profile + 1.
    """
    aot = profile + 1
    bits = (aot << 11) | (sr_idx << 7) | (ch_cfg << 3)
    return bytes([(bits >> 8) & 0xFF, bits & 0xFF])


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
            decode_failed = False

            for packet in container.demux(stream):
                if not self._running:
                    container.close()
                    return

                try:
                    decoded = packet.decode()
                except av.InvalidDataError as e:
                    # ADTS demuxer + AAC decoder can disagree on channel layout
                    # (e.g. ADTS claims 5.1 but raw_data_block is stereo). Fall back
                    # to manual ADTS parsing with a fresh codec context.
                    if (stream.codec_context.codec
                            and stream.codec_context.codec.name == 'aac'
                            and self._file_path.lower().endswith('.aac')):
                        logger.warning(
                            "AAC decoder rejected ADTS-framed packet (%s); "
                            "retrying with manual ADTS parsing", e)
                        decode_failed = True
                        break
                    raise

                for frame in decoded:
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

            container.close()

            if decode_failed and self._running:
                self._decode_aac_manual(max_samples)
                return

            # Flush resampler
            if self._running:
                resampled = resampler.resample(None)
                for r_frame in resampled if isinstance(resampled, list) else [resampled]:
                    if r_frame is not None:
                        arr = r_frame.to_ndarray()
                        all_frames.append(arr)

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

    def _decode_aac_manual(self, max_samples: int) -> None:
        """Decode a raw ADTS .aac file by feeding raw_data_blocks to a fresh
        AAC decoder configured via AudioSpecificConfig.

        FFmpeg's adts demuxer + aac decoder occasionally rejects valid streams
        whose ADTS channel_configuration disagrees with the actual element
        layout. Stripping ADTS framing and supplying the AOT/sr/ch via
        extradata sidesteps that path.
        """
        import av
        from av.codec import CodecContext
        import av.packet

        try:
            with open(self._file_path, 'rb') as f:
                src = f.read()
        except OSError as e:
            self.error.emit(f"Cannot re-open AAC file: {e}")
            return

        # Skip leading ID3v2 tag if present
        start = 0
        if len(src) >= 10 and src[:3] == b'ID3':
            tag_size = ((src[6] & 0x7F) << 21
                        | (src[7] & 0x7F) << 14
                        | (src[8] & 0x7F) << 7
                        | (src[9] & 0x7F))
            start = 10 + tag_size

        # Probe first ADTS header for stream params
        if start + 7 > len(src) or src[start] != 0xFF or (src[start + 1] & 0xF0) != 0xF0:
            self.error.emit("AAC file lacks valid ADTS sync header")
            return

        b1 = src[start + 1]
        b2 = src[start + 2]
        b3 = src[start + 3]
        profile = (b2 >> 6) & 0x03
        sr_idx = (b2 >> 2) & 0x0F
        ch_cfg = ((b2 & 0x01) << 2) | ((b3 >> 6) & 0x03)
        sample_rate = (_AAC_SAMPLE_RATES[sr_idx]
                       if sr_idx < len(_AAC_SAMPLE_RATES) else 0)
        channels = _AAC_CHANNELS.get(ch_cfg, ch_cfg) or 2
        layout_name = {1: 'mono', 2: 'stereo', 3: '3.0',
                       4: '4.0', 5: '5.0', 6: '5.1', 8: '7.1'}.get(channels, 'stereo')

        if sample_rate == 0:
            self.error.emit(
                f"AAC ADTS header has unsupported sample rate index {sr_idx}")
            return

        ctx = CodecContext.create('aac', 'r')
        ctx.extradata = _build_audio_specific_config(profile, sr_idx, ch_cfg)

        resampler = av.AudioResampler(
            format='fltp', layout=layout_name, rate=self._sample_rate)

        all_frames = []
        total_samples = 0
        pos = start

        while pos + 7 <= len(src):
            if not self._running:
                return
            if src[pos] != 0xFF or (src[pos + 1] & 0xF0) != 0xF0:
                pos += 1
                continue
            hdr_b1 = src[pos + 1]
            hdr_b3 = src[pos + 3]
            hdr_b4 = src[pos + 4]
            hdr_b5 = src[pos + 5]
            frame_len = (((hdr_b3 & 0x03) << 11)
                         | (hdr_b4 << 3)
                         | ((hdr_b5 >> 5) & 0x07))
            if frame_len < 7 or pos + frame_len > len(src):
                break
            has_crc = (hdr_b1 & 0x01) == 0
            payload_off = pos + (9 if has_crc else 7)
            payload = src[payload_off:pos + frame_len]
            pos += frame_len

            try:
                frames = ctx.decode(av.packet.Packet(payload))
            except av.InvalidDataError:
                # Skip the bad frame and keep going
                continue

            for frame in frames:
                for r_frame in resampler.resample(frame):
                    if r_frame is None:
                        continue
                    arr = r_frame.to_ndarray()
                    all_frames.append(arr)
                    total_samples += (arr.shape[1] if arr.ndim == 2 else arr.shape[0])

            if total_samples >= max_samples:
                break

        # Flush resampler
        for r_frame in resampler.resample(None):
            if r_frame is not None:
                arr = r_frame.to_ndarray()
                all_frames.append(arr)

        if not self._running:
            return

        if not all_frames:
            self.error.emit("No audio data decoded from AAC file")
            return

        pcm_planar = np.concatenate(
            all_frames, axis=1 if all_frames[0].ndim == 2 else 0)
        pcm = pcm_planar.T if pcm_planar.ndim == 2 else pcm_planar.reshape(-1, 1)
        if len(pcm) > max_samples:
            pcm = pcm[:max_samples]

        duration_s = len(pcm) / self._sample_rate
        logger.info(
            f"AAC manual-decode: {len(pcm)} samples, {channels}ch, "
            f"{duration_s:.1f}s, layout={layout_name}")
        self.finished.emit(pcm, self._sample_rate, channels, layout_name)

    def stop(self):
        self._running = False
