"""FLV file writer — generates valid FLV from RTMP media payloads.

Used by Save As to export RTMP streams as playable FLV files.
"""

import struct
from typing import List, Tuple


def write_flv_file(path: str, payloads: List[Tuple[int, int, bytes]],
                   has_video: bool = True, has_audio: bool = True) -> int:
    """
    Write a valid FLV file from RTMP media payloads.

    Args:
        path: Output file path
        payloads: List of (tag_type_byte, timestamp_ms, payload_bytes)
                  tag_type_byte: 8=audio, 9=video, 18=script
        has_video: Whether stream contains video
        has_audio: Whether stream contains audio

    Returns:
        Number of bytes written

    FLV Structure:
        Header: "FLV" + version(1) + flags(1) + data_offset(4)
        PreviousTagSize0: 4 bytes (always 0)
        Tags: [tag_header(11) + data + prev_tag_size(4)] * N
    """
    total_written = 0

    with open(path, "wb") as f:
        # --- FLV Header (9 bytes) ---
        f.write(b"FLV")                          # Signature
        f.write(b"\x01")                         # Version 1
        flags = 0x00
        if has_audio:
            flags |= 0x04
        if has_video:
            flags |= 0x01
        f.write(bytes([flags]))                  # Type flags
        f.write(struct.pack(">I", 9))            # Data offset (header size)
        total_written += 9

        # --- PreviousTagSize0 (always 0) ---
        f.write(struct.pack(">I", 0))
        total_written += 4

        # --- Tags ---
        for tag_type, timestamp_ms, payload in payloads:
            data_size = len(payload)

            # Tag header (11 bytes)
            # Byte 0: tag type (lower 5 bits)
            f.write(bytes([tag_type & 0x1F]))
            # Bytes 1-3: data size (uint24 big-endian)
            f.write(struct.pack(">I", data_size)[1:])
            # Bytes 4-6: timestamp lower 24 bits
            ts_low = timestamp_ms & 0xFFFFFF
            f.write(struct.pack(">I", ts_low)[1:])
            # Byte 7: timestamp extension (upper 8 bits)
            ts_ext = (timestamp_ms >> 24) & 0xFF
            f.write(bytes([ts_ext]))
            # Bytes 8-10: stream ID (always 0)
            f.write(b"\x00\x00\x00")

            # Tag data
            f.write(payload)

            # PreviousTagSize (11 + data_size)
            prev_tag_size = 11 + data_size
            f.write(struct.pack(">I", prev_tag_size))

            total_written += 11 + data_size + 4

    return total_written
