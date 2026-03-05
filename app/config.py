"""
Configuration management for the Rclone Manager application.

Handles loading, saving, and providing defaults for all service
configurations persisted in a JSON file inside the platform-appropriate
user config directory.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

# Default rclone performance/behaviour options applied to every new service
DEFAULT_RCLONE_CONFIG: Dict = {
    "transfers": 16,
    "checkers": 32,
    "drive_chunk_size": "128M",
    "buffer_size": "64M",
    # OneDrive "Almacén personal" causes errors – excluded by default
    "exclude_patterns": ["Almacén personal/**"],
    "resync": True,
    "sync_interval_minutes": 15,
}

# rclone remote type → human-readable display name
AVAILABLE_SERVICES: Dict[str, str] = {
    "onedrive": "Microsoft OneDrive",
    "drive": "Google Drive",
    "dropbox": "Dropbox",
    "box": "Box",
    "s3": "Amazon S3",
    "b2": "Backblaze B2",
    "mega": "MEGA",
    "sftp": "SFTP",
    "ftp": "FTP",
    "webdav": "WebDAV",
}

# Sync interval label → minutes
SYNC_INTERVALS: Dict[str, int] = {
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


class AppConfig:
    """Manages persistent application configuration (services + app settings)."""

    def __init__(self) -> None:
        """Initialize and load existing configuration from disk."""
        self.config_dir: Path = self._get_config_dir()
        self.config_file: Path = self.config_dir / "config.json"
        self.services: List[Dict] = []
        self.app_settings: Dict = {}
        self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_config_dir(self) -> Path:
        """Return the platform-appropriate configuration directory."""
        if os.name == "nt":
            # Windows: %APPDATA%\RcloneManager
            base = Path(os.environ.get("APPDATA", str(Path.home())))
        elif hasattr(os, "uname") and os.uname().sysname == "Darwin":
            # macOS: ~/Library/Application Support/RcloneManager
            base = Path.home() / "Library" / "Application Support"
        else:
            # Linux / other POSIX: $XDG_CONFIG_HOME/RcloneManager
            base = Path(
                os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
            )
        config_dir = base / "RcloneManager"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir

    def _load(self) -> None:
        """Load configuration from the JSON file; silently reset on errors."""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self.services = data.get("services", [])
                self.app_settings = data.get("app_settings", {})
            except (json.JSONDecodeError, OSError):
                self.services = []
                self.app_settings = {}
        else:
            self.services = []
            self.app_settings = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Persist the current configuration to disk."""
        data = {
            "services": self.services,
            "app_settings": self.app_settings,
        }
        with open(self.config_file, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)

    def add_service(self, service: Dict) -> bool:
        """Add *service* to the list; returns False if the name already exists."""
        for existing in self.services:
            if existing.get("name") == service.get("name"):
                return False
        self.services.append(service)
        self.save()
        return True

    def remove_service(self, name: str) -> bool:
        """Remove the service identified by *name*; returns False if not found."""
        for idx, svc in enumerate(self.services):
            if svc.get("name") == name:
                self.services.pop(idx)
                self.save()
                return True
        return False

    def update_service(self, name: str, updates: Dict) -> bool:
        """Merge *updates* into an existing service; returns False if not found."""
        for svc in self.services:
            if svc.get("name") == name:
                svc.update(updates)
                self.save()
                return True
        return False

    def get_service(self, name: str) -> Optional[Dict]:
        """Return the service dict for *name*, or None if not found."""
        for svc in self.services:
            if svc.get("name") == name:
                return svc
        return None

    def create_service_config(
        self,
        name: str,
        service_type: str,
        local_path: str,
        display_name: str = "",
    ) -> Dict:
        """Build a new service configuration dict pre-filled with defaults."""
        return {
            "name": name,
            "display_name": display_name or name,
            "service_type": service_type,
            "local_path": local_path,
            # Root of the remote is used by default
            "remote_path": "/",
            "sync_interval": DEFAULT_RCLONE_CONFIG["sync_interval_minutes"],
            "exclude_patterns": list(DEFAULT_RCLONE_CONFIG["exclude_patterns"]),
            "excluded_folders": [],
            "autostart": False,
            "autostart_delay": 0,
            # Whether the initial --resync has been performed
            "first_sync_done": False,
            "enabled": True,
            "rclone_options": {
                "transfers": DEFAULT_RCLONE_CONFIG["transfers"],
                "checkers": DEFAULT_RCLONE_CONFIG["checkers"],
                "drive_chunk_size": DEFAULT_RCLONE_CONFIG["drive_chunk_size"],
                "buffer_size": DEFAULT_RCLONE_CONFIG["buffer_size"],
            },
        }
