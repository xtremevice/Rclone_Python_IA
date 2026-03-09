"""
Wrapper around the rclone command-line tool.

Provides methods to configure remotes, run bisync, check mount status,
and stream output from rclone operations.
"""

import os
import platform
import re
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

from src.config.config_manager import ConfigManager, get_rclone_config_path, PERSONAL_VAULT_PATTERN


def _bisync_cache_dir() -> Path:
    """Return the platform-appropriate directory where rclone stores bisync state.

    rclone writes lock files (``*.lck``) and partial listing files
    (``*.lst-new``) here.  A stale lock from an interrupted sync prevents the
    next run from proceeding, so we clean up these files before each bisync.

    Paths by platform:
        Linux  : ``~/.cache/rclone/bisync/``
        macOS  : ``~/Library/Caches/rclone/bisync/``
        Windows: ``%LOCALAPPDATA%\\rclone\\bisync\\``
    """
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "rclone" / "bisync"


def _clear_bisync_stale_files(
    remote_name: str,
    cache_dir: Path,
    emit_fn: Callable[[str], None],
) -> int:
    """Remove stale rclone bisync lock and partial-listing files for *remote_name*.

    Only files whose names begin with *remote_name* (case-sensitive) are
    removed, so we never accidentally delete state that belongs to a
    different remote.  Both ``*.lck`` and ``*.lst-new`` files are targeted.

    Args:
        remote_name: The rclone remote identifier (e.g. ``"duexy"``).
        cache_dir: Path to the rclone bisync cache directory.
        emit_fn: Callable that accepts a single log-message string.

    Returns:
        The number of files that were successfully deleted.
    """
    if not cache_dir.is_dir():
        return 0

    # An empty remote_name would match every file in the directory; skip.
    if not remote_name:
        return 0

    removed = 0
    for pattern in ("*.lck", "*.lst-new"):
        for stale in cache_dir.glob(pattern):
            if not stale.name.startswith(remote_name):
                continue
            try:
                stale.unlink()
                emit_fn(f"[LOCK] Archivo de bloqueo eliminado: {stale.name}")
                removed += 1
            except OSError as exc:
                emit_fn(f"[LOCK] No se pudo eliminar {stale.name}: {exc}")
    return removed


# Keywords that indicate an error or fatal condition in rclone output lines.
# rclone writes "ERROR : ..." and "FATAL : ..." to stderr (merged into stdout).
_RCLONE_ERROR_KEYWORDS = ("ERROR", "FATAL", "Fatal error", "error:")


def _rclone_base_args(config_manager: ConfigManager) -> List[str]:
    """Return the common rclone arguments including --config path."""
    return ["rclone", "--config", str(config_manager.rclone_config_path())]


