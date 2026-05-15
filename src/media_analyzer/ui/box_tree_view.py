"""Box tree view widget for MP4/MOV file display."""

from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QHeaderView
from PySide6.QtCore import Qt, Signal
from typing import List, Optional, Dict

from media_analyzer.core.models import PacketInfo


# Custom role for storing PacketInfo on tree items
BOX_DATA_ROLE = Qt.ItemDataRole.UserRole + 10


class BoxTreeView(QTreeWidget):
    """
    Tree view for MP4/MOV box (atom) hierarchy.

    Boxes are inserted in DFS (depth-first) order using the 'depth' field
    in PacketInfo.script_data to determine parent/child relationships.

    Selecting a box emits the box_selected signal with the PacketInfo.
    """

    box_selected = Signal(object)  # Emits PacketInfo

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._depth_stack: List[QTreeWidgetItem] = []  # Stack of parent items per depth
        self._index_map: Dict[int, QTreeWidgetItem] = {}  # box_index -> tree item (for mdat parent ref)
        self._box_count = 0

    def _setup_ui(self):
        """Configure tree widget appearance."""
        self.setHeaderLabels(["Box", "Size", "Offset"])
        self.setColumnWidth(0, 300)
        self.setColumnWidth(1, 100)
        self.setColumnWidth(2, 120)
        self.setAlternatingRowColors(True)
        self.setRootIsDecorated(True)
        self.setAnimated(False)
        self.setExpandsOnDoubleClick(True)

        header = self.header()
        header.setStretchLastSection(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)

        # Connect selection change
        self.currentItemChanged.connect(self._on_item_changed)

    def append_packets(self, packets: List[PacketInfo]) -> None:
        """
        Add boxes (as PacketInfo) to the tree.

        Each packet's script_data must contain:
          - box_type: str (e.g. "moov", "trak")
          - depth: int (0 = root level)
          - Optional: is_container (bool)
          - Optional: mdat_parent_index (int) — insert as child of mdat
        """
        for pkt in packets:
            if pkt.script_data is None:
                continue
            box_type = pkt.script_data.get("box_type", "????")
            depth = pkt.script_data.get("depth", 0)
            size = pkt.tag_total_size
            offset = pkt.offset

            # Format display text
            size_str = self._format_size(size)
            offset_str = f"0x{offset:08X}"

            # Create tree item
            item = QTreeWidgetItem([box_type, size_str, offset_str])
            item.setData(0, BOX_DATA_ROLE, pkt)

            # Check if this is a deferred mdat child (has mdat_parent_index)
            # or a sample child of a chunk (has chunk_parent_index)
            mdat_parent_idx = pkt.script_data.get("mdat_parent_index")
            chunk_parent_idx = pkt.script_data.get("chunk_parent_index")

            if chunk_parent_idx is not None and chunk_parent_idx in self._index_map:
                # Insert as child of chunk
                parent_item = self._index_map[chunk_parent_idx]
                parent_item.addChild(item)
            elif mdat_parent_idx is not None and mdat_parent_idx in self._index_map:
                # Insert as child of mdat
                parent_item = self._index_map[mdat_parent_idx]
                parent_item.addChild(item)
            elif depth == 0:
                # Root-level box
                self.addTopLevelItem(item)
                self._depth_stack = [item]
            else:
                # Find parent: depth_stack[depth-1] should be the parent
                while len(self._depth_stack) > depth:
                    self._depth_stack.pop()

                if self._depth_stack:
                    parent = self._depth_stack[-1]
                    parent.addChild(item)
                else:
                    # Fallback: add as top-level
                    self.addTopLevelItem(item)

                # Update stack
                if len(self._depth_stack) <= depth:
                    self._depth_stack.append(item)
                else:
                    self._depth_stack[depth] = item

            # Track item by box index for mdat parent reference
            self._index_map[self._box_count] = item
            self._box_count += 1

    def clear(self) -> None:
        """Clear all items."""
        super().clear()
        self._depth_stack.clear()
        self._index_map.clear()
        self._box_count = 0

    @property
    def box_count(self) -> int:
        return self._box_count

    def _on_item_changed(self, current: QTreeWidgetItem, previous: QTreeWidgetItem):
        """Handle tree item selection change."""
        if current is None:
            return
        pkt = current.data(0, BOX_DATA_ROLE)
        if isinstance(pkt, PacketInfo):
            self.box_selected.emit(pkt)

    @staticmethod
    def _format_size(size: int) -> str:
        """Format byte size for display."""
        if size >= 1024 * 1024:
            return f"{size:,} ({size / (1024*1024):.1f} MB)"
        elif size >= 1024:
            return f"{size:,} ({size / 1024:.1f} KB)"
        return f"{size:,}"
