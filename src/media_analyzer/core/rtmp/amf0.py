"""AMF0 encoder for RTMP command messages.

Decoding is handled by the existing parsers/flv/script.py AMF0Decoder.
This module provides encoding for client-to-server commands (connect, play, etc.).
"""

import struct
from typing import Any, Dict, List, Optional


# AMF0 type markers
AMF0_NUMBER = 0x00
AMF0_BOOLEAN = 0x01
AMF0_STRING = 0x02
AMF0_OBJECT = 0x03
AMF0_NULL = 0x05
AMF0_UNDEFINED = 0x06
AMF0_ECMA_ARRAY = 0x08
AMF0_OBJECT_END = 0x09
AMF0_STRICT_ARRAY = 0x0A


class AMF0Encoder:
    """Encodes Python values into AMF0 binary format."""

    def __init__(self):
        self._buf = bytearray()

    def encode(self, value: Any) -> None:
        """Auto-dispatch encoding based on Python type."""
        if value is None:
            self.encode_null()
        elif isinstance(value, bool):
            self.encode_boolean(value)
        elif isinstance(value, (int, float)):
            self.encode_number(float(value))
        elif isinstance(value, str):
            self.encode_string(value)
        elif isinstance(value, dict):
            self.encode_object(value)
        elif isinstance(value, (list, tuple)):
            self.encode_strict_array(value)
        else:
            self.encode_null()

    def encode_number(self, val: float) -> None:
        """Encode a number (float64)."""
        self._buf.append(AMF0_NUMBER)
        self._buf.extend(struct.pack(">d", val))

    def encode_boolean(self, val: bool) -> None:
        """Encode a boolean."""
        self._buf.append(AMF0_BOOLEAN)
        self._buf.append(0x01 if val else 0x00)

    def encode_string(self, val: str) -> None:
        """Encode a UTF-8 string (max 65535 bytes)."""
        encoded = val.encode("utf-8")
        self._buf.append(AMF0_STRING)
        self._buf.extend(struct.pack(">H", len(encoded)))
        self._buf.extend(encoded)

    def encode_object(self, obj: Dict[str, Any]) -> None:
        """Encode an AMF0 object (key-value pairs)."""
        self._buf.append(AMF0_OBJECT)
        for key, value in obj.items():
            # Property name (without type marker, just length-prefixed string)
            key_bytes = key.encode("utf-8")
            self._buf.extend(struct.pack(">H", len(key_bytes)))
            self._buf.extend(key_bytes)
            # Property value (with type marker)
            self.encode(value)
        # Object end marker: empty string + 0x09
        self._buf.extend(b"\x00\x00")
        self._buf.append(AMF0_OBJECT_END)

    def encode_null(self) -> None:
        """Encode null."""
        self._buf.append(AMF0_NULL)

    def encode_ecma_array(self, obj: Dict[str, Any]) -> None:
        """Encode an ECMA array (associative array)."""
        self._buf.append(AMF0_ECMA_ARRAY)
        self._buf.extend(struct.pack(">I", len(obj)))
        for key, value in obj.items():
            key_bytes = key.encode("utf-8")
            self._buf.extend(struct.pack(">H", len(key_bytes)))
            self._buf.extend(key_bytes)
            self.encode(value)
        # Object end marker
        self._buf.extend(b"\x00\x00")
        self._buf.append(AMF0_OBJECT_END)

    def encode_strict_array(self, arr: List[Any]) -> None:
        """Encode a strict (dense) array."""
        self._buf.append(AMF0_STRICT_ARRAY)
        self._buf.extend(struct.pack(">I", len(arr)))
        for item in arr:
            self.encode(item)

    def get_data(self) -> bytes:
        """Return encoded data and reset buffer."""
        data = bytes(self._buf)
        self._buf.clear()
        return data

    @classmethod
    def encode_command(cls, command_name: str, transaction_id: float,
                       command_object: Optional[Dict] = None,
                       *args: Any) -> bytes:
        """Convenience: encode a full RTMP command message.

        Format: string(command_name) + number(transaction_id) + object/null + args...
        """
        enc = cls()
        enc.encode_string(command_name)
        enc.encode_number(transaction_id)
        if command_object is not None:
            enc.encode_object(command_object)
        else:
            enc.encode_null()
        for arg in args:
            enc.encode(arg)
        return enc.get_data()
