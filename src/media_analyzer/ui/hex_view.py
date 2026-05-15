"""Hex view widget for displaying raw binary data.

Split into two synchronized panels:
  Left:  Offset + Hex bytes (selectable/copyable independently)
  Right: ASCII decoded text (selectable/copyable independently)
"""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPlainTextEdit, QLabel, QSplitter,
    QTextEdit,
)
from PySide6.QtGui import (
    QFont, QColor, QTextCharFormat, QSyntaxHighlighter,
    QTextDocument, QFontDatabase, QTextOption, QTextCursor,
)
from PySide6.QtCore import Qt


class HexPanelHighlighter(QSyntaxHighlighter):
    """Highlights the offset+hex panel: colored offsets, themed hex bytes."""

    def __init__(self, document: QTextDocument, font: QFont):
        super().__init__(document)
        from media_analyzer.ui.themes import get_current_theme
        theme = get_current_theme()

        self._offset_fmt = QTextCharFormat()
        self._offset_fmt.setForeground(QColor(*theme.hex_offset_color))
        self._offset_fmt.setFont(font)

        self._hex_fmt = QTextCharFormat()
        self._hex_fmt.setForeground(QColor(*theme.hex_byte_color))
        self._hex_fmt.setFont(font)

        self._header_fmt = QTextCharFormat()
        self._header_fmt.setForeground(QColor(*theme.hex_header_color))
        self._header_fmt.setFont(font)

    def highlightBlock(self, text: str):
        if not text or len(text) < 8:
            return

        # Header line (starts with space)
        if text[0] == ' ':
            self.setFormat(0, len(text), self._header_fmt)
            return

        # Data lines: must start with hex digits
        if not all(c in '0123456789ABCDEFabcdef' for c in text[:8]):
            return

        # Set entire line to hex format first (ensures uniform font on gap spaces)
        self.setFormat(0, len(text), self._hex_fmt)
        # Overlay offset color
        self.setFormat(0, 8, self._offset_fmt)


class AsciiPanelHighlighter(QSyntaxHighlighter):
    """Highlights the ASCII panel: green printable, dim dots."""

    def __init__(self, document: QTextDocument, font: QFont):
        super().__init__(document)
        from media_analyzer.ui.themes import get_current_theme
        theme = get_current_theme()

        self._printable_fmt = QTextCharFormat()
        self._printable_fmt.setForeground(QColor(*theme.hex_ascii_color))
        self._printable_fmt.setFont(font)

        self._dot_fmt = QTextCharFormat()
        self._dot_fmt.setForeground(QColor(*theme.hex_nonprint_color))
        self._dot_fmt.setFont(font)

        self._header_fmt = QTextCharFormat()
        self._header_fmt.setForeground(QColor(*theme.hex_header_color))
        self._header_fmt.setFont(font)

    def highlightBlock(self, text: str):
        if not text:
            return

        # Header line
        if text.startswith("Decoded"):
            self.setFormat(0, len(text), self._header_fmt)
            return

        for i, ch in enumerate(text):
            if ch == '.':
                self.setFormat(i, 1, self._dot_fmt)
            elif ch == ' ':
                pass
            else:
                self.setFormat(i, 1, self._printable_fmt)


