"""Frame probing using PyAV — demux + on-demand decode for frame type detection.

Two-phase approach:
1. Fast demux: get all packets' size/pts/is_keyframe (ms-level, no decode)
2. On-demand decode: for visible frame range, decode to get accurate pict_type (I/P/B)

This replaces the custom slice header parsing with FFmpeg's full decoder,
giving 100% accurate frame types and enabling proper reference analysis.
"""

import logging
from typing import List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

try:
    import av
    HAS_PYAV = True
except ImportError:
    HAS_PYAV = False


@dataclass
class ProbeFrame:
    """Frame information from PyAV probing."""
    index: int = 0
    pict_type: str = "P"       # "I", "P", "B"
    size_bytes: int = 0        # Packet size
    pts: int = 0               # Presentation timestamp (stream time_base units)
    dts: int = 0               # Decode timestamp
    pts_ms: float = 0.0        # PTS in milliseconds
    dts_ms: float = 0.0        # DTS in milliseconds
    is_keyframe: bool = False  # From packet header (fast, no decode needed)
    decoded: bool = False      # Whether pict_type is from actual decode


PICT_TYPE_MAP = {1: "I", 2: "P", 3: "B", 4: "S", 5: "SI", 6: "SP"}


class FrameProber:
    """
    Two-phase frame prober using PyAV.

    Phase 1 (instant): demux all packets to get size/keyframe info.
    Phase 2 (on-demand): decode specific frame ranges for accurate pict_type.
    """

    def __init__(self, file_path: str):
        self._file_path = file_path
        self._frames: List[ProbeFrame] = []
        self._container = None
        self._stream = None
        self._time_base: float = 1.0  # ms per time_base unit

    @property
    def frames(self) -> List[ProbeFrame]:
        return self._frames

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    def probe_fast(self) -> bool:
        """Phase 1: Fast demux — get all packets without decoding.

        Populates frames with size, pts, is_keyframe.
        pict_type is set to "I" for keyframes, "P" for others (approximate).
        Returns True on success.
        """
        if not HAS_PYAV:
            logger.error("PyAV not available")
            return False

        try:
            self._container = av.open(self._file_path)
            if not self._container.streams.video:
                logger.warning("No video stream found")
                return False

            self._stream = self._container.streams.video[0]
            tb = self._stream.time_base
            if tb:
                self._time_base = float(tb) * 1000  # Convert to ms
            else:
                self._time_base = 1.0

            # Demux all packets (very fast, no decode)
            self._container.seek(0)
            self._frames = []
            idx = 0
            for packet in self._container.demux(self._stream):
                if packet.size == 0:
                    continue
                pts = packet.pts if packet.pts is not None else 0
                dts = packet.dts if packet.dts is not None else 0
                pf = ProbeFrame(
                    index=idx,
                    pict_type="I" if packet.is_keyframe else "P",
                    size_bytes=packet.size,
                    pts=pts,
                    dts=dts,
                    pts_ms=pts * self._time_base,
                    dts_ms=dts * self._time_base,
                    is_keyframe=packet.is_keyframe,
                    decoded=False,
                )
                self._frames.append(pf)
                idx += 1

            return len(self._frames) > 0

        except Exception as e:
            logger.error(f"Fast probe failed: {e}")
            return False

    def decode_range(self, start: int, end: int) -> bool:
        """Phase 2: Decode frames in [start, end) to get accurate pict_type.

        Only decodes frames that haven't been decoded yet.
        Handles B-frame reorder: decoder outputs in display order (PTS),
        but our frames list is in decode order (DTS). We match via PTS lookup.
        Returns True on success.
        """
        if not self._container or not self._stream:
            return False

        # Check if all frames in range are already decoded
        need_decode = False
        for i in range(start, min(end, len(self._frames))):
            if not self._frames[i].decoded:
                need_decode = True
                break
        if not need_decode:
            return True

        try:
            # Build PTS → frame index lookup for matching decoded frames
            pts_to_idx: dict = {}
            for i, pf in enumerate(self._frames):
                if pf.pts not in pts_to_idx:
                    pts_to_idx[pf.pts] = i

            # Seek to nearest keyframe before start
            seek_frame = start
            for i in range(start, -1, -1):
                if self._frames[i].is_keyframe:
                    seek_frame = i
                    break

            # Seek using DTS of the keyframe (decode order seek)
            seek_dts = self._frames[seek_frame].dts
            if seek_frame > 0 and seek_dts:
                self._stream.codec_context.flush_buffers()
                self._container.seek(seek_dts, stream=self._stream)
            else:
                self._stream.codec_context.flush_buffers()
                self._container.seek(0)

            # Decode — output is in display order (PTS order)
            # We match each decoded frame back to our list via PTS
            decoded_count = 0
            max_decode = (end - seek_frame) + 20  # Decode enough to cover B-frame reorder
            for packet in self._container.demux(self._stream):
                if packet.size == 0:
                    continue
                for frame in self._stream.codec_context.decode(packet):
                    # Match decoded frame to our frames list via PTS
                    frame_pts = frame.pts if frame.pts is not None else 0
                    idx = pts_to_idx.get(frame_pts)
                    if idx is not None and idx < len(self._frames):
                        pf = self._frames[idx]
                        if not pf.decoded:
                            pt = PICT_TYPE_MAP.get(frame.pict_type, "?")
                            pf.pict_type = pt
                            pf.decoded = True

                    decoded_count += 1
                    if decoded_count >= max_decode:
                        break

                if decoded_count >= max_decode:
                    break

            # Check if we've decoded enough
            all_done = all(self._frames[i].decoded
                          for i in range(start, min(end, len(self._frames))))
            return all_done

        except Exception as e:
            logger.error(f"Decode range [{start}, {end}) failed: {e}")
            return False

    def close(self):
        """Release resources."""
        if self._container:
            self._container.close()
            self._container = None
            self._stream = None
