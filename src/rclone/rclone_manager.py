"""
Wrapper around the rclone command-line tool.

Provides methods to configure remotes, run bisync, check mount status,
and stream output from rclone operations.
"""

import configparser
import os
import platform
import re
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

from src.config.config_manager import ConfigManager, get_rclone_config_path, PERSONAL_VAULT_PATTERN


def _bisync_cache_dir() -> Path:
    """Return the platform-appropriate directory where rclone stores bisync state.

    This path (``~/.cache/rclone/bisync/`` on Linux) is the *old* shared
    working directory used by rclone bisync when no ``--workdir`` flag is
    given.  It is now used primarily as the **source** directory for the
    one-time migration performed by :func:`_migrate_bisync_state` when an
    existing installation is upgraded to the per-service workdir scheme.

    New bisync runs always use the per-service directory returned by
    :func:`_bisync_workdir_for_service` instead.

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


def _slug(text: str) -> str:
    """Convert *text* to a lowercase, filesystem-safe slug.

    Non-alphanumeric characters are replaced with underscores and leading /
    trailing underscores are stripped.  The result is always non-empty (falls
    back to ``"service"`` for an all-symbol input).
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return slug or "service"


def _bisync_workdir_for_service(svc: Dict) -> Path:
    """Return the per-service bisync working directory.

    Each service uses an isolated workdir so that multiple services can run
    bisync concurrently without their lock files or state snapshots conflicting.

    If the service dict has a non-empty ``"bisync_workdir"`` key that value is
    used as-is (allowing the user to override the location).  Otherwise the
    workdir is derived automatically from the **service name** (slugified) using
    the pattern::

        <cache_base>/bisync/<service_name_slug>

    For example, on Linux with a service named ``"Mi OneDrive"`` the default
    path is ``~/.cache/rclone/bisync/mi_onedrive``.

    Using the service name (rather than the remote name) makes the folders
    immediately recognisable in a file-manager and ensures each service has a
    unique, human-readable directory.

    Args:
        svc: Service configuration dictionary (must contain at least
             ``"name"``).

    Returns:
        Absolute :class:`~pathlib.Path` for the workdir.
    """
    stored = svc.get("bisync_workdir", "")
    if stored:
        return Path(stored)
    service_name = svc.get("name", "") or svc.get("remote_name", "default")
    # Place the per-service workdir *inside* the default bisync cache dir so
    # all service workdirs are grouped in one place and are easy to find:
    #   ~/.cache/rclone/bisync/mi_onedrive/
    return _bisync_cache_dir() / _slug(service_name)


def _migrate_bisync_state(
    remote_name: str,
    old_dir: Path,
    new_dir: Path,
    emit_fn: Callable[[str], None],
) -> int:
    """Move pre-existing bisync state files from *old_dir* to *new_dir*.

    This one-time migration runs when a service is first used under the new
    per-service ``--workdir`` scheme.  If state files from a previous
    (pre-workdir) installation exist in the shared *old_dir* they are moved
    into the service-specific *new_dir* so the first bisync run after the
    upgrade does not require a full ``--resync``.

    Only files whose names start with *remote_name* are moved; files belonging
    to other services are left untouched.  Files that already exist in *new_dir*
    are not overwritten.

    Args:
        remote_name: The rclone remote identifier (e.g. ``"duexy"``).
        old_dir: The old shared bisync cache directory
                 (typically ``~/.cache/rclone/bisync/``).
        new_dir: The new per-service working directory (already created).
        emit_fn: Callable that accepts a single log-message string.

    Returns:
        The number of files successfully moved.
    """
    if not old_dir.is_dir() or not remote_name:
        return 0

    moved = 0
    for pattern in ("*.lck", "*.lst", "*.lst-new", "*.lst-err"):
        for f in old_dir.glob(pattern):
            if not f.name.startswith(remote_name):
                continue
            dest = new_dir / f.name
            if dest.exists():
                # Already migrated or a newer copy already exists — skip.
                continue
            try:
                shutil.move(str(f), str(dest))
                emit_fn(f"[MIGRATE] Movido estado de bisync: {f.name} → {new_dir.name}/")
                moved += 1
            except OSError as exc:
                emit_fn(f"[MIGRATE] No se pudo mover {f.name}: {exc}")
    return moved


