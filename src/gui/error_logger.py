"""
Error logger for Rclone Python IA.

Keeps an in-memory list of error messages that occurred during the current
application session.  On start-up it pre-loads the previous session's log
from disk so the user always has recent history.  When the application closes
the current log is appended to the same file so records accumulate over time.

Usage:
    logger = ErrorLogger()
    logger.log("MyService", "Something went wrong")
    print(logger.get_all_text())
    logger.save_to_file()   # called automatically on app shutdown
"""

import datetime
import os
import sys
from typing import List

from src.config.config_manager import get_config_dir


# File name for the error log inside the application config directory
_LOG_FILE_NAME = "errors.txt"
# Maximum number of in-memory entries (older entries are dropped)
_MAX_ENTRIES = 500


def _log_file_path() -> str:
    """Return the absolute path to the error log file."""
    return os.path.join(str(get_config_dir()), _LOG_FILE_NAME)


class ErrorLogger:
    """
    Application-wide error log.

    In-memory entries are formatted as::

        [YYYY-MM-DD HH:MM:SS] [service_name] message

    Previous entries are loaded from disk on construction and new entries
    are prepended so that the most recent errors appear at the top.
    """

    def __init__(self) -> None:
        # Combined list: previous session entries + current session entries
        self._entries: List[str] = []
        self._load_from_file()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(self, service_name: str, message: str) -> None:
        """
        Add a new error entry.

        Args:
            service_name: Name of the service that produced the error.
            message: Human-readable error description.
        """
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] [{service_name}] {message}"
        # Insert newest first
        self._entries.insert(0, entry)
        # Trim to maximum
        if len(self._entries) > _MAX_ENTRIES:
            self._entries = self._entries[:_MAX_ENTRIES]

    def get_all_entries(self) -> List[str]:
        """Return all log entries (newest first)."""
        return list(self._entries)

    def get_all_text(self) -> str:
        """Return all log entries joined by newlines (newest first)."""
        return "\n".join(self._entries)

    def clear(self) -> None:
        """Clear all in-memory entries."""
        self._entries = []

    def save_to_file(self) -> None:
        """
        Append the current in-memory log to the on-disk log file.

        Uses a session separator so multiple sessions remain distinguishable.
        """
        if not self._entries:
            return
        try:
            log_path = _log_file_path()
            # Write entries in chronological order (oldest first) so the file
            # reads naturally from top to bottom.
            chronological = list(reversed(self._entries))
            separator = (
                f"\n{'=' * 60}\n"
                f"Sesión: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"{'=' * 60}\n"
            )
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(separator)
                fh.write("\n".join(chronological))
                fh.write("\n")
        except (OSError, IOError):
            pass

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_from_file(self) -> None:
        """
        Load the last ``_MAX_ENTRIES`` lines from the on-disk log file as the
        initial set of entries so that previous session errors are visible.
        """
        log_path = _log_file_path()
        if not os.path.exists(log_path):
            return
        try:
            with open(log_path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
            # Keep only lines that look like timestamped log entries
            # (format: "[YYYY-MM-DD HH:MM:SS] [service] message")
            entries = [
                line.rstrip("\n")
                for line in lines
                if line.startswith("[")
            ]
            # Reverse so newest-first, then cap
            entries.reverse()
            self._entries = entries[:_MAX_ENTRIES]
        except (OSError, IOError):
            pass
