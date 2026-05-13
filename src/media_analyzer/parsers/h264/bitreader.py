"""Bitstream reader for H.264/H.265 NAL unit parsing.

Handles:
- Bit-level reading (read_bits, read_bit)
- Unsigned/signed exponential-Golomb coding (read_ue, read_se)
- RBSP anti-emulation byte removal (0x00 0x00 0x03 → 0x00 0x00)
"""


class BitReader:
    """
    Reads bits from a byte buffer with support for exp-Golomb decoding.

    The input bytes should be raw NALU payload (after the NALU header byte).
    RBSP emulation prevention bytes (0x000003) are transparently removed.
    """

    def __init__(self, data: bytes):
        # Remove emulation prevention bytes (RBSP)
        self._data = self._remove_emulation_prevention(data)
        self._pos = 0  # Current bit position
        self._size = len(self._data) * 8  # Total bits

    @staticmethod
    def _remove_emulation_prevention(data: bytes) -> bytes:
        """
        Remove RBSP emulation prevention bytes.

        In H.264/H.265, the sequence 0x00 0x00 0x03 in the raw bitstream
        is an emulation prevention code. The 0x03 byte should be removed
        to get the actual RBSP data.

        0x00 0x00 0x03 0x00 → 0x00 0x00 0x00
        0x00 0x00 0x03 0x01 → 0x00 0x00 0x01
        0x00 0x00 0x03 0x02 → 0x00 0x00 0x02
        0x00 0x00 0x03 0x03 → 0x00 0x00 0x03
        """
        result = bytearray()
        i = 0
        length = len(data)
        while i < length:
            if (i + 2 < length and
                    data[i] == 0x00 and data[i + 1] == 0x00 and data[i + 2] == 0x03):
                result.append(0x00)
                result.append(0x00)
                i += 3  # Skip the 0x03 byte
            else:
                result.append(data[i])
                i += 1
        return bytes(result)

    @property
    def bits_remaining(self) -> int:
        """Number of bits remaining to read."""
        return self._size - self._pos

    def read_bit(self) -> int:
        """Read a single bit (0 or 1)."""
        if self._pos >= self._size:
            raise EOFError("No more bits to read")
        byte_idx = self._pos >> 3
        bit_idx = 7 - (self._pos & 7)
        self._pos += 1
        return (self._data[byte_idx] >> bit_idx) & 1

    def read_bits(self, n: int) -> int:
        """Read n bits and return as unsigned integer."""
        if n == 0:
            return 0
        if self._pos + n > self._size:
            raise EOFError(f"Not enough bits: need {n}, have {self.bits_remaining}")
        value = 0
        for _ in range(n):
            value = (value << 1) | self.read_bit()
        return value

    def read_bool(self) -> bool:
        """Read a single bit as boolean."""
        return self.read_bit() == 1

    def read_ue(self) -> int:
        """
        Read unsigned exponential-Golomb coded value.

        Format: [leading zeros] 1 [info bits]
        Value = 2^leadingZeros - 1 + read_bits(leadingZeros)
        """
        leading_zeros = 0
        while self.read_bit() == 0:
            leading_zeros += 1
            if leading_zeros > 31:
                raise ValueError("Exp-Golomb: too many leading zeros")
        if leading_zeros == 0:
            return 0
        value = self.read_bits(leading_zeros)
        return (1 << leading_zeros) - 1 + value

    def read_se(self) -> int:
        """
        Read signed exponential-Golomb coded value.

        Mapping: 0→0, 1→1, 2→-1, 3→2, 4→-2, ...
        """
        code = self.read_ue()
        if code == 0:
            return 0
        sign = 1 if (code & 1) else -1
        return sign * ((code + 1) >> 1)

    def skip_bits(self, n: int) -> None:
        """Skip n bits."""
        if self._pos + n > self._size:
            self._pos = self._size
        else:
            self._pos += n

    def byte_aligned(self) -> bool:
        """Check if current position is byte-aligned."""
        return (self._pos & 7) == 0

    def align_to_byte(self) -> None:
        """Skip bits to reach next byte boundary."""
        if not self.byte_aligned():
            self.skip_bits(8 - (self._pos & 7))
