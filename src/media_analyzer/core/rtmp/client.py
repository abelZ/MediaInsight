"""RTMP client — pure Python socket-based implementation.

Handles:
- TCP connection
- RTMP handshake (C0/C1/C2, S0/S1/S2) with raw byte capture
- Command messages (connect, createStream, play)
- Message receive loop with chunk reassembly
- Pause/resume via threading.Event
- Protocol control message handling (SetChunkSize, WindowAckSize, etc.)
"""

import os
import socket
import struct
import time
import threading
from dataclasses import dataclass
from typing import Optional, Tuple, List
from urllib.parse import urlparse

from media_analyzer.core.rtmp.constants import (
    MessageType, RTMP_DEFAULT_PORT, RTMP_HANDSHAKE_SIZE,
    RTMP_VERSION, RTMP_DEFAULT_CHUNK_SIZE,
    CSID_PROTOCOL_CONTROL, CSID_COMMAND,
)
from media_analyzer.core.rtmp.chunk import ChunkReader, ChunkWriter, RTMPMessage
from media_analyzer.core.rtmp.amf0 import AMF0Encoder


@dataclass
class RTMPUrl:
    """Parsed RTMP URL components."""
    host: str
    port: int
    app: str          # Application name (path segment)
    stream: str       # Stream/key name (last path segment)
    tc_url: str       # Full tcUrl for connect command


def parse_rtmp_url(url: str) -> RTMPUrl:
    """Parse rtmp://host[:port]/app[/instance]/stream_name[?query]

    Query parameters are appended to both tcUrl and stream name,
    as different servers expect them in different places.
    """
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    # Default port: 443 for rtmps, 1935 for rtmp
    default_port = 443 if parsed.scheme == "rtmps" else RTMP_DEFAULT_PORT
    port = parsed.port or default_port

    # Path: /app/stream or /app/instance/stream
    path = parsed.path.lstrip("/")
    parts = path.split("/")

    if len(parts) >= 2:
        app = parts[0]
        stream = "/".join(parts[1:])
    elif len(parts) == 1:
        app = parts[0]
        stream = ""
    else:
        app = ""
        stream = ""

    # Append query params to stream name (many servers require this)
    query = parsed.query
    if query and stream:
        stream = f"{stream}?{query}"

    # tcUrl = rtmp://host[:port]/app[?query]
    tc_url = f"rtmp://{host}:{port}/{app}"
    if query:
        tc_url = f"{tc_url}?{query}"

    return RTMPUrl(host=host, port=port, app=app, stream=stream, tc_url=tc_url)


@dataclass
class HandshakeData:
    """Captured handshake bytes for protocol display."""
    c0: bytes       # 1 byte: version
    c1: bytes       # 1536 bytes: time(4) + zero(4) + random(1528)
    s0: bytes       # 1 byte
    s1: bytes       # 1536 bytes
    s2: bytes       # 1536 bytes
    c2: bytes       # 1536 bytes


