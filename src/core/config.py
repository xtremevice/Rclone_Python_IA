"""
src/core/config.py

Handles loading, saving, and managing the application's JSON-based
configuration, including all service definitions and global settings.
"""
import json
import os
import uuid
import platform
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any


# ---------------------------------------------------------------------------
# Platform-aware configuration directory
# ---------------------------------------------------------------------------

def get_config_dir() -> Path:
    """Return the OS-specific directory where RclonePyIA stores its config."""
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        # Linux / other POSIX
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "RclonePyIA"


CONFIG_DIR: Path = get_config_dir()
CONFIG_FILE: Path = CONFIG_DIR / "config.json"

# Rclone config file managed by this application
RCLONE_CONFIG_FILE: Path = CONFIG_DIR / "rclone.conf"

# Sync-interval options exposed to the UI (label → minutes)
SYNC_INTERVAL_OPTIONS: Dict[str, int] = {
    "1 minuto": 1,
    "5 minutos": 5,
    "15 minutos": 15,
    "30 minutos": 30,
    "60 minutos": 60,
    "2 horas": 120,
    "3 horas": 180,
    "6 horas": 360,
    "12 horas": 720,
    "24 horas": 1440,
}

# Supported cloud-storage platforms (display name → rclone type)
SUPPORTED_PLATFORMS: Dict[str, str] = {
    "OneDrive": "onedrive",
    "Google Drive": "drive",
    "Dropbox": "dropbox",
    "Box": "box",
    "Amazon S3": "s3",
    "SFTP": "sftp",
    "WebDAV": "webdav",
    "FTP": "ftp",
    "pCloud": "pcloud",
    "Mega": "mega",
}


# ---------------------------------------------------------------------------
# Data model for a single service
# ---------------------------------------------------------------------------

