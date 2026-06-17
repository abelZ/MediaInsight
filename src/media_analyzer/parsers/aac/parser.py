"""ADTS AAC (Audio Data Transport Stream) parser.

Parses raw .aac files: a sequence of ADTS frames, each starting with the
12-bit sync word 0xFFF, followed by a 7- or 9-byte header and the raw AAC
payload. Yields one PacketInfo per ADTS frame.

Reference: ISO/IEC 13818-7 (MPEG-2 AAC) / 14496-3 (MPEG-4 AAC), section 1.A.2.2
"""

import logging
from typing import Generator, BinaryIO, Optional

from media_analyzer.parsers.base import BaseParser
from media_analyzer.core.models import PacketInfo, StreamInfo, TagType

logger = logging.getLogger(__name__)


# MPEG-4 Audio Object Types (profile + 1 in ADTS)
AAC_PROFILE_NAMES = {
    0: "Main",
    1: "LC",         # Low Complexity (most common)
    2: "SSR",        # Scalable Sample Rate
    3: "LTP",        # Long Term Prediction
}

# Sample rate index → Hz (ISO/IEC 14496-3, Table 1.16)
SAMPLING_FREQUENCIES = [
    96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050,
    16000, 12000, 11025, 8000, 7350, 0, 0, 0,
]

# Channel configuration → number of channels (Table 1.17)
CHANNEL_CONFIGURATIONS = {
    0: 0,   # Defined in AOT-specific config
    1: 1,   # Mono (center)
    2: 2,   # Stereo (L+R)
    3: 3,   # C+L+R
    4: 4,   # C+L+R+back center
    5: 5,   # C+L+R+back L+R
    6: 6,   # C+L+R+back L+R+LFE (5.1)
    7: 8,   # C+L+R+side L+R+back L+R+LFE (7.1)
}

# How many bytes we need at minimum to identify a valid ADTS header
_ADTS_HEADER_SIZE = 7
_ADTS_HEADER_SIZE_CRC = 9


def _is_adts_sync(data: bytes, pos: int) -> bool:
    """Return True if 12-bit ADTS sync word 0xFFF is at data[pos:pos+2]."""
    return (pos + 1 < len(data)
            and data[pos] == 0xFF
            and (data[pos + 1] & 0xF0) == 0xF0)


def _parse_adts_header(hdr: bytes) -> Optional[dict]:
    """Parse a 7-byte ADTS fixed+variable header.

    Layout (bit-level, MSB first):
      sync (12) | id (1) | layer (2) | protection_absent (1)
      profile (2) | sample_rate_idx (4) | private (1) | channel_cfg (3)
      original_copy (1) | home (1) | copyright_id_bit (1) | copyright_id_start (1)
      frame_length (13) | buffer_fullness (11) | num_aac_frames - 1 (2)
    """
    if len(hdr) < _ADTS_HEADER_SIZE:
        return None
    if hdr[0] != 0xFF or (hdr[1] & 0xF0) != 0xF0:
        return None

    b1 = hdr[1]
    b2 = hdr[2]
    b3 = hdr[3]
    b4 = hdr[4]
    b5 = hdr[5]
    b6 = hdr[6]

    mpeg_version = (b1 >> 3) & 0x01           # 0 = MPEG-4, 1 = MPEG-2
    layer = (b1 >> 1) & 0x03
    protection_absent = b1 & 0x01

    profile = (b2 >> 6) & 0x03                # AOT - 1
    sample_rate_idx = (b2 >> 2) & 0x0F
    private_bit = (b2 >> 1) & 0x01
    channel_cfg = ((b2 & 0x01) << 2) | ((b3 >> 6) & 0x03)

    original_copy = (b3 >> 5) & 0x01
    home = (b3 >> 4) & 0x01
    copyright_id_bit = (b3 >> 3) & 0x01
    copyright_id_start = (b3 >> 2) & 0x01

    frame_length = ((b3 & 0x03) << 11) | (b4 << 3) | ((b5 >> 5) & 0x07)
    buffer_fullness = ((b5 & 0x1F) << 6) | ((b6 >> 2) & 0x3F)
    num_raw_blocks = b6 & 0x03                # number_of_raw_data_blocks_in_frame

    sample_rate = (SAMPLING_FREQUENCIES[sample_rate_idx]
                   if sample_rate_idx < len(SAMPLING_FREQUENCIES) else 0)
    channels = CHANNEL_CONFIGURATIONS.get(channel_cfg, channel_cfg)
    has_crc = protection_absent == 0
    header_size = _ADTS_HEADER_SIZE_CRC if has_crc else _ADTS_HEADER_SIZE

    return {
        "mpeg_version": mpeg_version,
        "layer": layer,
        "protection_absent": protection_absent,
        "profile": profile,
        "sample_rate_idx": sample_rate_idx,
        "sample_rate": sample_rate,
        "private_bit": private_bit,
        "channel_cfg": channel_cfg,
        "channels": channels,
        "original_copy": original_copy,
        "home": home,
        "copyright_id_bit": copyright_id_bit,
        "copyright_id_start": copyright_id_start,
        "frame_length": frame_length,
        "buffer_fullness": buffer_fullness,
        "num_raw_blocks": num_raw_blocks,
        "header_size": header_size,
        "has_crc": has_crc,
    }


