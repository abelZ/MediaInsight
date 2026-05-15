"""Color themes for the application.

Each theme defines a complete color palette used across all UI components.
Themes are inspired by popular VS Code color themes.
"""

from dataclasses import dataclass, field
from typing import Dict, Tuple
from PySide6.QtGui import QColor


@dataclass
class Theme:
    """Complete color theme definition."""
    name: str
    display_name: str

    # --- Base colors ---
    bg_primary: str          # Main window / widget background
    bg_secondary: str        # Input fields, table background (slightly darker/different)
    bg_tertiary: str         # Headers, buttons, dialogs
    fg_primary: str          # Main text
    fg_secondary: str        # Secondary/muted text
    fg_accent: str           # Links, header text accent

    # --- Borders ---
    border: str              # Standard borders
    border_light: str        # Lighter borders (focus, hover)

    # --- Selection & Highlight ---
    selection_bg: str        # Selected item background
    selection_fg: str        # Selected item text
    hover_bg: str            # Hovered item background

    # --- Scrollbar ---
    scrollbar_bg: str        # Scrollbar track
    scrollbar_handle: str    # Scrollbar thumb
    scrollbar_hover: str     # Scrollbar thumb hovered

    # --- Row type colors (table) ---
    row_header_bg: Tuple[int, int, int] = (38, 32, 42)
    row_header_fg: Tuple[int, int, int] = (180, 160, 200)
    row_video_bg: Tuple[int, int, int] = (30, 34, 44)
    row_video_fg: Tuple[int, int, int] = (160, 185, 220)
    row_audio_bg: Tuple[int, int, int] = (30, 38, 34)
    row_audio_fg: Tuple[int, int, int] = (160, 200, 170)
    row_script_bg: Tuple[int, int, int] = (38, 36, 30)
    row_script_fg: Tuple[int, int, int] = (200, 190, 150)

    # --- Video sub-type colors ---
    row_idr_bg: Tuple[int, int, int] = (32, 36, 50)
    row_idr_fg: Tuple[int, int, int] = (170, 195, 230)
    row_seq_bg: Tuple[int, int, int] = (36, 32, 46)
    row_seq_fg: Tuple[int, int, int] = (175, 160, 210)

    # --- Hex view colors ---
    hex_offset_color: Tuple[int, int, int] = (130, 180, 230)
    hex_byte_color: Tuple[int, int, int] = (210, 210, 220)
    hex_header_color: Tuple[int, int, int] = (100, 130, 160)
    hex_ascii_color: Tuple[int, int, int] = (140, 210, 140)
    hex_nonprint_color: Tuple[int, int, int] = (80, 80, 100)
    hex_highlight_bg: Tuple[int, int, int] = (80, 60, 20)
    hex_highlight_fg: Tuple[int, int, int] = (255, 220, 100)


# =============================================================================
# Built-in themes
# =============================================================================

THEME_ONE_DARK_PRO = Theme(
    name="one_dark_pro",
    display_name="One Dark Pro",
    bg_primary="#282c34",
    bg_secondary="#21252b",
    bg_tertiary="#2c313a",
    fg_primary="#abb2bf",
    fg_secondary="#7f848e",
    fg_accent="#61afef",
    border="#3e4452",
    border_light="#528bff",
    selection_bg="#2c313a",
    selection_fg="#ffffff",
    hover_bg="#2c313a",
    scrollbar_bg="#21252b",
    scrollbar_handle="#4b5263",
    scrollbar_hover="#5c6370",
    row_header_bg=(36, 34, 44),
    row_header_fg=(160, 155, 185),
    row_video_bg=(33, 37, 46),
    row_video_fg=(150, 170, 200),
    row_audio_bg=(33, 38, 44),
    row_audio_fg=(145, 165, 190),
    row_script_bg=(37, 36, 40),
    row_script_fg=(165, 160, 175),
    row_idr_bg=(35, 42, 55),
    row_idr_fg=(130, 180, 230),
    row_seq_bg=(38, 35, 48),
    row_seq_fg=(170, 155, 210),
    hex_offset_color=(97, 175, 239),
    hex_byte_color=(171, 178, 191),
    hex_header_color=(92, 99, 112),
    hex_ascii_color=(130, 175, 145),
    hex_nonprint_color=(75, 82, 99),
    hex_highlight_bg=(73, 72, 42),
    hex_highlight_fg=(229, 192, 123),
)

