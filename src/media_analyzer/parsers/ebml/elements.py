"""EBML element definitions and VInt parsing utilities.

EBML uses variable-length integers (VInt) for both element IDs and sizes.
The number of leading zero bits in the first byte determines the total length:
  1xxxxxxx = 1 byte  (7 data bits)
  01xxxxxx = 2 bytes (14 data bits)
  001xxxxx = 3 bytes (21 data bits)
  ...up to 8 bytes (56 data bits)

Reference: https://www.matroska.org/technical/elements.html
"""

import struct
from typing import Tuple, Optional


# --- EBML Element IDs ---

# EBML Header
EBML_HEADER = 0x1A45DFA3
EBML_VERSION = 0x4286
EBML_READ_VERSION = 0x42F7
EBML_MAX_ID_LENGTH = 0x42F2
EBML_MAX_SIZE_LENGTH = 0x42F3
DOC_TYPE = 0x4282
DOC_TYPE_VERSION = 0x4287
DOC_TYPE_READ_VERSION = 0x4285

# Segment (top-level container)
SEGMENT = 0x18538067

# Segment Information
INFO = 0x1549A966
TIMESTAMP_SCALE = 0x2AD7B1
DURATION = 0x4489
MUXING_APP = 0x4D80
WRITING_APP = 0x5741
TITLE = 0x7BA9

# Track declarations
TRACKS = 0x1654AE6B
TRACK_ENTRY = 0xAE
TRACK_NUMBER = 0xD7
TRACK_UID = 0x73C5
TRACK_TYPE = 0x83
CODEC_ID = 0x86
CODEC_PRIVATE = 0x63A2
DEFAULT_DURATION = 0x23E383
CODEC_NAME = 0x258688

# Video settings
VIDEO = 0xE0
PIXEL_WIDTH = 0xB0
PIXEL_HEIGHT = 0xBA

# Audio settings
AUDIO_ELEMENT = 0xE1
SAMPLING_FREQUENCY = 0xB5
CHANNELS = 0x9F

# Cluster
CLUSTER = 0x1F43B675
CLUSTER_TIMESTAMP = 0xE7
SIMPLE_BLOCK = 0xA3
BLOCK_GROUP = 0xA0
BLOCK = 0xA1
BLOCK_DURATION = 0x9B

# Cues (seek index)
CUES = 0x1C53BB6B

# Seek Head
SEEK_HEAD = 0x114D9B74

# Tags
TAGS = 0x1254C367

# Track types
TRACK_TYPE_VIDEO = 1
TRACK_TYPE_AUDIO = 2
TRACK_TYPE_SUBTITLE = 17

# Container elements (should be recursed into)
CONTAINER_ELEMENTS = {
    EBML_HEADER, SEGMENT, INFO, TRACKS, TRACK_ENTRY,
    VIDEO, AUDIO_ELEMENT, CLUSTER, BLOCK_GROUP,
    CUES, SEEK_HEAD, TAGS,
    0x4DBB,   # Seek
    0xBB,     # CuePoint
    0xB7,     # CueTrackPositions
    0x7373,   # Tag
    0x63C0,   # Targets
    0x67C8,   # SimpleTag
    0x55B0,   # Colour
    0x1043A770,  # Chapters
    0x45B9,   # EditionEntry
    0xB6,     # ChapterAtom
    0x80,     # ChapterDisplay
    0x1941A469,  # Attachments
    0x61A7,   # AttachedFile
}

# Codec ID mapping
CODEC_MAP = {
    # Video
    "V_VP8": "VP8",
    "V_VP9": "VP9",
    "V_AV1": "AV1",
    "V_MPEG4/ISO/AVC": "H.264",
    "V_MPEGH/ISO/HEVC": "H.265",
    "V_MPEG4/ISO/SP": "MPEG-4",
    "V_MPEG2": "MPEG-2",
    "V_THEORA": "Theora",
    # Audio
    "A_OPUS": "Opus",
    "A_VORBIS": "Vorbis",
    "A_AAC": "AAC",
    "A_AAC/MPEG4/LC": "AAC-LC",
    "A_AAC/MPEG2/LC": "AAC-LC",
    "A_AC3": "AC-3",
    "A_EAC3": "E-AC-3",
    "A_DTS": "DTS",
    "A_FLAC": "FLAC",
    "A_MPEG/L3": "MP3",
    "A_MPEG/L2": "MP2",
    "A_PCM/INT/LIT": "PCM",
}


def read_vint(buf: bytes, pos: int) -> Tuple[int, int]:
    """
    Read an EBML Variable-Length Integer (VInt).

    Returns (value, bytes_consumed).
    The value has the VINT_MARKER bit cleared (data bits only).
    Raises ValueError if not enough data.
    """
    if pos >= len(buf):
        raise ValueError("Not enough data for VInt")

    first = buf[pos]
    if first == 0:
        raise ValueError("Invalid VInt (zero first byte)")

    # Count leading zeros to determine length
    length = 1
    mask = 0x80
    while length <= 8 and not (first & mask):
        length += 1
        mask >>= 1

    if length > 8:
        raise ValueError("Invalid VInt length > 8")
    if pos + length > len(buf):
        raise ValueError(f"Not enough data for {length}-byte VInt")

    # Read value: first byte without marker bit, then remaining bytes
    value = first & (mask - 1)  # Clear the VINT_MARKER bit
    for i in range(1, length):
        value = (value << 8) | buf[pos + i]

    return value, length


def read_vint_raw(buf: bytes, pos: int) -> Tuple[int, int]:
    """
    Read a VInt without clearing the marker bit (for Element IDs).
    Returns (raw_value, bytes_consumed).
    """
    if pos >= len(buf):
        raise ValueError("Not enough data for VInt")

    first = buf[pos]
    if first == 0:
        raise ValueError("Invalid VInt")

    length = 1
    mask = 0x80
    while length <= 8 and not (first & mask):
        length += 1
        mask >>= 1

    if length > 8 or pos + length > len(buf):
        raise ValueError("Invalid or truncated VInt")

    # For element IDs, keep all bits including marker
    value = first
    for i in range(1, length):
        value = (value << 8) | buf[pos + i]

    return value, length


def read_element_header(buf: bytes, pos: int) -> Tuple[int, int, int]:
    """
    Read an EBML element header (ID + Size).

    Returns (element_id, data_size, header_total_bytes).
    data_size = -1 means unknown size (all 1-bits).
    """
    start = pos

    # Read Element ID (raw VInt with marker bit kept)
    element_id, id_len = read_vint_raw(buf, pos)
    pos += id_len

    # Read Data Size (VInt with marker bit cleared)
    size_val, size_len = read_vint(buf, pos)
    pos += size_len

    # Check for unknown size (all data bits set to 1)
    max_val = (1 << (7 * size_len)) - 1
    if size_val == max_val:
        size_val = -1  # Unknown/indeterminate size

    return element_id, size_val, pos - start
