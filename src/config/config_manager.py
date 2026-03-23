"""
Manages application configuration and service settings.
Stores data in a JSON file located in the user's config directory.
"""

import json
import os
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_config_dir() -> Path:
    """Return the platform-appropriate configuration directory for this app."""
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    config_dir = base / "RclonePythonIA"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_rclone_config_path() -> Path:
    """Return the path to the rclone config file used by this application."""
    return get_config_dir() / "rclone.conf"


# Default sync interval in seconds (30 minutes)
DEFAULT_SYNC_INTERVAL = 1800

# Default rclone options for transfers.
# Buffer and chunk sizes are kept deliberately modest (16 MiB / 32 MiB) to
# reduce peak RAM usage: large values only help for sustained sequential I/O
# but consume memory even when idle.
DEFAULT_RCLONE_OPTS = {
    "transfers": 16,
    "checkers": 32,
    "drive_chunk_size": "32M",
    "buffer_size": "16M",
    "vfs_cache_mode": "writes",
    "vfs_cache_max_size": "10G",
    # Maximum API transactions per second (0 = unlimited).  Set to a low value
    # (e.g. 5) to avoid Google Drive "Quota exceeded for 'Queries per minute'"
    # 403 errors when bisync generates too many API calls in a short window.
    "tpslimit": 0,
}

# Exclusion pattern for the OneDrive Personal Vault folder
PERSONAL_VAULT_PATTERN = "/Almacén personal/**"

# Default exclusion rules applied to every service
DEFAULT_EXCLUSIONS = [PERSONAL_VAULT_PATTERN]

# Number of files in the sync tree above which the "large directory" refresh
# interval is used instead of the "small directory" one.
TREE_FILE_THRESHOLD = 1000

# Sync providers available when adding a service.
# "rclone" is the default and uses rclone bisync.
# "nativo" uses the platform's direct REST API (only OneDrive and Google Drive).
SYNC_PROVIDERS = ["rclone", "nativo"]

# Platforms that support the "nativo" (direct API) sync provider.
NATIVE_SYNC_PLATFORMS = ["onedrive", "drive"]

# Platforms supported by rclone that are offered in the wizard.
# The first section lists the most commonly used cloud drives that were
# already supported.  The remainder of the list mirrors the full set of
# backends reported by `rclone config providers` and is kept in the same
# order as that output so it is easy to audit against a fresh rclone build.
SUPPORTED_PLATFORMS = [
    # ── Popular cloud drives (original list, most frequently used) ─────────
    "onedrive",           # Microsoft OneDrive
    "drive",              # Google Drive
    "dropbox",
    "box",
    "s3",                 # Amazon S3 and compatible (AWS, Wasabi, Minio…)
    "sftp",               # SSH/SFTP
    "ftp",
    "mega",               # Mega (email + password auth)
    "pcloud",
    "yandex",             # Yandex Disk
    # ── Additional rclone backends (alphabetical by rclone type) ──────────
    "amazon cloud drive", # Amazon Drive (service discontinued, may not work)
    "azureblob",          # Microsoft Azure Blob Storage
    "b2",                 # Backblaze B2
    "cache",              # Cache a remote (deprecated in recent rclone; use VFS instead)
    "chunker",            # Transparently chunk/split large files
    "combine",            # Combine several remotes into one
    "compress",           # Compress a remote
    "crypt",              # Encrypt / Decrypt a remote
    "fichier",            # 1Fichier
    "filefabric",         # Enterprise File Fabric
    "google cloud storage",
    "google photos",
    "hasher",             # Better checksums for other remotes
    "hdfs",               # Hadoop distributed file system
    "hidrive",            # HiDrive
    "http",               # HTTP (read-only remote)
    "internetarchive",    # Internet Archive
    "jottacloud",
    "koofr",              # Koofr, Digi Storage and compatible
    "local",              # Local Disk
    "mailru",             # Mail.ru Cloud
    "memory",             # In-memory object storage
    "netstorage",         # Akamai NetStorage
    "opendrive",          # OpenDrive
    "premiumizeme",       # premiumize.me
    "putio",              # Put.io
    "sharefile",          # Citrix Sharefile
    "sia",                # Sia Decentralized Cloud
    "smb",                # SMB / CIFS
    "sugarsync",          # Sugarsync
    "swift",              # OpenStack Swift (Rackspace, OVH…)
    "union",              # Union merges the contents of several upstream fs
    "uptobox",            # Uptobox
    "webdav",             # WebDAV
    "zoho",               # Zoho
    "alias",              # Alias for an existing remote
]

