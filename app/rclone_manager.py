"""
Rclone interaction layer.

Provides helpers to check for rclone, manage remotes, run bisync, list
remote contents, and report local disk usage – all via subprocess calls to
the rclone binary found on PATH.
"""

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


def _get_rclone_binary() -> str:
    """Return the path to the rclone binary, or 'rclone' as fallback."""
    binary = shutil.which("rclone")
    return binary if binary else "rclone"


class RcloneManager:
    """Wraps rclone CLI operations used by the application."""

    def __init__(self) -> None:
        """Initialise the manager and detect the rclone binary location."""
        self.rclone = _get_rclone_binary()

    # ------------------------------------------------------------------
    # Availability / version
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if rclone is installed and executable."""
        try:
            result = subprocess.run(
                [self.rclone, "version"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def get_version(self) -> str:
        """Return the rclone version string, or an error message."""
        try:
            result = subprocess.run(
                [self.rclone, "version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                # First line is e.g. "rclone v1.66.0"
                return result.stdout.splitlines()[0].strip()
            return "rclone no encontrado"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return "rclone no encontrado"

    # ------------------------------------------------------------------
    # Remote management
    # ------------------------------------------------------------------

    def list_remotes(self) -> List[str]:
        """Return a list of configured rclone remote names."""
        try:
            result = subprocess.run(
                [self.rclone, "listremotes"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                # Each line is "name:" – strip the trailing colon
                return [
                    line.strip().rstrip(":")
                    for line in result.stdout.splitlines()
                    if line.strip()
                ]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return []

    def remote_exists(self, name: str) -> bool:
        """Return True if a remote named *name* exists in rclone's config."""
        return name in self.list_remotes()

    def create_remote(self, name: str, service_type: str) -> bool:
        """
        Create a minimal rclone remote entry of the given *service_type*.

        The entry is created without credentials so that
        :meth:`authenticate` can be called afterwards to complete OAuth.
        Returns True on success.
        """
        try:
            result = subprocess.run(
                [self.rclone, "config", "create", name, service_type],
                capture_output=True,
                timeout=30,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def authenticate(
        self,
        name: str,
        on_complete: Optional[Callable[[bool], None]] = None,
    ) -> subprocess.Popen:
        """
        Launch the rclone OAuth flow for remote *name* in a background process.

        Opens the system browser automatically.  *on_complete* is called with
        True/False when the process finishes.  Returns the Popen object so
        the caller can monitor or terminate it.
        """
        # Use DETACHED_PROCESS on Windows so the console window is hidden
        kwargs: Dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS

        process = subprocess.Popen(
            [self.rclone, "config", "reconnect", f"{name}:"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs,
        )

        if on_complete is not None:
            def _wait() -> None:
                process.wait()
                on_complete(process.returncode == 0)

            threading.Thread(target=_wait, daemon=True).start()

        return process

    def delete_remote(self, name: str) -> bool:
        """Remove the rclone remote configuration for *name*."""
        try:
            result = subprocess.run(
                [self.rclone, "config", "delete", name],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    # ------------------------------------------------------------------
    # Synchronisation
    # ------------------------------------------------------------------

    def build_bisync_cmd(
        self,
        remote_name: str,
        remote_path: str,
        local_path: str,
        exclude_patterns: List[str],
        rclone_options: Dict,
        resync: bool = False,
    ) -> List[str]:
        """
        Build the rclone bisync command list for the given service parameters.

        Args:
            remote_name: Name of the rclone remote (e.g. "onedrive").
            remote_path: Path inside the remote (usually "/").
            local_path: Absolute local directory path.
            exclude_patterns: List of rclone exclude patterns.
            rclone_options: Dict with keys transfers, checkers, etc.
            resync: When True, appends --resync to force a full re-sync.
        """
        # Remote specification: "name:path" (path may be empty for root)
        remote_root = remote_path.lstrip("/")
        remote_spec = f"{remote_name}:{remote_root}"

        cmd = [self.rclone, "bisync", remote_spec, local_path]

        # Performance options
        cmd += [
            "--transfers", str(rclone_options.get("transfers", 16)),
            "--checkers", str(rclone_options.get("checkers", 32)),
            "--buffer-size", str(rclone_options.get("buffer_size", "64M")),
            "-P",  # progress output
        ]

        # Drive-specific chunk size option
        if "drive_chunk_size" in rclone_options:
            cmd += ["--drive-chunk-size", rclone_options["drive_chunk_size"]]

        # Exclude patterns
        for pattern in exclude_patterns:
            cmd += ["--exclude", pattern]

        if resync:
            cmd.append("--resync")

        return cmd

    def run_bisync(
        self,
        remote_name: str,
        remote_path: str,
        local_path: str,
        exclude_patterns: List[str],
        rclone_options: Dict,
        resync: bool = False,
        on_output: Optional[Callable[[str], None]] = None,
        on_complete: Optional[Callable[[bool, str], None]] = None,
    ) -> subprocess.Popen:
        """
        Start a bisync process in the background.

        *on_output* is called for each line of combined stdout/stderr.
        *on_complete* is called with (success: bool, output: str) when done.
        Returns the Popen object.
        """
        cmd = self.build_bisync_cmd(
            remote_name,
            remote_path,
            local_path,
            exclude_patterns,
            rclone_options,
            resync=resync,
        )

        # Ensure local directory exists
        Path(local_path).mkdir(parents=True, exist_ok=True)

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        def _reader() -> None:
            """Read process output line by line and invoke callbacks."""
            lines: List[str] = []
            # Stream output as it arrives
            assert process.stdout is not None
            for line in process.stdout:
                clean = line.rstrip()
                lines.append(clean)
                if on_output:
                    on_output(clean)
            process.wait()
            if on_complete:
                on_complete(process.returncode == 0, "\n".join(lines))

        threading.Thread(target=_reader, daemon=True).start()
        return process

    # ------------------------------------------------------------------
    # Remote file listing
    # ------------------------------------------------------------------

    def list_remote_dirs(self, remote_name: str, path: str = "") -> List[str]:
        """
        Return a list of directory names at *path* inside *remote_name*.

        Returns an empty list on error.
        """
        remote_spec = f"{remote_name}:{path}"
        try:
            result = subprocess.run(
                [self.rclone, "lsf", "--dirs-only", remote_spec],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return [
                    d.strip().rstrip("/")
                    for d in result.stdout.splitlines()
                    if d.strip()
                ]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return []

    def list_recent_changes(
        self, local_path: str, limit: int = 50
    ) -> List[Tuple[str, str]]:
        """
        Return up to *limit* recently-modified files under *local_path*.

        Each entry is a (relative_path, iso_mtime) tuple sorted newest first.
        Falls back to an empty list on any error.
        """
        results: List[Tuple[str, str]] = []
        base = Path(local_path)
        if not base.is_dir():
            return results
        try:
            # Collect (mtime, path) for all files
            entries = []
            for f in base.rglob("*"):
                if f.is_file():
                    try:
                        entries.append((f.stat().st_mtime, f))
                    except OSError:
                        pass
            # Sort newest first
            entries.sort(key=lambda x: x[0], reverse=True)
            for mtime, fpath in entries[:limit]:
                rel = str(fpath.relative_to(base))
                import datetime
                dt = datetime.datetime.fromtimestamp(mtime).strftime(
                    "%Y-%m-%d %H:%M"
                )
                results.append((rel, dt))
        except OSError:
            pass
        return results

    # ------------------------------------------------------------------
    # Disk usage
    # ------------------------------------------------------------------

    def get_local_disk_usage(self, local_path: str) -> str:
        """
        Return a human-readable string for the disk space used by *local_path*.

        Example: "1.23 GB"
        """
        base = Path(local_path)
        if not base.is_dir():
            return "0 B"
        total_bytes = sum(
            f.stat().st_size
            for f in base.rglob("*")
            if f.is_file()
        )
        return _format_bytes(total_bytes)

    def free_local_cache(self, local_path: str) -> Tuple[bool, str]:
        """
        Delete the contents of *local_path* to free cached/downloaded files.

        Returns (success, message).
        """
        import shutil as _shutil

        base = Path(local_path)
        if not base.is_dir():
            return False, "El directorio no existe"
        try:
            for item in base.iterdir():
                if item.is_dir():
                    _shutil.rmtree(item)
                else:
                    item.unlink()
            return True, "Caché liberada correctamente"
        except OSError as exc:
            return False, f"Error al liberar caché: {exc}"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _format_bytes(num_bytes: float) -> str:
    """Convert *num_bytes* to a human-readable size string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PB"