def _clear_bisync_stale_files(
    remote_name: str,
    cache_dir: Path,
    emit_fn: Callable[[str], None],
) -> int:
    """Remove stale rclone bisync lock and partial-listing files for *remote_name*.

    When a per-service workdir is used (via ``--workdir``) rclone stores *all*
    state files inside that directory without name-prefixing, so this function
    removes every ``*.lck`` and ``*.lst-new`` file in *cache_dir* that starts
    with *remote_name*.  If the workdir is fully isolated (one remote per dir)
    all matching files in the directory are candidates for removal.

    Only files whose names begin with *remote_name* (case-sensitive) are
    removed, so we never accidentally delete state that belongs to a
    different remote.  Both ``*.lck`` and ``*.lst-new`` files are targeted.

    Args:
        remote_name: The rclone remote identifier (e.g. ``"duexy"``).
        cache_dir: Path to the rclone bisync working directory.
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

# Phrase emitted by rclone when a OneDrive/SharePoint remote is missing the
# drive_id and drive_type fields that newer rclone versions require.  This
# happens when an existing remote was configured with an older rclone version
# that did not write those fields to rclone.conf.  Bisync fails immediately
# and retrying with --resync would fail in the same way because the problem is
# in the stored configuration, not in the sync state.
_DRIVE_ID_MISSING_PHRASE = "unable to get drive_id and drive_type"

# Phrase emitted by rclone when bisync state files (snapshots) do not exist.
# This happens on the very first bisync run, or when a previous run terminated
# abnormally before it could write the snapshot.  Unlike a real bisync failure,
# the correct recovery is to run with --resync to initialise the state.
_BISYNC_NO_PRIOR_PHRASE = "cannot find prior Path1 or Path2 listings"

# Phrase that appears in rclone error output when the host network is not
# reachable (e.g. IPv6 connectivity issues or temporarily offline).  When we
# detect this, bisync is known to fail on every path listing — generating
# potentially hundreds of individual ERROR lines that would flood the log.
# We suppress those per-file entries and emit a single, clear summary message
# instead.  Retrying with --resync is also pointless while the host is
# offline, so the retry is skipped for this class of error.
#
# This string must match the exact phrase rclone writes to its output.  It
# originates from the Go standard library's net package
# ("read tcp … network is unreachable") and is present in all rclone versions.
_NETWORK_UNREACHABLE_PHRASE = "network is unreachable"

# Minimum free disk space (bytes) required before starting a bisync run.
# When the local filesystem has less than this amount of free space the sync
# is aborted with an informational message to prevent filling the disk.
# 10 GiB = 10 * 1024^3 bytes.
_MIN_FREE_SPACE_GIB: int = 10
_MIN_FREE_SPACE_BYTES: int = _MIN_FREE_SPACE_GIB * 1024 ** 3

# Tolerance (seconds) for comparing local vs remote file modification times.
# FAT32 filesystems store mtimes with 2-second precision; some cloud providers
# also round timestamps.  Using a 2 s window avoids false "diff" reports.
_MTIME_TOLERANCE_SECS: float = 2.0

# Number of fractional-second digits (microseconds) that Python's strptime
# understands.  rclone returns nanoseconds (9 digits); we normalise to this
# precision before parsing.
_MICROSECOND_PRECISION: int = 6


def _check_local_free_space(local_path: str) -> int:
    """Return the number of free bytes on the filesystem containing *local_path*.

    Uses :func:`shutil.disk_usage` so it works on Linux, macOS, and Windows
    without requiring any external command.  Returns 0 when *local_path* does
    not exist or the query fails.
    """
    try:
        # If the path does not yet exist, walk up to the first existing ancestor
        # so we can still query the filesystem it would be created on.
        p = Path(local_path)
        while p != p.parent and not p.exists():
            p = p.parent
        if not p.exists():
            return 0
        return shutil.disk_usage(str(p)).free
    except OSError:
        return 0


def _parse_rclone_mtime(s: str) -> Optional[float]:
    """Parse an rclone ISO-8601 ModTime string to a UTC Unix timestamp.

    rclone returns timestamps with nanosecond precision, e.g.
    ``"2024-01-15T10:30:00.123456789Z"``.  Python's
    :func:`datetime.strptime` only supports up to 6 fractional digits, so
    any extra digits are truncated before parsing.

    Returns the timestamp as a ``float`` (seconds since the Unix epoch,
    UTC), or ``None`` when the string cannot be parsed.
    """
    from datetime import datetime, timezone as _tz

    # Normalise any fractional seconds to exactly _MICROSECOND_PRECISION digits
    # then strip trailing Z.  The regex matches the fractional-seconds part of
    # the timestamp (e.g. ".123456789"), pads short fractions with trailing
    # zeros, and truncates long fractions (like nanoseconds) to exactly 6 digits
    # — the maximum Python's strptime ``%f`` directive understands.
    _pad = "0" * _MICROSECOND_PRECISION
    normalised = re.sub(
        r"\.(\d+)",
        lambda m: "." + (m.group(1) + _pad)[:_MICROSECOND_PRECISION],
        s,
    ).rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(normalised, fmt)
            return dt.replace(tzinfo=_tz.utc).timestamp()
        except ValueError:
            continue
    return None


def _scan_local_mtimes(local_path: str) -> Dict[str, float]:
    """Walk *local_path* and return ``{rel_posix_path: mtime_utc_secs}`` for every file.

    Paths are relative to *local_path* and use forward slashes (POSIX style)
    so they can be compared directly against rclone remote paths.
    Unreadable files are silently skipped.
    """
    result: Dict[str, float] = {}
    if not os.path.isdir(local_path):
        return result
    base = Path(local_path)
    for dirpath, _dirs, filenames in os.walk(local_path):
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                rel = str(Path(full).relative_to(base)).replace("\\", "/")
                result[rel] = os.stat(full).st_mtime
            except (OSError, ValueError):
                pass
    return result


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
        # Services whose last bisync failed due to a missing drive_id/drive_type
        # configuration error.  _do_bisync checks this before retrying with
        # --resync to avoid a pointless retry for what is a config problem.
        self._config_error_services: set = set()
        # Services whose last bisync run failed because there were no prior
        # bisync state files (first run, or state lost after a crash).  Unlike
        # a real failure, the correct recovery is --resync, which is handled
        # automatically by _do_bisync.
        self._no_prior_listing_services: set = set()
        # Services whose last bisync run encountered "network is unreachable"
        # errors.  When this is detected, per-file error lines are suppressed
        # (to avoid log spam) and the --resync retry is skipped because the
        # network problem won't be fixed by rerunning with --resync.
        self._network_error_services: set = set()
        # Optional callback(service_name, status_str) called on status changes
        self.on_status_change: Optional[Callable[[str, str], None]] = None
        # Optional callback(service_name, file_path, synced) for history updates
        self.on_file_synced: Optional[Callable[[str, str, bool], None]] = None
        # Optional callback(service_name, error_message) called on sync errors
        self.on_error: Optional[Callable[[str, str], None]] = None
        # Optional callback(service_name) fired when a drive_id/drive_type
        # configuration error is detected in bisync output.  The UI registers
        # this to surface an in-tab "fix now" action for the user.
        self.on_drive_id_error: Optional[Callable[[str], None]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_service_workdirs(self) -> int:
        """Assign and persist a unique bisync workdir to every configured service.

        This method should be called once at application startup.  It:

        1. Computes the default workdir for every service that does not yet
           have ``bisync_workdir`` set in the config (using
           :func:`_bisync_workdir_for_service`).
        2. Detects **collisions** — two or more services that would end up in
           the same directory.  When a collision is found the conflicting
           services receive unique suffixes based on their ``remote_name`` so
           each one ends up in a distinct folder.
        3. Persists the resolved paths back to the service config so that
           every subsequent run uses exactly the same workdirs without
           recomputing them.

        Returns the total number of services whose ``bisync_workdir`` was
        updated (newly assigned or collision-resolved).
        """
        services = self._config.get_services()
        if not services:
            return 0

        # Pass 1: compute the candidate workdir for every service.
        # Services that already have bisync_workdir set keep their path.
        candidates: Dict[str, Path] = {}
        for svc in services:
            name = svc.get("name", "")
            if not name:
                continue
            candidates[name] = _bisync_workdir_for_service(svc)

        # Pass 2: detect collisions (two services mapped to the same path).
        from collections import defaultdict
        path_to_names: Dict[Path, list] = defaultdict(list)
        for svc_name, path in candidates.items():
            path_to_names[path].append(svc_name)

        # Pass 3: resolve collisions by appending the remote_name slug.
        # Services whose path is unique are left untouched.
        svc_by_name = {s.get("name", ""): s for s in services}
        resolved: Dict[str, Path] = {}
        for path, names in path_to_names.items():
            if len(names) == 1:
                resolved[names[0]] = path
            else:
                # Multiple services would share this workdir.  Disambiguate by
                # appending the remote_name slug so the folder still reflects
                # both the service and the remote.
                for svc_name in names:
                    svc = svc_by_name.get(svc_name, {})
                    remote_slug = _slug(svc.get("remote_name", "") or svc_name)
                    resolved[svc_name] = path.parent / f"{path.name}_{remote_slug}"

        # Pass 4: persist any path that differs from what is currently stored.
        updated = 0
        for svc in services:
            name = svc.get("name", "")
            if not name or name not in resolved:
                continue
            new_path = resolved[name]
            current = svc.get("bisync_workdir", "")
            if current == str(new_path):
                continue
            self._config.update_service(name, {"bisync_workdir": str(new_path)})
            updated += 1

        return updated

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
        ``*.lck`` or ``*.lst-new`` file in the service's bisync working
        directory that blocks the next run.  Any removed files are reported
        via the ``on_error`` callback.

        Returns the number of files that were deleted.
        """
        svc = self._config.get_service(service_name)
        if svc is None:
            return 0
        remote_name = svc.get("remote_name", "")
        workdir = _bisync_workdir_for_service(svc)
        return _clear_bisync_stale_files(
            remote_name,
            workdir,
            lambda msg: self._emit_error(service_name, msg),
        )

    def open_browser_auth(self, remote_name: str, platform_type: str) -> subprocess.Popen:
        """
        Launch 'rclone config create' in an interactive subprocess so that
        rclone opens the browser for OAuth authentication.

        Returns the Popen object so the caller can wait on it.
        stdin is closed (DEVNULL) so that any post-OAuth interactive prompts
        (e.g. OneDrive drive selection) receive EOF and rclone falls back to
        the built-in defaults instead of blocking indefinitely.
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
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return proc

    def remote_has_token(
        self, remote_name: str, extra_required_keys: "tuple[str, ...]" = ()
    ) -> bool:
        """Return True if *remote_name* exists in rclone.conf with an OAuth token
        and all *extra_required_keys*.

        Used to detect a completed OAuth flow even when ``rclone config create``
        is still running (e.g. hung on a post-OAuth drive-selection prompt).

        Pass ``extra_required_keys=("drive_id",)`` for OneDrive remotes so that
        the poll loop waits until rclone has also written the drive configuration
        (drive_id, drive_type) — not just the bare OAuth token.  Without those
        keys ``rclone bisync`` immediately fails with exit-code 1.
        """
        config_path = self._config.rclone_config_path()
        try:
            parser = configparser.RawConfigParser()
            parser.read(str(config_path), encoding="utf-8")
            if not parser.has_section(remote_name):
                return False
            if not parser.has_option(remote_name, "token"):
                return False
            for key in extra_required_keys:
                if not parser.has_option(remote_name, key):
                    return False
            return True
        except Exception:
            return False

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

    def import_remote(
        self,
        remote_name: str,
        new_name: str,
        remote_data: Dict[str, str],
    ) -> "tuple[bool, str]":
        """
        Import a remote from an external rclone config into the app's own config.

        Credentials (tokens, client_id, etc.) are written **directly** into the
        app's rclone.conf via ``configparser`` so that already-authenticated
        remotes keep their stored tokens without triggering a new OAuth browser
        flow.

        Parameters
        ----------
        remote_name:
            Original name of the remote in the source config (used only for
            error messages).
        new_name:
            Section name to register in the app's own rclone config.
        remote_data:
            ``{key: value}`` pairs from the remote's INI section, **including**
            the mandatory ``type`` key.

        Returns
        -------
        ``(True, "")`` on success; ``(False, error_message)`` on failure.
        """
        remote_type = remote_data.get("type", "").strip()
        if not remote_type:
            return False, f"El remote '{remote_name}' no tiene un campo 'type' válido."

        dest_path = self._config.rclone_config_path()

        try:
            # Load existing config (creates an empty parser if the file is new)
            parser = configparser.RawConfigParser()
            if dest_path.exists():
                parser.read(str(dest_path), encoding="utf-8")

            # Overwrite or add the target section
            if parser.has_section(new_name):
                parser.remove_section(new_name)
            parser.add_section(new_name)
            for key, value in remote_data.items():
                parser.set(new_name, key, value)

            # Ensure the parent directory exists
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # Write atomically: write to a temp file then rename
            tmp_path = dest_path.with_suffix(".conf.tmp")
            with tmp_path.open("w", encoding="utf-8") as fh:
                parser.write(fh)
            tmp_path.replace(dest_path)
        except OSError as exc:
            return False, f"No se pudo escribir la configuración: {exc}"

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

    # ── Drive-ID quick-fix helpers ───────────────────────────────────────

    @staticmethod
    def _candidate_rclone_configs() -> List[Path]:
        """Return a list of well-known rclone config paths to search for drive_id.

        Includes:
          - The standard rclone default config (~/.config/rclone/rclone.conf)
          - Flatpak sandboxes for popular GUI rclone front-ends
          - Any extra locations from the RCLONE_CONFIG environment variable
        """
        home = Path.home()
        candidates: List[Path] = [
            # Standard rclone location
            home / ".config" / "rclone" / "rclone.conf",
            # Flatpak: rclone-manager (io.github.zarestia_dev.rclone-manager)
            home / ".var" / "app" / "io.github.zarestia_dev.rclone-manager"
                  / "config" / "rclone" / "rclone.conf",
            # Flatpak: RcloneUI (com.rcloneui.RcloneUI)
            home / ".var" / "app" / "com.rcloneui.RcloneUI"
                  / "data" / "com.rclone.ui" / "configs" / "default" / "rclone.conf",
            # RCX / other common GUI tools
            home / ".config" / "rcx" / "rclone.conf",
            home / ".config" / "rclone-browser" / "rclone.conf",
        ]
        # Also honour the RCLONE_CONFIG environment variable if set
        env_config = os.environ.get("RCLONE_CONFIG", "")
        if env_config:
            candidates.insert(0, Path(env_config))
        return candidates

    def find_drive_id_in_known_configs(
        self, remote_name: str
    ) -> "List[Dict[str, str]]":
        """Search well-known rclone config files for ``drive_id`` / ``drive_type``.

        Looks at each candidate config file for **any** remote section that:

          * has a ``drive_id`` value, and
          * has a ``drive_type`` value, and
          * is **not** the same file as the app's own ``rclone.conf``.

        Sections in the same file as the app config are excluded because they
        are precisely the ones that are broken.

        Returns
        -------
        A list of dicts, each with keys:
            ``source_file``, ``section``, ``drive_id``, ``drive_type``.
        The list is empty when nothing is found.
        """
        own_config = self._config.rclone_config_path().resolve()
        results: List[Dict[str, str]] = []

        for candidate in self._candidate_rclone_configs():
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved == own_config:
                continue
            if not candidate.exists():
                continue
            try:
                parser = configparser.RawConfigParser()
                parser.read(str(candidate), encoding="utf-8")
            except Exception:
                continue
            for section in parser.sections():
                drive_id = parser.get(section, "drive_id", fallback="").strip()
                drive_type = parser.get(section, "drive_type", fallback="").strip()
                if drive_id and drive_type:
                    results.append(
                        {
                            "source_file": str(candidate),
                            "section": section,
                            "drive_id": drive_id,
                            "drive_type": drive_type,
                        }
                    )
        return results

    def patch_remote_drive_fields(
        self, remote_name: str, drive_id: str, drive_type: str
    ) -> "tuple[bool, str]":
        """Write ``drive_id`` and ``drive_type`` directly into the app's rclone.conf.

        This is the *quick-fix* path for a remote that was created with an older
        rclone version and therefore lacks these fields.  It avoids the full
        OAuth re-authentication flow when the correct values can be recovered
        from another existing config file.

        The write is atomic (write to a temporary file, then ``os.replace``).

        Returns
        -------
        ``(True, "")`` on success; ``(False, error_message)`` on failure.
        """
        config_path = self._config.rclone_config_path()
        try:
            parser = configparser.RawConfigParser()
            if config_path.exists():
                parser.read(str(config_path), encoding="utf-8")
            if not parser.has_section(remote_name):
                # Only include the first 64 characters of remote_name in the
                # message so an unusually long or unusual section name does not
                # produce a confusing or very long error string for the user.
                safe_name = remote_name[:64]
                return False, f"La sección '[{safe_name}]' no existe en rclone.conf."
            parser.set(remote_name, "drive_id", drive_id)
            parser.set(remote_name, "drive_type", drive_type)
            config_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = config_path.with_suffix(".conf.tmp")
            with tmp_path.open("w", encoding="utf-8") as fh:
                parser.write(fh)
            tmp_path.replace(config_path)
        except OSError as exc:
            return False, f"No se pudo actualizar la configuración: {exc}"
        return True, ""

    # Terminal emulators tried in preference order when launching an interactive
    # rclone configuration session.  Each tuple is (executable, argument-flag)
    # where the flag precedes the shell command string.
    _TERMINAL_CANDIDATES: "List[tuple[str, str]]" = [
        ("xterm", "-e"),
        ("gnome-terminal", "--"),
        ("xfce4-terminal", "--command"),
        ("konsole", "-e"),
        ("lxterminal", "-e"),
        ("rxvt", "-e"),
    ]

    def open_terminal_reconnect(self, remote_name: str) -> "tuple[bool, str]":
        """Launch ``rclone config reconnect <remote>:`` inside a terminal emulator.

        ``rclone config reconnect`` is the recommended way to fix a remote
        that was created with an older rclone version and therefore lacks the
        ``drive_id`` / ``drive_type`` fields required by newer releases.
        Unlike ``rclone config create --auto-confirm``, *reconnect* presents
        the full OAuth flow **including** the post-auth drive-selection
        prompt, so ``drive_id`` is properly populated after the user
        completes the authentication.

        This method tries the terminal emulators listed in
        ``_TERMINAL_CANDIDATES`` in order.  If none is found it returns the
        shell command so the caller can display it to the user as a manual
        step.

        Returns
        -------
        ``(True, "")``
            A terminal was launched successfully.
        ``(False, cmd)``
            No terminal emulator was available; ``cmd`` is the shell
            command the user should run in their own terminal.
        """
        config_path = self._config.rclone_config_path()
        cmd = (
            f"rclone --config {shlex.quote(str(config_path))} "
            f"config reconnect {shlex.quote(remote_name)}:"
        )
        for exe, flag in self._TERMINAL_CANDIDATES:
            try:
                subprocess.Popen(
                    [exe, flag, cmd],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
                return True, ""
            except (FileNotFoundError, OSError):
                continue
        return False, cmd

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

    def check_sync_status_mtime(self, service_name: str) -> Optional[List[Dict]]:
        """Compare remote vs local files using modification timestamps.

        Faster than :meth:`check_sync_status` because it only fetches file
        metadata (names and modification times) via ``rclone lsjson
        --recursive --files-only`` rather than computing checksums.  Local
        modification times are read with :func:`os.stat`.

        Status values returned in each dict's ``status`` key:

            ``synced``      – local mtime ≈ remote mtime (within
                              :data:`_MTIME_TOLERANCE_SECS`)
            ``diff``        – file exists on both sides but mtimes differ
            ``remote_only`` – file exists on the remote but not locally
            ``local_only``  – file exists locally but not on the remote

        Returns a list of ``{"rel": str, "status": str}`` dicts on success,
        or ``None`` when rclone is unavailable, the service is not fully
        configured, or the remote listing times out.
        """
        import json as _json

        svc = self._config.get_service(service_name)
        if svc is None:
            return None
        remote_name = svc.get("remote_name", "")
        remote_path = svc.get("remote_path", "/")
        local_path = svc.get("local_path", "")
        if not remote_name or not local_path:
            return None

        exclusions = svc.get("exclusions", [])
        remote = f"{remote_name}:{remote_path}"
        cmd = _rclone_base_args(self._config) + [
            "lsjson",
            "--recursive",
            "--files-only",
            "--no-mimetype",
            remote,
        ]
        for exc in exclusions:
            cmd += ["--exclude", exc]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                return None
            remote_items = _json.loads(result.stdout or "[]")
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return None

        # Build remote mtime map: rel_path → UTC unix timestamp
        remote_mtimes: Dict[str, float] = {}
        for item in remote_items:
            rel = item.get("Path", "").replace("\\", "/").strip("/")
            mtime_str = item.get("ModTime", "")
            if rel:
                ts = _parse_rclone_mtime(mtime_str)
                if ts is not None:
                    remote_mtimes[rel] = ts

        # Build local mtime map: rel_path → UTC unix timestamp
        local_mtimes: Dict[str, float] = _scan_local_mtimes(local_path)

        # Compare both maps to produce sync-status items
        all_paths = set(remote_mtimes) | set(local_mtimes)
        items: List[Dict] = []
        for rel in sorted(all_paths):
            r_ts = remote_mtimes.get(rel)
            l_ts = local_mtimes.get(rel)
            if r_ts is not None and l_ts is not None:
                status = "synced" if abs(r_ts - l_ts) <= _MTIME_TOLERANCE_SECS else "diff"
            elif r_ts is not None:
                status = "remote_only"
            else:
                status = "local_only"
            items.append({"rel": rel, "status": status})

        return items

    def check_sync_status(self, service_name: str) -> Optional[List[Dict]]:
        """Compare the remote path against the local path using ``rclone check``.

        Runs ``rclone check <remote> <local> --combined -`` which writes one
        status line per file to stdout:

            ``= path`` – identical on both sides (synced)
            ``* path`` – exists on both sides but content/mtime differs
            ``+ path`` – exists only on source (remote)
            ``- path`` – exists only on destination (local)

        Returns a list of ``{"rel": str, "status": str}`` dicts on success,
        or ``None`` when rclone is not installed, the service is not fully
        configured, or the command times out.
        """
        svc = self._config.get_service(service_name)
        if svc is None:
            return None
        remote_name = svc.get("remote_name", "")
        remote_path = svc.get("remote_path", "/")
        local_path = svc.get("local_path", "")
        if not remote_name or not local_path:
            return None

        exclusions = svc.get("exclusions", [])
        remote = f"{remote_name}:{remote_path}"
        cmd = _rclone_base_args(self._config) + [
            "check",
            remote,
            local_path,
            "--combined", "-",
        ]
        for exc in exclusions:
            cmd += ["--exclude", exc]

        _STATUS_MAP = {
            "=": "synced",
            "*": "diff",
            "+": "remote_only",
            "-": "local_only",
        }
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
            )
            # rclone check exits with code 1 when differences exist — that is
            # expected and still produces valid combined output in stdout.
            output = result.stdout or ""
            items: List[Dict] = []
            for line in output.splitlines():
                line = line.rstrip("\r\n")
                # Combined format: "<char> <path>"
                if len(line) >= 3 and line[1] == " ":
                    char = line[0]
                    rel = line[2:].strip().replace("\\", "/")
                    if rel:
                        items.append({"rel": rel, "status": _STATUS_MAP.get(char, "unknown")})
            return items
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

            # Distinguish first-time full sync from ongoing incremental updates.
            # After the first successful sync the service config has
            # "first_sync_done": True (persisted so the distinction survives
            # restarts).  On first run we show "Sincronizando…"; on all
            # subsequent runs we show "Actualizando cambios…" and pass
            # use_resync=True to _do_bisync so rclone re-establishes the
            # baseline from the current state of both directories.
            is_first = not svc.get("first_sync_done", False)
            status_in_progress = "Sincronizando…" if is_first else "Actualizando cambios…"
            self._set_status(service_name, status_in_progress)
            success = self._do_bisync(svc, use_resync=not is_first)
            if success:
                if is_first:
                    # Persist so the next cycle (and future sessions) use the
                    # "Actualizando cambios…" path from the start.
                    self._config.update_service(service_name, {"first_sync_done": True})
                self._set_status(service_name, "Actualizado")
            else:
                self._set_status(service_name, "Error en sincronización")
                self._emit_error(service_name, "Fallo en el ciclo de sincronización")

            # Wait for the configured interval (or stop early if signalled)
            interval = svc.get("sync_interval", 900)
            stop_event.wait(timeout=interval)

        self._set_status(service_name, "Detenido")

    def _do_bisync(self, svc: Dict, use_resync: bool = False) -> bool:
        """
        Run rclone bisync for a single service dictionary.

        Performs a disk-space check before attempting bisync: if the local
        filesystem has less than :data:`_MIN_FREE_SPACE_BYTES` (10 GiB) free,
        the sync is aborted with a clear error message.

        When *use_resync* is ``True`` the ``--resync`` flag is added to the
        initial command.  This is used from the second sync cycle onward, after
        the first full sync has completed successfully, so that rclone
        re-establishes the baseline from the current state of both directories
        rather than comparing against potentially stale listing files.

        On the very first bisync run (or after an abnormal termination that
        deleted the listing files), rclone may fail with "cannot find prior
        Path1 or Path2 listings".  This condition is detected automatically
        and a ``--resync`` retry is performed automatically — but only when
        *use_resync* is ``False`` (i.e. the initial command did not already
        include ``--resync``).

        Returns True on success.
        """
        remote = f"{svc['remote_name']}:{svc.get('remote_path', '/')}"
        local = svc.get("local_path", "")
        name = svc.get("name", "?")

        # --- Disk space guard -------------------------------------------
        # Abort early if the local filesystem is critically full.  This
        # prevents bisync from filling the disk and corrupting the state.
        free_bytes = _check_local_free_space(local)
        if free_bytes < _MIN_FREE_SPACE_BYTES:
            free_gb = free_bytes / (1024 ** 3)
            self._emit_error(
                name,
                f"⛔ Sincronización cancelada: espacio libre insuficiente en el disco "
                f"({free_gb:.1f} GiB disponibles; se requieren al menos "
                f"{_MIN_FREE_SPACE_GIB} GiB). "
                "Libera espacio y vuelve a intentarlo.",
            )
            return False

        # Clear any stale lock / partial-listing files left by a previous
        # interrupted bisync before attempting to run again.
        workdir = _bisync_workdir_for_service(svc)
        workdir.mkdir(parents=True, exist_ok=True)
        # Persist the workdir to config if it was not already stored.
        # This makes the path stable across runs and visible to the user
        # without requiring a bisync run to inspect which folder is in use.
        if not svc.get("bisync_workdir"):
            self._config.update_service(name, {"bisync_workdir": str(workdir)})
            svc["bisync_workdir"] = str(workdir)
        # One-time migration: move any state files that a pre-workdir
        # installation left in the shared bisync cache directory into this
        # service's own workdir so the first run after the upgrade does not
        # need a full --resync.
        _migrate_bisync_state(
            svc.get("remote_name", ""),
            _bisync_cache_dir(),
            workdir,
            lambda msg: self._emit_error(name, msg),
        )
        _clear_bisync_stale_files(
            svc.get("remote_name", ""),
            workdir,
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
        #
        # --transfers and --checkers default to 16/32 but are overridable per-service
        # so users can reduce them to avoid Google Drive API quota errors.
        transfers = str(svc.get("transfers", 16))
        checkers = str(svc.get("checkers", 32))
        perf_args = [
            "--transfers", transfers,
            "--checkers", checkers,
            "--drive-chunk-size", "128M",
            "--buffer-size", "64M",
            "-P",
        ]
        # --tpslimit throttles API calls per second.  When set to a positive value
        # it prevents Google Drive 403 "Quota exceeded for 'Queries per minute'"
        # errors caused by rclone making too many API requests in a short burst.
        # A value of 0 (default) means no limit.
        try:
            tpslimit_f = float(svc.get("tpslimit", 0))
        except (TypeError, ValueError):
            tpslimit_f = 0.0
        if tpslimit_f > 0:
            perf_args += ["--tpslimit", str(tpslimit_f)]
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

        # First attempt: standard bisync, or bisync --resync for subsequent runs
        # --workdir isolates this service's lock files and state snapshots from
        # all other services so concurrent bisync runs don't block each other.
        cmd = base + ["bisync", remote, local, "--workdir", str(workdir)] + perf_args + exclude_args
        if use_resync:
            # Include --resync so rclone re-establishes the baseline instead of
            # comparing against prior listing files.
            cmd += ["--resync"]
            if _rclone_supports_resync_mode(self._config):
                cmd += ["--resync-mode", resync_mode]
        # Log the exact command being run so it appears in the error log as reference
        self._emit_error(name, "[CMD] " + shlex.join(cmd))
        success = self._run_rclone(cmd, name, svc)

        # Second attempt: bisync --resync if first attempt failed.
        # Skip the retry entirely when the initial command already included
        # --resync: retrying with the same flag would be pointless.
        # Also skip the retry if a stop was explicitly requested (the process was
        # terminated on purpose, not due to a real error).
        # Clean stale lock files again before retrying: the first attempt may
        # have created a lock that it never released (e.g. if the process was
        # killed), which would cause the retry to fail immediately with
        # "prior lock file found".
        if not success and not use_resync:
            stop_ev = self._stop_events.get(name)
            if stop_ev and stop_ev.is_set():
                # Service is being stopped; don't retry or log spurious errors
                return False
            # If the failure was caused by a missing drive_id/drive_type config
            # error, skip the --resync retry: the problem is in the stored
            # remote configuration, not in the bisync state, so retrying with
            # --resync would fail in the same way.  The actionable message was
            # already emitted by _run_rclone.
            if name in self._config_error_services:
                self._config_error_services.discard(name)
                return False
            # If the failure was caused by a network connectivity error ("network
            # is unreachable"), skip the --resync retry: the remote server is
            # unreachable regardless of the sync state, so the retry would also
            # fail.  The single summary message was already emitted by
            # _run_rclone; just clear the flag and wait for the next cycle.
            if name in self._network_error_services:
                self._network_error_services.discard(name)
                return False
            # If the failure was caused by missing bisync state files ("cannot
            # find prior Path1 or Path2 listings"), emit an informational message
            # explaining what happened, then fall through to the --resync retry.
            # This is not an unexpected error — it is the expected first-run or
            # post-crash condition; --resync initialises the state safely.
            is_no_prior = name in self._no_prior_listing_services
            if is_no_prior:
                self._no_prior_listing_services.discard(name)
                self._emit_error(
                    name,
                    "ℹ️ No se encontraron archivos de estado previos de bisync "
                    "(primera ejecución o estado perdido). "
                    "Iniciando sincronización inicial con --resync…",
                )
            _clear_bisync_stale_files(
                svc.get("remote_name", ""),
                workdir,
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
                # Detect the drive_id/drive_type config error.  rclone emits
                # this when a remote was configured with an older rclone version
                # that did not write those fields to rclone.conf.  The line may
                # not contain a standard ERROR/FATAL keyword on all rclone
                # versions, so we check for it explicitly before the general
                # keyword scan to guarantee it is always logged.
                if _DRIVE_ID_MISSING_PHRASE in line:
                    # Always emit the raw rclone line so the user can see it.
                    if not any(kw in line for kw in _RCLONE_ERROR_KEYWORDS):
                        self._emit_error(service_name, line)
                    # Mark this service so _do_bisync can skip the --resync retry.
                    self._config_error_services.add(service_name)
                    self._emit_error(
                        service_name,
                        "⚠️ La configuración del remoto está desactualizada: "
                        "faltan los campos drive_id y drive_type. "
                        "Ve a Configuración → panel de información → "
                        "'Reconectar' para abrir un terminal con el comando "
                        "de reconfiguración, o usa '🔎 Buscar drive_id' si ya "
                        "tienes el valor en otro archivo de configuración.",
                    )
                    # Notify the UI so it can surface an in-tab "fix" action.
                    self._emit_drive_id_error(service_name)
                # Detect network-unreachable errors.  These generate one ERROR
                # line per listed folder, which can flood the log with dozens of
                # identical "couldn't list files: … network is unreachable"
                # messages.  On the first occurrence we record the condition and
                # emit a single clear human-readable summary.  Subsequent lines
                # that contain the same phrase are suppressed entirely so the
                # log stays readable.  The early-return below also skips
                # forwarding the line through the general keyword scan.
                if _NETWORK_UNREACHABLE_PHRASE in line:
                    if service_name not in self._network_error_services:
                        self._network_error_services.add(service_name)
                        self._emit_error(
                            service_name,
                            "🌐 Red no disponible: el host no puede alcanzar el "
                            "servidor remoto ('network is unreachable'). "
                            "La sincronización se reintentará automáticamente "
                            "en el próximo ciclo. "
                            "Comprueba la conexión a Internet o la configuración "
                            "de IPv6 del sistema.",
                        )
                    # Skip the rest of the per-line processing for this line so
                    # we don't emit it again through the general error scanner.
                    continue
                # Detect missing bisync state files ("no prior listings").  This
                # is the normal first-run or post-crash condition.  Flag the
                # service so _do_bisync knows to treat the retry as an expected
                # --resync initialisation, not an unexpected failure.
                if _BISYNC_NO_PRIOR_PHRASE in line:
                    self._no_prior_listing_services.add(service_name)
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

    def _emit_drive_id_error(self, service_name: str) -> None:
        """Fire the on_drive_id_error callback if registered."""
        if self.on_drive_id_error:
            try:
                self.on_drive_id_error(service_name)
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