THEME_DRACULA = Theme(
    name="dracula",
    display_name="Dracula",
    bg_primary="#282a36",
    bg_secondary="#21222c",
    bg_tertiary="#343746",
    fg_primary="#f8f8f2",
    fg_secondary="#6272a4",
    fg_accent="#bd93f9",
    border="#44475a",
    border_light="#bd93f9",
    selection_bg="#44475a",
    selection_fg="#f8f8f2",
    hover_bg="#3a3d4e",
    scrollbar_bg="#21222c",
    scrollbar_handle="#44475a",
    scrollbar_hover="#6272a4",
    row_header_bg=(36, 34, 46),
    row_header_fg=(165, 155, 195),
    row_video_bg=(32, 35, 48),
    row_video_fg=(155, 170, 210),
    row_audio_bg=(32, 36, 44),
    row_audio_fg=(148, 165, 195),
    row_script_bg=(38, 35, 40),
    row_script_fg=(170, 162, 180),
    row_idr_bg=(34, 38, 54),
    row_idr_fg=(160, 185, 240),
    row_seq_bg=(40, 34, 50),
    row_seq_fg=(180, 155, 220),
    hex_offset_color=(189, 147, 249),
    hex_byte_color=(200, 200, 210),
    hex_header_color=(98, 114, 164),
    hex_ascii_color=(130, 180, 145),
    hex_nonprint_color=(68, 71, 90),
    hex_highlight_bg=(60, 55, 30),
    hex_highlight_fg=(241, 250, 140),
)

THEME_MONOKAI_PRO = Theme(
    name="monokai_pro",
    display_name="Monokai Pro",
    bg_primary="#2d2a2e",
    bg_secondary="#221f22",
    bg_tertiary="#363337",
    fg_primary="#fcfcfa",
    fg_secondary="#939293",
    fg_accent="#ffd866",
    border="#403e41",
    border_light="#ffd866",
    selection_bg="#403e41",
    selection_fg="#fcfcfa",
    hover_bg="#363337",
    scrollbar_bg="#221f22",
    scrollbar_handle="#504e50",
    scrollbar_hover="#696769",
    row_header_bg=(38, 34, 40),
    row_header_fg=(170, 158, 188),
    row_video_bg=(34, 36, 42),
    row_video_fg=(158, 172, 200),
    row_audio_bg=(34, 37, 40),
    row_audio_fg=(152, 166, 185),
    row_script_bg=(38, 36, 34),
    row_script_fg=(175, 168, 155),
    row_idr_bg=(36, 40, 48),
    row_idr_fg=(140, 185, 225),
    row_seq_bg=(40, 34, 42),
    row_seq_fg=(175, 155, 210),
    hex_offset_color=(120, 180, 200),
    hex_byte_color=(200, 200, 200),
    hex_header_color=(147, 146, 147),
    hex_ascii_color=(140, 185, 140),
    hex_nonprint_color=(80, 78, 80),
    hex_highlight_bg=(70, 60, 25),
    hex_highlight_fg=(255, 216, 102),
)

THEME_GITHUB_DARK = Theme(
    name="github_dark",
    display_name="GitHub Dark",
    bg_primary="#0d1117",
    bg_secondary="#010409",
    bg_tertiary="#161b22",
    fg_primary="#c9d1d9",
    fg_secondary="#8b949e",
    fg_accent="#58a6ff",
    border="#30363d",
    border_light="#58a6ff",
    selection_bg="#1f6feb33",
    selection_fg="#ffffff",
    hover_bg="#161b22",
    scrollbar_bg="#010409",
    scrollbar_handle="#30363d",
    scrollbar_hover="#484f58",
    row_header_bg=(14, 16, 26),
    row_header_fg=(148, 145, 170),
    row_video_bg=(13, 18, 28),
    row_video_fg=(135, 160, 200),
    row_audio_bg=(13, 18, 24),
    row_audio_fg=(130, 155, 185),
    row_script_bg=(16, 16, 20),
    row_script_fg=(155, 150, 140),
    row_idr_bg=(15, 22, 35),
    row_idr_fg=(110, 175, 240),
    row_seq_bg=(18, 16, 28),
    row_seq_fg=(165, 140, 210),
    hex_offset_color=(88, 166, 255),
    hex_byte_color=(180, 188, 195),
    hex_header_color=(139, 148, 158),
    hex_ascii_color=(120, 165, 130),
    hex_nonprint_color=(48, 54, 61),
    hex_highlight_bg=(55, 50, 15),
    hex_highlight_fg=(210, 153, 34),
)

