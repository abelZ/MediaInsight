"""Entry point for media analyzer application."""

import sys
from media_analyzer.app import create_application
from media_analyzer.ui.main_window import MainWindow


def main():
    """Launch the Media Analyzer application."""
    app = create_application(sys.argv)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
