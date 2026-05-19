"""RTMP chunk-level protocol: reading/writing chunks and reassembling messages.

RTMP splits messages into chunks. Each chunk has a header (variable size depending
on format type) followed by up to chunk_size bytes of payload data.

Chunk Basic Header (1-3 bytes):
  Bits 6-7: fmt (ChunkFmt) — determines message header size
  Bits 0-5: csid
    - 0: csid is in next byte + 64
    - 1: csid is in next 2 bytes + 64 (little-endian)
    - 2-63: csid directly

Message Header (depends on fmt):
  fmt 0: timestamp(3) + msg_length(3) + msg_type_id(1) + msg_stream_id(4 LE)
  fmt 1: timestamp_delta(3) + msg_length(3) + msg_type_id(1)
  fmt 2: timestamp_delta(3)
  fmt 3: (none — reuse previous)

Extended Timestamp (4 bytes, present when timestamp/delta == 0xFFFFFF)
"""

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from media_analyzer.core.rtmp.constants import (
    ChunkFmt, MessageType, RTMP_DEFAULT_CHUNK_SIZE,
)


@dataclass
class RTMPMessage:
    """A fully reassembled RTMP message."""
    timestamp: int           # Absolute timestamp (ms)
    type_id: int             # Message type ID
    msg_stream_id: int       # Message stream ID
    payload: bytes           # Complete message payload
    csid: int                # Chunk stream ID this arrived on
    raw_bytes: bytes = b""   # Raw chunk bytes (for hex view)


@dataclass
class _ChunkStreamState:
    """Per-chunk-stream state for reassembly."""
    timestamp: int = 0
    timestamp_delta: int = 0  # Last delta for fmt 3 repeat
    msg_length: int = 0
    msg_type_id: int = 0
    msg_stream_id: int = 0
    # Reassembly buffer
    payload_buf: bytearray = field(default_factory=bytearray)
    bytes_remaining: int = 0
    # Raw bytes tracking
    raw_buf: bytearray = field(default_factory=bytearray)


