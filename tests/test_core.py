"""
test_core.py
------------
Unit tests for the core modules: ConfigManager, RcloneManager, ServiceManager.
Run with:  python3 -m pytest tests/test_core.py -v
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure the project root is in the path
sys.path.insert(0, str(Path(__file__).parent.parent))

import src.core.config_manager as cm_module
from src.core.config_manager import ConfigManager
from src.core.rclone_manager import RcloneManager, SUPPORTED_PLATFORMS, PERSONAL_VAULT_EXCLUDE
from src.core.service_manager import ServiceManager, SyncStatus


class TestConfigManager(unittest.TestCase):
    """Tests for ConfigManager: service CRUD and persistence."""

    def setUp(self):
        """Point ConfigManager at a temporary file for each test."""
        self._tmpfile = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmpfile.close()
        # Patch the module-level paths so ConfigManager uses our temp file
        cm_module.APP_CONFIG_FILE = Path(self._tmpfile.name)
        cm_module.APP_CONFIG_DIR = Path(self._tmpfile.name).parent
        self.mgr = ConfigManager()

    def tearDown(self):
        """Remove the temporary config file."""
        try:
            os.unlink(self._tmpfile.name)
        except FileNotFoundError:
            pass

    def test_initial_state_is_empty(self):
        """A freshly created ConfigManager should have no services."""
        self.assertEqual(self.mgr.get_services(), [])

    def test_add_service_creates_entry(self):
        """add_service() should create a service with the expected fields."""
        svc = self.mgr.add_service("TestSvc", "onedrive", "/tmp/test", "remote_test")
        self.assertEqual(svc["name"], "TestSvc")
        self.assertEqual(svc["platform"], "onedrive")
        self.assertEqual(svc["local_path"], "/tmp/test")
        self.assertEqual(svc["remote_name"], "remote_test")
        self.assertIn("id", svc)
        self.assertIn("sync_interval", svc)

    def test_add_service_persists_to_disk(self):
        """After add_service() the config file should contain the new entry."""
        self.mgr.add_service("TestSvc", "drive", "/tmp/gdrive", "gdrive_remote")
        # Re-load from disk
        with open(cm_module.APP_CONFIG_FILE) as f:
            data = json.load(f)
        self.assertEqual(len(data["services"]), 1)
        self.assertEqual(data["services"][0]["name"], "TestSvc")

    def test_get_service_returns_correct_entry(self):
        """get_service() should return the right service by ID."""
        svc1 = self.mgr.add_service("Svc1", "onedrive", "/a", "remote_a")
        svc2 = self.mgr.add_service("Svc2", "drive", "/b", "remote_b")
        self.assertEqual(self.mgr.get_service(svc1["id"])["name"], "Svc1")
        self.assertEqual(self.mgr.get_service(svc2["id"])["name"], "Svc2")
        self.assertIsNone(self.mgr.get_service("nonexistent-id"))

    def test_update_service_modifies_fields(self):
        """update_service() should merge new values into the existing entry."""
        svc = self.mgr.add_service("Upd", "onedrive", "/c", "remote_c")
        self.mgr.update_service(svc["id"], {"sync_interval": 60, "sync_paused": True})
        updated = self.mgr.get_service(svc["id"])
        self.assertEqual(updated["sync_interval"], 60)
        self.assertTrue(updated["sync_paused"])

    def test_delete_service_removes_entry(self):
        """delete_service() should remove the service from the list."""
        svc = self.mgr.add_service("Del", "dropbox", "/d", "remote_d")
        self.assertEqual(len(self.mgr.get_services()), 1)
        self.mgr.delete_service(svc["id"])
        self.assertEqual(len(self.mgr.get_services()), 0)
        self.assertIsNone(self.mgr.get_service(svc["id"]))

    def test_delete_nonexistent_service_is_noop(self):
        """delete_service() on a nonexistent ID should not raise."""
        self.mgr.add_service("X", "box", "/e", "remote_e")
        self.mgr.delete_service("fake-id-does-not-exist")
        self.assertEqual(len(self.mgr.get_services()), 1)

    def test_multiple_services_preserved(self):
        """Multiple services should all be stored and retrievable."""
        names = ["Alpha", "Beta", "Gamma"]
        ids = []
        for name in names:
            svc = self.mgr.add_service(name, "onedrive", f"/tmp/{name}", f"remote_{name}")
            ids.append(svc["id"])
        self.assertEqual(len(self.mgr.get_services()), 3)
        for i, name in enumerate(names):
            self.assertEqual(self.mgr.get_service(ids[i])["name"], name)

    def test_settings_update_persists(self):
        """update_settings() should persist global settings to disk."""
        self.mgr.update_settings({"startup_with_system": True, "startup_delay": 45})
        settings = self.mgr.get_settings()
        self.assertTrue(settings["startup_with_system"])
        self.assertEqual(settings["startup_delay"], 45)

    def test_corrupt_config_file_falls_back_to_default(self):
        """A corrupt JSON config file should silently reset to default."""
        with open(cm_module.APP_CONFIG_FILE, "w") as f:
            f.write("not valid json {{{{")
        mgr2 = ConfigManager()
        self.assertEqual(mgr2.get_services(), [])


class TestRcloneManager(unittest.TestCase):
    """Tests for RcloneManager: path detection, config path, token parsing."""

    def setUp(self):
        self.rm = RcloneManager()

    def test_supported_platforms_not_empty(self):
        """SUPPORTED_PLATFORMS should have at least the common providers."""
        self.assertIn("onedrive", SUPPORTED_PLATFORMS)
        self.assertIn("drive", SUPPORTED_PLATFORMS)
        self.assertIn("dropbox", SUPPORTED_PLATFORMS)

    def test_personal_vault_exclude_constant(self):
        """PERSONAL_VAULT_EXCLUDE should be the correct exclusion rule string."""
        self.assertIn("Almacén personal", PERSONAL_VAULT_EXCLUDE)

    def test_config_path_is_set(self):
        """config_path should point to a non-None Path object."""
        self.assertIsNotNone(self.rm.config_path)
        self.assertIsInstance(self.rm.config_path, Path)

    def test_is_rclone_available_returns_bool(self):
        """is_rclone_available() should return a boolean."""
        result = self.rm.is_rclone_available()
        self.assertIsInstance(result, bool)

    def test_extract_token_with_paste_markers(self):
        """_extract_token() should find the token between paste markers."""
        output = (
            "Some output lines\n"
            "Paste the following into your remote machine --->"
            '{"access_token":"abc123","refresh_token":"xyz","expiry":"2025-01-01T00:00:00Z"}'
            "<---End paste\n"
            "More output"
        )
        token = self.rm._extract_token(output)
        self.assertIsNotNone(token)
        self.assertIn("access_token", token)

    def test_extract_token_with_raw_json(self):
        """_extract_token() should find bare JSON if no paste markers exist."""
        output = 'Some text {"access_token":"tok","refresh_token":"ref"} more text'
        token = self.rm._extract_token(output)
        self.assertIsNotNone(token)
        self.assertIn("access_token", token)

    def test_extract_token_returns_none_when_absent(self):
        """_extract_token() should return None when no token is present."""
        output = "No token here, just some random output lines."
        self.assertIsNone(self.rm._extract_token(output))

    def test_parse_transferred_files_basic(self):
        """parse_transferred_files() should extract transferred file entries."""
        lines = [
            "2024/01/01 12:00:00 INFO  : Documents/file.txt: Copied (new)",
            "2024/01/01 12:00:01 INFO  : Photos/img.jpg: Copied (replaced)",
            "2024/01/01 12:00:02 INFO  : Old/data.csv: Deleted",
        ]
        files = self.rm.parse_transferred_files(lines)
        self.assertEqual(len(files), 3)
        paths = [f["path"] for f in files]
        self.assertIn("Documents/file.txt", paths)
        self.assertIn("Photos/img.jpg", paths)
        self.assertIn("Old/data.csv", paths)

    def test_parse_transferred_files_limit_50(self):
        """parse_transferred_files() should cap results at 50 unique entries."""
        lines = [
            f"2024/01/01 12:00:00 INFO  : file_{i}.txt: Copied (new)"
            for i in range(100)
        ]
        files = self.rm.parse_transferred_files(lines)
        self.assertLessEqual(len(files), 50)

    def test_parse_transferred_files_deduplication(self):
        """parse_transferred_files() should deduplicate repeated file paths."""
        lines = [
            "2024/01/01 12:00:00 INFO  : doc.txt: Copied (new)",
            "2024/01/01 12:00:01 INFO  : doc.txt: Copied (replaced)",
        ]
        files = self.rm.parse_transferred_files(lines)
        self.assertEqual(len(files), 1)

    def test_run_without_rclone_returns_error(self):
        """_run() should return error code 1 and message when rclone not found."""
        rm = RcloneManager()
        rm.rclone_path = None  # Simulate rclone not installed
        rc, stdout, stderr = rm._run(["version"])
        self.assertEqual(rc, 1)
        self.assertIn("not found", stderr.lower())

    def test_get_disk_usage_returns_tuple(self):
        """get_disk_usage() should return a 3-tuple of integers."""
        used, total, free = self.rm.get_disk_usage("/tmp")
        self.assertIsInstance(used, int)
        self.assertIsInstance(total, int)
        self.assertIsInstance(free, int)

    def test_get_disk_usage_nonexistent_path(self):
        """get_disk_usage() on a nonexistent path should return zeros."""
        used, total, free = self.rm.get_disk_usage("/nonexistent/path/xyz")
        self.assertEqual((used, total, free), (0, 0, 0))


class TestServiceManager(unittest.TestCase):
    """Tests for ServiceManager: status tracking, pause/resume, sync scheduling."""

    def setUp(self):
        """Use a temp config file and mock rclone for each test."""
        self._tmpfile = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmpfile.close()
        cm_module.APP_CONFIG_FILE = Path(self._tmpfile.name)
        cm_module.APP_CONFIG_DIR = Path(self._tmpfile.name).parent
        self.sm = ServiceManager()

    def tearDown(self):
        try:
            os.unlink(self._tmpfile.name)
        except FileNotFoundError:
            pass

    def test_initial_services_empty(self):
        """ServiceManager should start with no services."""
        self.assertEqual(self.sm.get_services(), [])

    def test_add_service_delegates_to_config(self):
        """add_service() should call config.add_service and return the new service."""
        svc = self.sm.config.add_service("S1", "onedrive", "/tmp/s1", "remote_s1")
        self.assertEqual(svc["name"], "S1")
        self.assertEqual(len(self.sm.get_services()), 1)

    def test_get_status_default_idle(self):
        """A newly added service should report IDLE status."""
        svc = self.sm.config.add_service("S2", "drive", "/tmp/s2", "remote_s2")
        status = self.sm.get_status(svc["id"])
        self.assertEqual(status, SyncStatus.IDLE)

    def test_pause_service_updates_config(self):
        """pause_service() should set sync_paused=True in the config."""
        svc = self.sm.config.add_service("S3", "dropbox", "/tmp/s3", "remote_s3")
        self.sm.pause_service(svc["id"])
        updated = self.sm.config.get_service(svc["id"])
        self.assertTrue(updated["sync_paused"])

    def test_pause_service_returns_paused_status(self):
        """After pausing, get_status() should return PAUSED."""
        svc = self.sm.config.add_service("S4", "box", "/tmp/s4", "remote_s4")
        self.sm.pause_service(svc["id"])
        self.assertEqual(self.sm.get_status(svc["id"]), SyncStatus.PAUSED)

    def test_resume_service_clears_pause(self):
        """resume_service() should set sync_paused=False in the config."""
        svc = self.sm.config.add_service("S5", "onedrive", "/tmp/s5", "remote_s5")
        self.sm.pause_service(svc["id"])
        self.sm.resume_service(svc["id"])
        updated = self.sm.config.get_service(svc["id"])
        self.assertFalse(updated["sync_paused"])

    def test_status_callback_invoked_on_pause(self):
        """Registered callbacks should be called when a service is paused."""
        svc = self.sm.config.add_service("S6", "drive", "/tmp/s6", "remote_s6")
        received = []
        self.sm.register_status_callback(lambda sid: received.append(sid))
        self.sm.pause_service(svc["id"])
        self.assertIn(svc["id"], received)

    def test_delete_service_removes_from_config(self):
        """delete_service() should remove the service from config."""
        svc = self.sm.config.add_service("S7", "dropbox", "/tmp/s7", "remote_s7")
        # Mock rclone.delete_remote so it doesn't try to call rclone
        self.sm.rclone.delete_remote = MagicMock(return_value=True)
        self.sm.delete_service(svc["id"])
        self.assertIsNone(self.sm.config.get_service(svc["id"]))
        self.assertEqual(len(self.sm.get_services()), 0)

    def test_get_changed_files_empty_initially(self):
        """get_changed_files() should return an empty list for a new service."""
        svc = self.sm.config.add_service("S8", "onedrive", "/tmp/s8", "remote_s8")
        self.assertEqual(self.sm.get_changed_files(svc["id"]), [])

    def test_get_last_sync_time_none_initially(self):
        """get_last_sync_time() should return None before any sync runs."""
        svc = self.sm.config.add_service("S9", "drive", "/tmp/s9", "remote_s9")
        self.assertIsNone(self.sm.get_last_sync_time(svc["id"]))

    def test_sync_status_constants(self):
        """SyncStatus constants should have distinct values."""
        statuses = {SyncStatus.IDLE, SyncStatus.SYNCING, SyncStatus.PAUSED, SyncStatus.ERROR}
        self.assertEqual(len(statuses), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
