"""Entry point for media analyzer application."""

import sys
import platform


def _setup_platform():
    """Platform-specific setup before QApplication is created."""
    system = platform.system()

    if system == "Windows":
        # Set AppUserModelID so taskbar shows our icon, not Python's
        try:
            import ctypes
            app_id = "MediaAnalyzer.MediaInsight.0.1"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except (ImportError, AttributeError, OSError):
            pass

    elif system == "Darwin":
        # macOS: ensure the app doesn't show as "Python" in Dock
        # Setting the process name helps some macOS versions
        try:
            import ctypes
            import ctypes.util
            libc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("c"))
            # Set process name (shown in Activity Monitor)
            libc.setprogname(b"MediaAnalyzer")
        except (ImportError, AttributeError, OSError):
            pass


def main():
    """Launch the Media Analyzer application."""
    _setup_platform()

    from media_analyzer.app import create_application
    from media_analyzer.ui.main_window import MainWindow

    app = create_application(sys.argv)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