THEME_CATPPUCCIN_MOCHA = Theme(
    name="catppuccin_mocha",
    display_name="Catppuccin Mocha",
    bg_primary="#1e1e2e",
    bg_secondary="#181825",
    bg_tertiary="#313244",
    fg_primary="#cdd6f4",
    fg_secondary="#a6adc8",
    fg_accent="#89b4fa",
    border="#45475a",
    border_light="#89b4fa",
    selection_bg="#45475a",
    selection_fg="#cdd6f4",
    hover_bg="#313244",
    scrollbar_bg="#181825",
    scrollbar_handle="#45475a",
    scrollbar_hover="#585b70",
    row_header_bg=(32, 30, 42),
    row_header_fg=(158, 152, 185),
    row_video_bg=(28, 32, 44),
    row_video_fg=(145, 165, 205),
    row_audio_bg=(28, 33, 40),
    row_audio_fg=(140, 160, 190),
    row_script_bg=(34, 32, 36),
    row_script_fg=(165, 158, 170),
    row_idr_bg=(30, 35, 52),
    row_idr_fg=(150, 180, 230),
    row_seq_bg=(35, 30, 46),
    row_seq_fg=(170, 150, 210),
    hex_offset_color=(137, 180, 250),
    hex_byte_color=(175, 182, 205),
    hex_header_color=(108, 112, 134),
    hex_ascii_color=(130, 175, 140),
    hex_nonprint_color=(69, 71, 90),
    hex_highlight_bg=(70, 60, 30),
    hex_highlight_fg=(249, 226, 175),
)

THEME_TOKYO_NIGHT = Theme(
    name="tokyo_night",
    display_name="Tokyo Night",
    bg_primary="#1a1b26",
    bg_secondary="#16161e",
    bg_tertiary="#24283b",
    fg_primary="#a9b1d6",
    fg_secondary="#565f89",
    fg_accent="#7aa2f7",
    border="#3b4261",
    border_light="#7aa2f7",
    selection_bg="#283457",
    selection_fg="#c0caf5",
    hover_bg="#292e42",
    scrollbar_bg="#16161e",
    scrollbar_handle="#3b4261",
    scrollbar_hover="#565f89",
    row_header_bg=(28, 26, 38),
    row_header_fg=(155, 148, 185),
    row_video_bg=(24, 28, 40),
    row_video_fg=(140, 158, 200),
    row_audio_bg=(24, 29, 36),
    row_audio_fg=(135, 152, 182),
    row_script_bg=(30, 28, 32),
    row_script_fg=(162, 155, 165),
    row_idr_bg=(26, 32, 48),
    row_idr_fg=(140, 175, 240),
    row_seq_bg=(30, 26, 42),
    row_seq_fg=(168, 148, 215),
    hex_offset_color=(122, 162, 247),
    hex_byte_color=(160, 168, 200),
    hex_header_color=(86, 95, 137),
    hex_ascii_color=(115, 170, 140),
    hex_nonprint_color=(59, 66, 97),
    hex_highlight_bg=(60, 55, 25),
    hex_highlight_fg=(224, 175, 104),
)

THEME_NORD = Theme(
    name="nord",
    display_name="Nord",
    bg_primary="#2e3440",
    bg_secondary="#272c36",
    bg_tertiary="#3b4252",
    fg_primary="#d8dee9",
    fg_secondary="#81a1c1",
    fg_accent="#88c0d0",
    border="#434c5e",
    border_light="#88c0d0",
    selection_bg="#434c5e",
    selection_fg="#eceff4",
    hover_bg="#3b4252",
    scrollbar_bg="#272c36",
    scrollbar_handle="#4c566a",
    scrollbar_hover="#5e6779",
    row_header_bg=(40, 38, 48),
    row_header_fg=(160, 155, 178),
    row_video_bg=(38, 42, 52),
    row_video_fg=(148, 165, 195),
    row_audio_bg=(38, 43, 48),
    row_audio_fg=(142, 158, 180),
    row_script_bg=(44, 42, 44),
    row_script_fg=(168, 162, 158),
    row_idr_bg=(40, 48, 58),
    row_idr_fg=(145, 175, 210),
    row_seq_bg=(44, 40, 52),
    row_seq_fg=(170, 150, 190),
    hex_offset_color=(136, 192, 208),
    hex_byte_color=(190, 196, 205),
    hex_header_color=(76, 86, 106),
    hex_ascii_color=(130, 168, 135),
    hex_nonprint_color=(67, 76, 94),
    hex_highlight_bg=(60, 55, 30),
    hex_highlight_fg=(235, 203, 139),
)

