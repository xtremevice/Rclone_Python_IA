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


# Default sync interval in seconds (15 minutes)
DEFAULT_SYNC_INTERVAL = 900

# Default rclone options for transfers
DEFAULT_RCLONE_OPTS = {
    "transfers": 16,
    "checkers": 32,
    "drive_chunk_size": "128M",
    "buffer_size": "64M",
    "vfs_cache_mode": "on_demand",
    "vfs_cache_max_size": "10G",
}

# Exclusion pattern for the OneDrive Personal Vault folder
PERSONAL_VAULT_PATTERN = "/Almacén personal/**"

# Default exclusion rules applied to every service
DEFAULT_EXCLUSIONS = [PERSONAL_VAULT_PATTERN]

# Platforms supported by rclone that are offered in the wizard
SUPPORTED_PLATFORMS = [
    "onedrive",
    "drive",       # Google Drive
    "dropbox",
    "box",
    "s3",
    "sftp",
    "ftp",
    "mega",
    "pcloud",
    "yandex",
]

# Human-readable labels for each platform
PLATFORM_LABELS = {
    "onedrive": "Microsoft OneDrive",
    "drive": "Google Drive",
    "dropbox": "Dropbox",
    "box": "Box",
    "s3": "Amazon S3",
    "sftp": "SFTP / SSH",
    "ftp": "FTP",
    "mega": "Mega",
    "pcloud": "pCloud",
    "yandex": "Yandex Disk",
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
            # rclone VFS cache settings
            "vfs_cache_mode": "on_demand",
            "vfs_cache_max_size": "10G",
            # Recent file sync history (list of dicts)
            "sync_history": [],
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