def _rclone_supports_resync_mode(config_manager: ConfigManager) -> bool:
    """Return True if the installed rclone version supports --resync-mode (v1.64+).

    ``--resync-mode`` was introduced in rclone v1.64.  On earlier versions the
    flag is unknown and causes a fatal error.  When the version cannot be
    determined we return False so the flag is safely omitted.
    """
    version_str = config_manager.get_rclone_version()
    match = re.search(r"v(\d+)\.(\d+)", version_str)
    if not match:
        return False
    major, minor = int(match.group(1)), int(match.group(2))
    return (major, minor) >= (1, 64)


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
        # Map of service_name → active rclone mount Popen process
        self._mount_procs: Dict[str, subprocess.Popen] = {}
        # Map of service_name → currently-running rclone bisync Popen process.
        # Used to terminate a long-running bisync immediately when stop_service
        # is called, without waiting for the process to finish naturally.
        self._running_procs: Dict[str, subprocess.Popen] = {}
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
        """Signal the background sync loop for this service to stop.

        In addition to setting the stop event (which interrupts the inter-cycle
        wait), any rclone bisync process that is actively running for this
        service is terminated immediately so the UI reflects the stopped state
        without delay.
        """
        event = self._stop_events.get(service_name)
        if event:
            event.set()
        # Terminate the running rclone process if present so the stop takes
        # effect immediately rather than waiting for the current bisync to finish.
        proc = self._running_procs.get(service_name)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                # Process may have exited between poll() and terminate()
                pass

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
        """Stop all running sync loops and mounts."""
        for name in list(self._sync_threads.keys()):
            self.stop_service(name)
        self.stop_all_mounts()

    def start_mount(self, service_name: str) -> bool:
        """
        Start a persistent rclone mount process for the given service.

        The mount path is taken from ``svc['mount_path']``.  Does nothing and
        returns False if the service is already mounted, mount is disabled, or
        no mount_path is configured.

        Returns True if the mount process was successfully launched.
        """
        if self.is_mounted(service_name):
            return True

        svc = self._config.get_service(service_name)
        if svc is None or not svc.get("mount_enabled", False):
            return False

        mount_path = svc.get("mount_path", "").strip()
        if not mount_path:
            self._emit_error(service_name, "[MOUNT] No se ha configurado la ruta de montaje")
            return False

        remote = f"{svc['remote_name']}:{svc.get('remote_path', '/')}"

        # Shared VFS cache options
        vfs_cache_mode = svc.get("vfs_cache_mode", "writes")
        vfs_cache_max_size = svc.get("vfs_cache_max_size", "10G")
        vfs_cache_dir = svc.get("vfs_cache_dir", "").strip()

        # Mount-specific VFS options
        vfs_read_chunk_size = svc.get("vfs_read_chunk_size", "10M")
        vfs_read_chunk_size_limit = svc.get("vfs_read_chunk_size_limit", "100M")

        # Ensure the mount directory exists
        try:
            Path(mount_path).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._emit_error(service_name, f"[MOUNT] No se pudo crear el directorio de montaje: {exc}")
            return False

        cmd = _rclone_base_args(self._config) + [
            "mount",
            remote,
            mount_path,
            "--vfs-cache-mode", vfs_cache_mode,
            "--vfs-cache-max-size", vfs_cache_max_size,
            "--vfs-read-chunk-size", vfs_read_chunk_size,
            "--vfs-read-chunk-size-limit", vfs_read_chunk_size_limit,
        ]
        if vfs_cache_dir:
            cmd += ["--cache-dir", vfs_cache_dir]

        # Log the command for reference
        self._emit_error(service_name, "[MOUNT CMD] " + shlex.join(cmd))

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self._mount_procs[service_name] = proc
            # Background thread to forward mount error output
            threading.Thread(
                target=self._read_mount_output,
                args=(service_name, proc),
                daemon=True,
                name=f"mount-{service_name}",
            ).start()
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            self._emit_error(service_name, f"[MOUNT] Error al iniciar montaje: {exc}")
            return False

    def stop_mount(self, service_name: str) -> None:
        """Terminate the rclone mount process for the given service."""
        proc = self._mount_procs.pop(service_name, None)
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def is_mounted(self, service_name: str) -> bool:
        """Return True if the mount process for this service is alive."""
        proc = self._mount_procs.get(service_name)
        return proc is not None and proc.poll() is None

    def start_all_mounts(self) -> None:
        """Start mount processes for all services that have mount_enabled=True."""
        for svc in self._config.get_services():
            if svc.get("mount_enabled", False):
                self.start_mount(svc["name"])

    def stop_all_mounts(self) -> None:
        """Terminate all running mount processes."""
        for name in list(self._mount_procs.keys()):
            self.stop_mount(name)

    def run_bisync_once(self, service_name: str) -> bool:
        """
        Execute a single bisync pass for the given service synchronously.

        Returns True on success, False on failure.
        """
        svc = self._config.get_service(service_name)
        if svc is None:
            return False
        return self._do_bisync(svc)

    def clear_bisync_locks(self, service_name: str) -> int:
        """
        Remove stale rclone bisync lock and partial-listing files for the
        given service.

        This is useful when a previous bisync was interrupted and left a
        ``*.lck`` or ``*.lst-new`` file in the rclone cache directory that
        blocks the next run.  Any removed files are reported via the
        ``on_error`` callback.

        Returns the number of files that were deleted.
        """
        svc = self._config.get_service(service_name)
        if svc is None:
            return 0
        remote_name = svc.get("remote_name", "")
        cache_dir = _bisync_cache_dir()
        return _clear_bisync_stale_files(
            remote_name,
            cache_dir,
            lambda msg: self._emit_error(service_name, msg),
        )

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

    def create_mega_remote(
        self, remote_name: str, user: str, password: str
    ) -> "tuple[bool, str]":
        """
        Create a Mega remote in the rclone config using username/password
        credentials.

        Mega does not use OAuth; rclone requires the user's email address and
        an *obscured* version of their password.  This method first calls
        ``rclone obscure`` to encode the plain-text password, then calls
        ``rclone config create`` with the result.

        Returns a ``(success, error_message)`` tuple.  On success the error
        message is an empty string.  On failure it contains the relevant output
        from rclone so it can be shown to the user.
        """
        base = _rclone_base_args(self._config)

        # Step 1: obscure the plain-text password so it can be stored safely.
        try:
            obscure_result = subprocess.run(
                ["rclone", "obscure", password],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except OSError as exc:
            return False, f"rclone no encontrado: {exc}"
        except subprocess.TimeoutExpired:
            return False, "rclone obscure superó el tiempo de espera."
        if obscure_result.returncode != 0:
            detail = obscure_result.stderr.strip() or obscure_result.stdout.strip()
            return False, detail or f"rclone obscure falló (código {obscure_result.returncode})."
        obscured = obscure_result.stdout.strip()

        # Step 2: create the Mega remote with the obscured credentials.
        try:
            create_result = subprocess.run(
                base + [
                    "config", "create",
                    remote_name, "mega",
                    f"user={user}",
                    f"pass={obscured}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except OSError as exc:
            return False, f"rclone no encontrado: {exc}"
        except subprocess.TimeoutExpired:
            return False, "rclone config create superó el tiempo de espera."
        if create_result.returncode != 0:
            detail = create_result.stderr.strip() or create_result.stdout.strip()
            return False, detail or f"rclone config create falló (código {create_result.returncode})."
        return True, ""

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

    def get_storage_info(self, service_name: str) -> Optional[str]:
        """
        Query the remote storage quota using ``rclone about remote:``.

        Returns a human-readable summary such as
        ``"Total: 1.024 TiB  |  Usado: 125.3 GiB  |  Libre: 898.7 GiB"``
        or ``None`` when the command fails (e.g. the service does not support
        ``about``, rclone is not installed, or the service is not configured).

        Supported platforms: OneDrive, Google Drive, Dropbox, Box, pCloud.
        Unsupported (returns None): Amazon S3, Backblaze B2, SFTP, FTP, etc.
        """
        svc = self._config.get_service(service_name)
        if svc is None:
            return None
        remote_name = svc.get("remote_name", "")
        if not remote_name:
            return None

        cmd = _rclone_base_args(self._config) + ["about", f"{remote_name}:"]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return None

            # Parse output lines such as "Total:   1.024 TiB", "Used:    125.3 GiB"
            info: dict = {}
            for line in result.stdout.strip().splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    info[key.strip().lower()] = value.strip()

            parts: List[str] = []
            if "total" in info:
                parts.append(f"Total: {info['total']}")
            if "used" in info:
                parts.append(f"Usado: {info['used']}")
            if "free" in info:
                parts.append(f"Libre: {info['free']}")

            return "  |  ".join(parts) if parts else None
        except (OSError, subprocess.TimeoutExpired):
            return None

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

        # Clear any stale lock / partial-listing files left by a previous
        # interrupted bisync before attempting to run again.
        _clear_bisync_stale_files(
            svc.get("remote_name", ""),
            _bisync_cache_dir(),
            lambda msg: self._emit_error(name, msg),
        )

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

        # Performance options.
        # Note: VFS cache flags (--vfs-cache-mode, --vfs-cache-max-size, etc.) are
        # NOT valid for bisync; they belong exclusively to the mount command and are
        # applied in start_mount() instead.
        perf_args = [
            "--transfers", "16",
            "--checkers", "32",
            "--drive-chunk-size", "128M",
            "--buffer-size", "64M",
            "-P",
        ]
        # Google Drive may flag some files as malware/spam and return HTTP 403
        # (cannotDownloadAbusiveFile).  This flag tells rclone to acknowledge
        # the warning and download the file anyway, preventing bisync from
        # failing due to files outside the user's control.
        if svc.get("platform") == "drive":
            perf_args.append("--drive-acknowledge-abuse")
        # Optional verbose output
        if svc.get("verbose_sync", False):
            perf_args.append("--verbose")

        # Conflict resolution mode used during --resync retries
        resync_mode = svc.get("resync_mode", "newer")

        base = _rclone_base_args(self._config)
        Path(local).mkdir(parents=True, exist_ok=True)

        # First attempt: standard bisync
        cmd = base + ["bisync", remote, local] + perf_args + exclude_args
        # Log the exact command being run so it appears in the error log as reference
        self._emit_error(name, "[CMD] " + shlex.join(cmd))
        success = self._run_rclone(cmd, name, svc)

        # Second attempt: bisync --resync if first attempt failed.
        # Skip the retry if a stop was explicitly requested (the process was
        # terminated on purpose, not due to a real error).
        # Clean stale lock files again before retrying: the first attempt may
        # have created a lock that it never released (e.g. if the process was
        # killed), which would cause the retry to fail immediately with
        # "prior lock file found".
        if not success:
            stop_ev = self._stop_events.get(name)
            if stop_ev and stop_ev.is_set():
                # Service is being stopped; don't retry or log spurious errors
                return False
            _clear_bisync_stale_files(
                svc.get("remote_name", ""),
                _bisync_cache_dir(),
                lambda msg: self._emit_error(name, msg),
            )
            cmd_resync = cmd + ["--resync"]
            if _rclone_supports_resync_mode(self._config):
                cmd_resync += ["--resync-mode", resync_mode]
            self._emit_error(name, "[CMD] " + shlex.join(cmd_resync))
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
            # Track the running process so stop_service() can terminate it
            # immediately without waiting for the current bisync to finish.
            self._running_procs[service_name] = proc
            # Read output line-by-line to detect file changes and errors
            for raw_line in proc.stdout or []:
                line = raw_line.strip()
                if not line:
                    continue
                # Forward any rclone error / fatal output to the error log
                if any(kw in line for kw in _RCLONE_ERROR_KEYWORDS):
                    self._emit_error(service_name, line)
                # rclone -P outputs lines like: "Transferred: <path>"
                # or lines starting with a file path
                file_path = _extract_file_path(line)
                if file_path and self.on_file_synced:
                    self.on_file_synced(service_name, file_path, True)
                    # Record in persistent history
                    self._config.add_sync_history_entry(service_name, file_path, True)

            proc.wait()
            self._running_procs.pop(service_name, None)
            if proc.returncode != 0:
                # Don't log a spurious error when we stopped the process on purpose
                stop_ev = self._stop_events.get(service_name)
                if not (stop_ev and stop_ev.is_set()):
                    self._emit_error(
                        service_name,
                        f"rclone terminó con código {proc.returncode}",
                    )
            return proc.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            self._running_procs.pop(service_name, None)
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

    def _read_mount_output(self, service_name: str, proc: subprocess.Popen) -> None:
        """
        Background thread: read mount process output and forward error lines.

        Called automatically by start_mount().
        """
        if proc.stdout:
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                if any(kw in line for kw in _RCLONE_ERROR_KEYWORDS):
                    self._emit_error(service_name, f"[MOUNT] {line}")
        proc.wait()
        rc = proc.returncode
        # -15 = SIGTERM (normal shutdown), -9 = SIGKILL, 0 = clean exit
        if rc is not None and rc not in (0, -15, -9):
            self._emit_error(
                service_name,
                f"[MOUNT] El proceso de montaje terminó inesperadamente con código {rc}",
            )


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
