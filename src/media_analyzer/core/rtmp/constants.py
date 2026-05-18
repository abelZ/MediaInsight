"""RTMP protocol constants and enumerations."""

from enum import IntEnum


class MessageType(IntEnum):
    """RTMP message type IDs."""
    SET_CHUNK_SIZE = 1
    ABORT = 2
    ACK = 3
    USER_CONTROL = 4
    WINDOW_ACK_SIZE = 5
    SET_PEER_BANDWIDTH = 6
    AUDIO = 8
    VIDEO = 9
    DATA_AMF3 = 15
    SHARED_OBJ_AMF3 = 16
    COMMAND_AMF3 = 17
    DATA_AMF0 = 18
    SHARED_OBJ_AMF0 = 19
    COMMAND_AMF0 = 20
    AGGREGATE = 22


class ChunkFmt(IntEnum):
    """Chunk header format types (bits 6-7 of basic header)."""
    TYPE_0 = 0   # 11 bytes: timestamp(3) + msg_length(3) + msg_type(1) + msg_stream_id(4)
    TYPE_1 = 1   # 7 bytes: timestamp_delta(3) + msg_length(3) + msg_type(1)
    TYPE_2 = 2   # 3 bytes: timestamp_delta(3)
    TYPE_3 = 3   # 0 bytes: use previous chunk's header


class UserControlEvent(IntEnum):
    """User control message event types."""
    STREAM_BEGIN = 0
    STREAM_EOF = 1
    STREAM_DRY = 2
    SET_BUFFER_LENGTH = 3
    STREAM_IS_RECORDED = 4
    PING_REQUEST = 6
    PING_RESPONSE = 7


class BandwidthLimitType(IntEnum):
    """Set Peer Bandwidth limit types."""
    HARD = 0
    SOFT = 1
    DYNAMIC = 2


# Human-readable message type labels
MESSAGE_TYPE_LABELS = {
    1: "SetChunkSize",
    2: "Abort",
    3: "Ack",
    4: "UserControl",
    5: "WindowAckSize",
    6: "SetPeerBandwidth",
    8: "Audio",
    9: "Video",
    15: "Data(AMF3)",
    16: "SharedObj(AMF3)",
    17: "Command(AMF3)",
    18: "Data(AMF0)",
    19: "SharedObj(AMF0)",
    20: "Command(AMF0)",
    22: "Aggregate",
}

# Protocol defaults
RTMP_DEFAULT_PORT = 1935
RTMP_HANDSHAKE_SIZE = 1536
RTMP_DEFAULT_CHUNK_SIZE = 128
RTMP_VERSION = 3

# Well-known chunk stream IDs
CSID_PROTOCOL_CONTROL = 2   # Protocol control messages
CSID_COMMAND = 3             # Command messages (connect, createStream, etc.)
CSID_AUDIO = 4              # Audio data
CSID_VIDEO = 6              # Video data
