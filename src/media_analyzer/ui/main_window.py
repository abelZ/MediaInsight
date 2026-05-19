"""Main application window."""

from PySide6.QtWidgets import (
    QMainWindow, QSplitter, QWidget, QVBoxLayout, QHBoxLayout,
    QMenuBar, QMenu, QFileDialog, QInputDialog,
    QProgressBar, QLabel, QMessageBox, QApplication,
    QStackedWidget, QTabBar,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QActionGroup
from typing import Optional, List

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
        self.setWindowTitle("MediaInsight")
        self.setMinimumSize(1280, 720)
        self.resize(1400, 800)

        self._worker: Optional[ParseWorker] = None
        self._rtmp_worker = None  # RTMPWorker instance (when RTMP active)
        self._stream_info: Optional[StreamInfo] = None
        self._current_packet: Optional[PacketInfo] = None
        self._pes_view_active: bool = False
        self._current_pes_data: Optional[bytes] = None  # Cached PES for NALU click
        self._format_detected: bool = False  # Whether we've auto-switched view for this file
        self._box_tree_view = None  # MP4 box tree widget (created on demand)
        self._rtmp_view = None  # RTMPDualView widget (created on demand)
        self._hls_view = None  # HLS segment list view (created on demand)
        self._current_file_path: Optional[str] = None  # Current file path for player page
        self._all_packets: List[PacketInfo] = []  # All packets for bitrate analysis (all formats)

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
        self._nav_bar.addTab("Bitrate")
        self._nav_bar.addTab("Player")
        self._nav_bar.setExpanding(False)
        self._nav_bar.setDrawBase(False)
        self._nav_bar.currentChanged.connect(self._on_nav_changed)
        central_layout.addWidget(self._nav_bar)

        # RTMP Control bar (hidden by default, shown during RTMP sessions)
        from media_analyzer.ui.rtmp_control_bar import RTMPControlBar
        self._rtmp_control_bar = RTMPControlBar()
        self._rtmp_control_bar.hide()
        self._rtmp_control_bar.pause_clicked.connect(self._rtmp_pause)
        self._rtmp_control_bar.resume_clicked.connect(self._rtmp_resume)
        self._rtmp_control_bar.disconnect_clicked.connect(self._rtmp_disconnect)
        central_layout.addWidget(self._rtmp_control_bar)

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

        # --- Page 1: Bitrate (lazy-loaded) ---
        self._bitrate_page = None
        bitrate_placeholder = QWidget()
        self._pages.addWidget(bitrate_placeholder)

        # --- Page 2: Player (lazy-loaded) ---
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

        self._save_as_action = QAction("Save As...", self)
        self._save_as_action.setShortcut(QKeySequence("Ctrl+S"))
        self._save_as_action.triggered.connect(self._save_as)
        self._save_as_action.setEnabled(False)
        file_menu.addAction(self._save_as_action)

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
        about_action.setMenuRole(QAction.MenuRole.NoRole)  # Prevent macOS from moving it
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _show_about(self):
        """Show about dialog."""
        QMessageBox.about(
            self,
            "About MediaInsight",
            "<h3>MediaInsight</h3>"
            "<p>A cross-platform media analysis tool.</p>"
            "<p>Parses media files at raw byte level without FFmpeg dependency.</p>"
            "<hr>"
            "<p><b>Supported Formats:</b></p>"
            "<ul>"
            "<li>FLV (Flash Video)</li>"
            "<li>MPEG-TS (Transport Stream)</li>"
            "<li>MP4/MOV (ISO BMFF)</li>"
            "<li>RTMP / RTMPS (Live Stream)</li>"
            "<li>HLS / M3U8 (HTTP Live Streaming)</li>"
            "<li>HTTP/HTTPS progressive download</li>"
            "</ul>"
            "<p><b>Features:</b> Packet analysis, PES reassembly, NALU parsing, "
            "Bitrate chart, Video playback, MediaInfo display</p>"
            "<hr>"
            "<p><b>Developer:</b> Abel</p>"
            "<p><b>Email:</b> fylaotou@gmail.com</p>"
            "<p><b>Version:</b> 0.3.0</p>"
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
            self._reset_player_on_new_file()
            source = FileSource(path)
            self._start_parsing(source)

    def _open_url(self):
        """Open a network stream URL."""
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Open URL")
        dialog.setLabelText("Enter stream URL (HTTP/HTTPS/RTMP/RTMPS/HLS):")
        dialog.setTextValue("http://")
        dialog.setInputMode(QInputDialog.InputMode.TextInput)
        dialog.resize(500, 150)
        ok = dialog.exec()
        url = dialog.textValue()
        if ok and url and url not in ("http://", "rtmp://"):
            self._current_file_path = url
            self._reset_player_on_new_file()
            if url.startswith("rtmp://") or url.startswith("rtmps://"):
                self._start_rtmp(url)
            elif ".m3u8" in url.split("?")[0] or url.endswith(".m3u8"):
                self._start_hls(url)
            else:
                source = StreamingHTTPSource(url)
                self._start_parsing(source)

    def _save_as(self):
        """Save downloaded stream content to a local file."""
        # RTMP mode: export as FLV
        if self._rtmp_worker or (self._rtmp_view and self._rtmp_view.flv_packet_count > 0):
            self._save_as_flv()
            return

        from media_analyzer.core.source import StreamingHTTPSource
        if not self._worker:
            return
        source = self._worker.source
        if not isinstance(source, StreamingHTTPSource):
            QMessageBox.information(self, "Save As",
                "Save As is only available for URL streams.")
            return
        if source.downloaded_bytes == 0:
            QMessageBox.warning(self, "Save As", "No data downloaded yet.")
            return

        # Suggest filename from URL
        suggested = source.name or "download.bin"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save As", suggested, "All Files (*)")
        if path:
            try:
                source.save_to_file(path)
                size_mb = source.downloaded_bytes / (1024 * 1024)
                self._status_label.setText(f"Saved: {path} ({size_mb:.1f} MB)")
            except Exception as e:
                QMessageBox.warning(self, "Save Error", str(e))

    def _save_as_flv(self):
        """Save RTMP stream as FLV file."""
        # Get payloads from the worker (may be stopped already)
        payloads = []
        has_video = False
        has_audio = False

        if self._rtmp_worker:
            payloads = self._rtmp_worker.flv_payloads
            has_video = self._rtmp_worker.has_video
            has_audio = self._rtmp_worker.has_audio
        elif hasattr(self, '_last_rtmp_payloads'):
            payloads = self._last_rtmp_payloads
            has_video = self._last_rtmp_has_video
            has_audio = self._last_rtmp_has_audio

        if not payloads:
            QMessageBox.warning(self, "Save As", "No FLV data captured yet.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save As FLV", "stream.flv", "FLV Files (*.flv);;All Files (*)")
        if path:
            try:
                from media_analyzer.core.rtmp.flv_writer import write_flv_file
                bytes_written = write_flv_file(path, payloads, has_video, has_audio)
                size_mb = bytes_written / (1024 * 1024)
                self._status_label.setText(
                    f"Saved FLV: {path} ({size_mb:.1f} MB, {len(payloads)} tags)")
            except Exception as e:
                QMessageBox.warning(self, "Save Error", str(e))

    def _stop_parsing(self):
        """Stop the current parsing operation."""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
            self._status_label.setText("Parsing stopped")
            self._stop_action.setEnabled(False)
            self._progress_bar.hide()
        # Also stop RTMP if active
        if self._rtmp_worker:
            self._stop_rtmp()
            self._stop_action.setEnabled(False)
            self._status_label.setText("RTMP stopped")
            self._save_as_action.setEnabled(True)

    # --- RTMP ---

    def _start_rtmp(self, url: str):
        """Start an RTMP session."""
        # Stop any existing workers
        self._stop_rtmp()
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)

        # Clear existing data
        self._detail_panel.clear()
        self._hex_view.clear()

        # Swap to RTMP dual view
        self._swap_to_rtmp_view()

        # Show control bar
        self._rtmp_control_bar.show()
        self._rtmp_control_bar.set_state("connecting")

        # Update UI
        self._stop_action.setEnabled(True)
        self._save_as_action.setEnabled(False)
        self._status_label.setText(f"Connecting: {url}")

        # Create and start worker
        from media_analyzer.workers.rtmp_worker import RTMPWorker
        self._rtmp_worker = RTMPWorker(url, self)
        self._rtmp_worker.rtmp_packets_ready.connect(self._on_rtmp_packets)
        self._rtmp_worker.flv_tags_ready.connect(self._on_flv_tags)
        self._rtmp_worker.stats_updated.connect(self._on_rtmp_stats)
        self._rtmp_worker.state_changed.connect(self._on_rtmp_state)
        self._rtmp_worker.error.connect(self._on_parse_error)
        self._rtmp_worker.start()

    def _stop_rtmp(self):
        """Stop RTMP session and clean up."""
        if self._rtmp_worker:
            # Preserve payloads for Save As after disconnect
            self._last_rtmp_payloads = self._rtmp_worker.flv_payloads
            self._last_rtmp_has_video = self._rtmp_worker.has_video
            self._last_rtmp_has_audio = self._rtmp_worker.has_audio
            self._rtmp_worker.stop()
            self._rtmp_worker.wait(3000)
            self._rtmp_worker = None
        self._rtmp_control_bar.set_state("disconnected")

    def _rtmp_pause(self):
        """Pause RTMP data reception."""
        if self._rtmp_worker:
            self._rtmp_worker.pause()

    def _rtmp_resume(self):
        """Resume RTMP data reception."""
        if self._rtmp_worker:
            self._rtmp_worker.resume()

    def _rtmp_disconnect(self):
        """Disconnect RTMP session."""
        self._stop_rtmp()
        self._stop_action.setEnabled(False)
        self._status_label.setText("RTMP disconnected")
        # Enable Save As if we have FLV data
        if self._rtmp_worker is None and hasattr(self, '_rtmp_view') and self._rtmp_view:
            self._save_as_action.setEnabled(True)

    def _on_rtmp_packets(self, packets):
        """Handle RTMP protocol packets from worker."""
        if self._rtmp_view:
            self._rtmp_view.append_rtmp_packets(packets)

    def _on_flv_tags(self, packets):
        """Handle FLV tags extracted from RTMP stream."""
        if self._rtmp_view:
            self._rtmp_view.append_flv_tags(packets)

    def _on_rtmp_stats(self, stats: dict):
        """Handle RTMP statistics update."""
        self._rtmp_control_bar.update_stats(stats)

    def _on_rtmp_state(self, state: str):
        """Handle RTMP state change."""
        self._rtmp_control_bar.set_state(state)
        if state == "playing":
            self._status_label.setText("RTMP: receiving data")
        elif state == "paused":
            self._status_label.setText("RTMP: paused")
        elif state == "disconnected":
            self._stop_action.setEnabled(False)
            self._status_label.setText("RTMP: disconnected")
            # Enable Save As if we have captured FLV payloads
            self._save_as_action.setEnabled(True)
        elif state == "error":
            self._stop_action.setEnabled(False)

    def _swap_to_rtmp_view(self):
        """Replace the left panel with RTMP dual view."""
        from media_analyzer.ui.rtmp_view import RTMPDualView

        if self._rtmp_view is not None:
            self._rtmp_view.clear()
            return  # Already in RTMP mode

        # Hide box tree if visible
        if hasattr(self, '_box_tree_view') and self._box_tree_view is not None:
            self._box_tree_view.hide()
            self._box_tree_view.deleteLater()
            self._box_tree_view = None

        self._rtmp_view = RTMPDualView()
        self._rtmp_view.packet_selected.connect(self._on_packet_selected)

        # Hide table, show RTMP view in the same splitter position
        self._table_view.hide()
        self._main_splitter.insertWidget(0, self._rtmp_view)

    def _swap_from_rtmp_view(self):
        """Remove RTMP view and restore normal table."""
        if self._rtmp_view is not None:
            self._rtmp_view.hide()
            self._rtmp_view.deleteLater()
            self._rtmp_view = None
        self._table_view.show()
        self._rtmp_control_bar.hide()

    # --- HLS ---

    def _start_hls(self, url: str):
        """Start HLS analysis: download M3U8, show segment list."""
        import urllib.request
        import urllib.error
        from media_analyzer.core.hls.m3u8_parser import parse_m3u8

        # Stop any existing workers
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        self._stop_rtmp()
        self._swap_from_rtmp_view()

        # Clear existing data
        self._table_model.clear()
        self._all_packets.clear()
        self._detail_panel.clear()
        self._hex_view.clear()

        self._status_label.setText(f"Downloading M3U8: {url}")

        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "MediaInsight/1.0")
            response = urllib.request.urlopen(req, timeout=15)
            content = response.read().decode("utf-8")
            response.close()
        except Exception as e:
            self._status_label.setText(f"Error: {str(e)}")
            QMessageBox.warning(self, "HLS Error", f"Failed to download M3U8:\n{str(e)}")
            return

        # Parse M3U8
        try:
            playlist = parse_m3u8(content, url)
        except Exception as e:
            self._status_label.setText(f"Error: {str(e)}")
            QMessageBox.warning(self, "HLS Error", f"Failed to parse M3U8:\n{str(e)}")
            return

        if playlist.is_master:
            self._status_label.setText("Error: Master playlist not supported")
            QMessageBox.warning(self, "HLS Error",
                "This is a Master playlist (multi-bitrate).\n"
                "Please open a specific rendition/variant M3U8 URL instead.")
            return

        if not playlist.segments:
            self._status_label.setText("Error: No segments found")
            QMessageBox.warning(self, "HLS Error", "M3U8 contains no media segments.")
            return

        # Show HLS view
        self._swap_to_hls_view()
        self._hls_view.load_playlist(playlist, raw_content=content)
        self._status_label.setText(
            f"HLS: {len(playlist.segments)} segments | "
            f"Total: {playlist.total_duration:.1f}s")
        self.setWindowTitle(f"MediaInsight - {url.split('/')[-1].split('?')[0]}")

    def _on_hls_segment_clicked(self, segment):
        """Handle HLS segment click — download and parse."""
        from media_analyzer.workers.hls_worker import HLSSegmentWorker

        # Stop any running segment worker
        if hasattr(self, '_hls_segment_worker') and self._hls_segment_worker:
            self._hls_segment_worker.stop()
            self._hls_segment_worker.wait(3000)

        # Mark as downloading
        self._hls_view.set_segment_status(segment.index, "downloading")

        # Clear right-side views
        self._table_model.clear()
        self._all_packets.clear()
        self._detail_panel.clear()
        self._hex_view.clear()
        self._format_detected = False
        self._swap_to_table_view()  # Ensure table view (not box tree)

        # Start download + parse
        self._hls_segment_worker = HLSSegmentWorker(segment.uri, self)
        self._hls_segment_worker.packets_ready.connect(self._on_packets_ready)
        self._hls_segment_worker.progress.connect(self._on_progress)
        self._hls_segment_worker.parse_finished.connect(
            lambda si: self._on_hls_segment_parsed(si, segment.index))
        self._hls_segment_worker.error.connect(
            lambda err: self._on_hls_segment_error(err, segment.index))
        self._hls_segment_worker.start()

        self._progress_bar.setValue(0)
        self._progress_bar.show()
        self._status_label.setText(f"Downloading segment #{segment.index}...")

        # Store worker as current for hex view access
        self._worker = self._hls_segment_worker

    def _on_hls_segment_parsed(self, stream_info, segment_index: int):
        """Handle HLS segment parse completion."""
        self._stream_info = stream_info
        self._progress_bar.hide()
        self._hls_view.set_segment_status(segment_index, "loaded")

        count = self._table_model.packet_count
        self._status_label.setText(
            f"Segment #{segment_index}: {count:,} packets | "
            f"{stream_info.format_name}")

    def _on_hls_segment_error(self, error_msg: str, segment_index: int):
        """Handle HLS segment error."""
        self._progress_bar.hide()
        self._hls_view.set_segment_status(segment_index, "error")
        self._status_label.setText(f"Error: {error_msg}")

    def _swap_to_hls_view(self):
        """Replace left panel with HLS segment list."""
        from media_analyzer.ui.hls_view import HLSView

        if hasattr(self, '_hls_view') and self._hls_view is not None:
            return  # Already in HLS mode

        # Hide other views
        if hasattr(self, '_box_tree_view') and self._box_tree_view is not None:
            self._box_tree_view.hide()
            self._box_tree_view.deleteLater()
            self._box_tree_view = None
        if self._rtmp_view is not None:
            self._rtmp_view.hide()
            self._rtmp_view.deleteLater()
            self._rtmp_view = None
            self._rtmp_control_bar.hide()

        self._hls_view = HLSView()
        self._hls_view.segment_clicked.connect(self._on_hls_segment_clicked)

        # Hide table initially, show HLS list + table side by side
        # HLS view replaces the table in the left splitter position
        self._table_view.hide()
        self._main_splitter.insertWidget(0, self._hls_view)
        # Also show table (it will be populated when segment is clicked)
        self._table_view.show()

    def _swap_from_hls_view(self):
        """Remove HLS view and restore normal layout."""
        if hasattr(self, '_hls_view') and self._hls_view is not None:
            self._hls_view.hide()
            self._hls_view.deleteLater()
            self._hls_view = None
        if hasattr(self, '_hls_segment_worker') and self._hls_segment_worker:
            self._hls_segment_worker.stop()
            self._hls_segment_worker.wait(3000)
            self._hls_segment_worker = None

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
            # Bitrate page — lazy load
            self._ensure_bitrate_page()
            self._load_bitrate_data()
        elif index == 2:
            # Player page — lazy load
            self._ensure_player_page()
            # Pass URL/path + source for MediaInfo temp file fallback
            if self._player_page and self._current_file_path:
                source = self._worker.source if self._worker else None
                # For RTMP: generate temp FLV for MediaInfo
                mediainfo_path = self._get_mediainfo_path()
                self._player_page.load_file(
                    self._current_file_path, mediainfo_path=mediainfo_path)
        self._pages.setCurrentIndex(index)

    def _get_mediainfo_path(self) -> Optional[str]:
        """Get a local file path for MediaInfo (generates temp file if needed)."""
        path = self._current_file_path
        if not path:
            return None

        # Local file: use directly
        if not path.startswith("http") and not path.startswith("rtmp"):
            return path

        # HTTP stream with downloaded data: save to temp file
        if self._worker:
            from media_analyzer.core.source import StreamingHTTPSource
            source = self._worker.source
            if isinstance(source, StreamingHTTPSource) and source.is_fully_downloaded:
                import tempfile, os
                ext = os.path.splitext(source.name)[1] or ".ts"
                tmp = tempfile.NamedTemporaryFile(
                    suffix=ext, delete=False, prefix="mediainsight_mi_")
                tmp.write(source.read_range(0, source.downloaded_bytes))
                tmp.close()
                return tmp.name

        # RTMP: generate temp FLV from captured data
        if path.startswith("rtmp://") or path.startswith("rtmps://"):
            payloads = []
            has_video = has_audio = False
            if self._rtmp_worker:
                payloads = self._rtmp_worker.flv_payloads
                has_video = self._rtmp_worker.has_video
                has_audio = self._rtmp_worker.has_audio
            elif hasattr(self, '_last_rtmp_payloads') and self._last_rtmp_payloads:
                payloads = self._last_rtmp_payloads
                has_video = getattr(self, '_last_rtmp_has_video', True)
                has_audio = getattr(self, '_last_rtmp_has_audio', True)
            if payloads:
                import tempfile
                from media_analyzer.core.rtmp.flv_writer import write_flv_file
                tmp = tempfile.NamedTemporaryFile(
                    suffix=".flv", delete=False, prefix="mediainsight_mi_")
                tmp.close()
                write_flv_file(tmp.name, payloads, has_video, has_audio)
                return tmp.name

        return None

    def _reset_player_on_new_file(self):
        """Reset player and bitrate pages when a new file is opened."""
        # Switch back to Analyzer tab
        if self._nav_bar.currentIndex() != 0:
            self._nav_bar.setCurrentIndex(0)

        if self._player_page:
            self._player_page.cleanup()
            self._player_page._loaded = False

        if self._bitrate_page:
            self._bitrate_page.clear()  # Also stops live mode

    def _ensure_bitrate_page(self):
        """Create the bitrate page on first use."""
        if self._bitrate_page is not None:
            return
        from media_analyzer.ui.bitrate_page import BitratePage
        self._bitrate_page = BitratePage()
        # Replace placeholder at index 1
        old = self._pages.widget(1)
        self._pages.removeWidget(old)
        old.deleteLater()
        self._pages.insertWidget(1, self._bitrate_page)

    def _load_bitrate_data(self):
        """Feed packet data to the bitrate page."""
        if not self._bitrate_page:
            return

        # RTMP mode: start live update
        if self._rtmp_view and self._rtmp_worker:
            packets = self._rtmp_view._flv_model._packets
            self._bitrate_page.load_packets(packets)
            # Start live mode — refresh chart every 2s with latest data
            self._bitrate_page.start_live_mode(
                packets_fn=lambda: self._rtmp_view._flv_model._packets if self._rtmp_view else []
            )
        else:
            # Static file mode: load once
            self._bitrate_page.stop_live_mode()
            packets = self._all_packets
            self._bitrate_page.load_packets(packets, self._stream_info)

    def _ensure_player_page(self):
        """Create the player page on first use."""
        if self._player_page is not None:
            return
        from media_analyzer.ui.player_page import PlayerPage
        self._player_page = PlayerPage()
        # Replace placeholder at index 2
        old = self._pages.widget(2)
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

        # Stop RTMP if active
        self._stop_rtmp()
        self._swap_from_rtmp_view()

        # Stop HLS if active
        self._swap_from_hls_view()

        # Clear existing data
        self._table_model.clear()
        self._all_packets.clear()
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
        self._worker.download_progress.connect(self._on_download_progress)
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

        # Always accumulate for bitrate analysis
        self._all_packets.extend(packets)

        # Update status (lightweight — just show count)
        count = self._table_model.packet_count
        self._status_label.setText(f"{count:,} tags loaded")

    def _on_progress(self, current: int, total: int):
        """Update progress bar."""
        if total > 0:
            percent = int(current * 100 / total)
            self._progress_bar.setValue(percent)

    def _on_download_progress(self, downloaded: int, total: int):
        """Update status bar with download progress."""
        if total > 0:
            percent = int(downloaded * 100 / total)
            mb_down = downloaded / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            self._status_label.setText(
                f"Downloading: {mb_down:.1f} / {mb_total:.1f} MB ({percent}%)")
            self._progress_bar.setValue(percent)
            self._progress_bar.show()
        else:
            mb_down = downloaded / (1024 * 1024)
            self._status_label.setText(f"Downloading: {mb_down:.1f} MB...")

        # Enable Save As only when fully downloaded
        if hasattr(self, '_save_as_action'):
            from media_analyzer.core.source import StreamingHTTPSource
            if self._worker and isinstance(self._worker.source, StreamingHTTPSource):
                self._save_as_action.setEnabled(self._worker.source.is_fully_downloaded)

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
        self.setWindowTitle(f"MediaInsight - {stream_info.source_path}")

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

        # Load hex data
        if self._rtmp_worker and self._rtmp_view:
            # RTMP mode: read hex from worker's stored raw bytes
            self._show_rtmp_hex(packet)
        elif self._pes_view_active and packet.script_data and packet.script_data.get("pusi"):
            self._show_pes_hex(packet)
        else:
            self._show_tag_hex(packet)

    def _on_nalu_selected(self, nalu: NALUInfo, packet: PacketInfo):
        """Handle NALU item click in detail panel - show NALU bytes in hex view."""
        # RTMP mode: read from stored FLV payload bytes
        if self._rtmp_worker and self._rtmp_view:
            try:
                raw = self._rtmp_worker.get_flv_raw_bytes(packet.index)
                if raw and len(raw) > 11:
                    # get_flv_raw_bytes returns 11-byte header + payload
                    # nalu.offset_in_tag is relative to payload start (after header)
                    nalu_start = 11 + nalu.offset_in_tag
                    read_size = min(4 + nalu.size, len(raw) - nalu_start)
                    if read_size > 0:
                        nalu_data = raw[nalu_start:nalu_start + read_size]
                        self._hex_view.set_data(nalu_data, nalu_start)
                    else:
                        self._hex_view.clear()
                else:
                    self._hex_view.clear()
            except Exception:
                self._hex_view.clear()
            return

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

    def _show_rtmp_hex(self, packet: PacketInfo):
        """Load hex data for RTMP packet (from worker's stored raw bytes)."""
        if not self._rtmp_worker:
            self._hex_view.clear()
            return
        try:
            # Determine which raw bytes to show based on packet type
            if packet.script_data and "rtmp_message_type" in packet.script_data:
                # RTMP protocol packet
                raw = self._rtmp_worker.get_rtmp_raw_bytes(packet.index)
            else:
                # FLV tag packet
                raw = self._rtmp_worker.get_flv_raw_bytes(packet.index)

            if raw:
                display_size = min(len(raw), 4096)
                self._hex_view.set_data(raw[:display_size], packet.offset)
                self._hex_view.clear_highlight()
            else:
                self._hex_view.clear()
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
        if self._rtmp_worker:
            self._rtmp_worker.stop()
            self._rtmp_worker.wait(3000)
        if self._player_page:
            self._player_page.cleanup()
        event.accept()