class ServiceConfig:
    """Represents the configuration for a single rclone-managed service."""

    def __init__(self, data: Optional[Dict[str, Any]] = None) -> None:
        """Initialise a ServiceConfig, optionally from a serialised dict."""
        if data is None:
            data = {}
        self.id: str = data.get("id", str(uuid.uuid4()))
        self.name: str = data.get("name", "")
        # Name used inside rclone.conf (must be unique and alphanumeric)
        self.remote_name: str = data.get("remote_name", "")
        # Human-readable platform key (e.g. "OneDrive")
        self.platform: str = data.get("platform", "")
        # Absolute local directory path
        self.local_path: str = data.get("local_path", "")
        # Remote path within the cloud storage (default: root)
        self.remote_path: str = data.get("remote_path", "/")
        # Whether a sync operation is currently running
        self.is_syncing: bool = data.get("is_syncing", False)
        # Sync interval in minutes
        self.sync_interval: int = data.get("sync_interval", 15)
        # Rclone --exclude rules (list of glob strings)
        self.exclude_rules: List[str] = data.get(
            "exclude_rules", ["/Almacén personal/**"]
        )
        # Whether to start syncing when the application launches
        self.sync_on_startup: bool = data.get("sync_on_startup", False)
        # Seconds to wait after application startup before first sync
        self.startup_delay: int = data.get("startup_delay", 0)
        # ISO-8601 timestamp of the last successful sync (or None)
        self.last_sync: Optional[str] = data.get("last_sync", None)
        # Rclone VFS cache mode ("off" | "minimal" | "writes" | "full")
        self.vfs_cache_mode: str = data.get("vfs_cache_mode", "full")
        # Download files on demand instead of all at once
        self.download_on_demand: bool = data.get("download_on_demand", True)
        # Use --resync flag on bisync (keeps cloud and local in sync)
        self.use_resync: bool = data.get("use_resync", True)
        # Whether this service's automatic sync is currently enabled
        self.sync_active: bool = data.get("sync_active", True)
        # Ring-buffer of the last ≤ 50 file-change events
        self.recent_files: List[Dict[str, Any]] = data.get("recent_files", [])
        # Folders that are explicitly excluded from sync
        self.excluded_folders: List[str] = data.get("excluded_folders", [])
        # Folders that are included (empty = all folders synced)
        self.included_folders: List[str] = data.get("included_folders", [])

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise this ServiceConfig to a JSON-compatible dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "remote_name": self.remote_name,
            "platform": self.platform,
            "local_path": self.local_path,
            "remote_path": self.remote_path,
            "is_syncing": self.is_syncing,
            "sync_interval": self.sync_interval,
            "exclude_rules": self.exclude_rules,
            "sync_on_startup": self.sync_on_startup,
            "startup_delay": self.startup_delay,
            "last_sync": self.last_sync,
            "vfs_cache_mode": self.vfs_cache_mode,
            "download_on_demand": self.download_on_demand,
            "use_resync": self.use_resync,
            "sync_active": self.sync_active,
            "recent_files": self.recent_files,
            "excluded_folders": self.excluded_folders,
            "included_folders": self.included_folders,
        }

    # ------------------------------------------------------------------
    def add_recent_file(self, filename: str, synced: bool) -> None:
        """Prepend a file-change event to the recent-files ring-buffer.

        Keeps at most the last 50 entries.
        """
        entry: Dict[str, Any] = {
            "filename": filename,
            "synced": synced,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        # Prepend so that index 0 is always the most recent
        self.recent_files = [entry] + self.recent_files[:49]


# ---------------------------------------------------------------------------
# Application-wide configuration
# ---------------------------------------------------------------------------

class AppConfig:
    """Manages the top-level application configuration (all services + globals)."""

    def __init__(self) -> None:
        """Initialise and immediately load from disk (or create defaults)."""
        self.services: List[ServiceConfig] = []
        self.start_with_system: bool = False
        self.config_dir: Path = CONFIG_DIR
        self.config_file: Path = CONFIG_FILE
        self.rclone_config_file: Path = RCLONE_CONFIG_FILE
        self._ensure_config_dir()
        self.load()

    # ------------------------------------------------------------------
    def _ensure_config_dir(self) -> None:
        """Create the configuration directory tree if it does not exist."""
        self.config_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def load(self) -> None:
        """Load configuration from the JSON file on disk.

        Silently resets to empty defaults if the file is absent or corrupt.
        """
        if not self.config_file.exists():
            return
        try:
            with open(self.config_file, "r", encoding="utf-8") as fh:
                data: Dict[str, Any] = json.load(fh)
            self.services = [ServiceConfig(s) for s in data.get("services", [])]
            self.start_with_system = data.get("start_with_system", False)
        except (json.JSONDecodeError, KeyError, TypeError):
            # Corrupt config – start fresh
            self.services = []
            self.start_with_system = False

    # ------------------------------------------------------------------
    def save(self) -> None:
        """Persist the current in-memory configuration to disk (JSON)."""
        data: Dict[str, Any] = {
            "services": [s.to_dict() for s in self.services],
            "start_with_system": self.start_with_system,
        }
        with open(self.config_file, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    def add_service(self, service: ServiceConfig) -> None:
        """Append a new service and persist the configuration."""
        self.services.append(service)
        self.save()

    # ------------------------------------------------------------------
    def remove_service(self, service_id: str) -> None:
        """Delete a service by ID and persist the configuration."""
        self.services = [s for s in self.services if s.id != service_id]
        self.save()

    # ------------------------------------------------------------------
    def get_service(self, service_id: str) -> Optional[ServiceConfig]:
        """Return the ServiceConfig with the given ID, or None if not found."""
        for service in self.services:
            if service.id == service_id:
                return service
        return None

    # ------------------------------------------------------------------
    def update_service(self, service: ServiceConfig) -> None:
        """Replace the in-memory record for a service and persist.

        Does nothing if the service ID is not found.
        """
        for index, existing in enumerate(self.services):
            if existing.id == service.id:
                self.services[index] = service
                self.save()
                return