def _create_monospace_font() -> QFont:
    """Create a guaranteed monospace font with no kerning."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(10)
    font.setFixedPitch(True)
    font.setKerning(False)
    font.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
    return font


def _make_text_edit(font: QFont) -> QPlainTextEdit:
    """Create a configured read-only monospace QPlainTextEdit."""
    edit = QPlainTextEdit()
    edit.setReadOnly(True)
    edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    edit.setFont(font)

    doc = edit.document()
    doc.setDefaultFont(font)

    text_option = QTextOption()
    text_option.setWrapMode(QTextOption.WrapMode.NoWrap)
    doc.setDefaultTextOption(text_option)

    edit.setStyleSheet("")  # Rely on global theme stylesheet
    return edit


class HexViewWidget(QWidget):
    """
    Displays raw bytes in a split hex editor layout:

      Left panel (Offset + Hex):
        00000000  46 4C 56 01 05 00 00 00  09 00 00 00 00 12 00 00

      Right panel (ASCII, independently selectable):
        FLV.............

    Both panels scroll in sync.
    """

    BYTES_PER_ROW = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: bytes = b""
        self._base_offset: int = 0
        self._mono_font: QFont = _create_monospace_font()
        self._syncing_scroll = False
        self._setup_ui()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Title - fixed height, no extra space
        self._title = QLabel("Hex View")
        self._title.setFixedHeight(20)
        self._title.setStyleSheet("""
            QLabel {
                font-weight: bold;
                font-size: 11px;
                padding: 1px 2px;
            }
        """)
        outer.addWidget(self._title)

        # Horizontal layout for hex + ascii (no splitter, use stretch ratios)
        panels = QHBoxLayout()
        panels.setContentsMargins(0, 0, 0, 0)
        panels.setSpacing(2)

        # --- Left: Offset + Hex ---
        self._hex_edit = _make_text_edit(self._mono_font)
        panels.addWidget(self._hex_edit, 3)  # stretch factor 3

        # --- Right: ASCII ---
        self._ascii_edit = _make_text_edit(self._mono_font)
        # ASCII is always 16 chars wide; calculate pixel width
        char_w = self._ascii_edit.fontMetrics().horizontalAdvance('X')
        # 16 chars + some padding for scrollbar + margins
        ascii_fixed_w = char_w * 18 + 20
        self._ascii_edit.setMinimumWidth(ascii_fixed_w)
        self._ascii_edit.setMaximumWidth(ascii_fixed_w)
        panels.addWidget(self._ascii_edit, 0)  # no stretch, fixed width

        self._ascii_highlighter = AsciiPanelHighlighter(
            self._ascii_edit.document(), self._mono_font
        )
        self._hex_highlighter = HexPanelHighlighter(
            self._hex_edit.document(), self._mono_font
        )

        outer.addLayout(panels)

        # Synchronize vertical scrolling between the two panels
        self._hex_edit.verticalScrollBar().valueChanged.connect(self._sync_scroll_from_hex)
        self._ascii_edit.verticalScrollBar().valueChanged.connect(self._sync_scroll_from_ascii)

        # Hide the ascii panel scrollbar since hex panel controls scrolling
        self._ascii_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _sync_scroll_from_hex(self, value: int):
        if self._syncing_scroll:
            return
        self._syncing_scroll = True
        self._ascii_edit.verticalScrollBar().setValue(value)
        self._syncing_scroll = False

    def _sync_scroll_from_ascii(self, value: int):
        if self._syncing_scroll:
            return
        self._syncing_scroll = True
        self._hex_edit.verticalScrollBar().setValue(value)
        self._syncing_scroll = False

    def set_data(self, data: bytes, base_offset: int = 0) -> None:
        """Render bytes into the split hex+ascii panels."""
        self._data = data
        self._base_offset = base_offset

        if not data:
            self._hex_edit.setPlainText("")
            self._ascii_edit.setPlainText("")
            self._title.setText("Hex View (no data)")
            return

        self._title.setText(f"Hex View - {len(data)} bytes from offset 0x{base_offset:08X}")

        hex_lines = []
        ascii_lines = []

        # Header row
        hdr_hex_lo = " ".join(f"{i:02X}" for i in range(8))
        hdr_hex_hi = " ".join(f"{i:02X}" for i in range(8, 16))
        hex_lines.append(f"          {hdr_hex_lo}  {hdr_hex_hi}")
        ascii_lines.append("Decoded text")

        # Data rows
        for i in range(0, len(data), self.BYTES_PER_ROW):
            row = data[i:i + self.BYTES_PER_ROW]

            # Offset
            offset_str = f"{base_offset + i:08X}"

            # Hex groups
            hex_lo_parts = []
            hex_hi_parts = []
            for j in range(8):
                hex_lo_parts.append(f"{row[j]:02X}" if j < len(row) else "  ")
            for j in range(8, 16):
                hex_hi_parts.append(f"{row[j]:02X}" if j < len(row) else "  ")

            hex_lo = " ".join(hex_lo_parts)
            hex_hi = " ".join(hex_hi_parts)
            hex_lines.append(f"{offset_str}  {hex_lo}  {hex_hi}")

            # ASCII
            ascii_chars = []
            for b in row:
                if 32 <= b < 127:
                    ascii_chars.append(chr(b))
                else:
                    ascii_chars.append('.')
            ascii_lines.append("".join(ascii_chars))

        self._hex_edit.setPlainText("\n".join(hex_lines))
        self._ascii_edit.setPlainText("\n".join(ascii_lines))
        # Clear any previous highlights
        self._hex_edit.setExtraSelections([])
        self._ascii_edit.setExtraSelections([])

    def clear(self) -> None:
        """Clear the hex view."""
        self._data = b""
        self._hex_edit.setPlainText("")
        self._ascii_edit.setPlainText("")
        self._title.setText("Hex View")

    def highlight_range(self, byte_offset: int, byte_length: int) -> None:
        """
        Highlight a range of bytes in both hex and ascii panels.

        byte_offset: offset relative to self._base_offset (i.e. relative to
                     the start of self._data, which is the tag start)
        byte_length: number of bytes to highlight
        """
        if not self._data or byte_length <= 0:
            self._hex_edit.setExtraSelections([])
            self._ascii_edit.setExtraSelections([])
            return

        # Highlight format
        hl_fmt = QTextCharFormat()
        from media_analyzer.ui.themes import get_current_theme
        theme = get_current_theme()
        hl_fmt.setBackground(QColor(*theme.hex_highlight_bg))
        hl_fmt.setForeground(QColor(*theme.hex_highlight_fg))

        hex_selections = []
        ascii_selections = []

        hex_doc = self._hex_edit.document()
        ascii_doc = self._ascii_edit.document()

        for byte_idx in range(byte_offset, min(byte_offset + byte_length, len(self._data))):
            row = byte_idx // self.BYTES_PER_ROW
            col = byte_idx % self.BYTES_PER_ROW

            # --- Hex panel ---
            # Line index: row+1 (row 0 is header)
            hex_block = hex_doc.findBlockByLineNumber(row + 1)
            if hex_block.isValid():
                # Column position within the line:
                # "XXXXXXXX  XX XX XX XX XX XX XX XX  XX XX XX XX XX XX XX XX"
                #  0       8 10                                             56
                # Each byte = 2 hex chars + 1 space = 3 chars
                # First group (0-7): starts at pos 10, each byte at 10 + col*3
                # Second group (8-15): starts at 10 + 8*3 + 2 = 36, at 36 + (col-8)*3
                if col < 8:
                    char_pos = 10 + col * 3
                else:
                    char_pos = 10 + 8 * 3 + 2 + (col - 8) * 3

                cursor = QTextCursor(hex_block)
                cursor.movePosition(QTextCursor.MoveOperation.Right,
                                    QTextCursor.MoveMode.MoveAnchor, char_pos)
                cursor.movePosition(QTextCursor.MoveOperation.Right,
                                    QTextCursor.MoveMode.KeepAnchor, 2)

                sel = QTextEdit.ExtraSelection()
                sel.cursor = cursor
                sel.format = hl_fmt
                hex_selections.append(sel)

            # --- ASCII panel ---
            # Line index: row+1 (row 0 is "Decoded text" header)
            ascii_block = ascii_doc.findBlockByLineNumber(row + 1)
            if ascii_block.isValid() and col < ascii_block.length():
                cursor = QTextCursor(ascii_block)
                cursor.movePosition(QTextCursor.MoveOperation.Right,
                                    QTextCursor.MoveMode.MoveAnchor, col)
                cursor.movePosition(QTextCursor.MoveOperation.Right,
                                    QTextCursor.MoveMode.KeepAnchor, 1)

                sel = QTextEdit.ExtraSelection()
                sel.cursor = cursor
                sel.format = hl_fmt
                ascii_selections.append(sel)

        self._hex_edit.setExtraSelections(hex_selections)
        self._ascii_edit.setExtraSelections(ascii_selections)

        # Scroll to make the first highlighted byte visible
        if hex_selections:
            self._hex_edit.setTextCursor(hex_selections[0].cursor)
            self._hex_edit.ensureCursorVisible()

    def clear_highlight(self) -> None:
        """Remove all byte highlights."""
        self._hex_edit.setExtraSelections([])
        self._ascii_edit.setExtraSelections([])
