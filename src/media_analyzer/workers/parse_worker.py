"""Background parse worker thread."""

from PySide6.QtCore import QThread, Signal
from typing import List, Optional

from media_analyzer.core.models import PacketInfo, StreamInfo
from media_analyzer.core.source import DataSource
from media_analyzer.parsers.base import BaseParser
from media_analyzer.parsers.flv.parser import FLVParser


# Batch size for emitting packets to UI
BATCH_SIZE = 500


class ParseWorker(QThread):
    """
    Parses a media file in a background thread.
    Emits batches of PacketInfo to the UI thread via signals.

    Thread-safety: only emits signals (which Qt marshals to the UI thread).
    The source is owned by this thread once started.
    """

    # Signals
    packets_ready = Signal(list)          # List[PacketInfo]
    progress = Signal(int, int)           # (bytes_processed, total_bytes)
    parse_finished = Signal(object)       # StreamInfo
    error = Signal(str)                   # Error message

    def __init__(self, source: DataSource, parent=None):
        super().__init__(parent)
        self._source = source
        self._running = True
        self._parser: Optional[BaseParser] = None

    @property
    def source(self) -> DataSource:
        """Access to the data source (for hex view reads)."""
        return self._source

    def run(self):
        """Main parse loop - runs in background thread."""
        try:
            reader = self._source.open()

            # Sniff format from first bytes
            header_peek = reader.read(16)
            reader.seek(0)

            if FLVParser.sniff(header_peek):
                self._parser = FLVParser()
            else:
                self.error.emit(f"Unsupported format (magic: {header_peek[:4].hex()})")
                return

            batch: List[PacketInfo] = []
            total_size = self._source.size

            for packet in self._parser.parse_incremental(reader):
                if not self._running:
                    break

                batch.append(packet)

                if len(batch) >= BATCH_SIZE:
                    self.packets_ready.emit(batch.copy())
                    batch.clear()

                    # Emit progress periodically
                    if total_size > 0:
                        self.progress.emit(
                            packet.offset + packet.data_size,
                            total_size
                        )

            # Emit remaining packets
            if batch and self._running:
                self.packets_ready.emit(batch.copy())

            # Emit final progress
            if total_size > 0:
                self.progress.emit(total_size, total_size)

            # Emit stream info
            if self._parser and self._running:
                stream_info = self._parser.get_stream_info()
                stream_info.source_path = self._source.name
                stream_info.file_size = self._source.size
                self.parse_finished.emit(stream_info)

        except Exception as e:
            self.error.emit(f"Parse error: {str(e)}")

    def stop(self):
        """Request graceful stop of parsing."""
        self._running = False