class RTMPClient:
    """
    Pure Python RTMP client for stream playback.

    Protocol flow:
        TCP connect → Handshake (C0C1→S0S1S2→C2) → connect() → createStream() → play()
        → receive media data loop
    """

    def __init__(self, url: str, timeout: float = 10.0):
        self._url_str = url
        self._url = parse_rtmp_url(url)
        self._timeout = timeout

        self._socket: Optional[socket.socket] = None
        self._chunk_reader = ChunkReader()
        self._chunk_writer = ChunkWriter()
        self._connected = False
        self._bytes_received = 0
        self._bytes_sent = 0

        # Threading control
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused by default

        # Message queue (from chunk reader, may return multiple per recv)
        self._msg_queue: List[RTMPMessage] = []

        # Protocol state
        self._transaction_id = 0
        self._msg_stream_id = 0  # Assigned by server via createStream result
        self._window_ack_size = 2500000
        self._bytes_since_ack = 0
        self._pending_ack_size = 0  # Deferred WindowAckSize response

        # Handshake capture
        self.handshake_data: Optional[HandshakeData] = None

    @property
    def bytes_received(self) -> int:
        return self._bytes_received

    @property
    def bytes_sent(self) -> int:
        return self._bytes_sent

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def url(self) -> RTMPUrl:
        return self._url

    def connect(self) -> None:
        """Establish TCP connection to RTMP server (with TLS for rtmps://)."""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.settimeout(self._timeout)
        self._socket.connect((self._url.host, self._url.port))

        # Wrap with TLS for rtmps://
        if self._url_str.startswith("rtmps://"):
            import ssl
            ctx = ssl.create_default_context()
            self._socket = ctx.wrap_socket(self._socket, server_hostname=self._url.host)

        self._connected = True

    def handshake(self) -> HandshakeData:
        """
        Perform RTMP handshake.

        C0 + C1 → server
        server → S0 + S1 + S2
        C2 → server

        Returns HandshakeData with all captured bytes.
        """
        if not self._socket:
            raise RuntimeError("Not connected")

        # C0: version byte
        c0 = bytes([RTMP_VERSION])

        # C1: time(4 bytes) + zero(4 bytes) + random(1528 bytes)
        c1_time = struct.pack(">I", int(time.time()) & 0xFFFFFFFF)
        c1_zero = b"\x00\x00\x00\x00"
        c1_random = os.urandom(RTMP_HANDSHAKE_SIZE - 8)
        c1 = c1_time + c1_zero + c1_random

        # Send C0 + C1
        self._send_raw(c0 + c1)

        # Receive S0 + S1 + S2 (1 + 1536 + 1536 = 3073 bytes)
        s0 = self._recv_exact(1)
        s1 = self._recv_exact(RTMP_HANDSHAKE_SIZE)
        s2 = self._recv_exact(RTMP_HANDSHAKE_SIZE)

        # C2: echo back S1 (time + time2 + random)
        # Per spec: C2 time = S1 time, C2 time2 = current time, C2 random = S1 random
        c2 = s1[:4] + struct.pack(">I", int(time.time()) & 0xFFFFFFFF) + s1[8:]
        self._send_raw(c2)

        self.handshake_data = HandshakeData(
            c0=c0, c1=c1, s0=s0, s1=s1, s2=s2, c2=c2
        )
        return self.handshake_data

    def send_connect(self) -> None:
        """Send RTMP connect command."""
        command_object = {
            "app": self._url.app,
            "flashVer": "LNX 9,0,124,2",
            "tcUrl": self._url.tc_url,
            "fpad": False,
            "capabilities": 15.0,
            "audioCodecs": 4071.0,   # All audio codecs
            "videoCodecs": 252.0,    # All video codecs
            "videoFunction": 1.0,
            "objectEncoding": 0.0,   # AMF0
        }

        payload = AMF0Encoder.encode_command(
            "connect", self._next_transaction_id(), command_object
        )
        self._send_message(MessageType.COMMAND_AMF0, payload, csid=CSID_COMMAND)

    def send_window_ack_size(self, size: int = 2500000) -> None:
        """Send Window Acknowledgement Size."""
        payload = struct.pack(">I", size)
        self._send_message(
            MessageType.WINDOW_ACK_SIZE, payload, csid=CSID_PROTOCOL_CONTROL
        )

    def send_create_stream(self) -> None:
        """Send createStream command."""
        payload = AMF0Encoder.encode_command(
            "createStream", self._next_transaction_id(), None
        )
        self._send_message(MessageType.COMMAND_AMF0, payload, csid=CSID_COMMAND)

    def send_play(self, stream_name: Optional[str] = None) -> None:
        """Send play command."""
        name = stream_name or self._url.stream
        payload = AMF0Encoder.encode_command(
            "play", 0, None, name
        )
        self._send_message(
            MessageType.COMMAND_AMF0, payload,
            csid=CSID_COMMAND, msg_stream_id=self._msg_stream_id
        )

    def send_set_buffer_length(self, stream_id: int, buffer_ms: int = 1000) -> None:
        """Send UserControl SetBufferLength event."""
        payload = struct.pack(">HII", 3, stream_id, buffer_ms)  # 3 = SetBufferLength
        self._send_message(
            MessageType.USER_CONTROL, payload, csid=CSID_PROTOCOL_CONTROL
        )

    def recv_message(self) -> Optional[RTMPMessage]:
        """
        Read data from socket and return the next complete message.
        Blocks until a message is available or connection is lost.
        Respects pause state (blocks when paused).
        """
        if not self._socket or not self._connected:
            return None

        # Wait if paused
        self._pause_event.wait()

        # Return buffered message if available
        if self._msg_queue:
            msg = self._msg_queue.pop(0)
            return msg

        while True:
            # Send any pending protocol responses before reading more data
            self._flush_pending_responses()

            try:
                data = self._socket.recv(4096)
            except socket.timeout:
                continue
            except (OSError, ConnectionError):
                self._connected = False
                return None

            if not data:
                self._connected = False
                return None

            self._bytes_received += len(data)
            self._bytes_since_ack += len(data)

            # Send ACK if needed
            if self._bytes_since_ack >= self._window_ack_size:
                self._send_ack()

            messages = self._chunk_reader.feed(data)

            if messages:
                # Handle protocol control messages inline for ALL messages
                # (SetChunkSize must take effect before subsequent chunks in same batch)
                for m in messages:
                    self._handle_protocol_message(m)
                # Return first, queue the rest
                msg = messages[0]
                self._msg_queue.extend(messages[1:])
                return msg

        return None

    def set_msg_stream_id(self, stream_id: int) -> None:
        """Set the message stream ID (from server's createStream response)."""
        self._msg_stream_id = stream_id

    def pause(self) -> None:
        """Pause receiving (blocks recv loop). Connection stays alive."""
        self._pause_event.clear()

    def resume(self) -> None:
        """Resume receiving."""
        self._pause_event.set()

    def disconnect(self) -> None:
        """Close the RTMP connection."""
        self._connected = False
        self._pause_event.set()  # Unblock if paused
        sock = self._socket
        self._socket = None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    # --- Internal Methods ---

    def _next_transaction_id(self) -> float:
        self._transaction_id += 1
        return float(self._transaction_id)

    def _send_raw(self, data: bytes) -> None:
        """Send raw bytes to socket."""
        if self._socket:
            self._socket.sendall(data)
            self._bytes_sent += len(data)

    def _send_message(self, type_id: int, payload: bytes,
                      csid: int = CSID_COMMAND,
                      msg_stream_id: int = 0, timestamp: int = 0) -> None:
        """Encode and send an RTMP message."""
        chunks = self._chunk_writer.encode(
            type_id=type_id, payload=payload,
            csid=csid, msg_stream_id=msg_stream_id, timestamp=timestamp
        )
        self._send_raw(chunks)

    def _recv_exact(self, n: int) -> bytes:
        """Read exactly n bytes from socket."""
        buf = bytearray()
        while len(buf) < n:
            remaining = n - len(buf)
            try:
                data = self._socket.recv(remaining)
            except (OSError, socket.timeout) as e:
                raise IOError(f"Failed to read {n} bytes: {e}") from e
            if not data:
                raise IOError(f"Connection closed (needed {remaining} more bytes)")
            buf.extend(data)
            self._bytes_received += len(data)
        return bytes(buf)

    def _send_ack(self) -> None:
        """Send acknowledgement of received bytes."""
        payload = struct.pack(">I", self._bytes_received)
        self._send_message(
            MessageType.ACK, payload, csid=CSID_PROTOCOL_CONTROL
        )
        self._bytes_since_ack = 0

    def _handle_protocol_message(self, msg: RTMPMessage) -> None:
        """Handle protocol control messages (adjust internal state)."""
        if msg.type_id == MessageType.SET_CHUNK_SIZE:
            if len(msg.payload) >= 4:
                new_size = struct.unpack(">I", msg.payload[:4])[0]
                self._chunk_reader.set_chunk_size(new_size)

        elif msg.type_id == MessageType.WINDOW_ACK_SIZE:
            if len(msg.payload) >= 4:
                self._window_ack_size = struct.unpack(">I", msg.payload[:4])[0]

        elif msg.type_id == MessageType.SET_PEER_BANDWIDTH:
            # Update window ack size; defer response to avoid send during recv
            if len(msg.payload) >= 4:
                size = struct.unpack(">I", msg.payload[:4])[0]
                self._window_ack_size = size
                self._pending_ack_size = size  # Will be sent before next recv

    def _flush_pending_responses(self) -> None:
        """Send any pending protocol responses (called before recv)."""
        if hasattr(self, '_pending_ack_size') and self._pending_ack_size > 0:
            self.send_window_ack_size(self._pending_ack_size)
            self._pending_ack_size = 0