# Human-readable labels for each platform (shown in the wizard listbox)
PLATFORM_LABELS = {
    # ── Popular cloud drives ───────────────────────────────────────────────
    "onedrive":           "Microsoft OneDrive",
    "drive":              "Google Drive",
    "dropbox":            "Dropbox",
    "box":                "Box",
    "s3":                 "Amazon S3",
    "sftp":               "SFTP / SSH",
    "ftp":                "FTP",
    "mega":               "Mega",
    "pcloud":             "pCloud",
    "yandex":             "Yandex Disk",
    # ── Additional rclone backends ─────────────────────────────────────────
    "amazon cloud drive": "Amazon Drive (discontinued)",
    "azureblob":          "Microsoft Azure Blob Storage",
    "b2":                 "Backblaze B2",
    "cache":              "Cache a remote (deprecated)",
    "chunker":            "Chunker (split large files)",
    "combine":            "Combine several remotes",
    "compress":           "Compress a remote",
    "crypt":              "Encrypt / Decrypt a remote",
    "fichier":            "1Fichier",
    "filefabric":         "Enterprise File Fabric",
    "google cloud storage": "Google Cloud Storage",
    "google photos":      "Google Photos",
    "hasher":             "Better checksums (Hasher)",
    "hdfs":               "Hadoop Distributed File System",
    "hidrive":            "HiDrive",
    "http":               "HTTP",
    "internetarchive":    "Internet Archive",
    "jottacloud":         "Jottacloud",
    "koofr":              "Koofr / Digi Storage",
    "local":              "Local Disk",
    "mailru":             "Mail.ru Cloud",
    "memory":             "In-memory object storage",
    "netstorage":         "Akamai NetStorage",
    "opendrive":          "OpenDrive",
    "premiumizeme":       "premiumize.me",
    "putio":              "Put.io",
    "sharefile":          "Citrix Sharefile",
    "sia":                "Sia Decentralized Cloud",
    "smb":                "SMB / CIFS",
    "sugarsync":          "Sugarsync",
    "swift":              "OpenStack Swift",
    "union":              "Union (merge several remotes)",
    "uptobox":            "Uptobox",
    "webdav":             "WebDAV",
    "zoho":               "Zoho",
    "alias":              "Alias for an existing remote",
}


