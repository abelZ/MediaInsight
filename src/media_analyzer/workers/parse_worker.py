"""Background parse worker thread."""

import logging
from PySide6.QtCore import QThread, Signal, QElapsedTimer
from typing import List, Optional

from media_analyzer.core.models import PacketInfo, StreamInfo
from media_analyzer.core.source import DataSource
from media_analyzer.parsers.base import BaseParser
from media_analyzer.parsers.flv.parser import FLVParser
from media_analyzer.parsers.ts.parser import TSParser
from media_analyzer.parsers.mp4.parser import MP4Parser

logger = logging.getLogger(__name__)


# Emit packets to UI in batches for efficiency
BATCH_SIZE = 2000
# Minimum interval between UI updates (ms) to avoid overwhelming the event loop
MIN_EMIT_INTERVAL_MS = 150
# First batch emits sooner so user sees content immediately
FIRST_BATCH_SIZE = 50
# Sleep after emit to give UI thread time to paint + handle user input
YIELD_MS = 10


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
    download_progress = Signal(int, int)  # (downloaded_bytes, total_bytes)
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
            # Set up download progress callback for streaming sources
            from media_analyzer.core.source import StreamingHTTPSource
            if isinstance(self._source, StreamingHTTPSource):
                self._source.set_download_callback(self._on_download_progress)

            reader = self._source.open()
            logger.info(f"Opened source: {self._source.name} ({self._source.size} bytes)")

            # Sniff format from first bytes (need enough for TS detection: 2 packets)
            header_peek = reader.read(400)
            reader.seek(0)

            if FLVParser.sniff(header_peek):
                self._parser = FLVParser()
                logger.info("Detected format: FLV")
            elif TSParser.sniff(header_peek):
                self._parser = TSParser()
                logger.info("Detected format: MPEG-TS")
            elif MP4Parser.sniff(header_peek):
                self._parser = MP4Parser()
                logger.info("Detected format: MP4/MOV")
            else:
                logger.warning(f"Unsupported format (magic: {header_peek[:4].hex()})")
                self.error.emit(f"Unsupported format (magic: {header_peek[:4].hex()})")
                return

            batch: List[PacketInfo] = []
            total_size = self._source.size
            timer = QElapsedTimer()
            timer.start()
            last_emit_time = 0
            first_batch_sent = False

            for packet in self._parser.parse_incremental(reader):
                if not self._running:
                    break

                batch.append(packet)

                # First batch: emit quickly so user sees content immediately
                if not first_batch_sent and len(batch) >= FIRST_BATCH_SIZE:
                    self.packets_ready.emit(batch.copy())
                    batch.clear()
                    first_batch_sent = True
                    last_emit_time = timer.elapsed()
                    # Yield to UI thread so it can paint the first rows
                    QThread.msleep(YIELD_MS)
                    continue

                # Subsequent batches: emit when full OR enough time has passed
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

                    # Yield to UI thread for painting + user interaction
                    QThread.msleep(YIELD_MS)

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
                logger.info(f"Parse complete: format={stream_info.format_name}, "
                            f"duration={stream_info.duration_ms}ms")
                self.parse_finished.emit(stream_info)

        except Exception as e:
            logger.error(f"Parse error: {e}", exc_info=True)
            self.error.emit(f"Parse error: {str(e)}")

    def stop(self):
        """Request graceful stop of parsing."""
        self._running = False

    def _on_download_progress(self, downloaded: int, total: int):
        """Callback from streaming source — emit download progress signal."""
        self.download_progress.emit(downloaded, total)