THEME_SOLARIZED_DARK = Theme(
    name="solarized_dark",
    display_name="Solarized Dark",
    bg_primary="#002b36",
    bg_secondary="#00212b",
    bg_tertiary="#073642",
    fg_primary="#839496",
    fg_secondary="#586e75",
    fg_accent="#268bd2",
    border="#073642",
    border_light="#268bd2",
    selection_bg="#073642",
    selection_fg="#fdf6e3",
    hover_bg="#073642",
    scrollbar_bg="#00212b",
    scrollbar_handle="#073642",
    scrollbar_hover="#586e75",
    row_header_bg=(4, 38, 46),
    row_header_fg=(128, 138, 145),
    row_video_bg=(2, 40, 50),
    row_video_fg=(118, 140, 165),
    row_audio_bg=(2, 40, 44),
    row_audio_fg=(115, 135, 150),
    row_script_bg=(6, 38, 40),
    row_script_fg=(135, 132, 125),
    row_idr_bg=(2, 42, 55),
    row_idr_fg=(100, 160, 210),
    row_seq_bg=(6, 36, 48),
    row_seq_fg=(145, 120, 170),
    hex_offset_color=(38, 139, 210),
    hex_byte_color=(131, 148, 150),
    hex_header_color=(88, 110, 117),
    hex_ascii_color=(100, 140, 110),
    hex_nonprint_color=(7, 54, 66),
    hex_highlight_bg=(40, 50, 20),
    hex_highlight_fg=(181, 137, 0),
)

# All built-in themes
BUILTIN_THEMES: Dict[str, Theme] = {
    t.name: t for t in [
        THEME_CATPPUCCIN_MOCHA,
        THEME_ONE_DARK_PRO,
        THEME_DRACULA,
        THEME_TOKYO_NIGHT,
        THEME_MONOKAI_PRO,
        THEME_GITHUB_DARK,
        THEME_NORD,
        THEME_SOLARIZED_DARK,
    ]
}

# Default theme
DEFAULT_THEME = THEME_CATPPUCCIN_MOCHA

# Global current theme reference
_current_theme: Theme = DEFAULT_THEME


def get_current_theme() -> Theme:
    """Get the currently active theme."""
    return _current_theme


def set_current_theme(theme: Theme) -> None:
    """Set the current theme (call apply_theme after this)."""
    global _current_theme
    _current_theme = theme


