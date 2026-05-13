"""Background parse worker thread."""

from PySide6.QtCore import QThread, Signal, QElapsedTimer
from typing import List, Optional

from media_analyzer.core.models import PacketInfo, StreamInfo
from media_analyzer.core.source import DataSource
from media_analyzer.parsers.base import BaseParser
from media_analyzer.parsers.flv.parser import FLVParser


# Emit packets to UI in batches for efficiency
BATCH_SIZE = 1000
# Minimum interval between UI updates (ms) to avoid overwhelming the event loop
MIN_EMIT_INTERVAL_MS = 50


class ParseWorker(QThread):
    """
    Parses a media file in a background thread.
    Emits batches of PacketInfo to the UI thread via signals.

    Performance optimizations:
    - Batches packets (up to BATCH_SIZE) before emitting
    - Throttles signal emission to at most once per MIN_EMIT_INTERVAL_MS
    - Yields control back periodically to keep the thread responsive to stop()
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
            timer = QElapsedTimer()
            timer.start()
            last_emit_time = 0

            for packet in self._parser.parse_incremental(reader):
                if not self._running:
                    break

                batch.append(packet)

                # Emit when batch is full OR enough time has passed
                elapsed = timer.elapsed()
                if len(batch) >= BATCH_SIZE or (elapsed - last_emit_time >= MIN_EMIT_INTERVAL_MS and batch):
                    self.packets_ready.emit(batch.copy())
                    batch.clear()
                    last_emit_time = elapsed

                    # Emit progress (not every packet, only on batch emit)
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
