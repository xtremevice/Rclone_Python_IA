"""
src/core/rclone.py

Provides Python wrappers around the rclone command-line tool.
Handles remote creation, bidirectional sync (bisync), OAuth authorisation,
disk-usage queries, and folder listing.

All public functions must be called from a worker thread so they do not block
the Qt event loop.  Qt signals are used to report progress back to the UI.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt5.QtCore import QObject, pyqtSignal, QThread

from src.core.config import RCLONE_CONFIG_FILE, SUPPORTED_PLATFORMS


# ---------------------------------------------------------------------------
# Helper: locate the rclone binary
# ---------------------------------------------------------------------------

def find_rclone() -> Optional[str]:
    """Return the absolute path to the rclone binary, or None if not found."""
    # 1. Check PATH first
    path = shutil.which("rclone")
    if path:
        return path
    # 2. On Windows, check common install locations
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\rclone\rclone.exe",
            r"C:\rclone\rclone.exe",
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
    return None


RCLONE_BIN: Optional[str] = find_rclone()


def rclone_available() -> bool:
    """Return True when rclone is found on this system."""
    return RCLONE_BIN is not None


def _base_cmd() -> List[str]:
    """Return the base rclone command with the custom config file."""
    return [RCLONE_BIN, "--config", str(RCLONE_CONFIG_FILE)]


# ---------------------------------------------------------------------------
# Remote management
# ---------------------------------------------------------------------------

def create_remote(remote_name: str, platform_key: str) -> Tuple[bool, str]:
    """Create a new rclone remote with an interactive browser OAuth flow.

    Opens the user's browser for authentication and waits for rclone to
    finish before returning.  Returns (success, message).
    """
    rclone_type = SUPPORTED_PLATFORMS.get(platform_key)
    if not rclone_type:
        return False, f"Plataforma desconocida: {platform_key}"
    if not rclone_available():
        return False, "rclone no está instalado o no se encontró en el PATH."

    # Build the config create command.  auto-confirm=true lets rclone open the
    # browser automatically; the process blocks until the OAuth round-trip is
    # complete.
    cmd = _base_cmd() + [
        "config", "create",
        remote_name,
        rclone_type,
        "auto_acknowledge_abuse", "true",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,   # 5-minute timeout for the OAuth flow
        )
        if result.returncode == 0:
            return True, "Remoto creado correctamente."
        return False, result.stderr or result.stdout
    except subprocess.TimeoutExpired:
        return False, "Tiempo de espera agotado durante la autorización."
    except Exception as exc:
        return False, str(exc)


def delete_remote(remote_name: str) -> Tuple[bool, str]:
    """Delete an rclone remote from the config file. Returns (success, message)."""
    if not rclone_available():
        return False, "rclone no está disponible."
    cmd = _base_cmd() + ["config", "delete", remote_name]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True, "Remoto eliminado."
        return False, result.stderr
    except Exception as exc:
        return False, str(exc)


def list_remotes() -> List[str]:
    """Return the list of configured remote names."""
    if not rclone_available():
        return []
    cmd = _base_cmd() + ["listremotes"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        # Each line ends with ':'
        return [line.strip().rstrip(":") for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def list_remote_folders(remote_name: str, remote_path: str = "/") -> List[str]:
    """Return the top-level folders of a remote path.

    Each item is the folder name (not a full path).
    """
    if not rclone_available():
        return []
    remote = f"{remote_name}:{remote_path}"
    cmd = _base_cmd() + ["lsd", "--max-depth", "1", remote]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        folders = []
        for line in result.stdout.splitlines():
            # lsd output: "  -1 2024-01-01 00:00:00        -1 FolderName"
            parts = line.split()
            if len(parts) >= 5:
                folders.append(parts[-1])
        return folders
    except Exception:
        return []


def get_disk_usage(remote_name: str, remote_path: str = "/") -> str:
    """Return a human-readable string for the total space used by a remote."""
    if not rclone_available():
        return "N/A"
    remote = f"{remote_name}:{remote_path}"
    cmd = _base_cmd() + ["about", "--json", remote]
    try:
        import json
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            used = data.get("used", 0)
            return _format_bytes(used)
        return "N/A"
    except Exception:
        return "N/A"


def _format_bytes(num: int) -> str:
    """Convert a byte count to a human-readable string (GiB / MiB / KiB)."""
    for unit in ("TiB", "GiB", "MiB", "KiB"):
        divisor = {"TiB": 1 << 40, "GiB": 1 << 30, "MiB": 1 << 20, "KiB": 1 << 10}[unit]
        if num >= divisor:
            return f"{num / divisor:.2f} {unit}"
    return f"{num} B"


# ---------------------------------------------------------------------------
# Sync worker (runs in a QThread to avoid blocking the UI)
# ---------------------------------------------------------------------------

class SyncWorker(QObject):
    """Runs a single rclone bisync operation in a background QThread.

    Signals
    -------
    progress(str)   Emitted for each line of rclone output.
    file_synced(str, bool)  Emitted per transferred file (filename, success).
    finished(bool, str)     Emitted when the operation completes.
    """

    progress = pyqtSignal(str)
    file_synced = pyqtSignal(str, bool)
    finished = pyqtSignal(bool, str)

    # Regular expression that matches rclone's progress lines for transferred
    # files.  Example: "Transferred:   doc.txt"
    _TRANSFER_RE = re.compile(r"^\s*Transferred:\s+(.+)$")

    def __init__(
        self,
        remote_name: str,
        local_path: str,
        remote_path: str,
        exclude_rules: List[str],
        use_resync: bool,
        download_on_demand: bool,
        parent: Optional[QObject] = None,
    ) -> None:
        """Initialise the worker with sync parameters."""
        super().__init__(parent)
        self.remote_name = remote_name
        self.local_path = local_path
        self.remote_path = remote_path
        self.exclude_rules = exclude_rules
        self.use_resync = use_resync
        self.download_on_demand = download_on_demand
        self._cancelled = False

    def cancel(self) -> None:
        """Signal the worker to abort the current sync at the next opportunity."""
        self._cancelled = True

    def run(self) -> None:
        """Execute the rclone bisync command and emit progress signals.

        This method is invoked automatically by the owning QThread.
        All lines written to rclone's stdout/stderr are forwarded to the
        ``progress`` signal.  Individual file transfers also trigger the
        ``file_synced`` signal.  When done, ``finished`` is emitted with a
        boolean success flag and a descriptive message.
        """
        if not rclone_available():
            self.finished.emit(False, "rclone no está disponible.")
            return

        # Build the remote specifier
        remote_src = f"{self.remote_name}:{self.remote_path}"

        # Core bisync arguments
        cmd = _base_cmd() + [
            "bisync",
            remote_src,
            self.local_path,
            "--transfers", "16",
            "--checkers", "32",
            "--drive-chunk-size", "128M",
            "--buffer-size", "64M",
            "-P",
            "--log-level", "INFO",
        ]

        # Add --resync flag when requested (required for first run or after errors)
        if self.use_resync:
            cmd.append("--resync")

        # Add exclude rules
        for rule in self.exclude_rules:
            cmd += ["--exclude", rule]

        # VFS / on-demand download is handled at mount time, not bisync time,
        # but we record the preference so the caller can act on it if needed.

        self.progress.emit(f"Iniciando sincronización: {remote_src} → {self.local_path}")
        self.progress.emit("Comando: " + " ".join(cmd))

        try:
            # Use Popen so we can stream output line by line
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            for line in proc.stdout:
                if self._cancelled:
                    proc.terminate()
                    self.finished.emit(False, "Sincronización cancelada por el usuario.")
                    return

                line = line.rstrip()
                self.progress.emit(line)

                # Check for transferred-file lines in the output
                match = self._TRANSFER_RE.match(line)
                if match:
                    filename = match.group(1).strip()
                    self.file_synced.emit(filename, True)

            proc.wait()

            if proc.returncode == 0:
                self.finished.emit(True, "Sincronización completada correctamente.")
            else:
                # bisync returns non-zero on resync-needed errors; retry once
                # with --resync if we were not already using it
                if not self.use_resync and proc.returncode in (1, 2):
                    self.progress.emit(
                        "⚠️ Error detectado. Reintentando con --resync …"
                    )
                    self.use_resync = True
                    self.run()
                else:
                    self.finished.emit(
                        False,
                        f"rclone terminó con código {proc.returncode}.",
                    )
        except Exception as exc:
            self.finished.emit(False, str(exc))


# ---------------------------------------------------------------------------
# Auth worker (runs OAuth flow in a QThread)
# ---------------------------------------------------------------------------

class AuthWorker(QObject):
    """Opens the browser for rclone OAuth and emits ``finished`` when done.

    Signals
    -------
    finished(bool, str)   success flag + message
    """

    finished = pyqtSignal(bool, str)

    def __init__(
        self,
        remote_name: str,
        platform_key: str,
        parent: Optional[QObject] = None,
    ) -> None:
        """Initialise with the target remote name and platform key."""
        super().__init__(parent)
        self.remote_name = remote_name
        self.platform_key = platform_key

    def run(self) -> None:
        """Execute the rclone config create command (blocking OAuth flow)."""
        success, message = create_remote(self.remote_name, self.platform_key)
        self.finished.emit(success, message)


def get_rclone_version() -> str:
    """Return the rclone version string, or 'no disponible' if not found."""
    if not rclone_available():
        return "no disponible"
    try:
        result = subprocess.run(
            [RCLONE_BIN, "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        first_line = result.stdout.splitlines()[0] if result.stdout else ""
        return first_line.strip()
    except Exception:
        return "no disponible"
