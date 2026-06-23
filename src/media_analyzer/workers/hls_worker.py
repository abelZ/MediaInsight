"""HLS segment download and parse worker."""

import logging
import urllib.request
import urllib.error
from typing import List, Optional

from PySide6.QtCore import QThread, Signal, QElapsedTimer

from media_analyzer.core.models import PacketInfo, StreamInfo
from media_analyzer.core.source import BufferSource
from media_analyzer.parsers.base import BaseParser
from media_analyzer.parsers.flv.parser import FLVParser
from media_analyzer.parsers.ts.parser import TSParser
from media_analyzer.parsers.mp4.parser import MP4Parser
from media_analyzer.parsers.aac.parser import AACParser

logger = logging.getLogger(__name__)


# Batching (same as ParseWorker)
FIRST_BATCH_SIZE = 50
BATCH_SIZE = 2000
MIN_EMIT_INTERVAL_MS = 150
YIELD_MS = 10


class HLSSegmentWorker(QThread):
    """
    Downloads a single HLS segment and parses it.

    Emits packets incrementally like ParseWorker, so existing
    UI signal handlers work unchanged.
    """

    packets_ready = Signal(list)       # List[PacketInfo]
    progress = Signal(int, int)        # (bytes_processed, total_bytes)
    parse_finished = Signal(object)    # StreamInfo
    error = Signal(str)

    def __init__(self, segment_url: str, init_bytes: Optional[bytes] = None,
                 parent=None):
        super().__init__(parent)
        self._url = segment_url
        self._init_bytes = init_bytes
        self._running = True
        self._source: Optional[BufferSource] = None

    @property
    def source(self) -> Optional[BufferSource]:
        return self._source

    def run(self):
        try:
            # Step 1: Download segment
            logger.info(f"Downloading HLS segment: {self._url}")
            data = self._download()
            if not data or not self._running:
                return
            logger.info(f"Segment downloaded: {len(data)} bytes")

            # Step 2: For fMP4 segments, prepend the init segment so moov/codec
            # config is available to the parser. We detect "looks like an ISO
            # box" by checking bytes 4..8 — a TS file starts with 0x47, so this
            # won't collide.
            filename = self._url.split("/")[-1].split("?")[0]
            box_type = data[4:8] if len(data) >= 8 else b""
            looks_like_iso = box_type in (b"styp", b"moof", b"ftyp",
                                          b"moov", b"sidx", b"emsg")
            if self._init_bytes and looks_like_iso:
                buffer = self._init_bytes + data
                logger.debug(f"Prepended {len(self._init_bytes)} init bytes "
                             f"to {filename}")
            else:
                buffer = data

            self._source = BufferSource(buffer, name=filename)

            # Step 3: Detect format and create parser
            reader = self._source.open()
            header_peek = reader.read(400)
            reader.seek(0)

            parser: Optional[BaseParser] = None
            # Prefer MP4 sniff when the leading bytes look like an ISO box —
            # avoids the TS sniffer occasionally false-matching on a 0x47 byte.
            if looks_like_iso and MP4Parser.sniff(header_peek):
                parser = MP4Parser()
                logger.debug("Segment format: MP4/fMP4")
            elif TSParser.sniff(header_peek):
                parser = TSParser()
                logger.debug("Segment format: MPEG-TS")
            elif MP4Parser.sniff(header_peek):
                parser = MP4Parser()
                logger.debug("Segment format: MP4/fMP4")
            elif FLVParser.sniff(header_peek):
                parser = FLVParser()
                logger.debug("Segment format: FLV")
            elif AACParser.sniff(header_peek):
                parser = AACParser()
                logger.debug("Segment format: AAC (ADTS)")
            else:
                logger.warning(f"Unknown segment format (magic: {header_peek[:4].hex()})")
                self.error.emit(
                    f"Unknown segment format (magic: {header_peek[:4].hex()})")
                return

            # Step 4: Parse with batching
            batch: List[PacketInfo] = []
            total_size = self._source.size
            timer = QElapsedTimer()
            timer.start()
            last_emit_time = 0
            first_batch_sent = False

            for packet in parser.parse_incremental(reader):
                if not self._running:
                    break

                batch.append(packet)

                if not first_batch_sent and len(batch) >= FIRST_BATCH_SIZE:
                    self.packets_ready.emit(batch.copy())
                    batch.clear()
                    first_batch_sent = True
                    last_emit_time = timer.elapsed()
                    QThread.msleep(YIELD_MS)
                    continue

                elapsed = timer.elapsed()
                if (len(batch) >= BATCH_SIZE or
                        (elapsed - last_emit_time >= MIN_EMIT_INTERVAL_MS and batch)):
                    self.packets_ready.emit(batch.copy())
                    batch.clear()
                    last_emit_time = elapsed

                    if total_size > 0:
                        self.progress.emit(
                            packet.offset + packet.data_size, total_size)
                    QThread.msleep(YIELD_MS)

            # Emit remaining
            if batch and self._running:
                self.packets_ready.emit(batch.copy())

            if total_size > 0:
                self.progress.emit(total_size, total_size)

            # Emit stream info
            if parser and self._running:
                stream_info = parser.get_stream_info()
                stream_info.source_path = filename
                stream_info.file_size = total_size
                self.parse_finished.emit(stream_info)

        except Exception as e:
            self.error.emit(f"HLS segment error: {str(e)}")

    def _download(self) -> Optional[bytes]:
        """Download segment data."""
        try:
            req = urllib.request.Request(self._url)
            req.add_header("User-Agent", "MediaInsight/1.0")
            response = urllib.request.urlopen(req, timeout=30)
            data = response.read()
            response.close()
            return data
        except (urllib.error.URLError, IOError) as e:
            self.error.emit(f"Download failed: {str(e)}")
            return None

    def stop(self):
        self._running = False
