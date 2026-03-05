"""
config_manager.py
-----------------
Manages the application's persistent JSON configuration file.
Handles reading/writing services and global settings to disk.
"""

import json
import os
import uuid
from pathlib import Path


# Directory where the application stores its configuration
APP_CONFIG_DIR = Path.home() / ".rclone_manager"
# Path to the main configuration JSON file
APP_CONFIG_FILE = APP_CONFIG_DIR / "config.json"

# Default synchronization settings applied to every new service
DEFAULT_SERVICE_SETTINGS = {
    "sync_from_root": True,             # Sync from / (root) of the remote
    "on_demand": True,                  # Download files only when accessed (vfs)
    "resync": True,                     # Use --resync flag on bisync
    "exclude_personal_vault": True,     # Exclude OneDrive Personal Vault by default
    "sync_interval": 15,                # Sync every 15 minutes by default
    "autostart": False,                 # Start with the operating system
    "autostart_delay": 30,              # Seconds to wait after OS startup before syncing
}


class ConfigManager:
    """Manages reading and writing the application configuration JSON."""

    def __init__(self):
        # Ensure the config directory exists on first use
        self._ensure_config_dir()
        # Load config from disk, or create a default empty config
        self._config = self._load_or_create()

    def _ensure_config_dir(self):
        """Create the application config directory if it does not exist."""
        APP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def _load_or_create(self):
        """Load config.json or return a default skeleton if not found."""
        if APP_CONFIG_FILE.exists():
            try:
                with open(APP_CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                # If the file is corrupt, start fresh
                return self._default_config()
        return self._default_config()

    def _default_config(self):
        """Return an empty default configuration skeleton."""
        return {
            "services": [],
            "settings": {
                "startup_with_system": False,
                "startup_delay": 30,
            },
        }

    def save(self):
        """Persist the current in-memory config to disk as JSON."""
        with open(APP_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self._config, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Service helpers
    # ------------------------------------------------------------------

    def get_services(self):
        """Return the list of configured services."""
        return self._config.get("services", [])

    def get_service(self, service_id):
        """Return a single service dict by its ID, or None if not found."""
        for svc in self.get_services():
            if svc.get("id") == service_id:
                return svc
        return None

    def add_service(self, name, platform, local_path, remote_name):
        """
        Add a new service entry to the config and persist it.

        Parameters
        ----------
        name        : Human-readable label for the service
        platform    : rclone provider type (e.g. 'onedrive', 'drive')
        local_path  : Absolute local directory path for sync
        remote_name : Name of the rclone remote (key in rclone.conf)

        Returns
        -------
        The newly created service dict.
        """
        # Generate a unique identifier for the new service
        service_id = str(uuid.uuid4())
        new_service = {
            "id": service_id,
            "name": name,
            "platform": platform,
            "local_path": str(local_path),
            "remote_name": remote_name,
            # Sync settings with sensible defaults
            "sync_from_root": DEFAULT_SERVICE_SETTINGS["sync_from_root"],
            "on_demand": DEFAULT_SERVICE_SETTINGS["on_demand"],
            "resync": DEFAULT_SERVICE_SETTINGS["resync"],
            "exclude_personal_vault": DEFAULT_SERVICE_SETTINGS["exclude_personal_vault"],
            "sync_interval": DEFAULT_SERVICE_SETTINGS["sync_interval"],
            "autostart": DEFAULT_SERVICE_SETTINGS["autostart"],
            "autostart_delay": DEFAULT_SERVICE_SETTINGS["autostart_delay"],
            # Runtime state (not persisted across sessions as meaningful values)
            "excluded_paths": [],          # User-defined exclusion patterns
            "selected_folders": [],        # Folders explicitly enabled for sync
            "sync_paused": False,          # Whether syncing is currently paused
            "last_sync": None,             # ISO timestamp of last successful sync
        }
        self._config["services"].append(new_service)
        self.save()
        return new_service

    def update_service(self, service_id, updates: dict):
        """
        Apply a dict of updates to an existing service and save.

        Parameters
        ----------
        service_id : ID of the service to update
        updates    : Dict of keys/values to merge into the service record
        """
        for svc in self._config["services"]:
            if svc.get("id") == service_id:
                svc.update(updates)
                break
        self.save()

    def delete_service(self, service_id):
        """Remove a service from the config by ID and save."""
        self._config["services"] = [
            s for s in self._config["services"] if s.get("id") != service_id
        ]
        self.save()

    # ------------------------------------------------------------------
    # Global settings helpers
    # ------------------------------------------------------------------

    def get_settings(self):
        """Return the global application settings dict."""
        return self._config.get("settings", {})

    def update_settings(self, updates: dict):
        """Merge updates into global settings and save."""
        self._config.setdefault("settings", {}).update(updates)
        self.save()
