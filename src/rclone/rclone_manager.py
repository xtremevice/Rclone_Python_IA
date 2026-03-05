"""
Wrapper around the rclone command-line tool.

Provides methods to configure remotes, run bisync, check mount status,
and stream output from rclone operations.
"""

import os
import platform
import subprocess
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

from src.config.config_manager import ConfigManager, get_rclone_config_path, PERSONAL_VAULT_PATTERN


def _rclone_base_args(config_manager: ConfigManager) -> List[str]:
    """Return the common rclone arguments including --config path."""
    return ["rclone", "--config", str(config_manager.rclone_config_path())]


class RcloneManager:
    """
    Manages rclone operations for all configured services.

    Each service runs its own sync loop in a background thread.
    Callbacks are invoked when sync events occur so the UI can update.
    """

    def __init__(self, config_manager: ConfigManager) -> None:
        # Reference to the shared application config manager
        self._config = config_manager
        # Map of service_name → background sync thread
        self._sync_threads: Dict[str, threading.Thread] = {}
        # Map of service_name → stop-event for its thread
        self._stop_events: Dict[str, threading.Event] = {}
        # Map of service_name → current sync status string
        self._status: Dict[str, str] = {}
        # Optional callback(service_name, status_str) called on status changes
        self.on_status_change: Optional[Callable[[str, str], None]] = None
        # Optional callback(service_name, file_path, synced) for history updates
        self.on_file_synced: Optional[Callable[[str, str, bool], None]] = None
        # Optional callback(service_name, error_message) called on sync errors
        self.on_error: Optional[Callable[[str, str], None]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_service(self, service_name: str) -> None:
        """
        Start the background sync loop for the given service.

        Does nothing if the service is already running.
        """
        if service_name in self._sync_threads and self._sync_threads[service_name].is_alive():
            return

        stop_event = threading.Event()
        self._stop_events[service_name] = stop_event
        thread = threading.Thread(
            target=self._sync_loop,
            args=(service_name, stop_event),
            daemon=True,
            name=f"sync-{service_name}",
        )
        self._sync_threads[service_name] = thread
        thread.start()

    def stop_service(self, service_name: str) -> None:
        """Signal the background sync loop for this service to stop."""
        event = self._stop_events.get(service_name)
        if event:
            event.set()

    def is_running(self, service_name: str) -> bool:
        """Return True if the sync thread for this service is alive."""
        thread = self._sync_threads.get(service_name)
        return thread is not None and thread.is_alive()

    def get_status(self, service_name: str) -> str:
        """Return the human-readable sync status for this service."""
        return self._status.get(service_name, "Detenido")

    def start_all(self) -> None:
        """Start sync loops for all services that have sync_enabled=True."""
        for svc in self._config.get_services():
            if svc.get("sync_enabled", True):
                self.start_service(svc["name"])

    def stop_all(self) -> None:
        """Stop all running sync loops."""
        for name in list(self._sync_threads.keys()):
            self.stop_service(name)

    def run_bisync_once(self, service_name: str) -> bool:
        """
        Execute a single bisync pass for the given service synchronously.

        Returns True on success, False on failure.
        """
        svc = self._config.get_service(service_name)
        if svc is None:
            return False
        return self._do_bisync(svc)

    def open_browser_auth(self, remote_name: str, platform_type: str) -> subprocess.Popen:
        """
        Launch 'rclone config create' in an interactive subprocess so that
        rclone opens the browser for OAuth authentication.

        Returns the Popen object so the caller can wait on it.
        """
        args = _rclone_base_args(self._config) + [
            "config",
            "create",
            remote_name,
            platform_type,
            "--auto-confirm",
        ]
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return proc

    def delete_remote(self, remote_name: str) -> bool:
        """
        Remove a remote from the rclone config file.

        Returns True if successful.
        """
        args = _rclone_base_args(self._config) + ["config", "delete", remote_name]
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def get_disk_usage(self, service_name: str) -> str:
        """
        Return a human-readable string of local disk space used by the service.
        Falls back to 'N/A' if the path doesn't exist or du fails.
        """
        svc = self._config.get_service(service_name)
        if svc is None:
            return "N/A"
        local_path = svc.get("local_path", "")
        if not local_path or not Path(local_path).exists():
            return "N/A"
        try:
            if platform.system() == "Windows":
                # Use Python to calculate on Windows
                total = sum(
                    f.stat().st_size
                    for f in Path(local_path).rglob("*")
                    if f.is_file()
                )
                return _human_size(total)
            else:
                result = subprocess.run(
                    ["du", "-sh", local_path],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if result.returncode == 0:
                    return result.stdout.split()[0]
        except (OSError, subprocess.TimeoutExpired):
            pass
        return "N/A"

    def free_cache(self, service_name: str) -> bool:
        """
        Run 'rclone vfs/forget' to release locally cached files back to
        cloud-only mode for the given service.

        Returns True if the command succeeded.
        """
        svc = self._config.get_service(service_name)
        if svc is None:
            return False
        remote = f"{svc['remote_name']}:{svc.get('remote_path', '/')}"
        args = _rclone_base_args(self._config) + [
            "rc",
            "vfs/forget",
            f"fs={remote}",
        ]
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def list_remote_tree(self, service_name: str) -> List[Dict]:
        """
        Return a list of dicts representing the top-level remote directories
        with their sync status.  Each dict has 'path' and 'is_dir' keys.
        """
        svc = self._config.get_service(service_name)
        if svc is None:
            return []
        remote = f"{svc['remote_name']}:{svc.get('remote_path', '/')}"
        args = _rclone_base_args(self._config) + [
            "lsjson",
            "--dirs-only",
            "--fast-list",
            remote,
        ]
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                import json
                items = json.loads(result.stdout or "[]")
                return [
                    {"path": item.get("Path", ""), "is_dir": item.get("IsDir", False)}
                    for item in items
                ]
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
        return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sync_loop(self, service_name: str, stop_event: threading.Event) -> None:
        """
        Background thread that repeatedly bisync the service at the
        configured interval until the stop_event is set.
        """
        import time

        self._set_status(service_name, "Iniciando…")
        while not stop_event.is_set():
            svc = self._config.get_service(service_name)
            # Re-read in case config changed
            if svc is None or not svc.get("sync_enabled", True):
                break

            self._set_status(service_name, "Sincronizando…")
            success = self._do_bisync(svc)
            if success:
                self._set_status(service_name, "Actualizado")
            else:
                self._set_status(service_name, "Error en sincronización")
                self._emit_error(service_name, "Fallo en el ciclo de sincronización")

            # Wait for the configured interval (or stop early if signalled)
            interval = svc.get("sync_interval", 900)
            stop_event.wait(timeout=interval)

        self._set_status(service_name, "Detenido")

    def _do_bisync(self, svc: Dict) -> bool:
        """
        Run rclone bisync for a single service dictionary.

        Attempts bisync first; if it fails, retries with --resync.
        Returns True on success.
        """
        remote = f"{svc['remote_name']}:{svc.get('remote_path', '/')}"
        local = svc.get("local_path", "")
        name = svc.get("name", "?")

        # Build the exclusion flags
        exclude_args: List[str] = []
        # Apply the default OneDrive Personal Vault exclusion if configured
        if svc.get("exclude_personal_vault", True) and svc.get("platform") == "onedrive":
            exclude_args += ["--exclude", PERSONAL_VAULT_PATTERN]
        # Apply any user-defined exclusions, skipping the personal vault
        # pattern if it was already added above to avoid duplicates
        for pattern in svc.get("exclusions", []):
            if pattern != PERSONAL_VAULT_PATTERN:
                exclude_args += ["--exclude", pattern]

        # Performance options
        perf_args = [
            "--transfers", "16",
            "--checkers", "32",
            "--drive-chunk-size", "128M",
            "--buffer-size", "64M",
            "-P",
        ]

        # VFS cache options
        vfs_cache_mode = svc.get("vfs_cache_mode", "on_demand")
        vfs_cache_max_size = svc.get("vfs_cache_max_size", "10G")
        vfs_cache_dir = svc.get("vfs_cache_dir", "").strip()

        vfs_args = [
            "--vfs-cache-mode", vfs_cache_mode,
            "--vfs-cache-max-size", vfs_cache_max_size,
        ]
        if vfs_cache_dir:
            vfs_args += ["--cache-dir", vfs_cache_dir]

        base = _rclone_base_args(self._config)
        Path(local).mkdir(parents=True, exist_ok=True)

        # First attempt: standard bisync
        cmd = base + ["bisync", remote, local] + perf_args + vfs_args + exclude_args
        success = self._run_rclone(cmd, name, svc)

        # Second attempt: bisync --resync if first attempt failed
        if not success:
            cmd_resync = cmd + ["--resync"]
            success = self._run_rclone(cmd_resync, name, svc, is_retry=True)

        if not success:
            self._emit_error(name, f"La sincronización falló (remoto: {remote})")

        return success

    def _run_rclone(
        self,
        cmd: List[str],
        service_name: str,
        svc: Dict,
        is_retry: bool = False,
    ) -> bool:
        """
        Execute a rclone command, parse its output for changed files, and
        call the on_file_synced callback for each detected file.

        Returns True if the process exits with code 0.
        """
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            # Read output line-by-line to detect file changes
            for raw_line in proc.stdout or []:
                line = raw_line.strip()
                if not line:
                    continue
                # rclone -P outputs lines like: "Transferred: <path>"
                # or lines starting with a file path
                file_path = _extract_file_path(line)
                if file_path and self.on_file_synced:
                    self.on_file_synced(service_name, file_path, True)
                    # Record in persistent history
                    self._config.add_sync_history_entry(service_name, file_path, True)

            proc.wait()
            return proc.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            self._emit_error(service_name, f"Error al ejecutar rclone: {exc}")
            return False

    def _set_status(self, service_name: str, status: str) -> None:
        """Update the status cache and fire the on_status_change callback."""
        self._status[service_name] = status
        if self.on_status_change:
            try:
                self.on_status_change(service_name, status)
            except Exception:
                pass

    def _emit_error(self, service_name: str, message: str) -> None:
        """Fire the on_error callback if registered."""
        if self.on_error:
            try:
                self.on_error(service_name, message)
            except Exception:
                pass


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _extract_file_path(line: str) -> Optional[str]:
    """
    Try to extract a meaningful file path from a rclone output line.
    Returns None if the line does not look like a file transfer event.
    """
    # rclone -P lines that indicate a file transfer start with a path
    # preceded by certain keywords.  We look for common patterns.
    prefixes = ("Copied", "Deleted", "Moved", "Updated", "Transferred:")
    for prefix in prefixes:
        if prefix in line:
            # Grab the part after the keyword
            idx = line.index(prefix) + len(prefix)
            rest = line[idx:].strip(" :")
            if rest:
                return rest.split(" (")[0].strip()
    return None


def _human_size(num_bytes: int) -> str:
    """Convert a byte count to a human-readable string (e.g. '1.2 GB')."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"
