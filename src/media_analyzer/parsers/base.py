"""Base parser abstract class."""

from abc import ABC, abstractmethod
from typing import Generator, BinaryIO

from media_analyzer.core.models import PacketInfo, StreamInfo


class BaseParser(ABC):
    """Abstract parser interface - all format parsers implement this."""

    @abstractmethod
    def parse_header(self, data: bytes) -> dict:
        """Parse container header. Returns header info dict."""
        ...

    @abstractmethod
    def parse_incremental(self, source: BinaryIO) -> Generator[PacketInfo, None, None]:
        """
        Yield PacketInfo one-at-a-time for incremental UI updates.
        Supports both seekable files and non-seekable streams.
        """
        ...

    @abstractmethod
    def get_stream_info(self) -> StreamInfo:
        """Return aggregate stream info after (partial) parsing."""
        ...

    @classmethod
    @abstractmethod
    def sniff(cls, header_bytes: bytes) -> bool:
        """Return True if the first N bytes match this format."""
        ...
