"""Centralized logging configuration for MediaInsight.

Sets up Python's logging module with:
- A QtSignalHandler that emits log records via Qt signals (for the Log view)
- Console handler for development
- Consistent formatting across all modules

Usage in any module:
    import logging
    logger = logging.getLogger(__name__)
    logger.info("Something happened")
"""

import logging
import sys
from typing import Optional

from PySide6.QtCore import QObject, Signal


# Custom log level for protocol-level details
PROTOCOL = 15  # Between DEBUG(10) and INFO(20)
logging.addLevelName(PROTOCOL, "PROTOCOL")


class LogSignalEmitter(QObject):
    """Qt object that emits signals when log records arrive.

    This bridges Python's logging module to Qt's signal/slot system,
    allowing the Log view widget to receive records in a thread-safe way.
    """
    log_record = Signal(object)  # logging.LogRecord


class QtSignalHandler(logging.Handler):
    """Logging handler that emits each record as a Qt signal.

    Thread-safe: Qt queued connections deliver to the main thread
    regardless of which thread emits.
    """

    def __init__(self):
        super().__init__()
        self.emitter = LogSignalEmitter()

    def emit(self, record: logging.LogRecord):
        try:
            # Format the record before emitting (thread-safe)
            record.formatted_message = self.format(record)
            self.emitter.log_record.emit(record)
        except Exception:
            self.handleError(record)


# Module-level singleton instances
_signal_handler: Optional[QtSignalHandler] = None
_initialized = False


def get_signal_handler() -> QtSignalHandler:
    """Get the global Qt signal handler (creates it on first call)."""
    global _signal_handler
    if _signal_handler is None:
        _signal_handler = QtSignalHandler()
        _signal_handler.setFormatter(logging.Formatter(
            "%(asctime)s.%(msecs)03d  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%H:%M:%S"
        ))
    return _signal_handler


def setup_logging(level: int = logging.DEBUG) -> None:
    """Initialize logging for the entire application.

    Call once at app startup (before MainWindow is created).
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger("media_analyzer")
    root.setLevel(level)

    # Qt signal handler (for Log view)
    qt_handler = get_signal_handler()
    qt_handler.setLevel(logging.DEBUG)
    root.addHandler(qt_handler)

    # Console handler (for development)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S"
    ))
    root.addHandler(console)

    # Suppress overly verbose third-party loggers
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