def _validate_adts_chain(data: bytes, pos: int, max_links: int = 3) -> bool:
    """Check that a candidate ADTS sync at data[pos:] starts a valid chain.

    Walks forward up to max_links frames; each successor must also start with
    a sync word at the position implied by the previous frame_length. This
    avoids matching random 0xFFFx bytes inside another container.
    """
    cur = pos
    for _ in range(max_links):
        if cur + _ADTS_HEADER_SIZE > len(data):
            return True  # Hit EOF mid-chain — treat as plausible
        hdr = _parse_adts_header(data[cur:cur + _ADTS_HEADER_SIZE])
        if not hdr or hdr["frame_length"] < hdr["header_size"]:
            return False
        # Sample rate must be valid (non-zero index)
        if hdr["sample_rate"] == 0:
            return False
        cur += hdr["frame_length"]
    return True


class AACParser(BaseParser):
    """Parser for raw ADTS AAC streams (.aac files).

    Yields one PacketInfo per ADTS frame, plus an initial HEADER pseudo-tag
    summarising the stream parameters detected from the first frame.
    """

    def __init__(self):
        self._sample_rate: int = 0
        self._channels: int = 0
        self._profile: int = 0
        self._frame_count: int = 0
        self._total_samples: int = 0
        self._file_size: int = 0
        self._mpeg_version: int = 0

    @classmethod
    def sniff(cls, header_bytes: bytes) -> bool:
        """Detect ADTS by locating the sync word and validating a short chain.

        We allow up to 64 bytes of leading garbage (some files have ID3v2 tags
        or stray bytes). If a 'TAG' / 'ID3' header is present at offset 0 we
        skip it before searching.
        """
        if not header_bytes:
            return False

        # Skip ID3v2 header if present (10-byte header + synchsafe size)
        start = 0
        if (len(header_bytes) >= 10
                and header_bytes[:3] == b"ID3"):
            size = ((header_bytes[6] & 0x7F) << 21
                    | (header_bytes[7] & 0x7F) << 14
                    | (header_bytes[8] & 0x7F) << 7
                    | (header_bytes[9] & 0x7F))
            start = 10 + size
            if start >= len(header_bytes):
                return False

        # Look for sync within the first 64 bytes after any ID3 tag
        scan_end = min(len(header_bytes) - 1, start + 64)
        for pos in range(start, scan_end):
            if _is_adts_sync(header_bytes, pos):
                if _validate_adts_chain(header_bytes, pos):
                    return True
        return False

    def parse_header(self, data: bytes) -> dict:
        return {"format": "aac"}

    def parse_incremental(self, source: BinaryIO) -> Generator[PacketInfo, None, None]:
        """Yield PacketInfo for each ADTS frame in the stream.

        Reads in 64 KiB chunks; carries leftover bytes between iterations so
        frames spanning chunk boundaries are still emitted. Skips an ID3v2
        prefix if present.
        """
        self._frame_count = 0
        self._total_samples = 0

        # Skip ID3v2 tag if present (10-byte header + synchsafe-encoded size)
        prefix = source.read(10)
        offset = 0
        leftover = b""
        if len(prefix) >= 10 and prefix[:3] == b"ID3":
            tag_size = ((prefix[6] & 0x7F) << 21
                        | (prefix[7] & 0x7F) << 14
                        | (prefix[8] & 0x7F) << 7
                        | (prefix[9] & 0x7F))
            # Drain the tag body — works for seekable and non-seekable sources
            remaining = tag_size
            while remaining > 0:
                drop = source.read(min(remaining, 64 * 1024))
                if not drop:
                    break
                remaining -= len(drop)
            offset = 10 + tag_size
        else:
            # Not an ID3 prefix — keep those bytes as the start of the stream
            leftover = prefix

        header_emitted = False
        while True:
            chunk = source.read(64 * 1024)
            if not chunk and not leftover:
                break
            data = leftover + chunk
            pos = 0
            while pos + _ADTS_HEADER_SIZE <= len(data):
                if not _is_adts_sync(data, pos):
                    pos += 1
                    continue

                hdr = _parse_adts_header(data[pos:pos + _ADTS_HEADER_SIZE])
                if not hdr or hdr["frame_length"] < hdr["header_size"]:
                    pos += 1
                    continue

                frame_len = hdr["frame_length"]
                if pos + frame_len > len(data):
                    # Frame spans into the next chunk — defer
                    break

                if not header_emitted:
                    self._sample_rate = hdr["sample_rate"]
                    self._channels = hdr["channels"]
                    self._profile = hdr["profile"]
                    self._mpeg_version = hdr["mpeg_version"]
                    yield self._make_header_packet(offset + pos, hdr)
                    header_emitted = True
                    self._frame_count += 1

                yield self._make_frame_packet(
                    offset + pos, frame_len, hdr, self._frame_count)
                self._frame_count += 1
                # 1024 samples per AAC frame × (num_raw_blocks + 1)
                self._total_samples += 1024 * (hdr["num_raw_blocks"] + 1)
                pos += frame_len

            leftover = data[pos:]
            offset += pos
            if not chunk:
                # No more data and trailing leftover is unparsable — stop.
                break

        self._file_size = offset + len(leftover)

    def _make_header_packet(self, offset: int, hdr: dict) -> PacketInfo:
        """Build the synthetic HEADER row summarising the stream."""
        profile_name = AAC_PROFILE_NAMES.get(hdr["profile"], f"Unknown({hdr['profile']})")
        mpeg_label = "MPEG-2" if hdr["mpeg_version"] == 1 else "MPEG-4"
        chan_label = "Mono" if hdr["channels"] == 1 else (
            "Stereo" if hdr["channels"] == 2 else f"{hdr['channels']}ch")

        return PacketInfo(
            index=0,
            tag_type=TagType.HEADER,
            timestamp=0,
            data_size=0,
            offset=offset,
            stream_id=0,
            tag_total_size=0,
            script_data={
                "box_type": "ADTS (AAC)",
                "depth": 0,
                "is_container": True,
                "riff_layout": True,
                "detail": f"{mpeg_label} AAC-{profile_name}, "
                          f"{hdr['sample_rate']}Hz, {chan_label}",
                "fields": {
                    "MPEG Version": f"{mpeg_label} ({hdr['mpeg_version']})",
                    "Profile (AOT)": f"{profile_name} ({hdr['profile'] + 1})",
                    "Sample Rate": f"{hdr['sample_rate']:,} Hz "
                                   f"(idx={hdr['sample_rate_idx']})",
                    "Channels": f"{hdr['channels']} ({chan_label}) "
                                f"(cfg={hdr['channel_cfg']})",
                    "Protection": "CRC present" if hdr["has_crc"] else "No CRC",
                    "Header Size": f"{hdr['header_size']} bytes",
                },
            },
        )

    def _make_frame_packet(self, offset: int, frame_len: int,
                           hdr: dict, frame_idx: int) -> PacketInfo:
        """Build a PacketInfo row for a single ADTS frame."""
        profile_name = AAC_PROFILE_NAMES.get(hdr["profile"], f"P{hdr['profile']}")
        # Each AAC frame carries 1024 samples × (num_raw_blocks + 1)
        samples = 1024 * (hdr["num_raw_blocks"] + 1)
        timestamp_ms = (int(self._total_samples * 1000 / self._sample_rate)
                        if self._sample_rate > 0 else 0)
        chan_label = "Mono" if hdr["channels"] == 1 else (
            "Stereo" if hdr["channels"] == 2 else f"{hdr['channels']}ch")

        # byte_ranges relative to frame start (0 = first sync byte)
        byte_ranges = {
            "Sync Word": (0, 2),
            "MPEG Version": (1, 1),
            "Profile (AOT)": (2, 1),
            "Sample Rate Index": (2, 1),
            "Channel Config": (2, 2),
            "Frame Length": (3, 3),
            "Buffer Fullness": (5, 2),
            "Raw Data Blocks": (6, 1),
        }
        if hdr["has_crc"]:
            byte_ranges["CRC"] = (7, 2)

        fields = {
            "Sync Word": "0xFFF",
            "MPEG Version": "MPEG-4 (0)" if hdr["mpeg_version"] == 0 else "MPEG-2 (1)",
            "Profile (AOT)": f"{profile_name} ({hdr['profile'] + 1})",
            "Sample Rate Index": f"{hdr['sample_rate_idx']} "
                                 f"({hdr['sample_rate']:,} Hz)",
            "Channel Config": f"{hdr['channel_cfg']} ({chan_label})",
            "Protection Absent": str(hdr["protection_absent"]),
            "Frame Length": f"{frame_len:,} bytes",
            "Buffer Fullness": str(hdr["buffer_fullness"]),
            "Raw Data Blocks": f"{hdr['num_raw_blocks'] + 1}",
            "Samples": f"{samples}",
        }

        return PacketInfo(
            index=frame_idx,
            tag_type=TagType.AUDIO,
            timestamp=timestamp_ms,
            data_size=frame_len - hdr["header_size"],
            offset=offset,
            stream_id=0,
            tag_total_size=frame_len,
            sample_rate=hdr["sample_rate"],
            channels=hdr["channels"],
            script_data={
                "box_type": f"ADTS Frame #{frame_idx}",
                "depth": 1,
                "is_container": False,
                "riff_layout": True,
                "detail": f"{profile_name}, {samples} samples, "
                          f"{frame_len}B",
                "fields": fields,
                "byte_ranges": byte_ranges,
                "codec_name": f"AAC-{profile_name}",
            },
        )

    def get_stream_info(self) -> StreamInfo:
        """Return aggregate stream info."""
        duration_ms = 0
        if self._sample_rate > 0:
            duration_ms = int(self._total_samples * 1000 / self._sample_rate)

        profile_name = AAC_PROFILE_NAMES.get(self._profile, f"P{self._profile}")
        return StreamInfo(
            source_path="",
            format_name="AAC (ADTS)",
            duration_ms=duration_ms,
            total_tags=self._frame_count,
            audio_tags=max(0, self._frame_count - 1),
            audio_codec=f"AAC-{profile_name}",
            file_size=self._file_size,
        )
