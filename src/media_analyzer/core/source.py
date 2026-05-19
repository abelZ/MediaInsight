"""Data source abstractions for file and network access."""

import io
import mmap
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Optional


class DataSource(ABC):
    """Abstract data source for parsers."""

    @abstractmethod
    def open(self) -> BinaryIO:
        """Return a file-like object for sequential reading."""
        ...

    @abstractmethod
    def read_range(self, offset: int, size: int) -> bytes:
        """Random-access read (for hex view on demand)."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Release resources."""
        ...

    @property
    @abstractmethod
    def size(self) -> int:
        """Total size in bytes (0 if unknown, e.g., live stream)."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Display name for this source."""
        ...


class MmapReader:
    """
    File-like reader over mmap with minimal overhead for large files.
    Provides read() and seek() interface over memory-mapped data.
    """

    def __init__(self, mm: mmap.mmap):
        self._mm = mm
        self._pos = 0
        self._size = len(mm)

    def read(self, n: int = -1) -> bytes:
        if n == -1 or n > self._size - self._pos:
            n = self._size - self._pos
        if n <= 0:
            return b""
        data = self._mm[self._pos:self._pos + n]
        self._pos += len(data)
        return bytes(data)

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = self._size + offset
        # Clamp to valid range
        self._pos = max(0, min(self._pos, self._size))
        return self._pos

    def tell(self) -> int:
        return self._pos

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FileSource(DataSource):
    """
    Memory-mapped local file source for maximum performance.
    Uses mmap for zero-copy reads - ideal for multi-GB files.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._file: Optional[io.BufferedReader] = None
        self._mmap: Optional[mmap.mmap] = None
        self._size_val: int = 0

    def open(self) -> BinaryIO:
        """Open file with memory mapping, return file-like reader."""
        self._size_val = self._path.stat().st_size
        self._file = open(self._path, "rb")

        if self._size_val > 0:
            # Memory-map the file for zero-copy access
            self._mmap = mmap.mmap(
                self._file.fileno(), 0, access=mmap.ACCESS_READ
            )
            return MmapReader(self._mmap)
        else:
            # Empty file, return the file object directly
            return self._file

    def read_range(self, offset: int, size: int) -> bytes:
        """Zero-copy slice from memory-mapped file."""
        if self._mmap is None:
            raise RuntimeError("Source not opened")
        end = min(offset + size, self._size_val)
        if offset >= self._size_val:
            return b""
        return bytes(self._mmap[offset:end])

    @property
    def size(self) -> int:
        return self._size_val

    @property
    def name(self) -> str:
        return self._path.name

    def close(self) -> None:
        if self._mmap:
            self._mmap.close()
            self._mmap = None
        if self._file:
            self._file.close()
            self._file = None


class BufferSource(DataSource):
    """
    In-memory buffer data source.
    Used for HLS segments that are downloaded into memory.
    """

    def __init__(self, data: bytes, name: str = "buffer"):
        self._data = data
        self._name = name

    def open(self) -> BinaryIO:
        return io.BytesIO(self._data)

    def read_range(self, offset: int, size: int) -> bytes:
        end = min(offset + size, len(self._data))
        if offset >= len(self._data):
            return b""
        return self._data[offset:end]

    @property
    def size(self) -> int:
        return len(self._data)

    @property
    def name(self) -> str:
        return self._name

    def close(self) -> None:
        pass


class HTTPStreamSource(DataSource):
    """
    HTTP/HTTPS progressive download source.
    Downloads the file and buffers it for parsing.
    Supports both Content-Length aware and chunked transfers.
    """

    def __init__(self, url: str):
        self._url = url
        self._response = None
        self._buffer = bytearray()
        self._total_size = 0
        self._downloaded = False

    def open(self) -> BinaryIO:
        """
        Open HTTP connection and download content into memory buffer.
        Returns a BytesIO for sequential reading.
        """
        req = urllib.request.Request(self._url)
        req.add_header("User-Agent", "MediaAnalyzer/1.0")
        req.add_header("Accept", "*/*")

        try:
            self._response = urllib.request.urlopen(req, timeout=30)
        except urllib.error.URLError as e:
            raise IOError(f"Failed to open URL: {e}") from e

        content_length = self._response.headers.get("Content-Length")
        if content_length:
            self._total_size = int(content_length)

        # Read all data into buffer
        self._buffer = bytearray()
        while True:
            chunk = self._response.read(65536)  # 64KB chunks
            if not chunk:
                break
            self._buffer.extend(chunk)

        self._total_size = len(self._buffer)
        self._downloaded = True

        return io.BytesIO(self._buffer)

    def read_range(self, offset: int, size: int) -> bytes:
        """Read range from downloaded buffer."""
        if not self._downloaded:
            raise RuntimeError("Source not opened/downloaded")
        end = min(offset + size, len(self._buffer))
        if offset >= len(self._buffer):
            return b""
        return bytes(self._buffer[offset:end])

    @property
    def size(self) -> int:
        return self._total_size

    @property
    def name(self) -> str:
        # Extract filename from URL
        from urllib.parse import urlparse
        parsed = urlparse(self._url)
        path = parsed.path
        if path:
            return path.split("/")[-1] or self._url
        return self._url

    def close(self) -> None:
        if self._response:
            self._response.close()
            self._response = None
        self._buffer = bytearray()


class StreamingHTTPSource(DataSource):
    """
    HTTP source that streams data incrementally.
    Used for live streams where we can't download the whole file first.
    Supports progressive parsing as data arrives.
    """

    def __init__(self, url: str):
        self._url = url
        self._response = None
        self._buffer = bytearray()
        self._total_size = 0
        self._reader: Optional[_StreamingReader] = None
        self._download_callback = None  # (downloaded, total) -> None

    @property
    def url(self) -> str:
        return self._url

    @property
    def downloaded_bytes(self) -> int:
        return len(self._buffer)

    @property
    def is_fully_downloaded(self) -> bool:
        return self._total_size > 0 and len(self._buffer) >= self._total_size

    def set_download_callback(self, callback) -> None:
        """Set a callback for download progress: callback(downloaded_bytes, total_bytes)."""
        self._download_callback = callback

    def open(self) -> BinaryIO:
        """Open HTTP connection, return streaming reader."""
        req = urllib.request.Request(self._url)
        req.add_header("User-Agent", "MediaAnalyzer/1.0")
        req.add_header("Accept", "*/*")

        try:
            self._response = urllib.request.urlopen(req, timeout=30)
        except urllib.error.URLError as e:
            raise IOError(f"Failed to open URL: {e}") from e

        content_length = self._response.headers.get("Content-Length")
        if content_length:
            self._total_size = int(content_length)

        self._reader = _StreamingReader(
            self._response, self._buffer, self._total_size, self._download_callback)
        return self._reader

    def read_range(self, offset: int, size: int) -> bytes:
        """Read from buffered data."""
        end = min(offset + size, len(self._buffer))
        if offset >= len(self._buffer):
            return b""
        return bytes(self._buffer[offset:end])

    def save_to_file(self, path: str) -> None:
        """Save downloaded buffer to a local file."""
        with open(path, "wb") as f:
            f.write(self._buffer)

    @property
    def size(self) -> int:
        return self._total_size

    @property
    def name(self) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(self._url)
        return parsed.path.split("/")[-1] or self._url

    def close(self) -> None:
        if self._response:
            self._response.close()
            self._response = None


class _StreamingReader:
    """File-like reader that buffers network data as it reads."""

    def __init__(self, response, buffer: bytearray, total_size: int = 0, progress_callback=None):
        self._response = response
        self._buffer = buffer
        self._pos = 0
        self._total_size = total_size  # Known from Content-Length
        self._progress_callback = progress_callback

    def _fetch(self, min_bytes: int) -> None:
        """Fetch at least min_bytes from network into buffer."""
        while min_bytes > 0:
            chunk = self._response.read(min(min_bytes, 65536))
            if not chunk:
                break
            self._buffer.extend(chunk)
            min_bytes -= len(chunk)
            if self._progress_callback:
                self._progress_callback(len(self._buffer), self._total_size)

    def read(self, n: int = -1) -> bytes:
        """Read n bytes, fetching from network as needed."""
        if n == -1:
            # Read all remaining
            while True:
                chunk = self._response.read(65536)
                if not chunk:
                    break
                self._buffer.extend(chunk)
                if self._progress_callback:
                    self._progress_callback(len(self._buffer), self._total_size)
            data = bytes(self._buffer[self._pos:])
            self._pos = len(self._buffer)
            return data

        # Ensure we have enough data in buffer
        needed = self._pos + n - len(self._buffer)
        if needed > 0:
            self._fetch(needed)

        available = len(self._buffer) - self._pos
        to_read = min(n, available)
        if to_read <= 0:
            return b""

        data = bytes(self._buffer[self._pos:self._pos + to_read])
        self._pos += to_read
        return data

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 2:
            # Seek from end — use known total size if available
            if self._total_size > 0:
                self._pos = self._total_size + offset
            else:
                # Must download all to know size
                while True:
                    chunk = self._response.read(65536)
                    if not chunk:
                        break
                    self._buffer.extend(chunk)
                    if self._progress_callback:
                        self._progress_callback(len(self._buffer), self._total_size)
                self._pos = len(self._buffer) + offset
        elif whence == 0:
            # Seek to absolute position — download enough data if needed
            if offset > len(self._buffer):
                self._fetch(offset - len(self._buffer))
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        self._pos = max(0, self._pos)
        return self._pos
