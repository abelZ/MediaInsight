"""AMF0/AMF3 decoder for FLV script tags."""

import struct
from typing import Any, List, Dict, Optional


class AMF0DecodeError(Exception):
    """Raised when AMF0 decoding fails."""
    pass


class AMF0Decoder:
    """
    Decodes AMF0 encoded data found in FLV script tags (onMetaData, etc).

    AMF0 Type Markers:
      0x00 = Number (float64)
      0x01 = Boolean
      0x02 = String (uint16 length + UTF-8)
      0x03 = Object
      0x05 = Null
      0x06 = Undefined
      0x07 = Reference
      0x08 = ECMA Array
      0x0A = Strict Array
      0x0B = Date
      0x0C = Long String
    """

    # AMF0 type markers
    NUMBER = 0x00
    BOOLEAN = 0x01
    STRING = 0x02
    OBJECT = 0x03
    MOVIECLIP = 0x04  # Reserved
    NULL = 0x05
    UNDEFINED = 0x06
    REFERENCE = 0x07
    ECMA_ARRAY = 0x08
    OBJECT_END = 0x09
    STRICT_ARRAY = 0x0A
    DATE = 0x0B
    LONG_STRING = 0x0C

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    @property
    def remaining(self) -> int:
        """Bytes remaining to be decoded."""
        return len(self._data) - self._pos

    def decode(self) -> Any:
        """Decode next AMF0 value."""
        if self._pos >= len(self._data):
            return None

        marker = self._data[self._pos]
        self._pos += 1

        if marker == self.NUMBER:
            return self._read_number()
        elif marker == self.BOOLEAN:
            return self._read_boolean()
        elif marker == self.STRING:
            return self._read_string()
        elif marker == self.OBJECT:
            return self._read_object()
        elif marker == self.NULL:
            return None
        elif marker == self.UNDEFINED:
            return None
        elif marker == self.REFERENCE:
            # Reference index (uint16) - skip for now
            self._pos += 2
            return None
        elif marker == self.ECMA_ARRAY:
            return self._read_ecma_array()
        elif marker == self.STRICT_ARRAY:
            return self._read_strict_array()
        elif marker == self.DATE:
            return self._read_date()
        elif marker == self.LONG_STRING:
            return self._read_long_string()
        else:
            # Unknown marker - stop decoding
            return None

    def _read_number(self) -> float:
        """Read float64 big-endian."""
        if self._pos + 8 > len(self._data):
            raise AMF0DecodeError("Truncated number")
        val = struct.unpack_from(">d", self._data, self._pos)[0]
        self._pos += 8
        return val

    def _read_boolean(self) -> bool:
        """Read single byte boolean."""
        if self._pos >= len(self._data):
            raise AMF0DecodeError("Truncated boolean")
        val = self._data[self._pos] != 0
        self._pos += 1
        return val

    def _read_string(self) -> str:
        """Read uint16-length-prefixed UTF-8 string."""
        if self._pos + 2 > len(self._data):
            raise AMF0DecodeError("Truncated string length")
        length = struct.unpack_from(">H", self._data, self._pos)[0]
        self._pos += 2
        if self._pos + length > len(self._data):
            raise AMF0DecodeError("Truncated string data")
        val = self._data[self._pos:self._pos + length].decode("utf-8", errors="replace")
        self._pos += length
        return val

    def _read_long_string(self) -> str:
        """Read uint32-length-prefixed UTF-8 string."""
        if self._pos + 4 > len(self._data):
            raise AMF0DecodeError("Truncated long string length")
        length = struct.unpack_from(">I", self._data, self._pos)[0]
        self._pos += 4
        if self._pos + length > len(self._data):
            raise AMF0DecodeError("Truncated long string data")
        val = self._data[self._pos:self._pos + length].decode("utf-8", errors="replace")
        self._pos += length
        return val

    def _read_object(self) -> Dict[str, Any]:
        """Read AMF0 object (key-value pairs until end marker)."""
        obj = {}
        while self._pos < len(self._data):
            # Read property name (string without type marker)
            key = self._read_string()
            # Check for end marker: empty key + 0x09
            if key == "" and self._pos < len(self._data) and self._data[self._pos] == self.OBJECT_END:
                self._pos += 1  # Skip end marker
                break
            # Read property value
            obj[key] = self.decode()
        return obj

    def _read_ecma_array(self) -> Dict[str, Any]:
        """Read ECMA array (associative array with approximate count)."""
        if self._pos + 4 > len(self._data):
            raise AMF0DecodeError("Truncated ECMA array count")
        # Count is approximate, ignore it
        self._pos += 4
        # Same format as object
        return self._read_object()

    def _read_strict_array(self) -> List[Any]:
        """Read strict array (ordered, with exact count)."""
        if self._pos + 4 > len(self._data):
            raise AMF0DecodeError("Truncated strict array count")
        count = struct.unpack_from(">I", self._data, self._pos)[0]
        self._pos += 4
        result = []
        for _ in range(count):
            if self._pos >= len(self._data):
                break
            result.append(self.decode())
        return result

    def _read_date(self) -> Dict[str, Any]:
        """Read date (float64 timestamp + int16 timezone offset)."""
        if self._pos + 10 > len(self._data):
            raise AMF0DecodeError("Truncated date")
        timestamp = struct.unpack_from(">d", self._data, self._pos)[0]
        self._pos += 8
        tz_offset = struct.unpack_from(">h", self._data, self._pos)[0]
        self._pos += 2
        return {"timestamp": timestamp, "tz_offset": tz_offset}

    def decode_all(self) -> List[Any]:
        """Decode all values in the buffer."""
        values = []
        while self._pos < len(self._data):
            try:
                val = self.decode()
                if val is not None or (self._pos < len(self._data)):
                    values.append(val)
            except (AMF0DecodeError, struct.error):
                break
        return values
