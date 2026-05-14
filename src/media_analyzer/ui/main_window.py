"""Main application window."""

from PySide6.QtWidgets import (
    QMainWindow, QSplitter, QWidget, QVBoxLayout, QHBoxLayout,
    QMenuBar, QMenu, QFileDialog, QInputDialog,
    QProgressBar, QLabel, QMessageBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from typing import Optional

from media_analyzer.core.models import PacketInfo, StreamInfo, TagType, NALUInfo
from media_analyzer.core.source import FileSource, StreamingHTTPSource
from media_analyzer.ui.packet_table.model import PacketTableModel
from media_analyzer.ui.packet_table.view import PacketTableView
from media_analyzer.ui.hex_view import HexViewWidget
from media_analyzer.ui.detail_panel import DetailPanelWidget
from media_analyzer.workers.parse_worker import ParseWorker


class MainWindow(QMainWindow):
    """
    Main application window.

    Menu Bar:
      File: Open File, Open URL, Stop, Exit
      Filter: Show Video, Show Audio, Show Script
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Media Analyzer - FLV")
        self.setMinimumSize(1280, 720)
        self.resize(1400, 800)

        self._worker: Optional[ParseWorker] = None
        self._stream_info: Optional[StreamInfo] = None
        self._current_packet: Optional[PacketInfo] = None

        self._setup_models()
        self._setup_ui()
        self._setup_menubar()
        self._setup_statusbar()
        self._connect_signals()

    def _setup_models(self):
        """Initialize data models."""
        self._table_model = PacketTableModel(self)

    def _setup_ui(self):
        """Build the main UI layout."""
        # Main horizontal splitter: table | detail panel
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: packet table
        self._table_view = PacketTableView(self._table_model)
        main_splitter.addWidget(self._table_view)

        # Right: vertical splitter with detail + hex
        right_splitter = QSplitter(Qt.Orientation.Vertical)

        self._detail_panel = DetailPanelWidget()
        self._hex_view = HexViewWidget()

        right_splitter.addWidget(self._detail_panel)
        right_splitter.addWidget(self._hex_view)
        right_splitter.setSizes([300, 400])

        main_splitter.addWidget(right_splitter)
        main_splitter.setSizes([800, 450])

        self.setCentralWidget(main_splitter)

    def _setup_menubar(self):
        """Build the menu bar."""
        menubar = self.menuBar()
        menubar.setStyleSheet("""
            QMenuBar {
                background-color: #2d2d3d;
                color: #ddd;
                border-bottom: 1px solid #444;
                padding: 2px;
            }
            QMenuBar::item {
                padding: 4px 10px;
                border-radius: 3px;
            }
            QMenuBar::item:selected {
                background-color: #4d4d5d;
            }
            QMenu {
                background-color: #2d2d3d;
                color: #ddd;
                border: 1px solid #444;
            }
            QMenu::item {
                padding: 5px 30px 5px 20px;
            }
            QMenu::item:selected {
                background-color: #264f78;
            }
            QMenu::separator {
                height: 1px;
                background-color: #444;
                margin: 4px 10px;
            }
            QMenu::indicator {
                width: 14px;
                height: 14px;
                margin-left: 4px;
            }
            QMenu::indicator:checked {
                background-color: #264f78;
                border: 1px solid #77a;
                border-radius: 2px;
            }
            QMenu::indicator:unchecked {
                background-color: #2d2d3d;
                border: 1px solid #555;
                border-radius: 2px;
            }
        """)

        # --- File Menu ---
        file_menu = menubar.addMenu("File")

        open_file_action = QAction("Open File...", self)
        open_file_action.setShortcut(QKeySequence("Ctrl+O"))
        open_file_action.triggered.connect(self._open_file)
        file_menu.addAction(open_file_action)

        open_url_action = QAction("Open URL...", self)
        open_url_action.setShortcut(QKeySequence("Ctrl+U"))
        open_url_action.triggered.connect(self._open_url)
        file_menu.addAction(open_url_action)

        file_menu.addSeparator()

        self._stop_action = QAction("Stop Parsing", self)
        self._stop_action.setShortcut(QKeySequence("Escape"))
        self._stop_action.triggered.connect(self._stop_parsing)
        self._stop_action.setEnabled(False)
        file_menu.addAction(self._stop_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.setShortcut(QKeySequence("Alt+F4"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # --- Filter Menu ---
        filter_menu = menubar.addMenu("Filter")

        self._filter_video_action = QAction("Video", self)
        self._filter_video_action.setCheckable(True)
        self._filter_video_action.setChecked(True)
        self._filter_video_action.setShortcut(QKeySequence("Ctrl+1"))
        self._filter_video_action.toggled.connect(self._apply_filters)
        filter_menu.addAction(self._filter_video_action)

        self._filter_audio_action = QAction("Audio", self)
        self._filter_audio_action.setCheckable(True)
        self._filter_audio_action.setChecked(True)
        self._filter_audio_action.setShortcut(QKeySequence("Ctrl+2"))
        self._filter_audio_action.toggled.connect(self._apply_filters)
        filter_menu.addAction(self._filter_audio_action)

        self._filter_script_action = QAction("Script", self)
        self._filter_script_action.setCheckable(True)
        self._filter_script_action.setChecked(True)
        self._filter_script_action.setShortcut(QKeySequence("Ctrl+3"))
        self._filter_script_action.toggled.connect(self._apply_filters)
        filter_menu.addAction(self._filter_script_action)

        filter_menu.addSeparator()

        self._filter_idr_action = QAction("IDR Frames Only", self)
        self._filter_idr_action.setCheckable(True)
        self._filter_idr_action.setChecked(False)
        self._filter_idr_action.setShortcut(QKeySequence("Ctrl+4"))
        self._filter_idr_action.toggled.connect(self._apply_filters)
        filter_menu.addAction(self._filter_idr_action)

        self._filter_sei_action = QAction("Has SEI Only", self)
        self._filter_sei_action.setCheckable(True)
        self._filter_sei_action.setChecked(False)
        self._filter_sei_action.setShortcut(QKeySequence("Ctrl+5"))
        self._filter_sei_action.toggled.connect(self._apply_filters)
        filter_menu.addAction(self._filter_sei_action)

        filter_menu.addSeparator()

        show_all_action = QAction("Show All", self)
        show_all_action.setShortcut(QKeySequence("Ctrl+0"))
        show_all_action.triggered.connect(self._show_all_filters)
        filter_menu.addAction(show_all_action)

        # --- View Menu ---
        view_menu = menubar.addMenu("View")

        self._view_group = None  # Will hold QActionGroup for TS views

        self._view_pkt_action = QAction("TS Packet View", self)
        self._view_pkt_action.setCheckable(True)
        self._view_pkt_action.setShortcut(QKeySequence("Ctrl+Shift+1"))
        self._view_pkt_action.triggered.connect(self._switch_to_pkt_view)
        view_menu.addAction(self._view_pkt_action)

        self._view_pes_action = QAction("TS PES View", self)
        self._view_pes_action.setCheckable(True)
        self._view_pes_action.setShortcut(QKeySequence("Ctrl+Shift+2"))
        self._view_pes_action.triggered.connect(self._switch_to_pes_view)
        view_menu.addAction(self._view_pes_action)

        self._view_standard_action = QAction("Standard View", self)
        self._view_standard_action.setCheckable(True)
        self._view_standard_action.setChecked(True)
        self._view_standard_action.setShortcut(QKeySequence("Ctrl+Shift+3"))
        self._view_standard_action.triggered.connect(self._switch_to_standard_view)
        view_menu.addAction(self._view_standard_action)

    def _setup_statusbar(self):
        """Build the status bar."""
        statusbar = self.statusBar()
        statusbar.setStyleSheet("""
            QStatusBar {
                background-color: #1e1e2e;
                color: #aaa;
                border-top: 1px solid #444;
            }
            QLabel {
                padding: 2px 8px;
            }
        """)

        self._status_label = QLabel("Ready - Open a file or URL to begin analysis")
        statusbar.addWidget(self._status_label, 1)

        self._info_label = QLabel("")
        statusbar.addPermanentWidget(self._info_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumWidth(200)
        self._progress_bar.setMaximumHeight(16)
        self._progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #444;
                border-radius: 3px;
                text-align: center;
                background-color: #2d2d3d;
                color: #aaa;
            }
            QProgressBar::chunk {
                background-color: #264f78;
            }
        """)
        self._progress_bar.hide()
        statusbar.addPermanentWidget(self._progress_bar)

    def _connect_signals(self):
        """Connect internal signals."""
        self._table_view.packet_selected.connect(self._on_packet_selected)
        self._detail_panel.nalu_selected.connect(self._on_nalu_selected)
        self._detail_panel.field_byte_range.connect(self._on_field_byte_range)

    # --- Actions ---

    def _open_file(self):
        """Open a local media file."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Media File",
            "",
            "Media Files (*.flv *.ts *.m2ts);;FLV Files (*.flv);;TS Files (*.ts *.m2ts);;All Files (*)"
        )
        if path:
            source = FileSource(path)
            self._start_parsing(source)

    def _open_url(self):
        """Open a network stream URL."""
        url, ok = QInputDialog.getText(
            self,
            "Open URL",
            "Enter stream URL (HTTP/HTTPS):",
            text="http://"
        )
        if ok and url and url != "http://":
            source = StreamingHTTPSource(url)
            self._start_parsing(source)

    def _stop_parsing(self):
        """Stop the current parsing operation."""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
            self._status_label.setText("Parsing stopped")
            self._stop_action.setEnabled(False)
            self._progress_bar.hide()

    def _apply_filters(self):
        """Apply tag type filters to the table via the proxy model."""
        self._table_view.proxy_model.set_filter(
            show_video=self._filter_video_action.isChecked(),
            show_audio=self._filter_audio_action.isChecked(),
            show_script=self._filter_script_action.isChecked(),
            only_idr=self._filter_idr_action.isChecked(),
            only_has_sei=self._filter_sei_action.isChecked(),
        )
        # Update status with filter info
        count = self._table_model.packet_count
        if count > 0:
            visible = self._table_view.proxy_model.rowCount()
            if visible < count:
                self._status_label.setText(f"{visible:,} / {count:,} tags (filtered)")
            else:
                self._status_label.setText(f"{count:,} tags")

    def _show_all_filters(self):
        """Reset all filters to show everything."""
        self._filter_video_action.setChecked(True)
        self._filter_audio_action.setChecked(True)
        self._filter_script_action.setChecked(True)
        self._filter_idr_action.setChecked(False)
        self._filter_sei_action.setChecked(False)

    # --- View Switching ---

    def _switch_to_pkt_view(self):
        """Switch to TS Packet view (every 188-byte packet as a row, with CC column)."""
        self._view_pkt_action.setChecked(True)
        self._view_pes_action.setChecked(False)
        self._view_standard_action.setChecked(False)
        self._table_view.set_ts_pkt_view(True)

    def _switch_to_pes_view(self):
        """Switch to PES view (only frame-start packets shown)."""
        self._view_pkt_action.setChecked(False)
        self._view_pes_action.setChecked(True)
        self._view_standard_action.setChecked(False)
        self._table_view.set_pes_view(True)

    def _switch_to_standard_view(self):
        """Switch to standard view (FLV-style, no TS columns)."""
        self._view_pkt_action.setChecked(False)
        self._view_pes_action.setChecked(False)
        self._view_standard_action.setChecked(True)
        self._table_view.set_ts_pkt_view(False)
        self._table_view.set_pes_view(False)

    # --- Parsing ---

    def _start_parsing(self, source):
        """Start parsing a data source in background thread."""
        # Stop any existing worker
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)

        # Clear existing data
        self._table_model.clear()
        self._detail_panel.clear()
        self._hex_view.clear()

        # Update UI state
        self._progress_bar.setValue(0)
        self._progress_bar.show()
        self._stop_action.setEnabled(True)
        self._status_label.setText(f"Parsing: {source.name}...")
        self._info_label.setText("")

        # Start worker thread
        self._worker = ParseWorker(source, self)
        self._worker.packets_ready.connect(self._on_packets_ready)
        self._worker.progress.connect(self._on_progress)
        self._worker.parse_finished.connect(self._on_parse_finished)
        self._worker.error.connect(self._on_parse_error)
        self._worker.start()

    def _on_packets_ready(self, packets):
        """Handle batch of parsed packets from worker."""
        # Temporarily disable view updates for performance
        self._table_view.setUpdatesEnabled(False)
        self._table_model.append_packets(packets)
        self._table_view.setUpdatesEnabled(True)

        # Update status (lightweight — just show count)
        count = self._table_model.packet_count
        self._status_label.setText(f"{count:,} tags loaded")

        # Process pending user events (clicks, keyboard) to keep UI interactive
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def _on_progress(self, current: int, total: int):
        """Update progress bar."""
        if total > 0:
            percent = int(current * 100 / total)
            self._progress_bar.setValue(percent)

    def _on_parse_finished(self, stream_info: StreamInfo):
        """Handle parsing completion."""
        self._stream_info = stream_info
        self._progress_bar.hide()
        self._stop_action.setEnabled(False)

        # Build info string
        count = self._table_model.packet_count
        info_parts = [f"{count:,} tags"]

        if stream_info.file_size > 0:
            size_mb = stream_info.file_size / (1024 * 1024)
            info_parts.append(f"{size_mb:.1f} MB")

        if stream_info.duration_ms > 0:
            dur_sec = stream_info.duration_ms / 1000
            mins = int(dur_sec // 60)
            secs = dur_sec % 60
            info_parts.append(f"{mins}:{secs:05.2f}")

        info_parts.append(stream_info.format_name)

        self._status_label.setText(
            f"Done - {stream_info.source_path}"
        )
        self._info_label.setText(" | ".join(info_parts))

        # Update window title
        self.setWindowTitle(f"Media Analyzer - {stream_info.source_path}")

        # Auto-switch view based on format
        if stream_info.format_name == "MPEG-TS":
            self._switch_to_pkt_view()
        else:
            self._switch_to_standard_view()

    def _on_parse_error(self, error_msg: str):
        """Handle parsing error."""
        self._progress_bar.hide()
        self._stop_action.setEnabled(False)
        self._status_label.setText(f"Error: {error_msg}")

        QMessageBox.warning(self, "Parse Error", error_msg)

    # --- Selection ---

    def _on_packet_selected(self, packet: PacketInfo):
        """Handle packet row selection - update detail and hex panels."""
        self._current_packet = packet

        # Update detail panel
        self._detail_panel.show_packet(packet)

        # Load hex data for this tag
        self._show_tag_hex(packet)

    def _on_nalu_selected(self, nalu: NALUInfo, packet: PacketInfo):
        """Handle NALU item click in detail panel - show NALU bytes in hex view."""
        if self._worker and self._worker.source:
            try:
                # NALU absolute offset in file:
                #   packet.offset = tag start (including 11-byte tag header)
                #   +11 = skip tag header
                #   +nalu.offset_in_tag = offset within tag data to the length prefix
                # The offset_in_tag already includes the 5-byte AVC header (frametype+codec+avcpkttype+cts)
                # and points to the length prefix of this NALU
                abs_offset = packet.offset + 11 + nalu.offset_in_tag
                # Read: 4-byte length prefix + NALU data
                read_size = min(4 + nalu.size, 4096)
                raw = self._worker.source.read_range(abs_offset, read_size)
                self._hex_view.set_data(raw, abs_offset)
            except Exception:
                self._hex_view.clear()

    def _show_tag_hex(self, packet: PacketInfo):
        """Load full tag bytes into the hex view."""
        if self._worker and self._worker.source:
            try:
                read_size = min(packet.tag_total_size, 4096)
                raw = self._worker.source.read_range(packet.offset, read_size)
                self._hex_view.set_data(raw, packet.offset)
                self._hex_view.clear_highlight()
            except Exception:
                self._hex_view.clear()

    def _on_field_byte_range(self, offset: int, length: int):
        """Handle detail field click — highlight corresponding bytes in hex view."""
        if self._current_packet is None:
            return

        # Determine how the offset relates to the currently displayed hex data.
        #
        # Two cases:
        # 1. Hex shows full tag: field offsets are relative to tag start.
        #    hex_base == packet.offset, so highlight_offset = offset directly.
        #
        # 2. Hex shows NALU data: NALU sub-field offsets are relative to NALU start
        #    (i.e. relative to the displayed data). Use offset directly.
        #
        # Strategy: try to use offset directly. If it fits within the displayed
        # data, highlight it. Otherwise reload the full tag and use offset as-is.

        data_len = len(self._hex_view._data)

        if 0 <= offset < data_len:
            # Offset fits within current hex data — highlight directly
            self._hex_view.highlight_range(offset, length)
        else:
            # Outside current view — reload full tag, then highlight
            self._show_tag_hex(self._current_packet)
            self._hex_view.highlight_range(offset, length)

    # --- Cleanup ---

    def closeEvent(self, event):
        """Clean up on window close."""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        event.accept()