def generate_stylesheet(theme: Theme) -> str:
    """Generate the full application stylesheet from a theme."""
    return f"""
/* Global */
QWidget {{
    background-color: {theme.bg_primary};
    color: {theme.fg_primary};
    font-family: "Segoe UI", "SF Pro Display", "Helvetica Neue", sans-serif;
    font-size: 12px;
}}

/* Main Window */
QMainWindow {{
    background-color: {theme.bg_primary};
}}

/* Splitter */
QSplitter::handle {{
    background-color: {theme.border};
}}
QSplitter::handle:horizontal {{
    width: 3px;
}}
QSplitter::handle:vertical {{
    height: 3px;
}}

/* Table View */
QTableView {{
    background-color: {theme.bg_secondary};
    gridline-color: transparent;
    border: 1px solid {theme.border};
    selection-background-color: {theme.selection_bg};
    selection-color: {theme.selection_fg};
}}
QTableView::item:selected {{
    background-color: {theme.selection_bg};
    color: {theme.selection_fg};
}}
QHeaderView::section {{
    background-color: {theme.bg_tertiary};
    color: {theme.fg_accent};
    padding: 4px 6px;
    border: 1px solid {theme.border};
    font-weight: bold;
    font-size: 11px;
}}
QHeaderView::section:hover {{
    background-color: {theme.hover_bg};
}}

/* Scrollbar */
QScrollBar:vertical {{
    background-color: {theme.scrollbar_bg};
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background-color: {theme.scrollbar_handle};
    border-radius: 4px;
    min-height: 30px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {theme.scrollbar_hover};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background-color: {theme.scrollbar_bg};
    height: 12px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background-color: {theme.scrollbar_handle};
    border-radius: 4px;
    min-width: 30px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {theme.scrollbar_hover};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* Dialog */
QDialog {{
    background-color: {theme.bg_tertiary};
}}
QLineEdit {{
    background-color: {theme.bg_secondary};
    color: {theme.fg_primary};
    border: 1px solid {theme.border};
    border-radius: 3px;
    padding: 4px 8px;
}}
QLineEdit:focus {{
    border-color: {theme.border_light};
}}
QPushButton {{
    background-color: {theme.bg_tertiary};
    color: {theme.fg_primary};
    border: 1px solid {theme.border};
    border-radius: 3px;
    padding: 5px 15px;
}}
QPushButton:hover {{
    background-color: {theme.hover_bg};
    border-color: {theme.border_light};
}}
QPushButton:pressed {{
    background-color: {theme.selection_bg};
}}

/* Checkbox */
QCheckBox {{
    spacing: 4px;
    padding: 2px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {theme.border};
    border-radius: 2px;
    background-color: {theme.bg_tertiary};
}}
QCheckBox::indicator:checked {{
    background-color: {theme.selection_bg};
    border-color: {theme.border_light};
}}

/* Message Box */
QMessageBox {{
    background-color: {theme.bg_tertiary};
}}

/* Tool Tip */
QToolTip {{
    background-color: {theme.bg_tertiary};
    color: {theme.fg_primary};
    border: 1px solid {theme.border};
    padding: 4px;
}}

/* Menu Bar */
QMenuBar {{
    background-color: {theme.bg_tertiary};
    color: {theme.fg_primary};
    border-bottom: 1px solid {theme.border};
    padding: 2px;
}}
QMenuBar::item {{
    padding: 4px 10px;
    border-radius: 3px;
}}
QMenuBar::item:selected {{
    background-color: {theme.hover_bg};
}}
QMenu {{
    background-color: {theme.bg_tertiary};
    color: {theme.fg_primary};
    border: 1px solid {theme.border};
}}
QMenu::item {{
    padding: 5px 30px 5px 20px;
}}
QMenu::item:selected {{
    background-color: {theme.selection_bg};
}}
QMenu::separator {{
    height: 1px;
    background-color: {theme.border};
    margin: 4px 10px;
}}
QMenu::indicator {{
    width: 14px;
    height: 14px;
    margin-left: 4px;
}}
QMenu::indicator:checked {{
    background-color: {theme.selection_bg};
    border: 1px solid {theme.border_light};
    border-radius: 2px;
}}
QMenu::indicator:unchecked {{
    background-color: {theme.bg_tertiary};
    border: 1px solid {theme.border};
    border-radius: 2px;
}}

/* Status Bar */
QStatusBar {{
    background-color: {theme.bg_primary};
    color: {theme.fg_secondary};
    border-top: 1px solid {theme.border};
}}
QStatusBar QLabel {{
    padding: 2px 8px;
}}

/* Progress Bar */
QProgressBar {{
    border: 1px solid {theme.border};
    border-radius: 3px;
    text-align: center;
    background-color: {theme.bg_tertiary};
    color: {theme.fg_secondary};
}}
QProgressBar::chunk {{
    background-color: {theme.selection_bg};
}}

/* Tree Widget */
QTreeWidget {{
    background-color: {theme.bg_primary};
    color: {theme.fg_primary};
    border: 1px solid {theme.border};
    alternate-background-color: {theme.bg_secondary};
}}
QTreeWidget::item {{
    padding: 2px;
}}
QTreeWidget::item:selected {{
    background-color: {theme.selection_bg};
}}
QTreeWidget QHeaderView::section {{
    background-color: {theme.bg_tertiary};
    color: {theme.fg_secondary};
    padding: 4px;
    border: 1px solid {theme.border};
    font-weight: bold;
}}

/* Plain Text Edit (Hex View) */
QPlainTextEdit {{
    background-color: {theme.bg_secondary};
    color: {theme.fg_primary};
    border: 1px solid {theme.border};
    selection-background-color: {theme.selection_bg};
}}
QTextEdit {{
    background-color: {theme.bg_secondary};
    color: {theme.fg_primary};
    border: 1px solid {theme.border};
    selection-background-color: {theme.selection_bg};
}}
"""
