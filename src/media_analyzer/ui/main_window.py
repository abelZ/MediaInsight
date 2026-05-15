"""Main application window."""

from PySide6.QtWidgets import (
    QMainWindow, QSplitter, QWidget, QVBoxLayout, QHBoxLayout,
    QMenuBar, QMenu, QFileDialog, QInputDialog,
    QProgressBar, QLabel, QMessageBox, QApplication,
    QStackedWidget, QTabBar,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QActionGroup
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
        self._pes_view_active: bool = False
        self._current_pes_data: Optional[bytes] = None  # Cached PES for NALU click
        self._format_detected: bool = False  # Whether we've auto-switched view for this file
        self._box_tree_view = None  # MP4 box tree widget (created on demand)
        self._current_file_path: Optional[str] = None  # Current file path for player page

        self._setup_models()
        self._setup_ui()
        self._setup_menubar()
        self._setup_statusbar()
        self._connect_signals()

    def _setup_models(self):
        """Initialize data models."""
        self._table_model = PacketTableModel(self)

    def _setup_ui(self):
        """Build the main UI layout with navigation bar and stacked pages."""
        # Central container: nav bar + stacked pages
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        # Navigation tab bar
        self._nav_bar = QTabBar()
        self._nav_bar.addTab("Analyzer")
        self._nav_bar.addTab("Player")
        self._nav_bar.setExpanding(False)
        self._nav_bar.setDrawBase(False)
        self._nav_bar.currentChanged.connect(self._on_nav_changed)
        central_layout.addWidget(self._nav_bar)

        # Stacked widget for pages
        self._pages = QStackedWidget()
        central_layout.addWidget(self._pages, 1)

        # --- Page 0: Analyzer (existing layout) ---
        analyzer_page = QWidget()
        analyzer_layout = QVBoxLayout(analyzer_page)
        analyzer_layout.setContentsMargins(0, 0, 0, 0)
        analyzer_layout.setSpacing(0)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter = main_splitter

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

        analyzer_layout.addWidget(main_splitter)
        self._pages.addWidget(analyzer_page)

        # --- Page 1: Player (lazy-loaded) ---
        self._player_page = None
        player_placeholder = QWidget()  # Placeholder until first use
        self._pages.addWidget(player_placeholder)

        self.setCentralWidget(central)

    def _setup_menubar(self):
        """Build the menu bar."""
        menubar = self.menuBar()

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
        self._view_pkt_action.setEnabled(False)  # Disabled until TS file detected
        view_menu.addAction(self._view_pkt_action)

        self._view_pes_action = QAction("TS PES View", self)
        self._view_pes_action.setCheckable(True)
        self._view_pes_action.setShortcut(QKeySequence("Ctrl+Shift+2"))
        self._view_pes_action.triggered.connect(self._switch_to_pes_view)
        self._view_pes_action.setEnabled(False)  # Disabled until TS file detected
        view_menu.addAction(self._view_pes_action)

        self._view_standard_action = QAction("Standard View", self)
        self._view_standard_action.setCheckable(True)
        self._view_standard_action.setChecked(True)
        self._view_standard_action.setShortcut(QKeySequence("Ctrl+Shift+3"))
        self._view_standard_action.triggered.connect(self._switch_to_standard_view)
        view_menu.addAction(self._view_standard_action)

        # --- Theme Menu ---
        self._setup_theme_menu(menubar)

        # --- Help Menu ---
        help_menu = menubar.addMenu("Help")

        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _show_about(self):
        """Show about dialog."""
        QMessageBox.about(
            self,
            "About MediaInsight",
            "<h3>MediaInsight</h3>"
            "<p>A cross-platform media analysis tool.</p>"
            "<p>Supports FLV, MPEG-TS, MP4/MOV format parsing at raw byte level.</p>"
            "<hr>"
            "<p><b>Developer:</b> Abel</p>"
            "<p><b>Email:</b> fylaotou@gmail.com</p>"
            "<p><b>Version:</b> 0.1.0</p>"
        )

    def _setup_statusbar(self):
        """Build the status bar."""
        statusbar = self.statusBar()

        self._status_label = QLabel("Ready - Open a file or URL to begin analysis")
        statusbar.addWidget(self._status_label, 1)

        self._info_label = QLabel("")
        statusbar.addPermanentWidget(self._info_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumWidth(200)
        self._progress_bar.setMaximumHeight(16)
        self._progress_bar.hide()
        statusbar.addPermanentWidget(self._progress_bar)

    def _setup_theme_menu(self, menubar):
        """Build the Theme menu with all built-in themes."""
        from media_analyzer.ui.themes import BUILTIN_THEMES, get_current_theme

        theme_menu = menubar.addMenu("Theme")
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)

        current = get_current_theme()

        for name, theme in BUILTIN_THEMES.items():
            action = QAction(theme.display_name, self)
            action.setCheckable(True)
            if theme.name == current.name:
                action.setChecked(True)
            action.setData(name)
            action.triggered.connect(lambda checked, n=name: self._apply_theme(n))
            theme_group.addAction(action)
            theme_menu.addAction(action)

    def _apply_theme(self, theme_name: str):
        """Apply selected theme to the entire application."""
        from media_analyzer.ui.themes import BUILTIN_THEMES, set_current_theme, generate_stylesheet
        from media_analyzer.app import apply_theme

        theme = BUILTIN_THEMES.get(theme_name)
        if theme is None:
            return

        set_current_theme(theme)
        app = QApplication.instance()
        if app:
            apply_theme(app, theme)

        # Force table model to refresh colors
        self._table_model.beginResetModel()
        self._table_model.endResetModel()

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
            "Media Files (*.flv *.ts *.m2ts *.mp4 *.m4a *.m4v *.mov);;FLV Files (*.flv);;TS Files (*.ts *.m2ts);;MP4 Files (*.mp4 *.m4a *.m4v *.mov);;All Files (*)"
        )
        if path:
            self._current_file_path = path
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

    # --- Navigation ---

    def _on_nav_changed(self, index: int):
        """Handle navigation tab change."""
        if index == 1:
            # Player page — lazy load
            self._ensure_player_page()
            # If we have a file loaded, pass it to the player
            if self._player_page and self._current_file_path:
                self._player_page.load_file(self._current_file_path)
        self._pages.setCurrentIndex(index)

    def _ensure_player_page(self):
        """Create the player page on first use."""
        if self._player_page is not None:
            return
        from media_analyzer.ui.player_page import PlayerPage
        self._player_page = PlayerPage()
        # Replace placeholder at index 1
        old = self._pages.widget(1)
        self._pages.removeWidget(old)
        old.deleteLater()
        self._pages.addWidget(self._player_page)

    # --- View Switching ---

    def _switch_to_pkt_view(self):
        """Switch to TS Packet view (every 188-byte packet as a row, with CC column)."""
        self._view_pkt_action.setChecked(True)
        self._view_pes_action.setChecked(False)
        self._view_standard_action.setChecked(False)
        self._pes_view_active = False
        self._table_view.set_ts_pkt_view(True)

    def _switch_to_pes_view(self):
        """Switch to PES view (only frame-start packets shown)."""
        self._view_pkt_action.setChecked(False)
        self._view_pes_action.setChecked(True)
        self._view_standard_action.setChecked(False)
        self._pes_view_active = True
        self._table_view.set_pes_view(True)

    def _switch_to_standard_view(self):
        """Switch to standard view (FLV-style, no TS columns)."""
        self._view_pkt_action.setChecked(False)
        self._view_pes_action.setChecked(False)
        self._view_standard_action.setChecked(True)
        self._pes_view_active = False
        self._table_view.set_ts_pkt_view(False)
        self._table_view.set_pes_view(False)

    def _swap_to_box_tree_view(self):
        """Replace the left panel with a box tree view for MP4 files."""
        from media_analyzer.ui.box_tree_view import BoxTreeView

        if hasattr(self, '_box_tree_view') and self._box_tree_view is not None:
            return  # Already in box tree mode

        self._box_tree_view = BoxTreeView()
        self._box_tree_view.box_selected.connect(self._on_packet_selected)

        # Hide table, show tree in the same splitter position
        self._table_view.hide()
        self._main_splitter.insertWidget(0, self._box_tree_view)

    def _swap_to_table_view(self):
        """Restore the table view (when switching from MP4 back to FLV/TS)."""
        if hasattr(self, '_box_tree_view') and self._box_tree_view is not None:
            self._box_tree_view.hide()
            self._box_tree_view.deleteLater()
            self._box_tree_view = None
        self._table_view.show()

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
        self._format_detected = False

        # Restore table view if previously in box tree mode (MP4)
        self._swap_to_table_view()

        # Update UI state
        self._progress_bar.setValue(0)
        self._progress_bar.show()
        self._stop_action.setEnabled(True)
        self._status_label.setText(f"Parsing: {source.name}...")
        self._info_label.setText("")

        # Reset view state for new file
        self._view_pkt_action.setEnabled(False)
        self._view_pes_action.setEnabled(False)
        self._view_pkt_action.setChecked(False)
        self._view_pes_action.setChecked(False)
        self._view_standard_action.setChecked(True)
        self._pes_view_active = False

        # Start worker thread
        self._worker = ParseWorker(source, self)
        self._worker.packets_ready.connect(self._on_packets_ready)
        self._worker.progress.connect(self._on_progress)
        self._worker.parse_finished.connect(self._on_parse_finished)
        self._worker.error.connect(self._on_parse_error)
        self._worker.start()

    def _on_packets_ready(self, packets):
        """Handle batch of parsed packets from worker."""
        # Early format detection: auto-adapt columns and view based on format
        if not self._format_detected and packets:
            self._format_detected = True
            first_pkt = packets[0]
            if first_pkt.script_data and "pid" in first_pkt.script_data:
                # TS stream — enable TS view actions and switch to packet view
                self._view_pkt_action.setEnabled(True)
                self._view_pes_action.setEnabled(True)
                self._switch_to_pkt_view()
            elif first_pkt.script_data and "box_type" in first_pkt.script_data:
                # MP4 — swap to box tree view
                self._view_pkt_action.setEnabled(False)
                self._view_pes_action.setEnabled(False)
                self._swap_to_box_tree_view()
            else:
                # FLV — use FLV columns, disable TS-specific view options
                self._view_pkt_action.setEnabled(False)
                self._view_pes_action.setEnabled(False)
                self._table_view.set_flv_view()

        # Route packets to appropriate view
        if hasattr(self, '_box_tree_view') and self._box_tree_view is not None:
            # MP4 mode: send to box tree view
            self._box_tree_view.append_packets(packets)
        else:
            # FLV/TS mode: send to table model
            self._table_view.setUpdatesEnabled(False)
            self._table_model.append_packets(packets)
            self._table_view.setUpdatesEnabled(True)

        # Update status (lightweight — just show count)
        count = self._table_model.packet_count
        self._status_label.setText(f"{count:,} tags loaded")

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
        self._current_pes_data = None  # Reset cached PES data

        # Update detail panel
        self._detail_panel.show_packet(packet)

        # Load hex data: PES mode shows full PES, otherwise single TS packet/tag
        if self._pes_view_active and packet.script_data and packet.script_data.get("pusi"):
            self._show_pes_hex(packet)
        else:
            self._show_tag_hex(packet)

    def _on_nalu_selected(self, nalu: NALUInfo, packet: PacketInfo):
        """Handle NALU item click in detail panel - show NALU bytes in hex view."""
        if self._worker and self._worker.source:
            try:
                if self._pes_view_active and self._current_pes_data:
                    # PES view: NALU offset is within ES data
                    # ES starts at _es_offset_in_pes within the PES data
                    es_offset = packet.script_data.get("_es_offset_in_pes", 0) if packet.script_data else 0
                    nalu_start_in_pes = es_offset + nalu.offset_in_tag
                    # Read NALU: start_code + nalu_data
                    # Detect start code length (3 or 4 bytes)
                    sc_len = 3
                    if (nalu_start_in_pes >= 1 and
                        nalu_start_in_pes + 3 < len(self._current_pes_data) and
                        self._current_pes_data[nalu_start_in_pes] == 0 and
                        self._current_pes_data[nalu_start_in_pes + 1] == 0 and
                        self._current_pes_data[nalu_start_in_pes + 2] == 0):
                        sc_len = 4

                    # Include start code + nalu data
                    nalu_total = sc_len + nalu.size
                    read_size = min(nalu_total, 4096)
                    raw = self._current_pes_data[nalu_start_in_pes:nalu_start_in_pes + read_size]
                    # Display offset relative to PES start
                    self._hex_view.set_data(raw, nalu_start_in_pes)
                else:
                    # FLV mode: NALU offset in tag
                    # packet.offset = tag start (including 11-byte tag header)
                    # +11 = skip tag header
                    # +nalu.offset_in_tag = offset within tag data to the length prefix
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

    def _show_pes_hex(self, packet: PacketInfo):
        """Load full reassembled PES bytes into the hex view (PES view mode)."""
        if self._worker and self._worker.source:
            try:
                pes_bytes = self._read_pes_from_source(packet)
                if pes_bytes:
                    self._current_pes_data = pes_bytes
                    self._hex_view.set_data(pes_bytes, packet.offset)
                    self._hex_view.clear_highlight()
                else:
                    # Fallback to single TS packet
                    self._show_tag_hex(packet)
            except Exception:
                self._hex_view.clear()

    def _read_pes_from_source(self, packet: PacketInfo) -> Optional[bytes]:
        """
        Reconstruct full PES data by reading consecutive TS packets from file.

        Starts at packet.offset, reads TS packets for the same PID,
        strips TS headers, concatenates payloads until the next PUSI=1.
        """
        if not self._worker or not self._worker.source:
            return None

        source = self._worker.source
        pid = packet.stream_id
        if pid is None or pid == 0:
            return None

        pes_data = bytearray()
        offset = packet.offset
        max_packets = 512  # Safety limit (~96KB of payload)
        first = True

        for _ in range(max_packets):
            try:
                raw = source.read_range(offset, 188)
            except Exception:
                break
            if len(raw) < 188 or raw[0] != 0x47:
                break

            # Parse TS header
            b1, b2, b3 = raw[1], raw[2], raw[3]
            pkt_pusi = (b1 >> 6) & 0x01
            pkt_pid = ((b1 & 0x1F) << 8) | b2
            afc = (b3 >> 4) & 0x03

            # Skip packets for other PIDs
            if pkt_pid != pid:
                offset += 188
                continue

            # If we hit a new PUSI (not the first one), PES is complete
            if pkt_pusi and not first:
                break
            first = False

            # Extract payload
            payload_offset = 4
            if afc & 0x02:  # Adaptation field present
                af_len = raw[4]
                payload_offset = 5 + af_len

            has_payload = (afc & 0x01) != 0
            if has_payload and payload_offset < 188:
                pes_data.extend(raw[payload_offset:188])

            offset += 188

        return bytes(pes_data) if pes_data else None

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
        if self._player_page:
            self._player_page.cleanup()
        event.accept()