class ChunkReader:
    """
    Reads raw bytes and reassembles RTMP chunks into complete messages.

    Usage:
        reader = ChunkReader()
        messages = reader.feed(data)
        # Returns list of fully reassembled RTMPMessage objects
    """

    def __init__(self, chunk_size: int = RTMP_DEFAULT_CHUNK_SIZE):
        self._chunk_size = chunk_size
        self._streams: Dict[int, _ChunkStreamState] = {}
        self._buffer = bytearray()

    def set_chunk_size(self, size: int) -> None:
        """Update the chunk size (after receiving SetChunkSize message)."""
        self._chunk_size = size

    @property
    def chunk_size(self) -> int:
        return self._chunk_size

    def feed(self, data: bytes) -> List[RTMPMessage]:
        """
        Feed raw bytes from socket. Returns list of complete messages (may be empty).
        Buffers partial data internally.

        Handles SetChunkSize inline (updates chunk_size immediately upon receiving it)
        so that subsequent chunks in the same batch use the new size.
        """
        self._buffer.extend(data)
        messages = []

        while True:
            result = self._try_read_chunk()
            if result is None:
                break  # Not enough data
            if result is not False:
                # Handle SetChunkSize inline — affects subsequent chunk parsing
                if result.type_id == 1 and len(result.payload) >= 4:  # SET_CHUNK_SIZE
                    new_size = int.from_bytes(result.payload[:4], "big") & 0x7FFFFFFF
                    self._chunk_size = new_size
                messages.append(result)
            # result == False means chunk consumed but message not yet complete; continue

        return messages

    def _try_read_chunk(self):
        """
        Try to read one chunk from the buffer.
        Returns:
            RTMPMessage: if a complete message was reassembled
            False: if a chunk was consumed but message not yet complete
            None: if not enough data to parse a chunk
        """
        buf = self._buffer
        if len(buf) < 1:
            return None

        # --- Parse Basic Header ---
        pos = 0
        first_byte = buf[0]
        fmt = (first_byte >> 6) & 0x03
        csid_low = first_byte & 0x3F

        if csid_low == 0:
            # 2 bytes basic header: csid = next byte + 64
            if len(buf) < 2:
                return None
            csid = buf[1] + 64
            pos = 2
        elif csid_low == 1:
            # 3 bytes basic header: csid = next 2 bytes (LE) + 64
            if len(buf) < 3:
                return None
            csid = buf[1] + (buf[2] << 8) + 64
            pos = 3
        else:
            csid = csid_low
            pos = 1

        # --- Parse Message Header ---
        state = self._streams.get(csid)
        if state is None:
            state = _ChunkStreamState()
            self._streams[csid] = state

        if fmt == ChunkFmt.TYPE_0:
            # 11 bytes: timestamp(3) + msg_length(3) + msg_type(1) + msg_stream_id(4 LE)
            if len(buf) < pos + 11:
                return None
            timestamp = int.from_bytes(buf[pos:pos+3], "big")
            msg_length = int.from_bytes(buf[pos+3:pos+6], "big")
            msg_type_id = buf[pos+6]
            msg_stream_id = struct.unpack_from("<I", buf, pos+7)[0]
            pos += 11

            state.msg_length = msg_length
            state.msg_type_id = msg_type_id
            state.msg_stream_id = msg_stream_id

            # Extended timestamp
            if timestamp == 0xFFFFFF:
                if len(buf) < pos + 4:
                    return None
                timestamp = struct.unpack_from(">I", buf, pos)[0]
                pos += 4
            state.timestamp = timestamp
            state.timestamp_delta = 0  # Absolute timestamp, no delta

        elif fmt == ChunkFmt.TYPE_1:
            # 7 bytes: timestamp_delta(3) + msg_length(3) + msg_type(1)
            if len(buf) < pos + 7:
                return None
            timestamp_delta = int.from_bytes(buf[pos:pos+3], "big")
            msg_length = int.from_bytes(buf[pos+3:pos+6], "big")
            msg_type_id = buf[pos+6]
            pos += 7

            state.msg_length = msg_length
            state.msg_type_id = msg_type_id

            # Extended timestamp
            if timestamp_delta == 0xFFFFFF:
                if len(buf) < pos + 4:
                    return None
                timestamp_delta = struct.unpack_from(">I", buf, pos)[0]
                pos += 4
            state.timestamp += timestamp_delta
            state.timestamp_delta = timestamp_delta  # Save for fmt 3

        elif fmt == ChunkFmt.TYPE_2:
            # 3 bytes: timestamp_delta(3)
            if len(buf) < pos + 3:
                return None
            timestamp_delta = int.from_bytes(buf[pos:pos+3], "big")
            pos += 3

            # Extended timestamp
            if timestamp_delta == 0xFFFFFF:
                if len(buf) < pos + 4:
                    return None
                timestamp_delta = struct.unpack_from(">I", buf, pos)[0]
                pos += 4
            state.timestamp += timestamp_delta
            state.timestamp_delta = timestamp_delta  # Save for fmt 3

        else:  # fmt == ChunkFmt.TYPE_3
            # 0 bytes message header — reuse previous header values
            # Only apply delta if this is a NEW message (not a continuation chunk)
            # Continuation chunks (mid-message) should NOT advance timestamp
            if len(state.payload_buf) == 0:
                state.timestamp += state.timestamp_delta

        # --- Read Chunk Data ---
        # If this is a new message (buffer empty), initialize remaining
        if len(state.payload_buf) == 0:
            state.bytes_remaining = state.msg_length

        # How many bytes in this chunk?
        chunk_data_size = min(self._chunk_size, state.bytes_remaining)
        if len(buf) < pos + chunk_data_size:
            return None  # Need more data

        # Extract chunk data
        chunk_data = buf[pos:pos + chunk_data_size]
        pos += chunk_data_size

        # Track raw bytes for this chunk
        raw_chunk = bytes(buf[:pos])
        state.raw_buf.extend(raw_chunk)

        # Append to payload buffer
        state.payload_buf.extend(chunk_data)
        state.bytes_remaining -= chunk_data_size

        # Consume from input buffer
        del self._buffer[:pos]

        # Check if message is complete
        if state.bytes_remaining <= 0:
            msg = RTMPMessage(
                timestamp=state.timestamp,
                type_id=state.msg_type_id,
                msg_stream_id=state.msg_stream_id,
                payload=bytes(state.payload_buf),
                csid=csid,
                raw_bytes=bytes(state.raw_buf),
            )
            state.payload_buf.clear()
            state.raw_buf.clear()
            state.bytes_remaining = 0
            return msg

        return False  # Chunk consumed but message not yet complete (multi-chunk)


class ChunkWriter:
    """Encodes RTMP messages into chunks for sending."""

    def __init__(self, chunk_size: int = RTMP_DEFAULT_CHUNK_SIZE):
        self._chunk_size = chunk_size
        self._prev_streams: Dict[int, _ChunkStreamState] = {}

    def set_chunk_size(self, size: int) -> None:
        self._chunk_size = size

    def encode(self, type_id: int, payload: bytes, csid: int = 3,
               msg_stream_id: int = 0, timestamp: int = 0) -> bytes:
        """
        Encode a message into RTMP chunks.
        Always uses fmt 0 for first chunk, fmt 3 for continuation chunks.
        """
        result = bytearray()
        msg_length = len(payload)
        offset = 0
        first = True

        while offset < msg_length:
            chunk_data_size = min(self._chunk_size, msg_length - offset)

            if first:
                # Basic header + fmt 0 message header
                result.append((ChunkFmt.TYPE_0 << 6) | (csid & 0x3F))

                # Timestamp (3 bytes) + extended if needed
                ts = min(timestamp, 0xFFFFFF)
                result.extend(ts.to_bytes(3, "big"))
                # Message length (3 bytes)
                result.extend(msg_length.to_bytes(3, "big"))
                # Message type ID (1 byte)
                result.append(type_id)
                # Message stream ID (4 bytes, little-endian)
                result.extend(struct.pack("<I", msg_stream_id))
                # Extended timestamp
                if timestamp >= 0xFFFFFF:
                    result.extend(struct.pack(">I", timestamp))
                first = False
            else:
                # fmt 3 continuation chunk
                result.append((ChunkFmt.TYPE_3 << 6) | (csid & 0x3F))

            result.extend(payload[offset:offset + chunk_data_size])
            offset += chunk_data_size

        return bytes(result)
