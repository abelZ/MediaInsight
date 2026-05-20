"""RTMP worker thread — manages RTMP session in background.

Connects to RTMP server, performs handshake and commands,
receives media data, and emits packets for both RTMP-level
and FLV-level views.
"""

import logging
import struct
import time
from typing import List, Optional, Tuple, Dict, Any

from PySide6.QtCore import QThread, Signal, QElapsedTimer

from media_analyzer.core.models import PacketInfo, TagType, StreamInfo
from media_analyzer.core.rtmp.client import RTMPClient, parse_rtmp_url, HandshakeData
from media_analyzer.core.rtmp.chunk import RTMPMessage
from media_analyzer.core.rtmp.constants import (
    MessageType, MESSAGE_TYPE_LABELS,
    RTMP_HANDSHAKE_SIZE,
)

logger = logging.getLogger(__name__)


# Batching parameters (same philosophy as ParseWorker)
FIRST_BATCH_SIZE = 20
BATCH_SIZE = 500
MIN_EMIT_INTERVAL_MS = 150
YIELD_MS = 10
STATS_INTERVAL_MS = 500  # Emit stats updates every 500ms


class RTMPWorker(QThread):
    """
    Background thread that manages an RTMP session.

    Emits two streams of packets:
    1. RTMP-level packets (handshake, protocol control, commands, media chunks)
    2. FLV-level packets (audio/video/script tags extracted from RTMP messages)

    Supports pause/resume and disconnect operations.
    """

    # Signals
    rtmp_packets_ready = Signal(list)   # List[PacketInfo] — RTMP protocol packets
    flv_tags_ready = Signal(list)       # List[PacketInfo] — FLV-level packets
    stats_updated = Signal(dict)        # {bytes, rtmp_count, flv_count, duration_ms}
    state_changed = Signal(str)         # "connecting"/"handshake"/"playing"/"paused"/"disconnected"/"error"
    error = Signal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = url
        self._running = True
        self._client: Optional[RTMPClient] = None

        # Packet counters
        self._rtmp_index = 0
        self._flv_index = 0
        self._byte_offset = 0  # Cumulative byte offset for RTMP packets
        self._flv_offset = 9 + 4  # Start after FLV header (9) + PrevTagSize0 (4)

        # FLV payload storage for Save As
        self._flv_payloads: List[Tuple[int, int, bytes]] = []  # (tag_type, timestamp, payload)
        self._has_video = False
        self._has_audio = False

        # Raw bytes storage for hex view (indexed by packet index)
        self._rtmp_raw_bytes: List[bytes] = []  # RTMP raw chunk bytes per packet
        self._flv_raw_bytes: List[bytes] = []   # FLV tag payload bytes per packet

        # FLV parser instance for reusing tag parsing
        self._flv_parser = None

    @property
    def flv_payloads(self) -> List[Tuple[int, int, bytes]]:
        """Raw FLV payloads for Save As export."""
        return self._flv_payloads

    def get_rtmp_raw_bytes(self, index: int) -> bytes:
        """Get raw chunk bytes for an RTMP packet by index (for hex view)."""
        if 0 <= index < len(self._rtmp_raw_bytes):
            return self._rtmp_raw_bytes[index]
        return b""

    def get_flv_raw_bytes(self, index: int) -> bytes:
        """Get raw bytes for an FLV tag by index (for hex view).

        Returns a synthetic complete FLV tag: 11-byte header + payload,
        matching the layout expected by the detail panel's byte_range offsets.
        """
        if 0 <= index < len(self._flv_raw_bytes):
            payload = self._flv_raw_bytes[index]
            # Build synthetic FLV tag header (11 bytes) to match detail panel byte ranges
            if 0 <= index < len(self._flv_payloads):
                tag_type_val, timestamp_ms, _ = self._flv_payloads[index]
                data_size = len(payload)
                ts_low = timestamp_ms & 0xFFFFFF
                ts_ext = (timestamp_ms >> 24) & 0xFF

                header = bytes([tag_type_val & 0x1F])           # Byte 0: TagType
                header += struct.pack(">I", data_size)[1:]      # Bytes 1-3: DataSize
                header += struct.pack(">I", ts_low)[1:]         # Bytes 4-6: Timestamp low
                header += bytes([ts_ext])                        # Byte 7: TimestampExtended
                header += b"\x00\x00\x00"                       # Bytes 8-10: StreamID

                return header + payload
            return payload
        return b""

    @property
    def has_video(self) -> bool:
        return self._has_video

    @property
    def has_audio(self) -> bool:
        return self._has_audio

    def run(self):
        """Main worker loop: connect → handshake → play → receive."""
        try:
            self._client = RTMPClient(self._url)
            timer = QElapsedTimer()
            timer.start()

            # --- Phase 1: Connect ---
            self.state_changed.emit("connecting")
            logger.info(f"RTMP connecting to {self._client.url.host}:{self._client.url.port}")
            self._client.connect()
            logger.info("TCP connection established")

            if not self._running:
                return

            # --- Phase 2: Handshake ---
            self.state_changed.emit("handshake")
            hs = self._client.handshake()
            logger.info("RTMP handshake completed")
            self._emit_handshake_packets(hs)

            if not self._running:
                return

            # --- Phase 3: Connect command ---
            logger.debug(f"Sending connect command (app={self._client.url.app})")
            self._client.send_connect()

            # Read responses until we get _result for connect
            if not self._wait_for_connect_result():
                return
            logger.info("Connect command accepted")

            if not self._running:
                return

            # --- Phase 4: CreateStream ---
            logger.debug("Sending createStream command")
            self._client.send_create_stream()

            # Wait for createStream result
            if not self._wait_for_create_stream_result():
                return
            logger.info("createStream accepted")

            if not self._running:
                return

            # --- Phase 5: Play ---
            logger.info(f"Sending play command (stream={self._client.url.stream})")
            self._client.send_set_buffer_length(self._client._msg_stream_id, 1000)
            self._client.send_play()

            self.state_changed.emit("playing")
            logger.info("RTMP playback started — receiving media data")

            # --- Phase 6: Receive loop ---
            self._recv_loop(timer)

        except Exception as e:
            # Only emit error if not a user-initiated stop
            if self._running:
                logger.error(f"RTMP error: {e}", exc_info=True)
                self.state_changed.emit("error")
                self.error.emit(f"RTMP error: {str(e)}")
        finally:
            if self._client:
                self._client.disconnect()
            logger.info(f"RTMP session ended (rtmp={self._rtmp_index} pkts, flv={self._flv_index} tags)")
            self.state_changed.emit("disconnected")

    def _recv_loop(self, timer: QElapsedTimer):
        """Main receive loop — reads messages and emits packets."""
        rtmp_batch: List[PacketInfo] = []
        flv_batch: List[PacketInfo] = []
        last_emit_time = timer.elapsed()
        last_stats_time = timer.elapsed()
        first_batch_sent = False

        while self._running and self._client and self._client.is_connected:
            msg = self._client.recv_message()
            if msg is None:
                if not self._client.is_connected:
                    break
                continue

            # Create RTMP-level packet
            rtmp_pkt = self._make_rtmp_packet(msg)
            rtmp_batch.append(rtmp_pkt)

            # For media/data messages, also create FLV-level packet
            flv_pkt = self._make_flv_packet(msg)
            if flv_pkt is not None:
                flv_batch.append(flv_pkt)

            # Emit first batch quickly
            if not first_batch_sent and len(rtmp_batch) >= FIRST_BATCH_SIZE:
                self._emit_batches(rtmp_batch, flv_batch)
                rtmp_batch.clear()
                flv_batch.clear()
                first_batch_sent = True
                last_emit_time = timer.elapsed()
                QThread.msleep(YIELD_MS)
                continue

            # Subsequent batches: by size or time
            elapsed = timer.elapsed()
            if (len(rtmp_batch) >= BATCH_SIZE or
                    (elapsed - last_emit_time >= MIN_EMIT_INTERVAL_MS and rtmp_batch)):
                self._emit_batches(rtmp_batch, flv_batch)
                rtmp_batch.clear()
                flv_batch.clear()
                last_emit_time = elapsed
                QThread.msleep(YIELD_MS)

            # Emit stats periodically
            if elapsed - last_stats_time >= STATS_INTERVAL_MS:
                self._emit_stats(timer.elapsed())
                last_stats_time = elapsed

        # Emit remaining
        if rtmp_batch and self._running:
            self._emit_batches(rtmp_batch, flv_batch)

        # Final stats
        self._emit_stats(timer.elapsed())

    def _emit_batches(self, rtmp_batch: List[PacketInfo], flv_batch: List[PacketInfo]):
        """Emit both RTMP and FLV batches."""
        if rtmp_batch:
            self.rtmp_packets_ready.emit(rtmp_batch.copy())
        if flv_batch:
            self.flv_tags_ready.emit(flv_batch.copy())

    def _emit_stats(self, elapsed_ms: int):
        """Emit statistics update."""
        self.stats_updated.emit({
            "bytes": self._client.bytes_received if self._client else 0,
            "rtmp_count": self._rtmp_index,
            "flv_count": self._flv_index,
            "duration_ms": elapsed_ms,
        })

    def _emit_handshake_packets(self, hs: HandshakeData):
        """Create and emit RTMP packets for the handshake phase."""
        packets = []

        # C0+C1 sent
        c0c1_data = hs.c0 + hs.c1
        packets.append(PacketInfo(
            index=self._rtmp_index,
            tag_type=TagType.SCRIPT,
            timestamp=0,
            data_size=len(c0c1_data),
            offset=self._byte_offset,
            stream_id=0,
            tag_total_size=len(c0c1_data),
            script_data={
                "rtmp_message_type": "Handshake",
                "rtmp_message_type_id": 0,
                "direction": "C→S",
                "csid": 0,
                "msg_stream_id": 0,
                "handshake_phase": "C0+C1",
                "version": hs.c0[0],
                "c1_time": struct.unpack(">I", hs.c1[:4])[0],
            },
        ))
        self._rtmp_index += 1
        self._byte_offset += len(c0c1_data)

        # S0+S1+S2 received
        s_data = hs.s0 + hs.s1 + hs.s2
        packets.append(PacketInfo(
            index=self._rtmp_index,
            tag_type=TagType.SCRIPT,
            timestamp=0,
            data_size=len(s_data),
            offset=self._byte_offset,
            stream_id=0,
            tag_total_size=len(s_data),
            script_data={
                "rtmp_message_type": "Handshake",
                "rtmp_message_type_id": 0,
                "direction": "S→C",
                "csid": 0,
                "msg_stream_id": 0,
                "handshake_phase": "S0+S1+S2",
                "version": hs.s0[0],
                "s1_time": struct.unpack(">I", hs.s1[:4])[0],
            },
        ))
        self._rtmp_index += 1
        self._byte_offset += len(s_data)

        # C2 sent
        packets.append(PacketInfo(
            index=self._rtmp_index,
            tag_type=TagType.SCRIPT,
            timestamp=0,
            data_size=len(hs.c2),
            offset=self._byte_offset,
            stream_id=0,
            tag_total_size=len(hs.c2),
            script_data={
                "rtmp_message_type": "Handshake",
                "rtmp_message_type_id": 0,
                "direction": "C→S",
                "csid": 0,
                "msg_stream_id": 0,
                "handshake_phase": "C2",
            },
        ))
        self._rtmp_index += 1
        self._byte_offset += len(hs.c2)

        # Store raw bytes for hex view
        self._rtmp_raw_bytes.append(c0c1_data)
        self._rtmp_raw_bytes.append(s_data)
        self._rtmp_raw_bytes.append(hs.c2)

        self.rtmp_packets_ready.emit(packets)

    def _make_rtmp_packet(self, msg: RTMPMessage) -> PacketInfo:
        """Create an RTMP-level PacketInfo from a received message."""
        type_label = MESSAGE_TYPE_LABELS.get(msg.type_id, f"Unknown({msg.type_id})")

        script_data: Dict[str, Any] = {
            "rtmp_message_type": type_label,
            "rtmp_message_type_id": msg.type_id,
            "direction": "S→C",
            "csid": msg.csid,
            "msg_stream_id": msg.msg_stream_id,
        }

        # Parse type-specific details
        self._enrich_rtmp_details(msg, script_data)

        # Determine tag_type for row coloring
        if msg.type_id == MessageType.VIDEO:
            tag_type = TagType.VIDEO
        elif msg.type_id == MessageType.AUDIO:
            tag_type = TagType.AUDIO
        else:
            tag_type = TagType.SCRIPT

        pkt = PacketInfo(
            index=self._rtmp_index,
            tag_type=tag_type,
            timestamp=msg.timestamp,
            data_size=len(msg.payload),
            offset=self._byte_offset,
            stream_id=msg.msg_stream_id,
            tag_total_size=len(msg.raw_bytes) if msg.raw_bytes else len(msg.payload),
            script_data=script_data,
        )

        # Store raw bytes for hex view
        self._rtmp_raw_bytes.append(msg.raw_bytes if msg.raw_bytes else msg.payload)

        self._rtmp_index += 1
        self._byte_offset += len(msg.raw_bytes) if msg.raw_bytes else len(msg.payload)
        return pkt

    def _enrich_rtmp_details(self, msg: RTMPMessage, script_data: Dict[str, Any]):
        """Parse type-specific details for RTMP packet display."""
        payload = msg.payload

        if msg.type_id == MessageType.SET_CHUNK_SIZE and len(payload) >= 4:
            script_data["chunk_size"] = struct.unpack(">I", payload[:4])[0]

        elif msg.type_id == MessageType.WINDOW_ACK_SIZE and len(payload) >= 4:
            script_data["window_ack_size"] = struct.unpack(">I", payload[:4])[0]

        elif msg.type_id == MessageType.SET_PEER_BANDWIDTH and len(payload) >= 5:
            script_data["window_size"] = struct.unpack(">I", payload[:4])[0]
            script_data["limit_type"] = payload[4]

        elif msg.type_id == MessageType.USER_CONTROL and len(payload) >= 2:
            event_type = struct.unpack(">H", payload[:2])[0]
            event_names = {
                0: "StreamBegin", 1: "StreamEOF", 2: "StreamDry",
                3: "SetBufferLength", 4: "StreamIsRecorded",
                6: "PingRequest", 7: "PingResponse",
            }
            script_data["event_type"] = event_names.get(event_type, f"Unknown({event_type})")
            if len(payload) >= 6:
                script_data["event_stream_id"] = struct.unpack(">I", payload[2:6])[0]

        elif msg.type_id in (MessageType.COMMAND_AMF0, MessageType.DATA_AMF0):
            try:
                from media_analyzer.parsers.flv.script import AMF0Decoder
                decoder = AMF0Decoder(payload)
                values = []
                while decoder.remaining > 0:
                    values.append(decoder.decode())
                if values:
                    if isinstance(values[0], str):
                        script_data["command_name"] = values[0]
                    if len(values) > 1 and isinstance(values[1], (int, float)):
                        script_data["transaction_id"] = values[1]
                    if len(values) > 2:
                        script_data["amf_objects"] = values[2:]
            except Exception:
                pass

        elif msg.type_id == MessageType.ACK and len(payload) >= 4:
            script_data["sequence_number"] = struct.unpack(">I", payload[:4])[0]

    def _make_flv_packet(self, msg: RTMPMessage) -> Optional[PacketInfo]:
        """
        Create FLV-level PacketInfo from audio/video/data messages.
        Reuses FLVParser methods for deep parsing (codec, NALU, etc.).
        """
        # Only process media and data messages
        if msg.type_id == MessageType.AUDIO:
            tag_type = TagType.AUDIO
            self._has_audio = True
        elif msg.type_id == MessageType.VIDEO:
            tag_type = TagType.VIDEO
            self._has_video = True
        elif msg.type_id in (MessageType.DATA_AMF0, MessageType.DATA_AMF3):
            tag_type = TagType.SCRIPT
        else:
            return None

        # Store payload for Save As
        self._flv_payloads.append((tag_type.value, msg.timestamp, msg.payload))

        # Create packet
        packet = PacketInfo(
            index=self._flv_index,
            tag_type=tag_type,
            timestamp=msg.timestamp,
            data_size=len(msg.payload),
            offset=self._flv_offset,
            stream_id=0,
            tag_total_size=11 + len(msg.payload),  # Simulated FLV tag size
        )

        # Use FLVParser to parse tag content (codec, NALUs, etc.)
        if self._flv_parser is None:
            from media_analyzer.parsers.flv.parser import FLVParser
            self._flv_parser = FLVParser()

        try:
            if tag_type == TagType.VIDEO and len(msg.payload) > 0:
                self._flv_parser._parse_video_tag(packet, msg.payload)
            elif tag_type == TagType.AUDIO and len(msg.payload) > 0:
                self._flv_parser._parse_audio_tag(packet, msg.payload)
            elif tag_type == TagType.SCRIPT and len(msg.payload) > 0:
                self._flv_parser._parse_script_tag(packet, msg.payload)
        except Exception:
            pass  # Gracefully handle malformed tags

        # Store raw payload for hex view
        self._flv_raw_bytes.append(msg.payload)

        self._flv_index += 1
        # Advance virtual FLV offset: tag_header(11) + data + prev_tag_size(4)
        self._flv_offset += 11 + len(msg.payload) + 4

        return packet

    def _wait_for_connect_result(self) -> bool:
        """Read messages until we get _result for connect command."""
        for _ in range(50):  # Safety limit
            if not self._running:
                return False
            msg = self._client.recv_message()
            if msg is None:
                return False

            # Emit as RTMP packet
            pkt = self._make_rtmp_packet(msg)
            self.rtmp_packets_ready.emit([pkt])

            # Check for _result
            if msg.type_id == MessageType.COMMAND_AMF0:
                try:
                    from media_analyzer.parsers.flv.script import AMF0Decoder
                    decoder = AMF0Decoder(msg.payload)
                    cmd_name = decoder.decode()
                    if cmd_name == "_result":
                        return True
                    elif cmd_name == "_error":
                        self.error.emit("Server rejected connect command")
                        return False
                except Exception:
                    pass

        self.error.emit("Timeout waiting for connect result")
        return False

    def _wait_for_create_stream_result(self) -> bool:
        """Read messages until we get _result for createStream."""
        for _ in range(50):
            if not self._running:
                return False
            msg = self._client.recv_message()
            if msg is None:
                return False

            pkt = self._make_rtmp_packet(msg)
            self.rtmp_packets_ready.emit([pkt])

            if msg.type_id == MessageType.COMMAND_AMF0:
                try:
                    from media_analyzer.parsers.flv.script import AMF0Decoder
                    decoder = AMF0Decoder(msg.payload)
                    cmd_name = decoder.decode()
                    if cmd_name == "_result":
                        # Parse transaction_id and stream_id
                        tid = decoder.decode()  # transaction_id
                        obj = decoder.decode()  # command object (null)
                        stream_id = decoder.decode()  # stream ID (number)
                        if isinstance(stream_id, (int, float)):
                            self._client.set_msg_stream_id(int(stream_id))
                        return True
                    elif cmd_name == "_error":
                        self.error.emit("Server rejected createStream")
                        return False
                except Exception:
                    pass

        self.error.emit("Timeout waiting for createStream result")
        return False

    def pause(self) -> None:
        """Pause data reception (connection stays alive)."""
        if self._client:
            self._client.pause()
            self.state_changed.emit("paused")

    def resume(self) -> None:
        """Resume data reception."""
        if self._client:
            self._client.resume()
            self.state_changed.emit("playing")

    def stop(self) -> None:
        """Request graceful stop."""
        self._running = False
        if self._client:
            self._client.disconnect()