class ConfigManager:
    """
    Handles loading, saving, and accessing application configuration.

    The configuration is stored as a JSON file and includes:
    - A list of configured services (each with its own settings)
    - Global application preferences (startup delay, etc.)
    """

    CONFIG_FILE_NAME = "app_config.json"

    def __init__(self) -> None:
        # Determine the path to the configuration JSON file
        self._config_path = get_config_dir() / self.CONFIG_FILE_NAME
        # Load existing config or create a fresh default one
        self._data: Dict[str, Any] = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> Dict[str, Any]:
        """Load configuration from disk, returning defaults if not found."""
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError):
                pass
        return self._default_config()

    def save(self) -> None:
        """Persist the current configuration to disk."""
        with open(self._config_path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Default structure
    # ------------------------------------------------------------------

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        """Return a fresh default configuration dictionary."""
        return {
            "services": [],
            "preferences": {
                "start_with_system": False,
                "startup_delay_seconds": 30,
            },
        }

    @staticmethod
    def _default_service(name: str, platform: str, local_path: str) -> Dict[str, Any]:
        """
        Return a default service configuration dictionary.

        Args:
            name: Human-readable name for the service.
            platform: Rclone backend type (e.g. 'onedrive').
            local_path: Absolute path to the local sync directory.
        """
        return {
            "name": name,
            "platform": platform,
            "local_path": local_path,
            # Remote path inside the cloud service (default: root)
            "remote_path": "/",
            # rclone remote name as configured in rclone.conf
            "remote_name": f"{name.lower().replace(' ', '_')}",
            # Whether the service is actively syncing
            "sync_enabled": True,
            # Sync interval in seconds
            "sync_interval": DEFAULT_SYNC_INTERVAL,
            # List of exclusion glob patterns
            "exclusions": list(DEFAULT_EXCLUSIONS),
            # Whether to exclude "Almacén personal" (OneDrive Personal Vault)
            "exclude_personal_vault": True,
            # rclone VFS cache settings (used by the mount command only;
            # bisync does not accept VFS cache flags)
            "vfs_cache_mode": "writes",
            "vfs_cache_max_size": "10G",
            # Custom cache directory (empty = rclone default)
            "vfs_cache_dir": "",
            # bisync: per-service working directory for lock files and state.
            # Empty string means the path is derived automatically from the
            # remote name: <cache_base>/bisync-<remote_name>.  Set this to a
            # custom absolute path to override the default.
            "bisync_workdir": "",
            # bisync: conflict resolution mode used during --resync
            "resync_mode": "newer",
            # bisync: emit verbose output (--verbose flag)
            "verbose_sync": False,
            # bisync: create empty directories on the remote when they exist
            # locally.  Maps to the --create-empty-src-dirs rclone flag.
            # Enabled by default so that newly created local folders appear on
            # the remote even before any files are placed inside them.
            "create_empty_src_dirs": True,
            # tree scan: timeout (seconds) for rclone lsjson --recursive used
            # to list all remote files when refreshing the sync tree.  Remotes
            # with many files (hundreds of thousands) can take several minutes
            # to list; increase this value if scans are timing out.
            "lsjson_timeout": 1800,
            # rclone mount: whether to run a persistent mount process
            "mount_enabled": False,
            # rclone mount: local directory used as the mount point
            "mount_path": "",
            # rclone mount: VFS read chunk size (streamed reads)
            "vfs_read_chunk_size": "10M",
            # rclone mount: maximum VFS read chunk size
            "vfs_read_chunk_size_limit": "100M",
            # Sync provider: "rclone" (default) or "nativo" (direct API for
            # OneDrive and Google Drive only).
            "sync_provider": "rclone",
            # Recent file sync history (list of dicts)
            "sync_history": [],
            # Sync-tree auto-refresh intervals (seconds).  When the tree has
            # fewer than TREE_FILE_THRESHOLD items, the small interval is used;
            # otherwise the large one is used.  Both default to 1 hour (3600 s)
            # so routine background scans do not constantly hit the cloud API.
            "tree_refresh_small_secs": 3600,
            "tree_refresh_large_secs": 3600,
            # Set to True after the first successful tree scan completes.  Used
            # by the parallel-scan throttle so that initial scans run at full
            # concurrency while steady-state rescans share a global semaphore.
            "first_tree_scan_done": False,
        }

    # ------------------------------------------------------------------
    # Service management
    # ------------------------------------------------------------------

    def get_services(self) -> List[Dict[str, Any]]:
        """Return the list of all configured service dictionaries."""
        return self._data.get("services", [])

    def get_service(self, name: str) -> Optional[Dict[str, Any]]:
        """Return the service dictionary for the given name, or None."""
        for svc in self.get_services():
            if svc.get("name") == name:
                return svc
        return None

    def add_service(self, name: str, platform: str, local_path: str) -> Dict[str, Any]:
        """
        Add a new service to the configuration.

        Args:
            name: Service name.
            platform: Rclone backend type.
            local_path: Local directory to sync into.

        Returns:
            The newly created service configuration dictionary.
        """
        svc = self._default_service(name, platform, local_path)
        self._data.setdefault("services", []).append(svc)
        self.save()
        return svc

    def update_service(self, name: str, updates: Dict[str, Any]) -> None:
        """
        Update fields of an existing service and save.

        Args:
            name: The service name to update.
            updates: A dict of field → new value to apply.
        """
        for svc in self._data.get("services", []):
            if svc.get("name") == name:
                svc.update(updates)
                break
        self.save()

    def remove_service(self, name: str) -> None:
        """Remove a service by name and save the configuration."""
        self._data["services"] = [
            s for s in self._data.get("services", []) if s.get("name") != name
        ]
        self.save()

    def add_sync_history_entry(
        self, service_name: str, file_path: str, synced: bool
    ) -> None:
        """
        Add a file sync event to the service history (max 50 entries).

        Args:
            service_name: The service this event belongs to.
            file_path: The path of the file that was changed.
            synced: Whether the file has been successfully synced.
        """
        import datetime

        entry = {
            "file": file_path,
            "synced": synced,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }
        for svc in self._data.get("services", []):
            if svc.get("name") == service_name:
                history: List[Dict[str, Any]] = svc.setdefault("sync_history", [])
                # Insert at front so newest is first
                history.insert(0, entry)
                # Keep only the last 50 entries
                svc["sync_history"] = history[:50]
                break
        self.save()

    # ------------------------------------------------------------------
    # Global preferences
    # ------------------------------------------------------------------

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Retrieve a global preference value."""
        return self._data.get("preferences", {}).get(key, default)

    def set_preference(self, key: str, value: Any) -> None:
        """Set a global preference value and save."""
        self._data.setdefault("preferences", {})[key] = value
        self.save()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def rclone_config_path(self) -> Path:
        """Return the path to the rclone config file."""
        return get_rclone_config_path()

    def get_rclone_version(self) -> str:
        """Run rclone --version and return the version string, or 'not found'."""
        import subprocess

        try:
            result = subprocess.run(
                ["rclone", "version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            first_line = result.stdout.splitlines()[0] if result.stdout else ""
            return first_line.strip() or "unknown"
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return "rclone not found"
