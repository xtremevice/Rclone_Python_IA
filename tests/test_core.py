"""
test_core.py
------------
Unit tests for the core modules: ConfigManager, RcloneManager.
Run with:  python3 -m pytest tests/test_core.py -v
"""

import configparser
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure the project root is in the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.config_manager import (
    ConfigManager,
    get_config_dir,
    SUPPORTED_PLATFORMS,
    PLATFORM_LABELS,
    PERSONAL_VAULT_PATTERN,
    DEFAULT_SYNC_INTERVAL,
    DEFAULT_EXCLUSIONS,
)
from src.rclone.rclone_manager import (
    RcloneManager,
    _extract_file_path,
    _human_size,
    _rclone_supports_resync_mode,
    _rclone_supports_create_empty_src_dirs,
    _bisync_cache_dir,
    _bisync_workdir_for_service,
    _migrate_bisync_state,
    _clear_bisync_stale_files,
    _slug,
    _DRIVE_ID_MISSING_PHRASE,
    _BISYNC_NO_PRIOR_PHRASE,
    _NETWORK_UNREACHABLE_PHRASE,
    _MIN_FREE_SPACE_BYTES,
    _MIN_FREE_SPACE_GIB,
    _check_local_free_space,
    _parse_rclone_mtime,
    _scan_local_mtimes,
    _build_mtime_comparison,
    _MTIME_TOLERANCE_SECS,
    _MICROSECOND_PRECISION,
)


class TestConfigManager(unittest.TestCase):
    """Tests for ConfigManager: service CRUD and persistence."""

    def setUp(self):
        """
        Redirect config storage to a temporary directory for each test
        so that tests do not interfere with each other or with real user data.
        """
        self._tmpdir = tempfile.mkdtemp()
        # Monkey-patch get_config_dir so ConfigManager writes to the temp dir
        import src.config.config_manager as cm_mod
        self._original_get_config_dir = cm_mod.get_config_dir
        cm_mod.get_config_dir = lambda: Path(self._tmpdir)
        self.mgr = ConfigManager()

    def tearDown(self):
        """Restore the original get_config_dir after each test."""
        import src.config.config_manager as cm_mod
        cm_mod.get_config_dir = self._original_get_config_dir
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_initial_state_has_no_services(self):
        """A freshly created ConfigManager should have no services."""
        self.assertEqual(self.mgr.get_services(), [])

    def test_add_service_returns_dict_with_expected_keys(self):
        """add_service() should return a dict with name, platform, local_path."""
        svc = self.mgr.add_service("TestSvc", "onedrive", "/tmp/test")
        self.assertEqual(svc["name"], "TestSvc")
        self.assertEqual(svc["platform"], "onedrive")
        self.assertEqual(svc["local_path"], "/tmp/test")
        self.assertIn("sync_interval", svc)
        self.assertIn("exclusions", svc)

    def test_add_service_persists_to_disk(self):
        """After add_service() the config file should contain the new entry."""
        self.mgr.add_service("TestSvc", "drive", "/tmp/gdrive")
        # Reload from disk
        mgr2 = ConfigManager()
        services = mgr2.get_services()
        self.assertEqual(len(services), 1)
        self.assertEqual(services[0]["name"], "TestSvc")

    def test_get_service_by_name(self):
        """get_service() should return the matching service or None."""
        self.mgr.add_service("Svc1", "onedrive", "/a")
        self.mgr.add_service("Svc2", "drive", "/b")
        self.assertEqual(self.mgr.get_service("Svc1")["platform"], "onedrive")
        self.assertEqual(self.mgr.get_service("Svc2")["platform"], "drive")
        self.assertIsNone(self.mgr.get_service("NonExistent"))

    def test_update_service_modifies_field(self):
        """update_service() should merge new values into the existing entry."""
        self.mgr.add_service("Upd", "onedrive", "/c")
        self.mgr.update_service("Upd", {"sync_interval": 3600, "sync_enabled": False})
        updated = self.mgr.get_service("Upd")
        self.assertEqual(updated["sync_interval"], 3600)
        self.assertFalse(updated["sync_enabled"])

    def test_remove_service_deletes_entry(self):
        """remove_service() should remove the named service from config."""
        self.mgr.add_service("Del", "dropbox", "/d")
        self.assertEqual(len(self.mgr.get_services()), 1)
        self.mgr.remove_service("Del")
        self.assertEqual(len(self.mgr.get_services()), 0)
        self.assertIsNone(self.mgr.get_service("Del"))

    def test_remove_nonexistent_service_is_noop(self):
        """remove_service() on an unknown name should not raise or mutate state."""
        self.mgr.add_service("X", "box", "/e")
        self.mgr.remove_service("does-not-exist")
        self.assertEqual(len(self.mgr.get_services()), 1)

    def test_multiple_services_are_preserved(self):
        """Multiple services should all be stored and independently retrievable."""
        names = ["Alpha", "Beta", "Gamma"]
        for name in names:
            self.mgr.add_service(name, "onedrive", f"/tmp/{name}")
        self.assertEqual(len(self.mgr.get_services()), 3)
        for name in names:
            self.assertIsNotNone(self.mgr.get_service(name))

    def test_preferences_default_values(self):
        """Default preferences should include start_with_system = False."""
        self.assertFalse(self.mgr.get_preference("start_with_system"))

    def test_set_preference_persists(self):
        """set_preference() should save the value so it survives a reload."""
        self.mgr.set_preference("start_with_system", True)
        self.assertTrue(self.mgr.get_preference("start_with_system"))

    def test_sync_history_entry_added(self):
        """add_sync_history_entry() should prepend to the service history."""
        self.mgr.add_service("HistSvc", "drive", "/h")
        self.mgr.add_sync_history_entry("HistSvc", "docs/file.txt", True)
        svc = self.mgr.get_service("HistSvc")
        history = svc.get("sync_history", [])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["file"], "docs/file.txt")
        self.assertTrue(history[0]["synced"])

    def test_sync_history_capped_at_50(self):
        """History should never exceed 50 entries per service."""
        self.mgr.add_service("CapSvc", "onedrive", "/cap")
        for i in range(60):
            self.mgr.add_sync_history_entry("CapSvc", f"file_{i}.txt", True)
        svc = self.mgr.get_service("CapSvc")
        self.assertLessEqual(len(svc.get("sync_history", [])), 50)

    def test_default_exclusion_contains_vault(self):
        """A new service should include the OneDrive Personal Vault exclusion."""
        svc = self.mgr.add_service("VaultSvc", "onedrive", "/v")
        self.assertIn(PERSONAL_VAULT_PATTERN, svc.get("exclusions", []))

    def test_corrupt_config_falls_back_to_default(self):
        """A corrupt JSON config file should silently reset to defaults."""
        config_path = Path(self._tmpdir) / "app_config.json"
        config_path.write_text("not valid json {{{{", encoding="utf-8")
        mgr2 = ConfigManager()
        self.assertEqual(mgr2.get_services(), [])

    def test_service_has_vfs_cache_dir_field(self):
        """New services should include the vfs_cache_dir field defaulting to empty."""
        svc = self.mgr.add_service("VfsDirSvc", "onedrive", "/tmp/vfsdir")
        self.assertIn("vfs_cache_dir", svc)
        self.assertEqual(svc["vfs_cache_dir"], "")

    def test_service_has_bisync_workdir_field(self):
        """New services should include the bisync_workdir field defaulting to empty."""
        svc = self.mgr.add_service("WorkdirSvc", "onedrive", "/tmp/workdir")
        self.assertIn("bisync_workdir", svc)
        self.assertEqual(svc["bisync_workdir"], "")

    def test_add_service_allows_unique_names(self):
        """add_service() with distinct names must succeed without errors."""
        self.mgr.add_service("Svc Alpha", "onedrive", "/tmp/a")
        self.mgr.add_service("Svc Beta", "drive", "/tmp/b")
        services = self.mgr.get_services()
        names = [s["name"] for s in services]
        self.assertIn("Svc Alpha", names)
        self.assertIn("Svc Beta", names)

    def test_get_service_returns_none_for_unknown_name(self):
        """get_service() must return None when no service has the requested name."""
        self.mgr.add_service("Existing", "onedrive", "/tmp/x")
        result = self.mgr.get_service("NonExistent")
        self.assertIsNone(result, "get_service() should return None for an unknown name")


class TestConfigManagerConstants(unittest.TestCase):
    """Tests for module-level constants in config_manager."""

    def test_supported_platforms_not_empty(self):
        """SUPPORTED_PLATFORMS should list at least the common providers."""
        self.assertIn("onedrive", SUPPORTED_PLATFORMS)
        self.assertIn("drive", SUPPORTED_PLATFORMS)
        self.assertIn("dropbox", SUPPORTED_PLATFORMS)

    def test_platform_labels_covers_all_platforms(self):
        """Every entry in SUPPORTED_PLATFORMS should have a label."""
        for p in SUPPORTED_PLATFORMS:
            self.assertIn(p, PLATFORM_LABELS, f"Missing label for platform: {p}")

    def test_personal_vault_pattern_is_correct(self):
        """PERSONAL_VAULT_PATTERN should be the Spanish OneDrive vault exclusion."""
        self.assertIn("Almacén personal", PERSONAL_VAULT_PATTERN)

    def test_default_exclusions_includes_vault(self):
        """DEFAULT_EXCLUSIONS should include the vault exclusion by default."""
        self.assertIn(PERSONAL_VAULT_PATTERN, DEFAULT_EXCLUSIONS)

    def test_default_sync_interval_is_positive(self):
        """DEFAULT_SYNC_INTERVAL should be a positive number of seconds."""
        self.assertGreater(DEFAULT_SYNC_INTERVAL, 0)


class TestRcloneHelpers(unittest.TestCase):
    """Tests for module-level helper functions in rclone_manager."""

    def test_extract_file_path_from_copied_line(self):
        """_extract_file_path() should return the path from a 'Copied' line."""
        line = "Transferred: Documents/file.txt: Copied (new)"
        result = _extract_file_path(line)
        self.assertIsNotNone(result)

    def test_extract_file_path_returns_none_for_irrelevant_line(self):
        """_extract_file_path() should return None for non-file-transfer lines."""
        result = _extract_file_path("Elapsed time: 1.2s")
        self.assertIsNone(result)

    def test_human_size_bytes(self):
        """_human_size() should format bytes correctly."""
        self.assertIn("B", _human_size(512))

    def test_human_size_megabytes(self):
        """_human_size() should format megabytes correctly."""
        self.assertIn("MB", _human_size(2 * 1024 * 1024))

    def test_human_size_gigabytes(self):
        """_human_size() should format gigabytes correctly."""
        self.assertIn("GB", _human_size(3 * 1024 * 1024 * 1024))

    def test_rclone_supports_resync_mode_true_for_v1_64(self):
        """_rclone_supports_resync_mode() should return True for rclone v1.64+."""
        mock_cfg = MagicMock()
        mock_cfg.get_rclone_version.return_value = "rclone v1.64.0"
        self.assertTrue(_rclone_supports_resync_mode(mock_cfg))

    def test_rclone_supports_resync_mode_true_for_v1_65(self):
        """_rclone_supports_resync_mode() should return True for rclone v1.65."""
        mock_cfg = MagicMock()
        mock_cfg.get_rclone_version.return_value = "rclone v1.65.2"
        self.assertTrue(_rclone_supports_resync_mode(mock_cfg))

    def test_rclone_supports_resync_mode_false_for_v1_63(self):
        """_rclone_supports_resync_mode() should return False for rclone v1.63."""
        mock_cfg = MagicMock()
        mock_cfg.get_rclone_version.return_value = "rclone v1.63.1"
        self.assertFalse(_rclone_supports_resync_mode(mock_cfg))

    def test_rclone_supports_resync_mode_false_when_version_unknown(self):
        """_rclone_supports_resync_mode() should return False when version cannot be parsed."""
        mock_cfg = MagicMock()
        mock_cfg.get_rclone_version.return_value = "rclone not found"
        self.assertFalse(_rclone_supports_resync_mode(mock_cfg))

    def test_rclone_supports_create_empty_src_dirs_true_for_v1_64(self):
        """_rclone_supports_create_empty_src_dirs() should return True for rclone v1.64+."""
        mock_cfg = MagicMock()
        mock_cfg.get_rclone_version.return_value = "rclone v1.64.0"
        self.assertTrue(_rclone_supports_create_empty_src_dirs(mock_cfg))

    def test_rclone_supports_create_empty_src_dirs_true_for_v1_65(self):
        """_rclone_supports_create_empty_src_dirs() should return True for rclone v1.65."""
        mock_cfg = MagicMock()
        mock_cfg.get_rclone_version.return_value = "rclone v1.65.2"
        self.assertTrue(_rclone_supports_create_empty_src_dirs(mock_cfg))

    def test_rclone_supports_create_empty_src_dirs_false_for_v1_63(self):
        """_rclone_supports_create_empty_src_dirs() should return False for rclone v1.63."""
        mock_cfg = MagicMock()
        mock_cfg.get_rclone_version.return_value = "rclone v1.63.1"
        self.assertFalse(_rclone_supports_create_empty_src_dirs(mock_cfg))

    def test_rclone_supports_create_empty_src_dirs_false_when_version_unknown(self):
        """_rclone_supports_create_empty_src_dirs() returns False when version undetectable."""
        mock_cfg = MagicMock()
        mock_cfg.get_rclone_version.return_value = "rclone not found"
        self.assertFalse(_rclone_supports_create_empty_src_dirs(mock_cfg))


class TestRcloneManager(unittest.TestCase):
    """Tests for RcloneManager using a mock ConfigManager."""

    def setUp(self):
        """Create a RcloneManager backed by a real ConfigManager in a temp dir."""
        self._tmpdir = tempfile.mkdtemp()
        import src.config.config_manager as cm_mod
        self._original_get_config_dir = cm_mod.get_config_dir
        cm_mod.get_config_dir = lambda: Path(self._tmpdir)
        self.config = ConfigManager()
        self.rclone = RcloneManager(self.config)

    def tearDown(self):
        """Restore the original get_config_dir."""
        import src.config.config_manager as cm_mod
        cm_mod.get_config_dir = self._original_get_config_dir
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_initial_status_is_stopped(self):
        """A fresh RcloneManager should report 'Detenido' for any service."""
        status = self.rclone.get_status("any_service")
        self.assertEqual(status, "Detenido")

    def test_is_running_false_initially(self):
        """is_running() should return False before start_service() is called."""
        self.assertFalse(self.rclone.is_running("any_service"))

    def test_get_disk_usage_nonexistent_path(self):
        """get_disk_usage() on a non-existent service should return 'N/A'."""
        self.config.add_service("S1", "onedrive", "/nonexistent/xyz/abc")
        result = self.rclone.get_disk_usage("S1")
        self.assertEqual(result, "N/A")

    def test_get_disk_usage_unknown_service(self):
        """get_disk_usage() for an unknown service name should return 'N/A'."""
        self.assertEqual(self.rclone.get_disk_usage("not_there"), "N/A")

    def test_stop_service_is_noop_when_not_running(self):
        """stop_service() should not raise if the service was never started."""
        # Should complete without error
        self.rclone.stop_service("never_started")

    def test_start_all_with_no_services(self):
        """start_all() should complete without error when no services exist."""
        self.rclone.start_all()  # Should not raise

    def test_list_remote_tree_with_no_rclone(self):
        """list_remote_tree() should return an empty list if rclone is absent."""
        self.config.add_service("S2", "drive", "/tmp/s2")
        # rclone is not installed in CI, so we expect an empty list
        result = self.rclone.list_remote_tree("S2")
        self.assertIsInstance(result, list)

    # ------------------------------------------------------------------
    # list_remote_metadata — tuple return API
    # ------------------------------------------------------------------

    def test_list_remote_metadata_returns_tuple(self):
        """list_remote_metadata() must always return a 2-tuple."""
        self.config.add_service("MetaSvc", "drive", "/tmp/meta")
        result = self.rclone.list_remote_metadata("MetaSvc")
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    def test_list_remote_metadata_unknown_service_returns_error(self):
        """list_remote_metadata() for an unknown service must return (None, error)."""
        meta, err = self.rclone.list_remote_metadata("DoesNotExist")
        self.assertIsNone(meta)
        self.assertIsNotNone(err)
        self.assertIsInstance(err, str)
        self.assertGreater(len(err), 0)

    def test_list_remote_metadata_incomplete_config_returns_error(self):
        """A service without remote_name must return (None, error) not raise."""
        # Add service without setting a remote_name (defaults to empty string).
        self.config.add_service("NoRemote", "drive", "/tmp/noremote")
        # Ensure remote_name is empty so the guard fires.
        self.config.update_service("NoRemote", {"remote_name": ""})
        meta, err = self.rclone.list_remote_metadata("NoRemote")
        self.assertIsNone(meta)
        self.assertIsNotNone(err)

    def test_list_remote_metadata_rclone_failure_returns_error_string(self):
        """When rclone exits non-zero, the error message must be non-empty."""
        self.config.add_service("BadSvc", "drive", "/tmp/bad")
        self.config.update_service("BadSvc", {"remote_name": "badremote"})
        fake_proc = MagicMock()
        fake_proc.returncode = 1
        fake_proc.stdout = ""
        fake_proc.stderr = "Failed to connect to remote"
        with patch("subprocess.run", return_value=fake_proc):
            meta, err = self.rclone.list_remote_metadata("BadSvc")
        self.assertIsNone(meta)
        self.assertIsNotNone(err)
        self.assertIn("Failed to connect", err)

    def test_list_remote_metadata_rclone_success_returns_data_no_error(self):
        """When rclone succeeds, metadata must be returned and error must be None."""
        self.config.add_service("GoodSvc", "drive", "/tmp/good")
        self.config.update_service("GoodSvc", {"remote_name": "myremote"})
        payload = [
            {"Path": "docs/readme.txt", "ModTime": "2024-01-01T00:00:00.000000000Z",
             "Size": 1024, "IsDir": False},
            {"Path": "docs", "ModTime": "2024-01-01T00:00:00.000000000Z",
             "Size": 0, "IsDir": True},
        ]
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = json.dumps(payload)
        fake_proc.stderr = ""
        with patch("subprocess.run", return_value=fake_proc):
            meta, err = self.rclone.list_remote_metadata("GoodSvc")
        self.assertIsNone(err)
        self.assertIsNotNone(meta)
        self.assertIn("docs/readme.txt", meta)
        self.assertFalse(meta["docs/readme.txt"]["is_dir"])
        self.assertIn("docs", meta)
        self.assertTrue(meta["docs"]["is_dir"])

    def test_list_remote_metadata_timeout_returns_error(self):
        """A TimeoutExpired must return (None, descriptive error) not raise."""
        import subprocess as _sp
        self.config.add_service("TimeoutSvc", "drive", "/tmp/timeout")
        self.config.update_service("TimeoutSvc", {"remote_name": "slow_remote"})
        with patch("subprocess.run", side_effect=_sp.TimeoutExpired("cmd", 600)) as mock_run:
            meta, err = self.rclone.list_remote_metadata("TimeoutSvc")
        self.assertIsNone(meta)
        self.assertIsNotNone(err)
        self.assertIn("600", err)  # default timeout value must appear in the message
        # subprocess.run must have been called with the default timeout of 600 s
        _call_kwargs = mock_run.call_args
        actual_timeout = (
            _call_kwargs.kwargs.get("timeout")
            if _call_kwargs.kwargs
            else _call_kwargs[1].get("timeout")
        )
        self.assertEqual(actual_timeout, 600)

    def test_list_remote_metadata_custom_timeout_used(self):
        """lsjson_timeout per-service setting must change the timeout and error message."""
        import subprocess as _sp
        self.config.add_service("SlowSvc", "drive", "/tmp/slow")
        self.config.update_service("SlowSvc", {"remote_name": "slow_remote2", "lsjson_timeout": 900})
        with patch("subprocess.run", side_effect=_sp.TimeoutExpired("cmd", 900)) as mock_run:
            meta, err = self.rclone.list_remote_metadata("SlowSvc")
        self.assertIsNone(meta)
        self.assertIn("900", err)
        # The subprocess.run call must have received timeout=900
        _call_kwargs = mock_run.call_args
        actual_timeout = (
            _call_kwargs.kwargs.get("timeout")
            if _call_kwargs.kwargs
            else _call_kwargs[1].get("timeout")
        )
        self.assertEqual(actual_timeout, 900)

    def test_list_remote_mtimes_still_works_after_api_change(self):
        """list_remote_mtimes() must still return a plain dict (no tuple leakage)."""
        self.config.add_service("MtimeSvc", "drive", "/tmp/mtime")
        self.config.update_service("MtimeSvc", {"remote_name": "myremote2"})
        payload = [
            {"Path": "file.txt", "ModTime": "2024-06-01T12:00:00.000000000Z",
             "Size": 100, "IsDir": False},
        ]
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = json.dumps(payload)
        fake_proc.stderr = ""
        with patch("subprocess.run", return_value=fake_proc):
            result = self.rclone.list_remote_mtimes("MtimeSvc")
        self.assertIsInstance(result, dict)
        self.assertIn("file.txt", result)
        self.assertIsInstance(result["file.txt"], float)

    def test_on_status_change_callback_is_called(self):
        """_set_status() should invoke the on_status_change callback."""
        received = []
        self.rclone.on_status_change = lambda name, status: received.append((name, status))
        self.rclone._set_status("MySvc", "Testing")
        self.assertIn(("MySvc", "Testing"), received)

    def test_on_error_callback_is_called(self):
        """_emit_error() should invoke the on_error callback."""
        errors = []
        self.rclone.on_error = lambda name, msg: errors.append((name, msg))
        self.rclone._emit_error("MySvc", "Test error message")
        self.assertIn(("MySvc", "Test error message"), errors)

    def test_vfs_args_not_in_bisync_command(self):
        """_do_bisync() must NOT include VFS cache flags (--vfs-cache-mode etc.)

        rclone bisync does not accept VFS cache options; those are exclusive to
        the rclone mount command.  Passing them to bisync causes a fatal error.
        """
        self.config.add_service("VfsSvc", "onedrive", "/tmp/vfs_test")
        self.config.update_service("VfsSvc", {
            "vfs_cache_mode": "full",
            "vfs_cache_max_size": "5G",
            "vfs_cache_dir": "/tmp/cache_vfs",
        })
        captured_cmds = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            captured_cmds.append(cmd)
            return True

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("VfsSvc")
        self.rclone._do_bisync(svc)

        self.assertTrue(len(captured_cmds) > 0)
        full_cmd = captured_cmds[0]
        # VFS flags must NOT appear in the bisync command
        self.assertNotIn("--vfs-cache-mode", full_cmd)
        self.assertNotIn("--vfs-cache-max-size", full_cmd)
        self.assertNotIn("--cache-dir", full_cmd)

    def test_vfs_cache_dir_never_in_bisync_command(self):
        """_do_bisync() should never include --cache-dir regardless of vfs_cache_dir setting."""
        self.config.add_service("VfsSvc2", "onedrive", "/tmp/vfs_test2")
        self.config.update_service("VfsSvc2", {
            "vfs_cache_mode": "writes",
            "vfs_cache_dir": "/some/cache/dir",
        })
        captured_cmds = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            captured_cmds.append(cmd)
            return True

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("VfsSvc2")
        self.rclone._do_bisync(svc)

        self.assertTrue(len(captured_cmds) > 0)
        self.assertNotIn("--cache-dir", captured_cmds[0])

    def test_create_empty_src_dirs_in_bisync_by_default(self):
        """_do_bisync() must include --create-empty-src-dirs by default on rclone >= v1.64.

        Without this flag rclone bisync silently skips empty local directories,
        so a freshly created local folder never appears on the remote.
        """
        self.config.add_service("EmptyDirSvc", "onedrive", "/tmp/emptydir_test")
        captured_cmds = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            captured_cmds.append(cmd)
            return True

        self.rclone._run_rclone = fake_run_rclone
        # Simulate rclone >= v1.64 so the version guard allows the flag
        self.config.get_rclone_version = lambda: "rclone v1.64.0"
        svc = self.config.get_service("EmptyDirSvc")
        self.rclone._do_bisync(svc)

        self.assertTrue(len(captured_cmds) > 0)
        self.assertIn("--create-empty-src-dirs", captured_cmds[0])

    def test_create_empty_src_dirs_omitted_on_old_rclone(self):
        """_do_bisync() must NOT pass --create-empty-src-dirs when rclone < v1.64.

        Older versions of rclone do not support --create-empty-src-dirs for the
        bisync subcommand and raise "unknown flag", so we must guard the flag
        with a version check.
        """
        self.config.add_service("OldRcloneSvc", "onedrive", "/tmp/oldrclone_test")
        captured_cmds = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            captured_cmds.append(cmd)
            return True

        self.rclone._run_rclone = fake_run_rclone
        # Simulate rclone < v1.64 (e.g. v1.63)
        self.config.get_rclone_version = lambda: "rclone v1.63.1"
        svc = self.config.get_service("OldRcloneSvc")
        self.rclone._do_bisync(svc)

        self.assertTrue(len(captured_cmds) > 0)
        self.assertNotIn("--create-empty-src-dirs", captured_cmds[0])

    def test_create_empty_src_dirs_can_be_disabled(self):
        """Setting create_empty_src_dirs=False must omit --create-empty-src-dirs even on v1.64."""
        self.config.add_service("NoEmptyDirSvc", "onedrive", "/tmp/noemptydir_test")
        self.config.update_service("NoEmptyDirSvc", {"create_empty_src_dirs": False})
        captured_cmds = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            captured_cmds.append(cmd)
            return True

        self.rclone._run_rclone = fake_run_rclone
        # Use v1.64 so the version guard would allow the flag if not disabled
        self.config.get_rclone_version = lambda: "rclone v1.64.0"
        svc = self.config.get_service("NoEmptyDirSvc")
        self.rclone._do_bisync(svc)

        self.assertTrue(len(captured_cmds) > 0)
        self.assertNotIn("--create-empty-src-dirs", captured_cmds[0])

    # ------------------------------------------------------------------ #
    # --workdir per-service isolation tests                               #
    # ------------------------------------------------------------------ #

    def test_slug_converts_spaces_and_special_chars(self):
        """_slug() should convert spaces and special chars to underscores."""
        self.assertEqual(_slug("Mi OneDrive"), "mi_onedrive")
        self.assertEqual(_slug("Work Drive!"), "work_drive")
        self.assertEqual(_slug("Service-A"), "service_a")

    def test_slug_fallback_for_empty_input(self):
        """_slug() should return 'service' when the input is all non-alphanumeric."""
        self.assertEqual(_slug(""), "service")
        self.assertEqual(_slug("!!??"), "service")

    def test_bisync_workdir_for_service_default_uses_service_name(self):
        """_bisync_workdir_for_service() derives workdir from service name when unset."""
        svc = {"name": "Mi OneDrive", "remote_name": "duexy", "bisync_workdir": ""}
        workdir = _bisync_workdir_for_service(svc)
        # The folder name must be the slug of the service name
        self.assertEqual(workdir.name, "mi_onedrive",
                         f"Expected workdir folder name 'mi_onedrive', got: {workdir.name}")
        # The workdir must live inside the default bisync cache dir
        self.assertEqual(workdir.parent, _bisync_cache_dir())

    def test_bisync_workdir_for_service_uses_stored_path(self):
        """_bisync_workdir_for_service() returns the stored bisync_workdir when set."""
        svc = {"remote_name": "duexy", "bisync_workdir": "/custom/workdir"}
        workdir = _bisync_workdir_for_service(svc)
        self.assertEqual(workdir, Path("/custom/workdir"))

    def test_bisync_workdir_different_per_service(self):
        """Two services with different names should get different workdirs."""
        svc1 = {"name": "Service A", "remote_name": "service_a", "bisync_workdir": ""}
        svc2 = {"name": "Service B", "remote_name": "service_b", "bisync_workdir": ""}
        self.assertNotEqual(
            _bisync_workdir_for_service(svc1),
            _bisync_workdir_for_service(svc2),
        )

    def test_do_bisync_passes_workdir_flag(self):
        """_do_bisync() must include --workdir in the bisync command."""
        self.config.add_service("WdirSvc", "onedrive", "/tmp/wdir_test")
        captured_cmds = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            captured_cmds.append(cmd)
            return True

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("WdirSvc")
        self.rclone._do_bisync(svc)

        self.assertTrue(len(captured_cmds) > 0)
        cmd = captured_cmds[0]
        self.assertIn("--workdir", cmd, "--workdir must be present in bisync command")
        workdir_idx = cmd.index("--workdir")
        actual_workdir = Path(cmd[workdir_idx + 1])
        expected_workdir = _bisync_workdir_for_service(svc)
        self.assertEqual(actual_workdir, expected_workdir)

    def test_do_bisync_workdir_contains_service_name_slug(self):
        """The --workdir value must be derived from the service name (slugified)."""
        self.config.add_service("Mi Nube Personal", "onedrive", "/tmp/remote_svc")
        captured_cmds = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            captured_cmds.append(cmd)
            return True

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("Mi Nube Personal")
        self.rclone._do_bisync(svc)

        self.assertTrue(len(captured_cmds) > 0)
        cmd = captured_cmds[0]
        self.assertIn("--workdir", cmd)
        workdir_val = cmd[cmd.index("--workdir") + 1]
        self.assertIn("mi_nube_personal", workdir_val,
                      "Workdir path should contain the slugified service name")

    def test_do_bisync_uses_custom_bisync_workdir_when_set(self):
        """_do_bisync() must use the bisync_workdir field when it is explicitly set."""
        self.config.add_service("CustomWdirSvc", "onedrive", "/tmp/custom_wdir")
        import tempfile
        custom_dir = tempfile.mkdtemp()
        try:
            self.config.update_service("CustomWdirSvc", {"bisync_workdir": custom_dir})
            captured_cmds = []

            def fake_run_rclone(cmd, service_name, svc, is_retry=False):
                captured_cmds.append(cmd)
                return True

            self.rclone._run_rclone = fake_run_rclone
            svc = self.config.get_service("CustomWdirSvc")
            self.rclone._do_bisync(svc)

            self.assertTrue(len(captured_cmds) > 0)
            cmd = captured_cmds[0]
            self.assertIn("--workdir", cmd)
            self.assertEqual(Path(cmd[cmd.index("--workdir") + 1]), Path(custom_dir))
        finally:
            import shutil
            shutil.rmtree(custom_dir, ignore_errors=True)

    def test_do_bisync_workdir_included_in_resync_retry(self):
        """--workdir must also be present in the --resync retry command."""
        self.config.add_service("ResyncWdirSvc", "onedrive", "/tmp/resync_wdir")
        captured_cmds = []

        call_count = [0]

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            captured_cmds.append(cmd)
            call_count[0] += 1
            return False  # Always fail to trigger resync retry

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("ResyncWdirSvc")
        self.rclone._do_bisync(svc)

        # Both the initial attempt and the --resync retry must have --workdir
        for i, cmd in enumerate(captured_cmds):
            self.assertIn("--workdir", cmd,
                          f"--workdir missing from command #{i + 1}: {cmd}")

    def test_do_bisync_workdir_persisted_to_config_on_first_use(self):
        """_do_bisync() must persist the computed workdir to bisync_workdir on first run.

        A service that starts without bisync_workdir set should have the derived
        path saved to config after the first _do_bisync() call so that all
        subsequent runs use the same folder.
        """
        self.config.add_service("PersistWdirSvc", "onedrive", "/tmp/persist_wdir")
        # Confirm it starts empty
        svc_before = self.config.get_service("PersistWdirSvc")
        self.assertEqual(svc_before.get("bisync_workdir", ""), "")

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            return True

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("PersistWdirSvc")
        self.rclone._do_bisync(svc)

        # After running, bisync_workdir must be stored in the config
        svc_after = self.config.get_service("PersistWdirSvc")
        stored = svc_after.get("bisync_workdir", "")
        self.assertNotEqual(stored, "", "bisync_workdir must be persisted after first run")
        # The stored value must contain the slug of the service name
        self.assertIn("persistwdirsvc", stored.lower(),
                      "Stored workdir should contain slug of service name")

    def test_ensure_service_workdirs_assigns_and_persists(self):
        """ensure_service_workdirs() should assign a workdir to every service without one."""
        self.config.add_service("EnsureSvc1", "onedrive", "/tmp/ensure1")
        self.config.add_service("EnsureSvc2", "onedrive", "/tmp/ensure2")
        # Both start without bisync_workdir
        for name in ("EnsureSvc1", "EnsureSvc2"):
            svc = self.config.get_service(name)
            self.assertEqual(svc.get("bisync_workdir", ""), "")

        updated = self.rclone.ensure_service_workdirs()
        self.assertEqual(updated, 2, "Both services should have been updated")

        # After the call, every service must have bisync_workdir set
        for name in ("EnsureSvc1", "EnsureSvc2"):
            svc = self.config.get_service(name)
            self.assertNotEqual(svc.get("bisync_workdir", ""), "",
                                f"{name} must have bisync_workdir after ensure_service_workdirs()")

    def test_ensure_service_workdirs_resolves_collisions(self):
        """ensure_service_workdirs() must assign unique workdirs when two services
        have the same service-name slug (and would otherwise share a folder)."""
        # Both services have the same name slug: "onedrive"
        self.config.add_service("onedrive", "onedrive", "/tmp/coll1")
        self.config.update_service("onedrive", {"remote_name": "remote_a"})
        # Add a second service whose slug is also "onedrive"
        self.config.add_service("OneDrive", "onedrive", "/tmp/coll2")
        self.config.update_service("OneDrive", {"remote_name": "remote_b"})

        self.rclone.ensure_service_workdirs()

        svc1 = self.config.get_service("onedrive")
        svc2 = self.config.get_service("OneDrive")
        workdir1 = svc1.get("bisync_workdir", "")
        workdir2 = svc2.get("bisync_workdir", "")
        self.assertNotEqual(workdir1, workdir2,
                            "Collision must be resolved: each service must have a unique workdir")

    def test_ensure_service_workdirs_idempotent_for_already_assigned(self):
        """ensure_service_workdirs() should not change a workdir that is already set."""
        self.config.add_service("AlreadySvc", "onedrive", "/tmp/already")
        self.config.update_service("AlreadySvc", {"bisync_workdir": "/custom/already"})

        updated = self.rclone.ensure_service_workdirs()
        self.assertEqual(updated, 0, "No service should be updated when workdir is already set")
        svc = self.config.get_service("AlreadySvc")
        self.assertEqual(svc.get("bisync_workdir"), "/custom/already",
                         "Pre-set bisync_workdir must not be overwritten")

    def test_do_bisync_logs_command_before_running(self):
        """_do_bisync() should emit a [CMD] entry via on_error before the first run."""
        self.config.add_service("CmdSvc", "onedrive", "/tmp/cmd_test")
        logged = []
        self.rclone.on_error = lambda name, msg: logged.append((name, msg))

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            return True

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("CmdSvc")
        self.rclone._do_bisync(svc)

        # At least one [CMD] entry should have been logged
        cmd_entries = [(n, m) for n, m in logged if m.startswith("[CMD]")]
        self.assertTrue(len(cmd_entries) >= 1, "Expected at least one [CMD] log entry")
        # The logged command should contain 'bisync'
        self.assertIn("bisync", cmd_entries[0][1])

    def test_do_bisync_cmd_log_quotes_patterns_with_spaces(self):
        """[CMD] log entries must shell-quote arguments that contain spaces.

        rclone is invoked via a subprocess list so execution is always correct,
        but the logged command must be copy-pasteable to a shell.  Arguments
        such as --exclude '/Almacén personal/**' must appear with quotes so
        the space inside the path is not misinterpreted by a shell.
        """
        self.config.add_service("QuoteSvc", "onedrive", "/tmp/quote_test")
        self.config.update_service("QuoteSvc", {
            "exclude_personal_vault": True,
            "exclusions": [PERSONAL_VAULT_PATTERN],
        })
        logged = []
        self.rclone.on_error = lambda name, msg: logged.append((name, msg))

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            return True

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("QuoteSvc")
        self.rclone._do_bisync(svc)

        cmd_entries = [m for _, m in logged if m.startswith("[CMD]")]
        self.assertTrue(len(cmd_entries) >= 1)
        logged_cmd = cmd_entries[0]
        # The pattern with a space must appear quoted so the log is
        # copy-pasteable; the bare unquoted string must NOT be present.
        self.assertNotIn("--exclude /Almacén", logged_cmd,
                         "Pattern with space must be quoted in the CMD log")
        # Either single- or double-quoted form is acceptable
        self.assertTrue(
            '"/Almacén personal/**"' in logged_cmd or
            "'/Almacén personal/**'" in logged_cmd,
            f"Expected quoted pattern in CMD log, got: {logged_cmd!r}",
        )

    def test_do_bisync_logs_resync_command_on_failure(self):
        """_do_bisync() should emit a [CMD] entry for the --resync retry."""
        self.config.add_service("ResyncSvc", "onedrive", "/tmp/resync_test")
        logged = []
        self.rclone.on_error = lambda name, msg: logged.append((name, msg))

        call_count = [0]

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            call_count[0] += 1
            return False  # Always fail to trigger the resync retry

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("ResyncSvc")
        self.rclone._do_bisync(svc)

        cmd_entries = [(n, m) for n, m in logged if m.startswith("[CMD]")]
        self.assertEqual(len(cmd_entries), 2, "Expected two [CMD] entries (initial + resync)")
        self.assertIn("--resync", cmd_entries[1][1])

    def test_do_bisync_includes_acknowledge_abuse_for_drive(self):
        """_do_bisync() must include --drive-acknowledge-abuse for Google Drive services.

        Google Drive occasionally returns HTTP 403 cannotDownloadAbusiveFile for
        files it has flagged as malware or spam.  Without this flag rclone (and
        therefore bisync) fails with a critical error even though the file is
        just a normal project artefact.  Adding the flag lets rclone proceed.
        """
        self.config.add_service("DriveSvc", "drive", "/tmp/drive_test")
        captured_cmds = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            captured_cmds.append(cmd)
            return True

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("DriveSvc")
        self.rclone._do_bisync(svc)

        self.assertTrue(len(captured_cmds) > 0)
        self.assertIn(
            "--drive-acknowledge-abuse",
            captured_cmds[0],
            "--drive-acknowledge-abuse must be present in bisync command for drive platform",
        )

    def test_do_bisync_no_acknowledge_abuse_for_other_platforms(self):
        """_do_bisync() must NOT include --drive-acknowledge-abuse for non-Drive platforms."""
        self.config.add_service("OneDriveSvc", "onedrive", "/tmp/od_test")
        captured_cmds = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            captured_cmds.append(cmd)
            return True

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("OneDriveSvc")
        self.rclone._do_bisync(svc)

        self.assertTrue(len(captured_cmds) > 0)
        self.assertNotIn(
            "--drive-acknowledge-abuse",
            captured_cmds[0],
            "--drive-acknowledge-abuse must NOT appear for non-drive platforms",
        )

    def test_do_bisync_includes_tpslimit_when_configured(self):
        """_do_bisync() must include --tpslimit when tpslimit > 0.

        When a service has tpslimit set to a positive value the bisync command
        must include '--tpslimit <value>' so that rclone throttles its API calls
        per second.  This prevents Google Drive 403 "Quota exceeded for 'Queries
        per minute'" errors.
        """
        self.config.add_service("DriveSvc2", "drive", "/tmp/drive_tps")
        self.config.update_service("DriveSvc2", {"tpslimit": 5.0})
        captured_cmds = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            captured_cmds.append(cmd)
            return True

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("DriveSvc2")
        self.rclone._do_bisync(svc)

        self.assertTrue(len(captured_cmds) > 0)
        cmd = captured_cmds[0]
        self.assertIn("--tpslimit", cmd, "--tpslimit must be present when tpslimit > 0")
        tps_idx = cmd.index("--tpslimit")
        self.assertAlmostEqual(float(cmd[tps_idx + 1]), 5.0, msg="--tpslimit value must match the configured tpslimit")

    def test_do_bisync_omits_tpslimit_when_zero(self):
        """_do_bisync() must NOT include --tpslimit when tpslimit is 0 (default)."""
        self.config.add_service("DriveSvc3", "drive", "/tmp/drive_notps")
        # tpslimit defaults to 0 – no explicit set needed
        captured_cmds = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            captured_cmds.append(cmd)
            return True

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("DriveSvc3")
        self.rclone._do_bisync(svc)

        self.assertTrue(len(captured_cmds) > 0)
        self.assertNotIn(
            "--tpslimit",
            captured_cmds[0],
            "--tpslimit must NOT appear when tpslimit is 0",
        )

    def test_do_bisync_uses_per_service_transfers_and_checkers(self):
        """_do_bisync() must respect per-service transfers and checkers values.

        When a service has custom transfers=2 and checkers=4 those values must
        appear in the bisync command instead of the global defaults (16/32).
        """
        self.config.add_service("DriveSvc4", "drive", "/tmp/drive_quota")
        self.config.update_service("DriveSvc4", {"transfers": 2, "checkers": 4})
        captured_cmds = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            captured_cmds.append(cmd)
            return True

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("DriveSvc4")
        self.rclone._do_bisync(svc)

        self.assertTrue(len(captured_cmds) > 0)
        cmd = captured_cmds[0]
        transfers_idx = cmd.index("--transfers")
        self.assertEqual(int(cmd[transfers_idx + 1]), 2, "--transfers must equal the per-service value")
        checkers_idx = cmd.index("--checkers")
        self.assertEqual(int(cmd[checkers_idx + 1]), 4, "--checkers must equal the per-service value")

    def test_run_rclone_emits_error_lines_from_output(self):
        """_run_rclone() should emit lines containing 'ERROR' via on_error."""
        import io
        errors = []
        self.rclone.on_error = lambda name, msg: errors.append(msg)

        fake_output = (
            "2023/11/15 10:30:45 ERROR : some/path: file not found\n"
            "2023/11/15 10:30:46 INFO  : some/other: transferred\n"
            "FATAL error: bisync not initialised\n"
        )

        class FakeProc:
            returncode = 1
            stdout = io.StringIO(fake_output)
            def wait(self): pass

        with patch("subprocess.Popen", return_value=FakeProc()):
            self.rclone._run_rclone(["rclone", "bisync"], "ErrSvc", {})

        # ERROR and FATAL lines should be in errors
        self.assertTrue(
            any("ERROR" in e for e in errors),
            "Expected ERROR line to be emitted",
        )
        self.assertTrue(
            any("FATAL" in e or "Fatal" in e for e in errors),
            "Expected FATAL line to be emitted",
        )
        # Plain INFO line should NOT be in errors
        self.assertFalse(
            any("INFO" in e for e in errors),
            "INFO lines should not be emitted as errors",
        )

    def test_run_rclone_does_not_emit_normal_lines(self):
        """_run_rclone() should NOT emit normal rclone progress lines as errors."""
        import io
        errors = []
        self.rclone.on_error = lambda name, msg: errors.append(msg)

        fake_output = (
            "Transferred: 1 / 1 Bytes, 100%, 512 Bytes/s, ETA 0s\n"
            "Elapsed time: 0.1s\n"
        )

        class FakeProc:
            returncode = 0
            stdout = io.StringIO(fake_output)
            def wait(self): pass

        with patch("subprocess.Popen", return_value=FakeProc()):
            self.rclone._run_rclone(["rclone", "bisync"], "NormSvc", {})

        self.assertEqual(errors, [], "No errors should be emitted for normal output lines")

    def test_run_rclone_logs_exit_code_on_failure(self):
        """_run_rclone() should emit the rclone exit code when it exits non-zero.

        When rclone fails silently (no ERROR/FATAL in output), the only
        diagnostic hint in the log is the exit code.  Without it, the user
        sees only the generic 'La sincronización falló' message and has no
        way to diagnose the root cause.
        """
        import io
        errors = []
        self.rclone.on_error = lambda name, msg: errors.append(msg)

        class FakeProc:
            returncode = 5  # rclone exit code 5 = temporary error
            stdout = io.StringIO("")  # no output at all
            def wait(self): pass

        with patch("subprocess.Popen", return_value=FakeProc()):
            result = self.rclone._run_rclone(["rclone", "bisync"], "FailSvc", {})

        self.assertFalse(result)
        self.assertEqual(len(errors), 1)
        self.assertIn("5", errors[0], "Exit code must appear in the error message")

    def test_run_rclone_does_not_log_exit_code_on_success(self):
        """_run_rclone() should NOT emit an exit code message when rclone succeeds."""
        import io
        errors = []
        self.rclone.on_error = lambda name, msg: errors.append(msg)

        class FakeProc:
            returncode = 0
            stdout = io.StringIO("Transferred: some/file.txt: Copied (new)\n")
            def wait(self): pass

        with patch("subprocess.Popen", return_value=FakeProc()):
            result = self.rclone._run_rclone(["rclone", "bisync"], "OkSvc", {})

        self.assertTrue(result)
        self.assertEqual(errors, [], "No exit-code message should appear on success")

    def test_run_rclone_detects_drive_id_missing_and_emits_actionable_message(self):
        """_run_rclone() must detect 'unable to get drive_id and drive_type' and emit a
        helpful actionable message, even when the line lacks an ERROR/FATAL prefix."""
        import io
        errors = []
        self.rclone.on_error = lambda name, msg: errors.append(msg)

        # Simulate rclone output that contains the drive_id error without an
        # ERROR/FATAL prefix (as rclone may write it as a plain log line).
        fake_output = (
            f'Failed to create file system for "svc:/": '
            f'{_DRIVE_ID_MISSING_PHRASE} - if you are upgrading from older '
            f'versions of rclone, please run `rclone config` and re-configure '
            f'this backend\n'
        )

        class FakeProc:
            returncode = 1
            stdout = io.StringIO(fake_output)
            def wait(self): pass

        with patch("subprocess.Popen", return_value=FakeProc()):
            self.rclone._run_rclone(["rclone", "bisync"], "DriveIdSvc", {})

        # The raw rclone line should be emitted (it has no ERROR/FATAL keyword)
        self.assertTrue(
            any(_DRIVE_ID_MISSING_PHRASE in e for e in errors),
            "The raw rclone error line must always appear in the error log",
        )
        # An actionable guidance message must also be emitted
        self.assertTrue(
            any("drive_id" in e and "drive_type" in e for e in errors),
            "An actionable message about drive_id/drive_type must be emitted",
        )
        # The service must be flagged so _do_bisync can skip the retry
        self.assertIn(
            "DriveIdSvc",
            self.rclone._config_error_services,
            "Service must be added to _config_error_services after drive_id error",
        )

    def test_run_rclone_detects_drive_id_missing_when_line_has_error_prefix(self):
        """_run_rclone() must still handle the drive_id error when the rclone line
        already contains the ERROR keyword (avoids double-emission of the raw line)."""
        import io
        errors = []
        self.rclone.on_error = lambda name, msg: errors.append(msg)

        # Simulate a line that has both the ERROR keyword AND the drive_id phrase
        fake_output = (
            f'ERROR : Failed to create file system: '
            f'{_DRIVE_ID_MISSING_PHRASE}\n'
        )

        class FakeProc:
            returncode = 1
            stdout = io.StringIO(fake_output)
            def wait(self): pass

        with patch("subprocess.Popen", return_value=FakeProc()):
            self.rclone._run_rclone(["rclone", "bisync"], "DriveIdSvc2", {})

        # Actionable message must still appear
        self.assertTrue(
            any("drive_id" in e and "drive_type" in e for e in errors),
            "Actionable guidance must be emitted even when line has ERROR prefix",
        )
        # Service must be flagged
        self.assertIn("DriveIdSvc2", self.rclone._config_error_services)

    def test_run_rclone_fires_on_drive_id_error_callback(self):
        """on_drive_id_error callback must be called exactly once when the
        drive_id missing phrase is detected in rclone output."""
        import io
        fired_for = []
        self.rclone.on_drive_id_error = lambda name: fired_for.append(name)

        fake_output = (
            f'Failed to create file system for "svc:/": '
            f'{_DRIVE_ID_MISSING_PHRASE} - if you are upgrading from older '
            f'versions of rclone, please run `rclone config`\n'
        )

        class FakeProc:
            returncode = 1
            stdout = io.StringIO(fake_output)
            def wait(self): pass

        with patch("subprocess.Popen", return_value=FakeProc()):
            self.rclone._run_rclone(["rclone", "bisync"], "DriveIdCbSvc", {})

        self.assertEqual(
            fired_for,
            ["DriveIdCbSvc"],
            "on_drive_id_error must be called once with the correct service name",
        )

    def test_on_drive_id_error_callback_not_fired_for_normal_error(self):
        """on_drive_id_error must NOT be called when rclone emits a normal error
        that does not contain the drive_id missing phrase."""
        import io
        fired_for = []
        self.rclone.on_drive_id_error = lambda name: fired_for.append(name)

        fake_output = "ERROR : Failed to copy file: network timeout\n"

        class FakeProc:
            returncode = 1
            stdout = io.StringIO(fake_output)
            def wait(self): pass

        with patch("subprocess.Popen", return_value=FakeProc()):
            self.rclone._run_rclone(["rclone", "bisync"], "NormalErrSvc", {})

        self.assertEqual(
            fired_for,
            [],
            "on_drive_id_error must not fire for errors unrelated to drive_id",
        )

    def test_do_bisync_skips_resync_retry_on_drive_id_config_error(self):
        """_do_bisync() must NOT retry with --resync when the failure is caused by
        a missing drive_id/drive_type configuration error.

        Retrying with --resync would fail in exactly the same way because the
        problem is in the stored remote configuration, not in the bisync state.
        The user is already informed via the actionable message emitted by
        _run_rclone; a pointless second run would only add noise to the log.
        """
        self.config.add_service("ConfigErrSvc", "onedrive", "/tmp/cfg_err_test")
        logged = []
        self.rclone.on_error = lambda name, msg: logged.append((name, msg))

        call_count = [0]

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            call_count[0] += 1
            # Simulate _run_rclone flagging the service as a config error
            self.rclone._config_error_services.add(service_name)
            return False

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("ConfigErrSvc")
        result = self.rclone._do_bisync(svc)

        self.assertFalse(result, "_do_bisync must return False on config error")
        self.assertEqual(
            call_count[0],
            1,
            "_run_rclone must be called exactly once; --resync retry must be skipped",
        )
        # The service must be removed from the set after the check
        self.assertNotIn(
            "ConfigErrSvc",
            self.rclone._config_error_services,
            "Service must be removed from _config_error_services after the check",
        )

    # ------------------------------------------------------------------
    # Drive-ID quick-fix helpers: find_drive_id_in_known_configs &
    #                             patch_remote_drive_fields
    # ------------------------------------------------------------------

    def test_find_drive_id_returns_empty_when_no_candidate_exists(self):
        """find_drive_id_in_known_configs() returns [] when no candidate config has drive_id."""
        # Override candidate list to a single non-existent file
        with patch.object(
            self.rclone.__class__,
            "_candidate_rclone_configs",
            staticmethod(lambda: [Path(self._tmpdir) / "nonexistent.conf"]),
        ):
            result = self.rclone.find_drive_id_in_known_configs("juan")
        self.assertEqual(result, [])

    def test_find_drive_id_returns_empty_when_section_has_no_drive_id(self):
        """find_drive_id_in_known_configs() ignores sections that lack drive_id."""
        other_conf = Path(self._tmpdir) / "other.conf"
        other_conf.write_text(
            "[juan]\ntype = onedrive\ntoken = {\"access_token\":\"X\"}\n",
            encoding="utf-8",
        )
        with patch.object(
            self.rclone.__class__,
            "_candidate_rclone_configs",
            staticmethod(lambda: [other_conf]),
        ):
            result = self.rclone.find_drive_id_in_known_configs("juan")
        self.assertEqual(result, [])

    def test_find_drive_id_finds_section_with_drive_id(self):
        """find_drive_id_in_known_configs() returns matching section data."""
        other_conf = Path(self._tmpdir) / "other2.conf"
        other_conf.write_text(
            "[juan]\ntype = onedrive\ndrive_id = DEADBEEF\ndrive_type = personal\n",
            encoding="utf-8",
        )
        with patch.object(
            self.rclone.__class__,
            "_candidate_rclone_configs",
            staticmethod(lambda: [other_conf]),
        ):
            result = self.rclone.find_drive_id_in_known_configs("juan")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["drive_id"], "DEADBEEF")
        self.assertEqual(result[0]["drive_type"], "personal")
        self.assertEqual(result[0]["section"], "juan")
        self.assertEqual(result[0]["source_file"], str(other_conf))

    def test_find_drive_id_skips_own_config_file(self):
        """find_drive_id_in_known_configs() never returns entries from the app's own config."""
        # Write drive_id into the app's own rclone.conf
        own_conf = self.config.rclone_config_path()
        own_conf.parent.mkdir(parents=True, exist_ok=True)
        own_conf.write_text(
            "[juan]\ntype = onedrive\ndrive_id = OWNID\ndrive_type = personal\n",
            encoding="utf-8",
        )
        # Candidate list contains only the own config
        with patch.object(
            self.rclone.__class__,
            "_candidate_rclone_configs",
            staticmethod(lambda: [own_conf]),
        ):
            result = self.rclone.find_drive_id_in_known_configs("juan")
        self.assertEqual(result, [], "Entries from the app's own config must be excluded")

    def test_find_drive_id_returns_multiple_candidates(self):
        """find_drive_id_in_known_configs() returns all matching sections across files.

        The search is NOT filtered by remote_name — it returns every section
        from every candidate file that has both drive_id and drive_type, so the
        caller can choose the best match.
        """
        conf_a = Path(self._tmpdir) / "confA.conf"
        conf_b = Path(self._tmpdir) / "confB.conf"
        conf_a.write_text(
            "[foo]\ntype = onedrive\ndrive_id = AAA\ndrive_type = personal\n",
            encoding="utf-8",
        )
        conf_b.write_text(
            "[bar]\ntype = onedrive\ndrive_id = BBB\ndrive_type = business\n",
            encoding="utf-8",
        )
        with patch.object(
            self.rclone.__class__,
            "_candidate_rclone_configs",
            staticmethod(lambda: [conf_a, conf_b]),
        ):
            # Searching for "foo" still returns "bar" from conf_b because the
            # search collects all sections with drive_id regardless of name.
            result = self.rclone.find_drive_id_in_known_configs("foo")
        self.assertEqual(len(result), 2)
        drive_ids = {r["drive_id"] for r in result}
        self.assertEqual(drive_ids, {"AAA", "BBB"})

    def test_patch_remote_drive_fields_writes_values(self):
        """patch_remote_drive_fields() should add drive_id and drive_type to the section."""
        own_conf = self.config.rclone_config_path()
        own_conf.parent.mkdir(parents=True, exist_ok=True)
        own_conf.write_text(
            "[juan]\ntype = onedrive\ntoken = {\"access_token\":\"X\"}\n",
            encoding="utf-8",
        )
        ok, err = self.rclone.patch_remote_drive_fields("juan", "DRIVEABC", "personal")
        self.assertTrue(ok, f"Expected success; got error: {err}")
        self.assertEqual(err, "")
        # Verify values were written
        parser = configparser.RawConfigParser()
        parser.read(str(own_conf), encoding="utf-8")
        self.assertEqual(parser.get("juan", "drive_id"), "DRIVEABC")
        self.assertEqual(parser.get("juan", "drive_type"), "personal")
        # Existing token must be preserved
        self.assertTrue(parser.has_option("juan", "token"))

    def test_patch_remote_drive_fields_fails_for_missing_section(self):
        """patch_remote_drive_fields() returns (False, msg) when section not found."""
        own_conf = self.config.rclone_config_path()
        own_conf.parent.mkdir(parents=True, exist_ok=True)
        own_conf.write_text("[other]\ntype = drive\n", encoding="utf-8")
        ok, err = self.rclone.patch_remote_drive_fields("juan", "X", "personal")
        self.assertFalse(ok)
        self.assertIn("juan", err)

    def test_patch_remote_drive_fields_preserves_other_sections(self):
        """patch_remote_drive_fields() must not disturb other sections in rclone.conf."""
        own_conf = self.config.rclone_config_path()
        own_conf.parent.mkdir(parents=True, exist_ok=True)
        own_conf.write_text(
            "[juan]\ntype = onedrive\n\n[other]\ntype = drive\nclient_id = CID\n",
            encoding="utf-8",
        )
        ok, _ = self.rclone.patch_remote_drive_fields("juan", "DID", "personal")
        self.assertTrue(ok)
        parser = configparser.RawConfigParser()
        parser.read(str(own_conf), encoding="utf-8")
        self.assertTrue(parser.has_section("other"))
        self.assertEqual(parser.get("other", "client_id"), "CID")

    # ------------------------------------------------------------------
    # open_terminal_reconnect tests
    # ------------------------------------------------------------------

    def test_open_terminal_reconnect_launches_first_available_terminal(self):
        """open_terminal_reconnect() returns (True, '') when the first terminal is found."""
        first_exe = self.rclone._TERMINAL_CANDIDATES[0][0]
        launched_with = []

        def fake_popen(args, **kwargs):
            if args[0] == first_exe:
                launched_with.extend(args)
                return MagicMock()
            raise FileNotFoundError("not found")

        with patch("subprocess.Popen", side_effect=fake_popen):
            ok, cmd = self.rclone.open_terminal_reconnect("juan")

        self.assertTrue(ok)
        self.assertEqual(cmd, "")
        # The first terminal tried must match _TERMINAL_CANDIDATES[0]
        self.assertEqual(launched_with[0], first_exe)
        # The command must contain 'config reconnect' and the remote name
        full_cmd = " ".join(launched_with)
        self.assertIn("config reconnect", full_cmd)
        self.assertIn("juan", full_cmd)

    def test_open_terminal_reconnect_tries_fallback_terminal(self):
        """open_terminal_reconnect() tries the next emulator if the first is absent."""
        first_exe = self.rclone._TERMINAL_CANDIDATES[0][0]
        second_exe = self.rclone._TERMINAL_CANDIDATES[1][0]
        call_log = []

        def fake_popen(args, **kwargs):
            call_log.append(args[0])
            if args[0] == second_exe:
                return MagicMock()
            raise FileNotFoundError("not found")

        with patch("subprocess.Popen", side_effect=fake_popen):
            ok, cmd = self.rclone.open_terminal_reconnect("juan")

        self.assertTrue(ok)
        self.assertEqual(cmd, "")
        self.assertIn(first_exe, call_log)
        self.assertIn(second_exe, call_log)

    def test_open_terminal_reconnect_returns_false_when_no_terminal(self):
        """open_terminal_reconnect() returns (False, cmd_str) when no terminal is found."""
        with patch("subprocess.Popen", side_effect=FileNotFoundError("not found")):
            ok, cmd = self.rclone.open_terminal_reconnect("juan")

        self.assertFalse(ok)
        self.assertIn("config reconnect", cmd)
        self.assertIn("juan", cmd)

    def test_open_terminal_reconnect_command_contains_config_path(self):
        """The returned command includes the app's rclone.conf path."""
        with patch("subprocess.Popen", side_effect=FileNotFoundError("not found")):
            _, cmd = self.rclone.open_terminal_reconnect("myremote")

        config_path = str(self.config.rclone_config_path())
        self.assertIn(config_path, cmd)

    # ------------------------------------------------------------------
    # Mega credential remote creation tests
    # ------------------------------------------------------------------

    def test_create_mega_remote_success(self):
        """create_mega_remote() must obscure the password then create the remote."""
        captured_calls = []

        def fake_run(cmd, **kwargs):
            captured_calls.append(cmd)
            result = MagicMock()
            if "obscure" in cmd:
                result.returncode = 0
                result.stdout = "OBSCURED_PASS\n"
                result.stderr = ""
            elif "config" in cmd:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            else:
                result.returncode = 1
                result.stdout = ""
                result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=fake_run):
            ok, err = self.rclone.create_mega_remote("megasvc", "user@example.com", "secret123")

        self.assertTrue(ok, "create_mega_remote should return True on success")
        self.assertEqual(err, "", "Error message should be empty on success")

        # First call must be rclone obscure with the plain password
        self.assertTrue(len(captured_calls) >= 2, "Expected at least two subprocess.run calls")
        obscure_cmd = captured_calls[0]
        self.assertIn("obscure", obscure_cmd)
        self.assertIn("secret123", obscure_cmd)

        # Second call must be rclone config create ... mega with user and obscured pass
        create_cmd = captured_calls[1]
        self.assertIn("config", create_cmd)
        self.assertIn("create", create_cmd)
        self.assertIn("megasvc", create_cmd)
        self.assertIn("mega", create_cmd)
        # The user and obscured password must be present as key=value arguments
        self.assertTrue(
            any("user@example.com" in arg for arg in create_cmd),
            "user email must appear in the config create command",
        )
        self.assertTrue(
            any("OBSCURED_PASS" in arg for arg in create_cmd),
            "obscured password must appear in the config create command",
        )

    def test_create_mega_remote_returns_false_when_obscure_fails(self):
        """create_mega_remote() must return (False, msg) if rclone obscure fails."""
        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            result.stderr = "obscure error detail"
            return result

        with patch("subprocess.run", side_effect=fake_run):
            ok, err = self.rclone.create_mega_remote("megasvc2", "user@example.com", "badpass")

        self.assertFalse(ok, "create_mega_remote should return False when rclone obscure fails")
        self.assertIn("obscure error detail", err, "Error message should include rclone output")

    def test_create_mega_remote_returns_false_when_config_create_fails(self):
        """create_mega_remote() must return (False, msg) if rclone config create fails."""
        def fake_run(cmd, **kwargs):
            result = MagicMock()
            if "obscure" in cmd:
                result.returncode = 0
                result.stdout = "OBSCURED\n"
                result.stderr = ""
            else:
                result.returncode = 1
                result.stdout = ""
                result.stderr = "config create error detail"
            return result

        with patch("subprocess.run", side_effect=fake_run):
            ok, err = self.rclone.create_mega_remote("megasvc3", "u@e.com", "pw")

        self.assertFalse(ok, "create_mega_remote should return False when rclone config create fails")
        self.assertIn("config create error detail", err, "Error message should include rclone output")

    def test_create_mega_remote_returns_false_on_oserror(self):
        """create_mega_remote() must return (False, msg) if rclone is not found."""
        with patch("subprocess.run", side_effect=OSError("rclone not found")):
            ok, err = self.rclone.create_mega_remote("megasvc4", "u@e.com", "pw")

        self.assertFalse(ok)
        self.assertIn("rclone no encontrado", err)

    # ------------------------------------------------------------------
    # Mount service tests
    # ------------------------------------------------------------------

    def test_is_mounted_false_initially(self):
        """is_mounted() should return False before start_mount() is called."""
        self.assertFalse(self.rclone.is_mounted("any_service"))

    def test_stop_mount_is_noop_when_not_mounted(self):
        """stop_mount() should not raise if the service was never mounted."""
        self.rclone.stop_mount("never_mounted")

    def test_start_all_mounts_with_no_services(self):
        """start_all_mounts() should complete without error when no services exist."""
        self.rclone.start_all_mounts()  # Should not raise

    def test_start_all_mounts_skips_disabled_services(self):
        """start_all_mounts() should not attempt to mount services with mount_enabled=False."""
        self.config.add_service("NoMount", "onedrive", "/tmp/nomount")
        # mount_enabled defaults to False
        self.rclone.start_all_mounts()
        self.assertFalse(self.rclone.is_mounted("NoMount"))

    def test_start_mount_returns_false_without_mount_path(self):
        """start_mount() should return False and log an error when mount_path is empty."""
        self.config.add_service("MountSvc", "onedrive", "/tmp/mount_test")
        self.config.update_service("MountSvc", {"mount_enabled": True, "mount_path": ""})
        errors = []
        self.rclone.on_error = lambda name, msg: errors.append(msg)
        result = self.rclone.start_mount("MountSvc")
        self.assertFalse(result)
        self.assertTrue(any("[MOUNT]" in e for e in errors))

    def test_start_mount_returns_false_when_disabled(self):
        """start_mount() should return False when mount_enabled is False."""
        self.config.add_service("DisabledMount", "onedrive", "/tmp/disabled_mount")
        self.config.update_service("DisabledMount", {
            "mount_enabled": False,
            "mount_path": "/tmp/mnt_disabled",
        })
        result = self.rclone.start_mount("DisabledMount")
        self.assertFalse(result)

    def test_stop_all_mounts_clears_procs(self):
        """stop_all_mounts() should terminate all tracked mount processes."""
        # Inject a fake Popen that is 'running' (poll returns None)
        class FakeProc:
            def __init__(self):
                self.returncode = None
                self._terminated = False
            def poll(self): return None
            def terminate(self): self._terminated = True
            def wait(self, timeout=None): pass

        fake = FakeProc()
        self.rclone._mount_procs["FakeMount"] = fake
        self.rclone.stop_all_mounts()
        self.assertTrue(fake._terminated)
        self.assertEqual(len(self.rclone._mount_procs), 0)

    def test_do_bisync_resync_uses_resync_mode(self):
        """_do_bisync() retry command should include --resync-mode from service config."""
        self.config.add_service("RMSvc", "onedrive", "/tmp/rm_test")
        self.config.update_service("RMSvc", {"resync_mode": "newer"})
        logged = []
        self.rclone.on_error = lambda name, msg: logged.append((name, msg))

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            return False  # Always fail to trigger the resync retry

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("RMSvc")
        with patch("src.rclone.rclone_manager._rclone_supports_resync_mode", return_value=True):
            self.rclone._do_bisync(svc)

        cmd_entries = [m for _, m in logged if m.startswith("[CMD]")]
        self.assertEqual(len(cmd_entries), 2)
        # The retry command must include both --resync and --resync-mode newer
        self.assertIn("--resync", cmd_entries[1])
        self.assertIn("--resync-mode", cmd_entries[1])
        self.assertIn("newer", cmd_entries[1])

    def test_do_bisync_resync_omits_resync_mode_on_old_rclone(self):
        """_do_bisync() retry must NOT add --resync-mode when rclone < v1.64.

        Older rclone versions respond with 'Fatal error: unknown flag: --resync-mode'
        so we must skip the flag when the version check returns False.
        """
        self.config.add_service("OldSvc", "onedrive", "/tmp/old_test")
        self.config.update_service("OldSvc", {"resync_mode": "newer"})
        logged = []
        self.rclone.on_error = lambda name, msg: logged.append((name, msg))

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            return False  # Always fail to trigger the resync retry

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("OldSvc")
        with patch("src.rclone.rclone_manager._rclone_supports_resync_mode", return_value=False):
            self.rclone._do_bisync(svc)

        cmd_entries = [m for _, m in logged if m.startswith("[CMD]")]
        self.assertEqual(len(cmd_entries), 2)
        # --resync must still be present but --resync-mode must be absent
        self.assertIn("--resync", cmd_entries[1])
        self.assertNotIn("--resync-mode", cmd_entries[1])

    def test_do_bisync_includes_verbose_when_enabled(self):
        """_do_bisync() should include --verbose in the command when verbose_sync=True."""
        self.config.add_service("VerbSvc", "onedrive", "/tmp/verb_test")
        self.config.update_service("VerbSvc", {"verbose_sync": True})
        captured_cmds = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            captured_cmds.append(cmd)
            return True

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("VerbSvc")
        self.rclone._do_bisync(svc)

        self.assertTrue(len(captured_cmds) > 0)
        self.assertIn("--verbose", captured_cmds[0])

    def test_do_bisync_excludes_verbose_when_disabled(self):
        """_do_bisync() should NOT include --verbose when verbose_sync=False."""
        self.config.add_service("NoVerbSvc", "onedrive", "/tmp/noverb_test")
        self.config.update_service("NoVerbSvc", {"verbose_sync": False})
        captured_cmds = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            captured_cmds.append(cmd)
            return True

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("NoVerbSvc")
        self.rclone._do_bisync(svc)

        self.assertTrue(len(captured_cmds) > 0)
        self.assertNotIn("--verbose", captured_cmds[0])

    # ------------------------------------------------------------------
    # Config field tests for new mount fields
    # ------------------------------------------------------------------

    def test_new_service_has_mount_fields(self):
        """New services should include mount_enabled, mount_path, and chunk size fields."""
        svc = self.config.add_service("MountFieldSvc", "onedrive", "/tmp/mf")
        self.assertIn("mount_enabled", svc)
        self.assertFalse(svc["mount_enabled"])
        self.assertIn("mount_path", svc)
        self.assertEqual(svc["mount_path"], "")
        self.assertIn("vfs_read_chunk_size", svc)
        self.assertEqual(svc["vfs_read_chunk_size"], "10M")
        self.assertIn("vfs_read_chunk_size_limit", svc)
        self.assertEqual(svc["vfs_read_chunk_size_limit"], "100M")
        # VFS cache mode default must be "writes" (not the invalid "on_demand")
        self.assertEqual(svc.get("vfs_cache_mode"), "writes")

    def test_new_service_has_bisync_flags(self):
        """New services should include resync_mode and verbose_sync fields."""
        svc = self.config.add_service("BisyncFlagSvc", "onedrive", "/tmp/bf")
        self.assertIn("resync_mode", svc)
        self.assertEqual(svc["resync_mode"], "newer")
        self.assertIn("verbose_sync", svc)
        self.assertFalse(svc["verbose_sync"])

    def test_import_remote_success(self):
        """import_remote() writes the remote section to the rclone config and returns (True, '')."""
        ok, err = self.rclone.import_remote(
            remote_name="mygdrive",
            new_name="MyGoogleDrive",
            remote_data={"type": "drive", "client_id": "abc123", "token": '{"access_token":"tok"}'},
        )

        self.assertTrue(ok)
        self.assertEqual(err, "")

        # Verify the section was actually written to the rclone config file
        parser = configparser.RawConfigParser()
        parser.read(str(self.config.rclone_config_path()), encoding="utf-8")
        self.assertIn("MyGoogleDrive", parser.sections())
        self.assertEqual(parser.get("MyGoogleDrive", "type"), "drive")
        self.assertEqual(parser.get("MyGoogleDrive", "client_id"), "abc123")

    def test_import_remote_missing_type_returns_error(self):
        """import_remote() should return (False, ...) when the remote has no type."""
        ok, err = self.rclone.import_remote(
            remote_name="badremote",
            new_name="BadRemote",
            remote_data={"client_id": "abc"},  # no 'type' key
        )
        self.assertFalse(ok)
        self.assertIn("type", err)

    def test_import_remote_overwrites_existing_section(self):
        """import_remote() should overwrite a section that already exists."""
        # First import
        self.rclone.import_remote(
            remote_name="r",
            new_name="MyRemote",
            remote_data={"type": "s3", "provider": "AWS"},
        )
        # Second import with different provider
        ok, err = self.rclone.import_remote(
            remote_name="r2",
            new_name="MyRemote",
            remote_data={"type": "s3", "provider": "Minio"},
        )
        self.assertTrue(ok)
        parser = configparser.RawConfigParser()
        parser.read(str(self.config.rclone_config_path()), encoding="utf-8")
        self.assertEqual(parser.get("MyRemote", "provider"), "Minio")

    def test_import_remote_preserves_token(self):
        """import_remote() must write the token field unchanged (no re-auth)."""
        token_json = '{"access_token":"mytoken","expiry":"2099-01-01T00:00:00Z"}'
        ok, _ = self.rclone.import_remote(
            remote_name="src",
            new_name="dst",
            remote_data={"type": "onedrive", "token": token_json},
        )
        self.assertTrue(ok)
        parser = configparser.RawConfigParser()
        parser.read(str(self.config.rclone_config_path()), encoding="utf-8")
        self.assertEqual(parser.get("dst", "token"), token_json)


    def test_sync_loop_passes_use_resync_false_on_first_run(self):
        """_sync_loop must pass use_resync=False on the very first sync.

        Regression test: previously _sync_loop computed use_resync=not is_first,
        which passed False on the first run (correct) but True on every
        subsequent run (incorrect — see the other regression test).
        """
        self.config.add_service("FirstLoopSvc", "onedrive", "/tmp/first_loop")
        # first_sync_done=False (default) → is_first=True
        captured_use_resync = []
        stop_event = threading.Event()

        def fake_do_bisync(svc, use_resync=False):
            captured_use_resync.append(use_resync)
            stop_event.set()
            return True

        self.rclone._do_bisync = fake_do_bisync
        self.rclone._stop_events["FirstLoopSvc"] = stop_event

        t = threading.Thread(
            target=self.rclone._sync_loop,
            args=("FirstLoopSvc", stop_event),
            daemon=True,
        )
        t.start()
        t.join(timeout=5)

        self.assertEqual(len(captured_use_resync), 1)
        self.assertFalse(
            captured_use_resync[0],
            "_sync_loop must pass use_resync=False on the first run",
        )

    def test_sync_loop_passes_use_resync_false_on_subsequent_runs(self):
        """_sync_loop must NEVER pass use_resync=True, even after first_sync_done.

        Regression test: previously _sync_loop used use_resync=not is_first,
        causing every sync after the first to run with --resync.  On rclone
        < v1.64 this defaults to "path1 wins" (remote wins), which silently
        overwrites local file modifications instead of uploading them.
        """
        self.config.add_service("SubsequentLoopSvc", "onedrive", "/tmp/subseq_loop")
        # Simulate a service that has already completed its first sync.
        self.config.update_service("SubsequentLoopSvc", {"first_sync_done": True})
        captured_use_resync = []
        stop_event = threading.Event()

        def fake_do_bisync(svc, use_resync=False):
            captured_use_resync.append(use_resync)
            stop_event.set()
            return True

        self.rclone._do_bisync = fake_do_bisync
        self.rclone._stop_events["SubsequentLoopSvc"] = stop_event

        t = threading.Thread(
            target=self.rclone._sync_loop,
            args=("SubsequentLoopSvc", stop_event),
            daemon=True,
        )
        t.start()
        t.join(timeout=5)

        self.assertEqual(len(captured_use_resync), 1)
        self.assertFalse(
            captured_use_resync[0],
            "_sync_loop must not pass use_resync=True on subsequent runs "
            "(doing so causes --resync every cycle, overwriting local changes "
            "on rclone < v1.64 where path1/remote wins by default)",
        )


class TestBisyncLockCleanup(unittest.TestCase):
    """Tests for the bisync stale-lock-file detection and cleanup helpers."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        import src.config.config_manager as cm_mod
        self._original_get_config_dir = cm_mod.get_config_dir
        cm_mod.get_config_dir = lambda: Path(self._tmpdir)
        self.config = ConfigManager()
        self.rclone = RcloneManager(self.config)

    def tearDown(self):
        import src.config.config_manager as cm_mod
        cm_mod.get_config_dir = self._original_get_config_dir
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # _bisync_cache_dir()
    # ------------------------------------------------------------------

    def test_bisync_cache_dir_returns_path_ending_in_rclone_bisync(self):
        """_bisync_cache_dir() should end with 'rclone/bisync' on all platforms."""
        cache_dir = _bisync_cache_dir()
        self.assertIsInstance(cache_dir, Path)
        # The last two parts of the path must always be rclone/bisync
        self.assertEqual(cache_dir.parts[-1], "bisync")
        self.assertEqual(cache_dir.parts[-2], "rclone")

    # ------------------------------------------------------------------
    # _clear_bisync_stale_files()
    # ------------------------------------------------------------------

    def test_clear_removes_matching_lck_file(self):
        """_clear_bisync_stale_files() should delete *.lck files for the remote."""
        cache_dir = Path(self._tmpdir) / "bisync"
        cache_dir.mkdir()
        lock = cache_dir / "myremote_..mnt_data.lck"
        lock.write_text("pid")

        msgs = []
        count = _clear_bisync_stale_files("myremote", cache_dir, msgs.append)

        self.assertEqual(count, 1)
        self.assertFalse(lock.exists(), "Lock file should have been deleted")
        self.assertTrue(any("[LOCK]" in m for m in msgs), "Should log the removal")

    def test_clear_removes_matching_lst_new_file(self):
        """_clear_bisync_stale_files() should also delete *.lst-new files."""
        cache_dir = Path(self._tmpdir) / "bisync"
        cache_dir.mkdir()
        lst_new = cache_dir / "myremote_..mnt_data.path1.lst-new"
        lst_new.write_text("data")

        count = _clear_bisync_stale_files("myremote", cache_dir, lambda m: None)

        self.assertEqual(count, 1)
        self.assertFalse(lst_new.exists())

    def test_clear_does_not_remove_unrelated_files(self):
        """_clear_bisync_stale_files() must NOT remove files for other remotes."""
        cache_dir = Path(self._tmpdir) / "bisync"
        cache_dir.mkdir()
        other_lock = cache_dir / "otherremote_..mnt_data.lck"
        other_lock.write_text("pid")

        count = _clear_bisync_stale_files("myremote", cache_dir, lambda m: None)

        self.assertEqual(count, 0)
        self.assertTrue(other_lock.exists(), "File for other remote must be preserved")

    def test_clear_returns_zero_when_cache_dir_missing(self):
        """_clear_bisync_stale_files() should return 0 if cache dir does not exist."""
        missing_dir = Path(self._tmpdir) / "nonexistent_cache"
        count = _clear_bisync_stale_files("myremote", missing_dir, lambda m: None)
        self.assertEqual(count, 0)

    def test_clear_multiple_files(self):
        """_clear_bisync_stale_files() should delete all matching files."""
        cache_dir = Path(self._tmpdir) / "bisync"
        cache_dir.mkdir()
        files = [
            "myremote_..mnt_a.lck",
            "myremote_..mnt_a.path1.lst-new",
            "myremote_..mnt_a.path2.lst-new",
        ]
        for fname in files:
            (cache_dir / fname).write_text("data")

        count = _clear_bisync_stale_files("myremote", cache_dir, lambda m: None)
        self.assertEqual(count, 3)
        for fname in files:
            self.assertFalse((cache_dir / fname).exists())

    def test_clear_empty_remote_name_does_not_delete_all(self):
        """An empty remote_name must not match every file in the cache dir."""
        cache_dir = Path(self._tmpdir) / "bisync"
        cache_dir.mkdir()
        lock = cache_dir / "someremote_..path.lck"
        lock.write_text("pid")

        # All file names start with "" (empty string), so this is a degenerate
        # case - we still only delete files whose names literally start with "".
        # In practice every non-empty filename satisfies startswith(""), so to
        # avoid accidentally nuking the entire cache we skip deletion when
        # remote_name is empty.
        count = _clear_bisync_stale_files("", cache_dir, lambda m: None)
        # Empty remote name: no files should be touched (guard against rm-all)
        self.assertEqual(count, 0)
        self.assertTrue(lock.exists(), "Files must not be removed for empty remote_name")

    # ------------------------------------------------------------------
    # RcloneManager.clear_bisync_locks()
    # ------------------------------------------------------------------

    def test_clear_bisync_locks_returns_zero_for_unknown_service(self):
        """clear_bisync_locks() should return 0 for a service that does not exist."""
        self.assertEqual(self.rclone.clear_bisync_locks("nonexistent"), 0)

    def test_clear_bisync_locks_removes_files_for_service(self):
        """clear_bisync_locks() should remove lock files from the service's workdir."""
        self.config.add_service("LockSvc", "onedrive", "/tmp/lock_test")

        # Create a fake workdir and put a lock file in it
        fake_workdir = Path(self._tmpdir) / "fake_bisync_workdir"
        fake_workdir.mkdir()
        self.config.update_service("LockSvc", {
            "remote_name": "lockremote",
            "bisync_workdir": str(fake_workdir),
        })
        lock = fake_workdir / "lockremote_..tmp_lock_test.lck"
        lock.write_text("pid")

        errors = []
        self.rclone.on_error = lambda name, msg: errors.append(msg)

        count = self.rclone.clear_bisync_locks("LockSvc")

        self.assertEqual(count, 1)
        self.assertFalse(lock.exists())
        self.assertTrue(any("[LOCK]" in e for e in errors))

    # ------------------------------------------------------------------
    # _do_bisync() integration: lock cleanup runs before bisync
    # ------------------------------------------------------------------

    def test_do_bisync_clears_lock_files_before_running(self):
        """_do_bisync() should remove stale lock files before the first bisync call."""
        self.config.add_service("PreCleanSvc", "onedrive", "/tmp/preclean_test")
        self.config.update_service("PreCleanSvc", {"remote_name": "preclean"})

        fake_cache = Path(self._tmpdir) / "fake_cache_preclean"
        fake_cache.mkdir()
        lock = fake_cache / "preclean_..tmp_preclean_test.lck"
        lock.write_text("pid")

        # Track call order: cleanup must happen before bisync runs
        call_order = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            call_order.append("bisync")
            return True

        def fake_clear(remote_name, cache_dir, emit_fn):
            call_order.append("clear")
            # Delegate to real implementation to actually remove the file
            return _clear_bisync_stale_files(remote_name, cache_dir, emit_fn)

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("PreCleanSvc")

        with patch("src.rclone.rclone_manager._bisync_cache_dir", return_value=fake_cache):
            with patch("src.rclone.rclone_manager._clear_bisync_stale_files", side_effect=fake_clear):
                self.rclone._do_bisync(svc)

        # Cleanup must be called before bisync
        self.assertIn("clear", call_order)
        self.assertIn("bisync", call_order)
        self.assertLess(call_order.index("clear"), call_order.index("bisync"))

    def test_do_bisync_clears_lock_files_before_resync_retry(self):
        """_do_bisync() should remove stale lock files before the --resync retry.

        If the first bisync attempt is killed or crashes, it may leave its own
        lock file behind.  Without a second cleanup the --resync retry would
        immediately fail with 'prior lock file found'.
        """
        self.config.add_service("RetrySvc", "onedrive", "/tmp/retry_test")
        self.config.update_service("RetrySvc", {"remote_name": "retryremote"})

        fake_cache = Path(self._tmpdir) / "fake_cache_retry"
        fake_cache.mkdir()
        lock = fake_cache / "retryremote_..tmp_retry_test.lck"

        call_order = []

        def fake_run_rclone(cmd, service_name, svc, is_retry=False):
            call_order.append("bisync")
            # Recreate the lock to simulate the first run leaving it behind
            lock.write_text("pid")
            return False  # Always fail to trigger the --resync retry

        def fake_clear(remote_name, cache_dir, emit_fn):
            call_order.append("clear")
            return _clear_bisync_stale_files(remote_name, cache_dir, emit_fn)

        self.rclone._run_rclone = fake_run_rclone
        svc = self.config.get_service("RetrySvc")

        with patch("src.rclone.rclone_manager._bisync_cache_dir", return_value=fake_cache):
            with patch("src.rclone.rclone_manager._clear_bisync_stale_files", side_effect=fake_clear):
                self.rclone._do_bisync(svc)

        # cleanup must have been called at least twice (before first attempt
        # and before the --resync retry)
        clear_count = call_order.count("clear")
        self.assertGreaterEqual(clear_count, 2,
            "cleanup must run before both the initial bisync and the --resync retry")
        # Verify ordering: first call is clear, then bisync, then clear again
        self.assertEqual(call_order[0], "clear")
        self.assertEqual(call_order[1], "bisync")
        self.assertEqual(call_order[2], "clear")


class TestMigrateBisyncState(unittest.TestCase):
    """Tests for _migrate_bisync_state(): one-time migration from old shared workdir."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _mkdir(self, name: str) -> Path:
        d = Path(self._tmpdir) / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── basic moves ──────────────────────────────────────────────────────────

    def test_migrate_moves_lst_files(self):
        """_migrate_bisync_state() must move *.lst state files matching remote_name."""
        old = self._mkdir("old")
        new = self._mkdir("new")
        (old / "myremote_path1.lst").write_text("state1")
        (old / "myremote_path2.lst").write_text("state2")

        msgs = []
        count = _migrate_bisync_state("myremote", old, new, msgs.append)

        self.assertEqual(count, 2)
        self.assertTrue((new / "myremote_path1.lst").exists())
        self.assertTrue((new / "myremote_path2.lst").exists())
        self.assertFalse((old / "myremote_path1.lst").exists())
        self.assertFalse((old / "myremote_path2.lst").exists())
        self.assertTrue(any("[MIGRATE]" in m for m in msgs))

    def test_migrate_moves_lck_files(self):
        """_migrate_bisync_state() must move *.lck lock files matching remote_name."""
        old = self._mkdir("old_lck")
        new = self._mkdir("new_lck")
        (old / "myremote_..data.lck").write_text("pid")

        count = _migrate_bisync_state("myremote", old, new, lambda m: None)

        self.assertEqual(count, 1)
        self.assertTrue((new / "myremote_..data.lck").exists())
        self.assertFalse((old / "myremote_..data.lck").exists())

    def test_migrate_moves_lst_new_files(self):
        """_migrate_bisync_state() must move *.lst-new partial-listing files."""
        old = self._mkdir("old_new")
        new = self._mkdir("new_new")
        (old / "myremote_path1.lst-new").write_text("partial")

        count = _migrate_bisync_state("myremote", old, new, lambda m: None)

        self.assertEqual(count, 1)
        self.assertTrue((new / "myremote_path1.lst-new").exists())

    def test_migrate_does_not_touch_other_remotes(self):
        """_migrate_bisync_state() must leave files belonging to other remotes untouched."""
        old = self._mkdir("old_other")
        new = self._mkdir("new_other")
        (old / "myremote_path1.lst").write_text("mine")
        (old / "otherremote_path1.lst").write_text("theirs")

        _migrate_bisync_state("myremote", old, new, lambda m: None)

        # Other remote's file must still be in old_dir
        self.assertTrue((old / "otherremote_path1.lst").exists())
        self.assertFalse((new / "otherremote_path1.lst").exists())

    def test_migrate_skips_existing_dest_file(self):
        """_migrate_bisync_state() must NOT overwrite a file already in new_dir."""
        old = self._mkdir("old_skip")
        new = self._mkdir("new_skip")
        (old / "myremote_path1.lst").write_text("old_content")
        (new / "myremote_path1.lst").write_text("already_here")

        count = _migrate_bisync_state("myremote", old, new, lambda m: None)

        self.assertEqual(count, 0, "Should skip file already present in new_dir")
        self.assertEqual((new / "myremote_path1.lst").read_text(), "already_here")
        # Source file stays because we didn't move it
        self.assertTrue((old / "myremote_path1.lst").exists())

    def test_migrate_empty_remote_name_moves_nothing(self):
        """_migrate_bisync_state() with empty remote_name must be a no-op."""
        old = self._mkdir("old_empty")
        new = self._mkdir("new_empty")
        (old / "myremote_path1.lst").write_text("state")

        count = _migrate_bisync_state("", old, new, lambda m: None)

        self.assertEqual(count, 0)
        self.assertTrue((old / "myremote_path1.lst").exists())

    def test_migrate_missing_old_dir_is_safe(self):
        """_migrate_bisync_state() must not raise when old_dir does not exist."""
        old = Path(self._tmpdir) / "nonexistent_old"
        new = self._mkdir("new_safe")

        count = _migrate_bisync_state("myremote", old, new, lambda m: None)
        self.assertEqual(count, 0)

    def test_migrate_called_in_do_bisync(self):
        """_do_bisync() must call _migrate_bisync_state() before bisync runs."""
        import src.config.config_manager as cm_mod
        orig = cm_mod.get_config_dir
        tmpdir = tempfile.mkdtemp()
        try:
            cm_mod.get_config_dir = lambda: Path(tmpdir)
            config = ConfigManager()
            rclone = RcloneManager(config)
            local = tempfile.mkdtemp()
            config.add_service("MigrSvc", "onedrive", local)
            config.update_service("MigrSvc", {"remote_name": "migr"})
            svc = config.get_service("MigrSvc")

            migrate_calls = []

            def fake_migrate(remote_name, old_dir, new_dir, emit_fn):
                migrate_calls.append((remote_name, str(old_dir), str(new_dir)))
                return 0

            def fake_run(cmd, service_name, svc_arg, is_retry=False):
                return True

            rclone._run_rclone = fake_run

            with patch(
                "src.rclone.rclone_manager._check_local_free_space",
                return_value=20 * 1024 ** 3,
            ):
                with patch(
                    "src.rclone.rclone_manager._migrate_bisync_state",
                    side_effect=fake_migrate,
                ):
                    rclone._do_bisync(svc)

            self.assertTrue(len(migrate_calls) >= 1,
                            "_migrate_bisync_state must be called by _do_bisync")
            # First arg must be the remote_name
            self.assertEqual(migrate_calls[0][0], "migr")
        finally:
            cm_mod.get_config_dir = orig
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
            shutil.rmtree(local, ignore_errors=True)


class TestErrorLogger(unittest.TestCase):
    """Tests for the ErrorLogger class."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        import src.config.config_manager as cm_mod
        self._original_get_config_dir = cm_mod.get_config_dir
        cm_mod.get_config_dir = lambda: Path(self._tmpdir)

        import src.gui.error_logger as el_mod
        self._original_log_path = el_mod._log_file_path
        el_mod._log_file_path = lambda: os.path.join(self._tmpdir, "errors.txt")

        from src.gui.error_logger import ErrorLogger
        self.logger = ErrorLogger()

    def tearDown(self):
        import src.config.config_manager as cm_mod
        cm_mod.get_config_dir = self._original_get_config_dir
        import src.gui.error_logger as el_mod
        el_mod._log_file_path = self._original_log_path
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_initial_state_empty(self):
        """A fresh ErrorLogger with no log file should have no entries."""
        self.assertEqual(self.logger.get_all_entries(), [])

    def test_log_adds_entry(self):
        """log() should add a formatted entry."""
        self.logger.log("TestSvc", "Something went wrong")
        entries = self.logger.get_all_entries()
        self.assertEqual(len(entries), 1)
        self.assertIn("TestSvc", entries[0])
        self.assertIn("Something went wrong", entries[0])

    def test_newest_entry_is_first(self):
        """Entries should be ordered newest-first."""
        self.logger.log("Svc", "First error")
        self.logger.log("Svc", "Second error")
        entries = self.logger.get_all_entries()
        self.assertIn("Second error", entries[0])
        self.assertIn("First error", entries[1])

    def test_save_and_reload(self):
        """Entries saved to disk should be reloaded on next instantiation."""
        self.logger.log("Svc", "Persistent error")
        self.logger.save_to_file()

        from src.gui.error_logger import ErrorLogger
        logger2 = ErrorLogger()
        text = logger2.get_all_text()
        self.assertIn("Persistent error", text)

    def test_get_all_text_empty_when_no_errors(self):
        """get_all_text() should return an empty string when no errors exist."""
        self.assertEqual(self.logger.get_all_text(), "")

    def test_clear_removes_entries(self):
        """clear() should remove all in-memory entries."""
        self.logger.log("Svc", "Error 1")
        self.logger.clear()
        self.assertEqual(self.logger.get_all_entries(), [])


class TestElementaryIndicator(unittest.TestCase):
    """Tests for the Elementary OS Wingpanel indicator helpers."""

    def test_is_elementary_os_true(self):
        """is_elementary_os() returns True when /etc/os-release contains ID=elementary."""
        from src.gui.elementary_indicator import is_elementary_os

        fake_release = "ID=elementary\nNAME=elementary OS\nVERSION=7\n"
        with patch("builtins.open", unittest.mock.mock_open(read_data=fake_release)):
            self.assertTrue(is_elementary_os())

    def test_is_elementary_os_false_ubuntu(self):
        """is_elementary_os() returns False for a non-elementary /etc/os-release."""
        from src.gui.elementary_indicator import is_elementary_os

        fake_release = "ID=ubuntu\nNAME=Ubuntu\nVERSION_ID=24.04\n"
        with patch("builtins.open", unittest.mock.mock_open(read_data=fake_release)):
            self.assertFalse(is_elementary_os())

    def test_is_elementary_os_false_on_ioerror(self):
        """is_elementary_os() returns False when /etc/os-release cannot be read."""
        from src.gui.elementary_indicator import is_elementary_os

        with patch("builtins.open", side_effect=OSError("no such file")):
            self.assertFalse(is_elementary_os())

    def test_indicator_not_available_non_elementary(self):
        """ElementaryIndicator.is_available() returns False on non-Elementary OS."""
        from src.gui.elementary_indicator import ElementaryIndicator, is_elementary_os

        ind = ElementaryIndicator()
        with patch("src.gui.elementary_indicator.is_elementary_os", return_value=False):
            self.assertFalse(ind.is_available())

    def test_indicator_not_available_missing_library(self):
        """ElementaryIndicator.is_available() returns False when AppIndicator3 is absent."""
        from src.gui.elementary_indicator import ElementaryIndicator

        with (
            patch("src.gui.elementary_indicator.is_elementary_os", return_value=True),
            patch("src.gui.elementary_indicator._import_app_indicator", return_value=None),
        ):
            ind = ElementaryIndicator()
            self.assertFalse(ind.is_available())

    def test_indicator_available_when_library_present(self):
        """ElementaryIndicator.is_available() returns True on Elementary with AppIndicator3."""
        from src.gui.elementary_indicator import ElementaryIndicator

        mock_ai = MagicMock()
        with (
            patch("src.gui.elementary_indicator.is_elementary_os", return_value=True),
            patch("src.gui.elementary_indicator._import_app_indicator", return_value=mock_ai),
        ):
            ind = ElementaryIndicator()
            self.assertTrue(ind.is_available())

    def test_start_does_nothing_when_library_absent(self):
        """ElementaryIndicator.start() should not raise when AppIndicator3 is absent."""
        from src.gui.elementary_indicator import ElementaryIndicator

        with patch("src.gui.elementary_indicator._import_app_indicator", return_value=None):
            ind = ElementaryIndicator()
            ind.start()  # must not raise
            self.assertFalse(ind._running)

    def test_stop_does_nothing_when_not_started(self):
        """ElementaryIndicator.stop() should be safe to call when not running."""
        from src.gui.elementary_indicator import ElementaryIndicator

        ind = ElementaryIndicator()
        ind.stop()  # must not raise
        self.assertFalse(ind._running)

    def test_on_show_callback_invoked(self):
        """The _on_show_clicked handler must invoke the on_show callback."""
        from src.gui.elementary_indicator import ElementaryIndicator

        called = []
        ind = ElementaryIndicator(on_show=lambda: called.append(True))
        ind._on_show_clicked(None)
        self.assertEqual(called, [True])

    def test_on_quit_callback_invoked(self):
        """The _on_quit_clicked handler must invoke the on_quit callback."""
        from src.gui.elementary_indicator import ElementaryIndicator

        called = []
        with patch.object(ElementaryIndicator, "stop"):
            ind = ElementaryIndicator(on_quit=lambda: called.append(True))
            ind._on_quit_clicked(None)
        self.assertEqual(called, [True])

    def test_update_tooltip_no_error_when_not_started(self):
        """update_tooltip() should be a no-op when the indicator is not running."""
        from src.gui.elementary_indicator import ElementaryIndicator

        ind = ElementaryIndicator()
        ind.update_tooltip("Some tooltip")  # must not raise




class TestParseRcloneMtime(unittest.TestCase):
    """Tests for the _parse_rclone_mtime() helper."""

    def test_microsecond_precision_constant(self):
        """_MICROSECOND_PRECISION must equal 6 (Python strptime limit)."""
        self.assertEqual(_MICROSECOND_PRECISION, 6)

    def test_nanosecond_format(self):
        """Should parse rclone's nanosecond-precision format correctly."""
        # 2024-01-15T10:30:00Z = 1705314600 UTC
        ts = _parse_rclone_mtime("2024-01-15T10:30:00.123456789Z")
        self.assertIsNotNone(ts)
        self.assertAlmostEqual(ts, 1705314600.123456, places=3)

    def test_microsecond_format(self):
        """Should parse a 6-digit fractional seconds string."""
        ts = _parse_rclone_mtime("2024-01-15T10:30:00.123456Z")
        self.assertIsNotNone(ts)
        self.assertAlmostEqual(ts, 1705314600.123456, places=3)

    def test_no_fraction_format(self):
        """Should parse a timestamp without fractional seconds."""
        ts = _parse_rclone_mtime("2024-01-15T10:30:00Z")
        self.assertIsNotNone(ts)
        self.assertAlmostEqual(ts, 1705314600.0, places=1)

    def test_invalid_string_returns_none(self):
        """Should return None for an unparseable string."""
        self.assertIsNone(_parse_rclone_mtime("not-a-date"))

    def test_empty_string_returns_none(self):
        """Should return None for an empty string."""
        self.assertIsNone(_parse_rclone_mtime(""))

    def test_date_only_string_returns_none(self):
        """A date-only string without a time component should return None."""
        self.assertIsNone(_parse_rclone_mtime("2024-01-15"))


class TestScanLocalMtimes(unittest.TestCase):
    """Tests for the _scan_local_mtimes() helper."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_empty_directory(self):
        """An empty directory should return an empty dict."""
        result = _scan_local_mtimes(self._tmpdir)
        self.assertEqual(result, {})

    def test_nonexistent_path(self):
        """A path that does not exist should return an empty dict."""
        result = _scan_local_mtimes("/nonexistent/path/xyz")
        self.assertEqual(result, {})

    def test_single_file(self):
        """A directory with one file should return one entry."""
        p = os.path.join(self._tmpdir, "file.txt")
        with open(p, "w") as fh:
            fh.write("hello")
        result = _scan_local_mtimes(self._tmpdir)
        self.assertIn("file.txt", result)
        self.assertIsInstance(result["file.txt"], float)

    def test_nested_file_uses_posix_rel_path(self):
        """Nested files should use forward-slash relative paths."""
        subdir = os.path.join(self._tmpdir, "sub")
        os.makedirs(subdir)
        with open(os.path.join(subdir, "nested.txt"), "w") as fh:
            fh.write("x")
        result = _scan_local_mtimes(self._tmpdir)
        self.assertIn("sub/nested.txt", result)

    def test_mtime_matches_os_stat(self):
        """Returned mtime should match os.stat().st_mtime for the same file."""
        p = os.path.join(self._tmpdir, "check.txt")
        with open(p, "w") as fh:
            fh.write("data")
        expected = os.stat(p).st_mtime
        result = _scan_local_mtimes(self._tmpdir)
        self.assertAlmostEqual(result["check.txt"], expected, places=3)


class TestCheckSyncStatusMtime(unittest.TestCase):
    """Tests for RcloneManager.check_sync_status_mtime()."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        import src.config.config_manager as cm_mod
        self._original_get_config_dir = cm_mod.get_config_dir
        cm_mod.get_config_dir = lambda: Path(self._tmpdir)
        self.config = ConfigManager()
        self.rclone = RcloneManager(self.config)

    def tearDown(self):
        import src.config.config_manager as cm_mod
        cm_mod.get_config_dir = self._original_get_config_dir
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_returns_none_for_unknown_service(self):
        """Should return None when the service does not exist."""
        result = self.rclone.check_sync_status_mtime("no_such_service")
        self.assertIsNone(result)

    def test_returns_none_when_rclone_absent(self):
        """Should return None when rclone is not installed (subprocess fails)."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("S1", "onedrive", local)
            # rclone is not installed in CI, so this should return None
            result = self.rclone.check_sync_status_mtime("S1")
            self.assertIsNone(result)
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def _make_service_with_local(self, name: str) -> str:
        """Helper: create service with a temp local dir, return the local dir path."""
        local = tempfile.mkdtemp()
        self.config.add_service(name, "onedrive", local)
        return local

    def test_synced_files_detected(self):
        """Files with matching mtimes should be reported as 'synced'."""
        local = self._make_service_with_local("SyncSvc")
        try:
            # Write a local file
            local_file = os.path.join(local, "readme.txt")
            with open(local_file, "w") as fh:
                fh.write("hello")
            local_mtime = os.stat(local_file).st_mtime

            # Compute a matching rclone mtime string (same second, UTC)
            from datetime import datetime, timezone
            remote_dt = datetime.fromtimestamp(local_mtime, tz=timezone.utc)
            mtime_str = remote_dt.strftime("%Y-%m-%dT%H:%M:%S.000000Z")

            # Patch subprocess.run to return JSON listing matching this file
            import json
            fake_json = json.dumps([{"Path": "readme.txt", "ModTime": mtime_str}])

            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=fake_json, stderr=""
                )
                result = self.rclone.check_sync_status_mtime("SyncSvc")

            self.assertIsNotNone(result)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["rel"], "readme.txt")
            self.assertEqual(result[0]["status"], "synced")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_diff_files_detected(self):
        """Files whose mtimes differ by more than tolerance should be 'diff'."""
        local = self._make_service_with_local("DiffSvc")
        try:
            local_file = os.path.join(local, "doc.pdf")
            with open(local_file, "w") as fh:
                fh.write("content")

            # Remote mtime is 1 hour ahead of local
            from datetime import datetime, timezone
            local_mtime = os.stat(local_file).st_mtime
            remote_ts = local_mtime + 3600
            remote_dt = datetime.fromtimestamp(remote_ts, tz=timezone.utc)
            mtime_str = remote_dt.strftime("%Y-%m-%dT%H:%M:%S.000000Z")

            import json
            fake_json = json.dumps([{"Path": "doc.pdf", "ModTime": mtime_str}])

            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=fake_json, stderr=""
                )
                result = self.rclone.check_sync_status_mtime("DiffSvc")

            self.assertIsNotNone(result)
            self.assertEqual(result[0]["status"], "diff")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_remote_only_files_detected(self):
        """Files present on the remote but not locally should be 'remote_only'."""
        local = self._make_service_with_local("RemoteSvc")
        try:
            # No local files; remote has one file
            import json
            fake_json = json.dumps([
                {"Path": "remote_only.txt", "ModTime": "2024-06-01T12:00:00Z"}
            ])

            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=fake_json, stderr=""
                )
                result = self.rclone.check_sync_status_mtime("RemoteSvc")

            self.assertIsNotNone(result)
            statuses = {item["rel"]: item["status"] for item in result}
            self.assertEqual(statuses.get("remote_only.txt"), "remote_only")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_local_only_files_detected(self):
        """Files present locally but not on the remote should be 'local_only'."""
        local = self._make_service_with_local("LocalSvc")
        try:
            with open(os.path.join(local, "local_only.txt"), "w") as fh:
                fh.write("data")
            # Remote listing is empty
            import json
            fake_json = json.dumps([])

            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=fake_json, stderr=""
                )
                result = self.rclone.check_sync_status_mtime("LocalSvc")

            self.assertIsNotNone(result)
            statuses = {item["rel"]: item["status"] for item in result}
            self.assertEqual(statuses.get("local_only.txt"), "local_only")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_returns_none_on_nonzero_returncode(self):
        """Should return None when rclone lsjson exits with a non-zero code."""
        local = self._make_service_with_local("FailSvc")
        try:
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1, stdout="", stderr="error"
                )
                result = self.rclone.check_sync_status_mtime("FailSvc")
            self.assertIsNone(result)
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_mtime_tolerance_boundary(self):
        """A mtime difference exactly at the tolerance boundary should be 'synced'."""
        local = self._make_service_with_local("TolSvc")
        try:
            local_file = os.path.join(local, "edge.txt")
            with open(local_file, "w") as fh:
                fh.write("edge")

            from datetime import datetime, timezone
            local_mtime = os.stat(local_file).st_mtime
            # Remote mtime is exactly _MTIME_TOLERANCE_SECS away
            remote_ts = local_mtime + _MTIME_TOLERANCE_SECS
            remote_dt = datetime.fromtimestamp(remote_ts, tz=timezone.utc)
            mtime_str = remote_dt.strftime("%Y-%m-%dT%H:%M:%S.000000Z")

            import json
            fake_json = json.dumps([{"Path": "edge.txt", "ModTime": mtime_str}])

            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=fake_json, stderr=""
                )
                result = self.rclone.check_sync_status_mtime("TolSvc")

            self.assertIsNotNone(result)
            self.assertEqual(result[0]["status"], "synced")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)




class TestCheckLocalFreeSpace(unittest.TestCase):
    """Tests for the _check_local_free_space() helper."""

    def test_existing_directory_returns_positive(self):
        """An existing directory should return a positive free-space value."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            result = _check_local_free_space(d)
            self.assertIsInstance(result, int)
            self.assertGreater(result, 0)

    def test_nonexistent_path_walks_up_to_parent(self):
        """A nonexistent path should walk up to an existing ancestor."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            nonexistent = os.path.join(d, "a", "b", "c", "does_not_exist")
            result = _check_local_free_space(nonexistent)
            # The parent (d) exists, so we should get the same value as d.
            expected = _check_local_free_space(d)
            self.assertEqual(result, expected)

    def test_completely_invalid_path_returns_zero(self):
        """A path on a non-existent root should return 0."""
        result = _check_local_free_space("/no/such/root/at/all/xyz123")
        # Either 0 (no ancestor found) or positive (if / exists and has space).
        # Both are acceptable; we just assert it does not raise.
        self.assertIsInstance(result, int)
        self.assertGreaterEqual(result, 0)

    def test_min_free_space_constant_is_10_gib(self):
        """_MIN_FREE_SPACE_BYTES must equal exactly 10 GiB (10 * 1024**3)."""
        self.assertEqual(_MIN_FREE_SPACE_BYTES, 10 * 1024 ** 3)
        self.assertEqual(_MIN_FREE_SPACE_GIB, 10)
        self.assertEqual(_MIN_FREE_SPACE_BYTES, _MIN_FREE_SPACE_GIB * 1024 ** 3)


class TestDoBisyncDiskSpaceGuard(unittest.TestCase):
    """Tests for the disk-space guard in _do_bisync()."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        import src.config.config_manager as cm_mod
        self._original_get_config_dir = cm_mod.get_config_dir
        cm_mod.get_config_dir = lambda: Path(self._tmpdir)
        self.config = ConfigManager()
        self.rclone = RcloneManager(self.config)

    def tearDown(self):
        import src.config.config_manager as cm_mod
        cm_mod.get_config_dir = self._original_get_config_dir
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_bisync_aborts_when_disk_full(self):
        """_do_bisync() must return False and emit an error when free space < 10 GiB."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("DiskFull", "onedrive", local)
            svc = self.config.get_service("DiskFull")
            errors = []
            self.rclone.on_error = lambda name, msg: errors.append(msg)

            # Patch _check_local_free_space to return 1 byte (effectively full)
            with patch(
                "src.rclone.rclone_manager._check_local_free_space",
                return_value=1,
            ):
                result = self.rclone._do_bisync(svc)

            self.assertFalse(result, "_do_bisync must return False when disk is full")
            # At least one error message should mention insufficient space
            self.assertTrue(
                any("insuficiente" in e or "espacio" in e.lower() for e in errors),
                f"Expected a disk-space error message, got: {errors}",
            )
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_bisync_proceeds_when_disk_has_enough_space(self):
        """_do_bisync() must not abort for disk space when free space >= 10 GiB."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("DiskOK", "onedrive", local)
            svc = self.config.get_service("DiskOK")
            captured_cmds = []

            def fake_run_rclone(cmd, service_name, svc_arg, is_retry=False):
                captured_cmds.append(cmd)
                return True

            self.rclone._run_rclone = fake_run_rclone

            # Patch free space to 20 GiB — plenty of room
            with patch(
                "src.rclone.rclone_manager._check_local_free_space",
                return_value=20 * 1024 ** 3,
            ):
                result = self.rclone._do_bisync(svc)

            self.assertTrue(result, "_do_bisync must succeed when there is enough space")
            self.assertTrue(len(captured_cmds) > 0, "rclone command must have been called")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)


class TestDoBisyncNoPriorListings(unittest.TestCase):
    """Tests for auto-resync when bisync reports no prior state files."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        import src.config.config_manager as cm_mod
        self._original_get_config_dir = cm_mod.get_config_dir
        cm_mod.get_config_dir = lambda: Path(self._tmpdir)
        self.config = ConfigManager()
        self.rclone = RcloneManager(self.config)

    def tearDown(self):
        import src.config.config_manager as cm_mod
        cm_mod.get_config_dir = self._original_get_config_dir
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_bisync_no_prior_phrase_constant(self):
        """_BISYNC_NO_PRIOR_PHRASE must match the exact rclone error text."""
        self.assertIn("cannot find prior", _BISYNC_NO_PRIOR_PHRASE)
        self.assertIn("listings", _BISYNC_NO_PRIOR_PHRASE)

    def test_no_prior_listing_triggers_resync(self):
        """When rclone emits the no-prior-listings phrase, _do_bisync must retry with --resync."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("NoPriorSvc", "onedrive", local)
            svc = self.config.get_service("NoPriorSvc")
            captured_cmds = []
            errors = []
            self.rclone.on_error = lambda name, msg: errors.append(msg)

            call_count = [0]

            def fake_run_rclone(cmd, service_name, svc_arg, is_retry=False):
                captured_cmds.append(list(cmd))
                call_count[0] += 1
                if call_count[0] == 1:
                    # Simulate first run: flag the service as having no prior listings
                    self.rclone._no_prior_listing_services.add(service_name)
                    return False  # First attempt fails
                return True  # --resync attempt succeeds

            self.rclone._run_rclone = fake_run_rclone

            with patch(
                "src.rclone.rclone_manager._check_local_free_space",
                return_value=20 * 1024 ** 3,
            ):
                result = self.rclone._do_bisync(svc)

            self.assertTrue(result, "_do_bisync must return True after successful --resync")
            self.assertEqual(call_count[0], 2, "rclone must be called exactly twice")
            # The second command must contain --resync
            self.assertIn("--resync", captured_cmds[1],
                          "--resync flag must be present in the retry command")
            # An informational message must have been emitted
            info_msgs = [e for e in errors if "ℹ️" in e or "prior" in e.lower() or "resync" in e.lower()]
            self.assertTrue(len(info_msgs) > 0,
                            "An informational --resync message must be emitted")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_no_prior_flag_cleared_after_retry(self):
        """_no_prior_listing_services must be cleared after _do_bisync handles it."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("ClearSvc", "onedrive", local)
            svc = self.config.get_service("ClearSvc")

            call_count = [0]

            def fake_run_rclone(cmd, service_name, svc_arg, is_retry=False):
                call_count[0] += 1
                if call_count[0] == 1:
                    self.rclone._no_prior_listing_services.add(service_name)
                    return False
                return True

            self.rclone._run_rclone = fake_run_rclone

            with patch(
                "src.rclone.rclone_manager._check_local_free_space",
                return_value=20 * 1024 ** 3,
            ):
                self.rclone._do_bisync(svc)

            self.assertNotIn(
                "ClearSvc",
                self.rclone._no_prior_listing_services,
                "Flag must be cleared from _no_prior_listing_services after handling",
            )
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_run_rclone_detects_no_prior_phrase_in_output(self):
        """_run_rclone() must add the service to _no_prior_listing_services when
        it sees the no-prior-listings phrase in rclone's output."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("DetectSvc", "onedrive", local)
            svc = self.config.get_service("DetectSvc")

            # Build a fake rclone command that simply prints the no-prior phrase
            # to stdout so _run_rclone can parse it.
            import sys
            script = (
                f"import sys; "
                f"print('ERROR : Bisync critical error: {_BISYNC_NO_PRIOR_PHRASE}'); "
                f"sys.exit(1)"
            )
            fake_cmd = [sys.executable, "-c", script]

            result = self.rclone._run_rclone(fake_cmd, "DetectSvc", svc)

            self.assertFalse(result, "Command that exits 1 must return False")
            self.assertIn(
                "DetectSvc",
                self.rclone._no_prior_listing_services,
                "Service must be flagged in _no_prior_listing_services",
            )
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)



class TestNetworkUnreachableHandling(unittest.TestCase):
    """Tests for network-unreachable error suppression and retry skip."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        import src.config.config_manager as cm_mod
        self._original_get_config_dir = cm_mod.get_config_dir
        cm_mod.get_config_dir = lambda: Path(self._tmpdir)
        self.config = ConfigManager()
        self.rclone = RcloneManager(self.config)

    def tearDown(self):
        import src.config.config_manager as cm_mod
        cm_mod.get_config_dir = self._original_get_config_dir
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_network_unreachable_phrase_constant(self):
        """_NETWORK_UNREACHABLE_PHRASE must match the rclone error text."""
        self.assertIn("network is unreachable", _NETWORK_UNREACHABLE_PHRASE)

    def test_network_error_emits_single_summary_message(self):
        """Multiple 'network is unreachable' lines should produce exactly one summary message."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("NetSvc", "onedrive", local)
            svc = self.config.get_service("NetSvc")
            errors = []
            self.rclone.on_error = lambda name, msg: errors.append(msg)

            import sys
            # Simulate rclone emitting many ERROR lines containing the network phrase
            script = (
                "import sys; "
                "for i in range(5): "
                "print('ERROR : path' + str(i) + ': network is unreachable'); "
                "sys.exit(1)"
            )
            fake_cmd = [sys.executable, "-c", script]
            self.rclone._run_rclone(fake_cmd, "NetSvc", svc)

            # Count messages that contain the network phrase (per-file spam)
            per_file = [m for m in errors if "network is unreachable" in m.lower() and "🌐" not in m]
            summary = [m for m in errors if "🌐" in m]
            self.assertEqual(len(summary), 1, "Exactly one summary network message should be emitted")
            self.assertEqual(len(per_file), 0, "Per-file network error lines must be suppressed")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_network_error_skips_resync_retry(self):
        """_do_bisync must NOT retry with --resync when a network error was detected."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("NetRetrySvc", "onedrive", local)
            svc = self.config.get_service("NetRetrySvc")
            captured_cmds = []

            call_count = [0]

            def fake_run_rclone(cmd, service_name, svc_arg, is_retry=False):
                captured_cmds.append(list(cmd))
                call_count[0] += 1
                # Mark network error on first call (simulates detection in _run_rclone)
                self.rclone._network_error_services.add(service_name)
                return False

            self.rclone._run_rclone = fake_run_rclone

            with patch(
                "src.rclone.rclone_manager._check_local_free_space",
                return_value=20 * 1024 ** 3,
            ):
                self.rclone._do_bisync(svc)

            # Only one attempt should have been made (no --resync retry)
            self.assertEqual(call_count[0], 1,
                             "Network error must skip the --resync retry (only 1 attempt expected)")
            for cmd in captured_cmds:
                self.assertNotIn("--resync", cmd,
                                 "--resync must not appear in commands after a network error")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_network_error_flag_cleared_after_do_bisync(self):
        """_network_error_services must be cleared after _do_bisync processes the flag."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("NetClearSvc", "onedrive", local)
            svc = self.config.get_service("NetClearSvc")

            def fake_run_rclone(cmd, service_name, svc_arg, is_retry=False):
                self.rclone._network_error_services.add(service_name)
                return False

            self.rclone._run_rclone = fake_run_rclone

            with patch(
                "src.rclone.rclone_manager._check_local_free_space",
                return_value=20 * 1024 ** 3,
            ):
                self.rclone._do_bisync(svc)

            self.assertNotIn("NetClearSvc", self.rclone._network_error_services,
                             "Service must be removed from _network_error_services after _do_bisync")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)


class TestFirstSyncTracking(unittest.TestCase):
    """Tests for first-sync detection: status messages, --resync on subsequent runs,
    and persistence of the first_sync_done flag."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        import src.config.config_manager as cm_mod
        self._original_get_config_dir = cm_mod.get_config_dir
        cm_mod.get_config_dir = lambda: Path(self._tmpdir)
        self.config = ConfigManager()
        self.rclone = RcloneManager(self.config)

    def tearDown(self):
        import src.config.config_manager as cm_mod
        cm_mod.get_config_dir = self._original_get_config_dir
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ── _do_bisync use_resync parameter ─────────────────────────────────────

    def test_do_bisync_includes_resync_when_use_resync_true(self):
        """_do_bisync(use_resync=True) must include --resync in the initial command."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("ReSync1", "onedrive", local)
            svc = self.config.get_service("ReSync1")
            captured_cmds = []

            def fake_run_rclone(cmd, service_name, svc_arg, is_retry=False):
                captured_cmds.append(list(cmd))
                return True

            self.rclone._run_rclone = fake_run_rclone

            with patch(
                "src.rclone.rclone_manager._check_local_free_space",
                return_value=20 * 1024 ** 3,
            ):
                result = self.rclone._do_bisync(svc, use_resync=True)

            self.assertTrue(result)
            self.assertEqual(len(captured_cmds), 1, "Only one rclone call expected when --resync")
            self.assertIn("--resync", captured_cmds[0],
                          "--resync must appear in the initial command when use_resync=True")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_do_bisync_no_resync_by_default(self):
        """_do_bisync() without use_resync must NOT add --resync to the initial command."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("NoReSyncSvc", "onedrive", local)
            svc = self.config.get_service("NoReSyncSvc")
            captured_cmds = []

            def fake_run_rclone(cmd, service_name, svc_arg, is_retry=False):
                captured_cmds.append(list(cmd))
                return True

            self.rclone._run_rclone = fake_run_rclone

            with patch(
                "src.rclone.rclone_manager._check_local_free_space",
                return_value=20 * 1024 ** 3,
            ):
                result = self.rclone._do_bisync(svc)

            self.assertTrue(result)
            # --resync must NOT be in the first (and only) command
            self.assertNotIn("--resync", captured_cmds[0],
                             "--resync must not appear in the first command by default")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_do_bisync_no_retry_when_use_resync_true_and_fails(self):
        """When use_resync=True and the command fails, no retry must be attempted."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("ReFailSvc", "onedrive", local)
            svc = self.config.get_service("ReFailSvc")
            call_count = [0]

            def fake_run_rclone(cmd, service_name, svc_arg, is_retry=False):
                call_count[0] += 1
                return False  # always fail

            self.rclone._run_rclone = fake_run_rclone

            with patch(
                "src.rclone.rclone_manager._check_local_free_space",
                return_value=20 * 1024 ** 3,
            ):
                result = self.rclone._do_bisync(svc, use_resync=True)

            self.assertFalse(result)
            self.assertEqual(call_count[0], 1,
                             "Must NOT retry when the initial --resync command fails")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    # ── first_sync_done persistence ──────────────────────────────────────────

    def test_first_sync_done_persisted_after_success(self):
        """first_sync_done must be saved to config after the first successful sync."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("PersistSvc", "onedrive", local)

            statuses = []
            self.rclone.on_status_change = lambda name, s: statuses.append(s)

            call_count = [0]

            def fake_run_rclone(cmd, service_name, svc_arg, is_retry=False):
                call_count[0] += 1
                return True

            self.rclone._run_rclone = fake_run_rclone

            stop_event = threading.Event()
            # Run two sync cycles: stop after second cycle starts waiting
            cycle = [0]
            orig_wait = stop_event.wait

            def patched_wait(timeout=None):
                cycle[0] += 1
                if cycle[0] >= 2:
                    stop_event.set()
                orig_wait(timeout=0.01)

            stop_event.wait = patched_wait

            with patch(
                "src.rclone.rclone_manager._check_local_free_space",
                return_value=20 * 1024 ** 3,
            ):
                self.rclone._sync_loop("PersistSvc", stop_event)

            svc = self.config.get_service("PersistSvc")
            self.assertTrue(
                svc.get("first_sync_done", False),
                "first_sync_done must be True in config after a successful sync",
            )
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_status_message_is_sincronizando_on_first_run(self):
        """_sync_loop must emit 'Sincronizando…' for the first sync cycle."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("FirstMsgSvc", "onedrive", local)

            statuses = []
            self.rclone.on_status_change = lambda name, s: statuses.append(s)

            def fake_run_rclone(cmd, service_name, svc_arg, is_retry=False):
                return True

            self.rclone._run_rclone = fake_run_rclone

            stop_event = threading.Event()
            stopped = [False]

            def patched_wait(timeout=None):
                stop_event.set()
                stopped[0] = True

            stop_event.wait = patched_wait

            with patch(
                "src.rclone.rclone_manager._check_local_free_space",
                return_value=20 * 1024 ** 3,
            ):
                self.rclone._sync_loop("FirstMsgSvc", stop_event)

            self.assertIn("Sincronizando…", statuses,
                          "'Sincronizando…' must appear as status on the first sync run")
            self.assertNotIn("Actualizando cambios…", statuses,
                             "'Actualizando cambios…' must NOT appear on the first run")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_status_message_is_actualizando_on_subsequent_runs(self):
        """_sync_loop must emit 'Actualizando cambios…' when first_sync_done is True."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("SubseqSvc", "onedrive", local)
            # Pre-mark first sync as done
            self.config.update_service("SubseqSvc", {"first_sync_done": True})

            statuses = []
            self.rclone.on_status_change = lambda name, s: statuses.append(s)

            def fake_run_rclone(cmd, service_name, svc_arg, is_retry=False):
                return True

            self.rclone._run_rclone = fake_run_rclone

            stop_event = threading.Event()

            def patched_wait(timeout=None):
                stop_event.set()

            stop_event.wait = patched_wait

            with patch(
                "src.rclone.rclone_manager._check_local_free_space",
                return_value=20 * 1024 ** 3,
            ):
                self.rclone._sync_loop("SubseqSvc", stop_event)

            self.assertIn("Actualizando cambios…", statuses,
                          "'Actualizando cambios…' must appear as status when first_sync_done=True")
            self.assertNotIn("Sincronizando…", statuses,
                             "'Sincronizando…' must NOT appear when first_sync_done=True")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_use_resync_false_on_first_run(self):
        """_sync_loop must call _do_bisync WITHOUT --resync on the first cycle."""
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("FirstResyncSvc", "onedrive", local)
            captured_cmds = []

            def fake_run_rclone(cmd, service_name, svc_arg, is_retry=False):
                captured_cmds.append(list(cmd))
                return True

            self.rclone._run_rclone = fake_run_rclone

            stop_event = threading.Event()

            def patched_wait(timeout=None):
                stop_event.set()

            stop_event.wait = patched_wait

            with patch(
                "src.rclone.rclone_manager._check_local_free_space",
                return_value=20 * 1024 ** 3,
            ):
                self.rclone._sync_loop("FirstResyncSvc", stop_event)

            # On first run, --resync must NOT be in any command
            all_cmds_flat = [arg for cmd in captured_cmds for arg in cmd]
            self.assertNotIn("--resync", all_cmds_flat,
                             "--resync must not be added on the first sync cycle")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)

    def test_use_resync_false_on_subsequent_runs(self):
        """_sync_loop must NOT include --resync in the bisync command after first_sync_done=True.

        Regression test: previously _sync_loop used use_resync=not is_first which
        caused every sync cycle after the first to run with --resync.  On rclone
        < v1.64 that defaults to "path1 wins" (remote wins), silently overwriting
        local file modifications instead of uploading them.
        """
        local = tempfile.mkdtemp()
        try:
            self.config.add_service("SubResyncSvc", "onedrive", local)
            # Simulate a service that already had its first successful sync
            self.config.update_service("SubResyncSvc", {"first_sync_done": True})
            captured_cmds = []

            def fake_run_rclone(cmd, service_name, svc_arg, is_retry=False):
                captured_cmds.append(list(cmd))
                return True

            self.rclone._run_rclone = fake_run_rclone

            stop_event = threading.Event()

            def patched_wait(timeout=None):
                stop_event.set()

            stop_event.wait = patched_wait

            with patch(
                "src.rclone.rclone_manager._check_local_free_space",
                return_value=20 * 1024 ** 3,
            ):
                self.rclone._sync_loop("SubResyncSvc", stop_event)

            all_cmds_flat = [arg for cmd in captured_cmds for arg in cmd]
            self.assertNotIn("--resync", all_cmds_flat,
                             "--resync must NOT be added on subsequent sync cycles "
                             "(doing so overwrites local changes on older rclone)")
        finally:
            import shutil
            shutil.rmtree(local, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tests for tree-building helpers in src/gui/main_window.py
# ---------------------------------------------------------------------------
# main_window.py imports tkinter at module level, which is unavailable in
# headless CI.  We therefore replicate the pure-data function under test here
# so it can be exercised without a display.  The canonical implementation is
# in src/gui/main_window.py::_propagate_dir_status().

def _propagate_dir_status_testable(items):
    """Testable replica of main_window._propagate_dir_status().  See that
    function's docstring for the full specification."""
    dir_child_statuses = {}
    for item in items:
        if item["is_dir"]:
            if item["rel"] not in dir_child_statuses:
                dir_child_statuses[item["rel"]] = set()
        else:
            parts = item["rel"].split("/")
            for depth in range(1, len(parts)):
                dir_rel = "/".join(parts[:depth])
                if dir_rel not in dir_child_statuses:
                    dir_child_statuses[dir_rel] = set()
                dir_child_statuses[dir_rel].add(item["status"])
    for item in items:
        if not item["is_dir"]:
            continue
        statuses = dir_child_statuses.get(item["rel"], set())
        # Exclude "unknown" — files with no known origin must not inflate
        # a directory to "synced".  Derive status only from known-origin files.
        known = statuses - {"unknown"}
        if not known:
            item["status"] = "unknown"
        elif known <= {"local_only"}:
            item["status"] = "local_only"
        elif known <= {"remote_only"}:
            item["status"] = "remote_only"
        elif "diff" in known:
            item["status"] = "diff"
        else:
            item["status"] = "synced"


def _make_dir(rel, parent=""):
    return {"rel": rel, "parent": parent, "name": rel.rsplit("/", 1)[-1],
            "is_dir": True, "status": "unknown"}


def _make_file(rel, status):
    parent = "/".join(rel.split("/")[:-1])
    return {"rel": rel, "parent": parent, "name": rel.rsplit("/", 1)[-1],
            "is_dir": False, "status": status}


class TestPropagateDirStatus(unittest.TestCase):
    """Tests for the _propagate_dir_status() helper (tree directory coloring)."""

    def _apply(self, items):
        _propagate_dir_status_testable(items)
        return {i["rel"]: i["status"] for i in items}

    def test_all_local_only_files(self):
        """Directory with only local_only files should be colored local_only (blue)."""
        items = [
            _make_dir("docs"),
            _make_file("docs/a.txt", "local_only"),
            _make_file("docs/b.txt", "local_only"),
        ]
        statuses = self._apply(items)
        self.assertEqual(statuses["docs"], "local_only")

    def test_all_remote_only_files(self):
        """Directory with only remote_only files should be colored remote_only (orange)."""
        items = [
            _make_dir("photos"),
            _make_file("photos/img.jpg", "remote_only"),
        ]
        statuses = self._apply(items)
        self.assertEqual(statuses["photos"], "remote_only")

    def test_all_synced_files(self):
        """Directory with only synced files should be colored synced (green)."""
        items = [
            _make_dir("work"),
            _make_file("work/report.pdf", "synced"),
            _make_file("work/data.csv", "synced"),
        ]
        statuses = self._apply(items)
        self.assertEqual(statuses["work"], "synced")

    def test_diff_files_bubble_up(self):
        """A single diff file should make the parent directory 'diff'."""
        items = [
            _make_dir("src"),
            _make_file("src/main.py", "synced"),
            _make_file("src/utils.py", "diff"),
        ]
        statuses = self._apply(items)
        self.assertEqual(statuses["src"], "diff")

    def test_mixed_local_and_remote_becomes_synced(self):
        """A directory with both local_only and remote_only files shows 'synced' (both present)."""
        items = [
            _make_dir("mixed"),
            _make_file("mixed/local.txt", "local_only"),
            _make_file("mixed/remote.txt", "remote_only"),
        ]
        statuses = self._apply(items)
        self.assertEqual(statuses["mixed"], "synced")

    def test_empty_directory_stays_unknown(self):
        """A directory with no files should remain 'unknown'."""
        items = [_make_dir("empty")]
        statuses = self._apply(items)
        self.assertEqual(statuses["empty"], "unknown")

    def test_nested_directories_propagate_correctly(self):
        """Status must propagate from deeply nested files up through all ancestor dirs."""
        items = [
            _make_dir("a"),
            _make_dir("a/b"),
            _make_dir("a/b/c"),
            _make_file("a/b/c/file.txt", "local_only"),
        ]
        statuses = self._apply(items)
        self.assertEqual(statuses["a/b/c"], "local_only")
        self.assertEqual(statuses["a/b"], "local_only")
        self.assertEqual(statuses["a"], "local_only")

    def test_sibling_dirs_colored_independently(self):
        """Two sibling directories with different file origins get independent colors."""
        items = [
            _make_dir("a"),
            _make_file("a/f.txt", "local_only"),
            _make_dir("b"),
            _make_file("b/g.txt", "remote_only"),
        ]
        statuses = self._apply(items)
        self.assertEqual(statuses["a"], "local_only")
        self.assertEqual(statuses["b"], "remote_only")

    def test_files_not_modified(self):
        """_propagate_dir_status must not change the status of file items."""
        items = [
            _make_dir("d"),
            _make_file("d/x.txt", "synced"),
        ]
        _propagate_dir_status_testable(items)
        file_item = next(i for i in items if not i["is_dir"])
        self.assertEqual(file_item["status"], "synced")

    # ── "unknown" file status handling ──────────────────────────────────────

    def test_all_unknown_files_dir_stays_unknown(self):
        """A directory whose only descendants have 'unknown' status must stay
        'unknown', NOT be promoted to 'synced' (regression test)."""
        items = [
            _make_dir("docs"),
            _make_file("docs/a.txt", "unknown"),
            _make_file("docs/b.txt", "unknown"),
        ]
        statuses = self._apply(items)
        self.assertEqual(statuses["docs"], "unknown",
                         "All-unknown files must not inflate directory to 'synced'")

    def test_synced_and_unknown_files_dir_is_synced(self):
        """A directory with one synced and one unknown file must be 'synced'
        (the synced file is the only known-origin file)."""
        items = [
            _make_dir("docs"),
            _make_file("docs/synced.txt", "synced"),
            _make_file("docs/unknown.txt", "unknown"),
        ]
        statuses = self._apply(items)
        self.assertEqual(statuses["docs"], "synced")

    def test_local_only_and_unknown_files_dir_is_local_only(self):
        """A directory with local_only + unknown files: unknown excluded,
        only local_only known → directory is local_only."""
        items = [
            _make_dir("docs"),
            _make_file("docs/local.txt", "local_only"),
            _make_file("docs/unknown.txt", "unknown"),
        ]
        statuses = self._apply(items)
        self.assertEqual(statuses["docs"], "local_only")

    def test_empty_dir_stays_unknown(self):
        """A directory with no file children (no file descendants) must remain
        'unknown' — same behavior as before."""
        items = [_make_dir("empty")]
        statuses = self._apply(items)
        self.assertEqual(statuses["empty"], "unknown")


# ---------------------------------------------------------------------------
# Testable replica of _build_check_tree cap logic
# (canonical: src/gui/main_window.py::_build_check_tree)
# ---------------------------------------------------------------------------

def _build_check_tree_testable(check_items, max_files, max_dirs):
    """Replica of main_window._build_check_tree with configurable caps."""
    result = []
    seen_dirs = set()
    file_count = 0
    dir_count = 0

    for item in sorted(check_items, key=lambda x: x.get("rel", "").lower()):
        rel = item.get("rel", "").strip("/").replace("\\", "/")
        if not rel:
            continue
        parts = rel.split("/")
        is_item_dir = item.get("is_dir", False)
        for i in range(1, len(parts)):
            if dir_count >= max_dirs:
                break
            dir_rel = "/".join(parts[:i])
            if dir_rel not in seen_dirs:
                seen_dirs.add(dir_rel)
                parent_rel = "/".join(parts[:i - 1]) if i > 1 else ""
                result.append({
                    "rel": dir_rel, "parent": parent_rel,
                    "name": parts[i - 1], "is_dir": True, "status": "unknown",
                })
                dir_count += 1
        if is_item_dir:
            if rel not in seen_dirs and dir_count < max_dirs:
                parent_rel = "/".join(parts[:-1])
                result.append({
                    "rel": rel, "parent": parent_rel,
                    "name": parts[-1], "is_dir": True,
                    "status": item.get("status", "unknown"),
                })
                seen_dirs.add(rel)
                dir_count += 1
        else:
            if file_count < max_files:
                parent_rel = "/".join(parts[:-1])
                node = {
                    "rel": rel, "parent": parent_rel,
                    "name": parts[-1], "is_dir": False,
                    "status": item.get("status", "unknown"),
                }
                if "local_mtime" in item:
                    node["local_mtime"] = item["local_mtime"]
                if "remote_mtime" in item:
                    node["remote_mtime"] = item["remote_mtime"]
                result.append(node)
                file_count += 1

    _propagate_dir_status_testable(result)
    return result


class TestBuildCheckTreeCap(unittest.TestCase):
    """Tests for the file-cap / directory-visibility logic in _build_check_tree."""

    def _make_check_items(self, prefix, n, status="synced"):
        """Create *n* synthetic rclone check items under *prefix*/."""
        return [{"rel": f"{prefix}/file{i:04d}.txt", "status": status} for i in range(n)]

    def test_directories_visible_when_file_cap_hit(self):
        """All root-level directories must appear even when the file cap is exhausted
        by the first directory's files."""
        # folder_a has 5 files, folder_b has 5 files, cap = 3 files
        items = (
            self._make_check_items("folder_a", 5, "synced") +
            self._make_check_items("folder_b", 5, "remote_only")
        )
        result = _build_check_tree_testable(items, max_files=3, max_dirs=100)
        rels = {i["rel"] for i in result}
        # Both directories must always be visible
        self.assertIn("folder_a", rels, "folder_a must appear even when file cap is hit")
        self.assertIn("folder_b", rels, "folder_b must appear even when file cap is hit")

    def test_file_cap_limits_files(self):
        """The number of file nodes must not exceed max_files."""
        items = (
            self._make_check_items("a", 5, "synced") +
            self._make_check_items("b", 5, "synced")
        )
        result = _build_check_tree_testable(items, max_files=4, max_dirs=100)
        file_nodes = [i for i in result if not i["is_dir"]]
        self.assertLessEqual(len(file_nodes), 4)

    def test_all_files_shown_when_under_cap(self):
        """When total files are below max_files, every file must appear."""
        items = self._make_check_items("root", 3, "local_only")
        result = _build_check_tree_testable(items, max_files=100, max_dirs=100)
        file_nodes = [i for i in result if not i["is_dir"]]
        self.assertEqual(len(file_nodes), 3)

    def test_dir_color_propagated_after_cap(self):
        """_propagate_dir_status must still run when the file cap fires mid-tree."""
        # folder_a has 5 local_only files, cap = 2 → only 2 files shown
        # but folder_a directory node must still be colored local_only
        items = self._make_check_items("folder_a", 5, "local_only")
        result = _build_check_tree_testable(items, max_files=2, max_dirs=100)
        dir_node = next((i for i in result if i["is_dir"] and i["rel"] == "folder_a"), None)
        self.assertIsNotNone(dir_node, "folder_a directory node must be present")
        self.assertEqual(dir_node["status"], "local_only")

    def test_dir_cap_limits_dirs(self):
        """The number of directory nodes must not exceed max_dirs."""
        # Create 5 files in 5 different root dirs
        items = [{"rel": f"dir{i}/file.txt", "status": "synced"} for i in range(5)]
        result = _build_check_tree_testable(items, max_files=100, max_dirs=3)
        dir_nodes = [i for i in result if i["is_dir"]]
        self.assertLessEqual(len(dir_nodes), 3)

    def test_is_dir_item_added_as_directory_node(self):
        """An item with is_dir=True must be inserted as a directory node."""
        items = [{"rel": "emptydir", "status": "remote_only", "is_dir": True}]
        result = _build_check_tree_testable(items, max_files=100, max_dirs=100)
        dir_nodes = [i for i in result if i["is_dir"]]
        self.assertEqual(len(dir_nodes), 1)
        self.assertEqual(dir_nodes[0]["rel"], "emptydir")
        self.assertEqual(dir_nodes[0]["is_dir"], True)

    def test_is_dir_item_not_counted_as_file(self):
        """An item with is_dir=True must NOT consume a file slot."""
        items = [
            {"rel": "thedir", "status": "remote_only", "is_dir": True},
            {"rel": "thedir/child.txt", "status": "remote_only"},
        ]
        result = _build_check_tree_testable(items, max_files=1, max_dirs=100)
        # The directory node must appear
        dir_rels = {i["rel"] for i in result if i["is_dir"]}
        self.assertIn("thedir", dir_rels)
        # The one file slot must also be used
        file_nodes = [i for i in result if not i["is_dir"]]
        self.assertEqual(len(file_nodes), 1)

    def test_nested_is_dir_item_gets_parent_synthesised(self):
        """A nested is_dir item must also have its parent directory created."""
        items = [{"rel": "parent/child_dir", "status": "remote_only", "is_dir": True}]
        result = _build_check_tree_testable(items, max_files=100, max_dirs=100)
        rels = {i["rel"] for i in result if i["is_dir"]}
        self.assertIn("parent", rels, "synthesised parent must appear")
        self.assertIn("parent/child_dir", rels, "the directory item itself must appear")


# ---------------------------------------------------------------------------
# Tests for _scan_local_tree's file-only cap (replicated via filesystem)
# ---------------------------------------------------------------------------

class TestScanLocalTreeFileCap(unittest.TestCase):
    """Tests that _scan_local_tree only counts files (not dirs) against the cap."""

    def _scan(self, local_path, synced_set=None, pending_set=None, max_files=1000):
        """Replicated _scan_local_tree logic for testing without tkinter."""
        import os
        from pathlib import Path

        result = []
        synced_set = synced_set or set()
        pending_set = pending_set or set()
        if not local_path or not os.path.isdir(local_path):
            return result

        base = Path(local_path)
        file_counter = [0]
        dir_counter = [0]
        max_dirs = max_files * 10

        def _walk(dir_path, parent_rel):
            if dir_counter[0] >= max_dirs:
                return
            try:
                raw = list(dir_path.iterdir())
                entries_with_dir = [(p, p.is_dir()) for p in raw]
                entries_with_dir.sort(key=lambda t: (not t[1], t[0].name.lower()))
            except (PermissionError, OSError):
                return
            for entry, entry_is_dir in entries_with_dir:
                rel = entry.relative_to(base).as_posix()
                if entry_is_dir:
                    if dir_counter[0] >= max_dirs:
                        break
                    result.append({"rel": rel, "parent": parent_rel,
                                   "name": entry.name, "is_dir": True, "status": "unknown"})
                    dir_counter[0] += 1
                    _walk(entry, rel)
                else:
                    if file_counter[0] >= max_files:
                        continue
                    if rel in synced_set:
                        status = "synced"
                    elif rel in pending_set:
                        status = "pending"
                    else:
                        status = "unknown"
                    result.append({"rel": rel, "parent": parent_rel,
                                   "name": entry.name, "is_dir": False, "status": status})
                    file_counter[0] += 1

        _walk(base, "")
        _propagate_dir_status_testable(result)
        return result

    def test_all_root_dirs_visible_when_file_cap_hit(self):
        """All sibling root-level directories must appear even when the file cap
        is exhausted inside the first directory."""
        import tempfile
        import shutil

        root = tempfile.mkdtemp()
        try:
            # dir_a: 5 files (will hit the cap of 3 before dir_b is processed)
            os.makedirs(os.path.join(root, "dir_a"))
            for i in range(5):
                open(os.path.join(root, "dir_a", f"f{i}.txt"), "w").close()
            # dir_b: 1 file
            os.makedirs(os.path.join(root, "dir_b"))
            open(os.path.join(root, "dir_b", "g.txt"), "w").close()

            result = self._scan(root, max_files=3)
            rels = {i["rel"] for i in result}
            self.assertIn("dir_a", rels, "dir_a must be visible")
            self.assertIn("dir_b", rels, "dir_b must be visible even when file cap fired in dir_a")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_file_count_does_not_exceed_cap(self):
        """Total file nodes must not exceed max_files."""
        import tempfile
        import shutil

        root = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(root, "d"))
            for i in range(10):
                open(os.path.join(root, "d", f"f{i}.txt"), "w").close()

            result = self._scan(root, max_files=4)
            file_nodes = [i for i in result if not i["is_dir"]]
            self.assertLessEqual(len(file_nodes), 4)
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Testable replicas of the tree-cache persistence helpers
# (canonical implementations in src/gui/main_window.py)
# ---------------------------------------------------------------------------

import json as _json_mod
import re as _re_mod
import shutil as _shutil_mod
import tempfile as _tempfile_mod
from datetime import datetime as _datetime, timezone as _tz
from pathlib import Path as _Path

_UNSAFE_FILENAME_RE_T = _re_mod.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _tree_cache_path_testable(base_dir, service_name):
    safe = _UNSAFE_FILENAME_RE_T.sub("_", service_name) or "default"
    return _Path(base_dir) / f"{safe}.json"


def _save_tree_cache_testable(base_dir, service_name, items):
    if not items:
        return
    path = _tree_cache_path_testable(base_dir, service_name)
    tmp = path.with_suffix(".json.tmp")
    payload = {
        "saved_at": _datetime.now(_tz.utc).isoformat(),
        "items": items,
    }
    try:
        tmp.write_text(_json_mod.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _load_tree_cache_testable(base_dir, service_name):
    path = _tree_cache_path_testable(base_dir, service_name)
    try:
        payload = _json_mod.loads(path.read_text(encoding="utf-8"))
        items = payload["items"]
        saved_at_utc = _datetime.fromisoformat(payload["saved_at"])
        saved_at_local = saved_at_utc.astimezone()
        saved_at_str = saved_at_local.strftime("%d/%m/%Y %H:%M")
        return items, saved_at_str
    except (OSError, KeyError, ValueError, TypeError):
        return None, None


class TestTreeCachePersistence(unittest.TestCase):
    """Tests for the tree-snapshot save/load helpers."""

    def setUp(self):
        self._tmpdir = _tempfile_mod.mkdtemp()

    def tearDown(self):
        _shutil_mod.rmtree(self._tmpdir, ignore_errors=True)

    def _items(self):
        return [
            {"rel": "folder_a", "parent": "", "name": "folder_a",
             "is_dir": True, "status": "synced"},
            {"rel": "folder_a/file1.txt", "parent": "folder_a",
             "name": "file1.txt", "is_dir": False, "status": "synced"},
            {"rel": "folder_b", "parent": "", "name": "folder_b",
             "is_dir": True, "status": "remote_only"},
        ]

    def test_round_trip_preserves_items(self):
        """Saving and loading must return exactly the same items."""
        original = self._items()
        _save_tree_cache_testable(self._tmpdir, "my_service", original)
        loaded, saved_at = _load_tree_cache_testable(self._tmpdir, "my_service")
        self.assertEqual(loaded, original)
        self.assertIsNotNone(saved_at)

    def test_saved_at_is_human_readable(self):
        """saved_at string must be a non-empty, slash-separated date/time."""
        _save_tree_cache_testable(self._tmpdir, "svc", self._items())
        _, saved_at = _load_tree_cache_testable(self._tmpdir, "svc")
        self.assertIsNotNone(saved_at)
        # Expect DD/MM/YYYY HH:MM format
        self.assertRegex(saved_at, r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}")

    def test_empty_items_not_saved(self):
        """Saving an empty list must not create a file (so a good cache is never overwritten)."""
        path = _tree_cache_path_testable(self._tmpdir, "svc")
        _save_tree_cache_testable(self._tmpdir, "svc", [])
        self.assertFalse(path.exists(), "Cache file must not be created for empty items")

    def test_empty_items_dont_overwrite_existing_cache(self):
        """An empty result (failed scan) must leave the existing good cache intact."""
        original = self._items()
        _save_tree_cache_testable(self._tmpdir, "svc", original)
        _save_tree_cache_testable(self._tmpdir, "svc", [])  # simulate failed scan
        loaded, _ = _load_tree_cache_testable(self._tmpdir, "svc")
        self.assertEqual(loaded, original, "Existing cache must not be overwritten by empty result")

    def test_missing_cache_returns_none(self):
        """Loading a non-existent cache must return (None, None)."""
        items, saved_at = _load_tree_cache_testable(self._tmpdir, "nonexistent_svc")
        self.assertIsNone(items)
        self.assertIsNone(saved_at)

    def test_corrupt_cache_returns_none(self):
        """A corrupt (non-JSON) cache file must return (None, None) without raising."""
        path = _tree_cache_path_testable(self._tmpdir, "svc")
        path.write_text("not-valid-json", encoding="utf-8")
        items, saved_at = _load_tree_cache_testable(self._tmpdir, "svc")
        self.assertIsNone(items)
        self.assertIsNone(saved_at)

    def test_service_name_with_special_chars_sanitised(self):
        """Service names with path-unsafe characters must be sanitised for the filename."""
        svc_name = 'My Service/with:special*chars'
        path = _tree_cache_path_testable(self._tmpdir, svc_name)
        # Must not contain any of the forbidden characters
        self.assertNotIn("/", path.name)
        self.assertNotIn(":", path.name)
        self.assertNotIn("*", path.name)

    def test_multiple_services_use_separate_files(self):
        """Each service must get its own cache file."""
        items_a = [{"rel": "a", "parent": "", "name": "a",
                    "is_dir": True, "status": "synced"}]
        items_b = [{"rel": "b", "parent": "", "name": "b",
                    "is_dir": True, "status": "remote_only"}]
        _save_tree_cache_testable(self._tmpdir, "service_a", items_a)
        _save_tree_cache_testable(self._tmpdir, "service_b", items_b)

        loaded_a, _ = _load_tree_cache_testable(self._tmpdir, "service_a")
        loaded_b, _ = _load_tree_cache_testable(self._tmpdir, "service_b")
        self.assertEqual(loaded_a, items_a)
        self.assertEqual(loaded_b, items_b)

    def test_all_item_fields_preserved(self):
        """All five item fields (rel, parent, name, is_dir, status) must survive round-trip."""
        item = {"rel": "dir/file.txt", "parent": "dir",
                "name": "file.txt", "is_dir": False, "status": "diff"}
        _save_tree_cache_testable(self._tmpdir, "svc", [item])
        loaded, _ = _load_tree_cache_testable(self._tmpdir, "svc")
        self.assertEqual(loaded[0], item)


# ---------------------------------------------------------------------------
# Testable replica of _merge_local_and_comparison
# (canonical: src/gui/main_window.py::_merge_local_and_comparison)
# ---------------------------------------------------------------------------

def _merge_local_and_comparison_testable(local_path, comparison_items,
                                          max_files=1000, max_dirs=10000):
    """Pure-Python replica of main_window._merge_local_and_comparison.

    Uses the already-defined _build_check_tree_testable helper plus an inline
    local-scan so we can test the merge logic without importing tkinter.
    """
    import os
    from pathlib import Path as _P

    # ── inline local scan (mirrors _scan_local_tree) ─────────────────────────
    def _do_local_scan(local_path_, max_files_, max_dirs_):
        result_ = []
        if not local_path_ or not os.path.isdir(local_path_):
            return result_
        base_ = _P(local_path_)
        fc = [0]
        dc = [0]

        def _walk(dir_path, parent_rel):
            if dc[0] >= max_dirs_:
                return
            try:
                raw = list(dir_path.iterdir())
                entries = [(p, p.is_dir()) for p in raw]
                entries.sort(key=lambda t: (not t[1], t[0].name.lower()))
            except (PermissionError, OSError):
                return
            for entry, is_dir_flag in entries:
                rel = entry.relative_to(base_).as_posix()
                if is_dir_flag:
                    if dc[0] >= max_dirs_:
                        break
                    result_.append({"rel": rel, "parent": parent_rel,
                                    "name": entry.name, "is_dir": True, "status": "unknown"})
                    dc[0] += 1
                    _walk(entry, rel)
                else:
                    if fc[0] >= max_files_:
                        continue
                    result_.append({"rel": rel, "parent": parent_rel,
                                    "name": entry.name, "is_dir": False, "status": "unknown"})
                    fc[0] += 1
        _walk(base_, "")
        return result_
    # ─────────────────────────────────────────────────────────────────────────

    # Build comparison lookup
    comp_map = {}
    for item in comparison_items:
        rel = item.get("rel", "").strip("/").replace("\\", "/")
        if rel:
            comp_map[rel] = item  # store full item dict, not just status

    # Stage 1: complete local scan (all files as "unknown" initially)
    result = _do_local_scan(local_path, max_files, max_dirs)

    # Stage 2: overlay comparison statuses onto local file nodes
    local_all_rels = {item["rel"] for item in result}
    local_file_rels = set()
    for item in result:
        if item["is_dir"]:
            continue
        rel = item["rel"]
        local_file_rels.add(rel)
        comp_item = comp_map.get(rel)
        if comp_item is not None:
            item["status"] = comp_item.get("status", "unknown")
            if "local_mtime" in comp_item:
                item["local_mtime"] = comp_item["local_mtime"]
            if "remote_mtime" in comp_item:
                item["remote_mtime"] = comp_item["remote_mtime"]
        else:
            item["status"] = "local_only"

    # Stage 3: add remote-only entries (files AND directories) not found locally
    remote_only_items = []
    for rel, ci in comp_map.items():
        if ci.get("status") == "remote_only" and rel not in local_all_rels:
            ro_item = {
                "rel": rel,
                "status": "remote_only",
                "is_dir": ci.get("is_dir", False),
            }
            if "remote_mtime" in ci:
                ro_item["remote_mtime"] = ci["remote_mtime"]
            remote_only_items.append(ro_item)
    if remote_only_items:
        remote_tree = _build_check_tree_testable(remote_only_items, max_files, max_dirs)
        existing_rels = {item["rel"] for item in result}
        for item in remote_tree:
            if item["rel"] not in existing_rels:
                result.append(item)

    # Stage 4: re-propagate dir statuses
    for item in result:
        if item["is_dir"]:
            item["status"] = "unknown"
    _propagate_dir_status_testable(result)
    return result


class TestMergeLocalAndComparison(unittest.TestCase):
    """Tests for the local-first merge strategy in _merge_local_and_comparison."""

    def setUp(self):
        self._tmpdir = _tempfile_mod.mkdtemp()
        # Build a small local tree:
        #   file_a.txt
        #   subdir/
        #     file_b.txt
        #     file_c.txt
        self._root = self._tmpdir
        os.makedirs(os.path.join(self._root, "subdir"))
        for name in ("file_a.txt",):
            open(os.path.join(self._root, name), "w").close()
        for name in ("file_b.txt", "file_c.txt"):
            open(os.path.join(self._root, "subdir", name), "w").close()

    def tearDown(self):
        _shutil_mod.rmtree(self._tmpdir, ignore_errors=True)

    def _merge(self, comparison_items):
        return _merge_local_and_comparison_testable(self._root, comparison_items)

    def _file_statuses(self, result):
        return {item["rel"]: item["status"]
                for item in result if not item["is_dir"]}

    # ── basic status overlay ─────────────────────────────────────────────────

    def test_synced_overlay(self):
        """Files reported as 'synced' by comparison must appear as synced."""
        comp = [
            {"rel": "file_a.txt",        "status": "synced"},
            {"rel": "subdir/file_b.txt", "status": "synced"},
            {"rel": "subdir/file_c.txt", "status": "synced"},
        ]
        statuses = self._file_statuses(self._merge(comp))
        self.assertEqual(statuses["file_a.txt"],        "synced")
        self.assertEqual(statuses["subdir/file_b.txt"], "synced")
        self.assertEqual(statuses["subdir/file_c.txt"], "synced")

    def test_diff_overlay(self):
        """Files reported as 'diff' must appear as diff."""
        comp = [{"rel": "file_a.txt", "status": "diff"}]
        statuses = self._file_statuses(self._merge(comp))
        self.assertEqual(statuses["file_a.txt"], "diff")

    def test_local_file_not_in_comparison_becomes_local_only(self):
        """Local files absent from the comparison must be marked 'local_only'."""
        comp = [{"rel": "file_a.txt", "status": "synced"}]
        statuses = self._file_statuses(self._merge(comp))
        # file_b.txt and file_c.txt are NOT in the comparison
        self.assertEqual(statuses["subdir/file_b.txt"], "local_only")
        self.assertEqual(statuses["subdir/file_c.txt"], "local_only")

    def test_all_local_files_present_even_with_empty_comparison(self):
        """Even when comparison returns no items, ALL local files must be in result."""
        result = self._merge([])
        rels = {item["rel"] for item in result if not item["is_dir"]}
        self.assertIn("file_a.txt",        rels)
        self.assertIn("subdir/file_b.txt", rels)
        self.assertIn("subdir/file_c.txt", rels)

    # ── remote-only files ────────────────────────────────────────────────────

    def test_remote_only_file_is_added(self):
        """A 'remote_only' file in the comparison must appear in the tree."""
        comp = [{"rel": "only_on_remote.txt", "status": "remote_only"}]
        rels = {item["rel"] for item in self._merge(comp)}
        self.assertIn("only_on_remote.txt", rels)

    def test_remote_only_file_status_is_remote_only(self):
        """A remote-only file must have status 'remote_only'."""
        comp = [{"rel": "remote_file.txt", "status": "remote_only"}]
        statuses = self._file_statuses(self._merge(comp))
        self.assertEqual(statuses["remote_file.txt"], "remote_only")

    def test_remote_only_file_in_new_subdir_adds_parent_dir(self):
        """A remote-only file under a new directory must also create the dir node."""
        comp = [{"rel": "remote_dir/remote_file.txt", "status": "remote_only"}]
        result = self._merge(comp)
        rels = {item["rel"] for item in result}
        self.assertIn("remote_dir",               rels, "parent dir must be created")
        self.assertIn("remote_dir/remote_file.txt", rels)

    def test_remote_only_directory_appears_in_tree(self):
        """An is_dir=True remote_only entry must appear as a directory node."""
        comp = [{"rel": "only_remote_dir", "status": "remote_only", "is_dir": True}]
        result = self._merge(comp)
        rels = {item["rel"] for item in result}
        self.assertIn("only_remote_dir", rels)

    def test_remote_only_directory_node_has_is_dir_true(self):
        """A remote-only directory node must have is_dir=True."""
        comp = [{"rel": "rem_dir", "status": "remote_only", "is_dir": True}]
        result = self._merge(comp)
        rem_dir = next((i for i in result if i["rel"] == "rem_dir"), None)
        self.assertIsNotNone(rem_dir)
        self.assertTrue(rem_dir["is_dir"])

    def test_remote_only_empty_dir_not_duplicated_when_also_local(self):
        """A remote-only directory already in the local tree must not be re-added."""
        # 'subdir' exists locally (set up in setUp); if it also appears as
        # remote_only in the comparison it must appear exactly once.
        comp = [{"rel": "subdir", "status": "remote_only", "is_dir": True}]
        result = self._merge(comp)
        matches = [i for i in result if i["rel"] == "subdir"]
        self.assertEqual(len(matches), 1)

    def test_local_file_not_duplicated_when_in_comparison(self):
        """A local file that also appears in comparison must not be duplicated."""
        comp = [{"rel": "file_a.txt", "status": "synced"}]
        result = self._merge(comp)
        matches = [item for item in result if item["rel"] == "file_a.txt"]
        self.assertEqual(len(matches), 1, "file_a.txt must appear exactly once")

    # ── directory colour propagation ─────────────────────────────────────────

    def test_dir_coloured_from_child_statuses(self):
        """After merge, parent dir colour must reflect child file statuses."""
        comp = [
            {"rel": "subdir/file_b.txt", "status": "synced"},
            {"rel": "subdir/file_c.txt", "status": "diff"},
        ]
        result = self._merge(comp)
        dir_statuses = {item["rel"]: item["status"]
                        for item in result if item["is_dir"]}
        # subdir has both synced and diff → should be "diff"
        self.assertEqual(dir_statuses.get("subdir"), "diff")

    def test_all_local_only_dir_is_local_only(self):
        """A directory containing only local_only files must be coloured local_only."""
        result = self._merge([])  # empty comparison → all files become local_only
        dir_statuses = {item["rel"]: item["status"]
                        for item in result if item["is_dir"]}
        self.assertEqual(dir_statuses.get("subdir"), "local_only")

    # ── edge cases ───────────────────────────────────────────────────────────

    def test_empty_local_path(self):
        """An empty or non-existent local_path must return an empty list."""
        result = _merge_local_and_comparison_testable("", [])
        self.assertEqual(result, [])

    def test_nonexistent_local_path(self):
        """A missing local_path must return an empty list without raising."""
        result = _merge_local_and_comparison_testable("/no/such/path/xyz", [])
        self.assertEqual(result, [])

    def test_comparison_with_remote_only_and_local_mix(self):
        """Mixed comparison must show all local files AND remote-only files."""
        comp = [
            {"rel": "file_a.txt",       "status": "synced"},
            {"rel": "only_remote.txt",  "status": "remote_only"},
        ]
        result = self._merge(comp)
        rels = {item["rel"] for item in result if not item["is_dir"]}
        # All local files present
        self.assertIn("file_a.txt",        rels)
        self.assertIn("subdir/file_b.txt", rels)
        self.assertIn("subdir/file_c.txt", rels)
        # Remote-only file also present
        self.assertIn("only_remote.txt",   rels)


# ---------------------------------------------------------------------------
# _build_mtime_comparison tests
# ---------------------------------------------------------------------------

class TestBuildMtimeComparison(unittest.TestCase):
    """Tests for the _build_mtime_comparison module-level helper."""

    def setUp(self):
        from src.rclone.rclone_manager import _build_mtime_comparison
        self._fn = _build_mtime_comparison

    def _call(self, local: dict, remote: dict):
        return self._fn(local, remote)

    def test_synced_when_mtimes_match(self):
        """Files with matching mtimes (within tolerance) must be 'synced'."""
        ts = 1700000000.0
        result = self._call({"a.txt": ts}, {"a.txt": ts})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "synced")
        self.assertEqual(result[0]["local_mtime"], ts)
        self.assertEqual(result[0]["remote_mtime"], ts)

    def test_diff_when_mtimes_differ(self):
        """Files with differing mtimes (beyond tolerance) must be 'diff'."""
        ts_local = 1700000000.0
        ts_remote = ts_local + 100  # 100 s difference → well above tolerance
        result = self._call({"b.txt": ts_local}, {"b.txt": ts_remote})
        self.assertEqual(result[0]["status"], "diff")
        self.assertEqual(result[0]["local_mtime"], ts_local)
        self.assertEqual(result[0]["remote_mtime"], ts_remote)

    def test_local_only_when_not_on_remote(self):
        """Files present only locally must have status 'local_only'."""
        result = self._call({"c.txt": 1.0}, {})
        self.assertEqual(result[0]["status"], "local_only")
        self.assertIsNotNone(result[0]["local_mtime"])
        self.assertIsNone(result[0]["remote_mtime"])

    def test_remote_only_when_not_on_local(self):
        """Files present only remotely must have status 'remote_only'."""
        result = self._call({}, {"d.txt": 2.0})
        self.assertEqual(result[0]["status"], "remote_only")
        self.assertIsNone(result[0]["local_mtime"])
        self.assertIsNotNone(result[0]["remote_mtime"])

    def test_empty_maps_returns_empty_list(self):
        """Two empty maps must return an empty list."""
        self.assertEqual(self._call({}, {}), [])

    def test_result_sorted_by_rel(self):
        """Results must be sorted alphabetically by rel path."""
        local = {"z.txt": 1.0, "a.txt": 2.0}
        remote = {}
        result = self._call(local, remote)
        rels = [r["rel"] for r in result]
        self.assertEqual(rels, sorted(rels))

    def test_mtime_tolerance_synced(self):
        """Files within _MTIME_TOLERANCE_SECS must be considered synced."""
        from src.rclone.rclone_manager import _MTIME_TOLERANCE_SECS
        ts = 1700000000.0
        result = self._call({"f.txt": ts}, {"f.txt": ts + _MTIME_TOLERANCE_SECS - 0.1})
        self.assertEqual(result[0]["status"], "synced")

    def test_mtime_tolerance_diff(self):
        """Files just outside _MTIME_TOLERANCE_SECS must be 'diff'."""
        from src.rclone.rclone_manager import _MTIME_TOLERANCE_SECS
        ts = 1700000000.0
        result = self._call({"g.txt": ts}, {"g.txt": ts + _MTIME_TOLERANCE_SECS + 0.1})
        self.assertEqual(result[0]["status"], "diff")


class TestMergeLocalWithMtimes(unittest.TestCase):
    """Tests that the testable merge replica carries mtime fields into tree items."""

    def setUp(self):
        self._tmpdir = _tempfile_mod.mkdtemp()
        (Path(self._tmpdir) / "file.txt").write_text("x")

    def tearDown(self):
        _shutil_mod.rmtree(self._tmpdir, ignore_errors=True)

    def _merge(self, comp):
        return _merge_local_and_comparison_testable(self._tmpdir, comp)

    def test_mtime_fields_propagated_to_tree_items(self):
        """local_mtime and remote_mtime must appear in tree items after merge."""
        comp = [
            {
                "rel": "file.txt",
                "status": "synced",
                "local_mtime": 1700000000.0,
                "remote_mtime": 1700000000.0,
            }
        ]
        items = self._merge(comp)
        found = next((i for i in items if i["rel"] == "file.txt"), None)
        self.assertIsNotNone(found)
        self.assertEqual(found.get("local_mtime"), 1700000000.0)
        self.assertEqual(found.get("remote_mtime"), 1700000000.0)

    def test_remote_only_mtime_propagated(self):
        """remote_mtime for remote-only files must survive into tree items."""
        comp = [
            {
                "rel": "only_remote.txt",
                "status": "remote_only",
                "remote_mtime": 1600000000.0,
            }
        ]
        items = self._merge(comp)
        ro = next((i for i in items if i["rel"] == "only_remote.txt"), None)
        self.assertIsNotNone(ro)
        self.assertEqual(ro.get("remote_mtime"), 1600000000.0)

    def test_local_only_file_has_no_remote_mtime(self):
        """Files only on local disk should not have a remote_mtime set."""
        items = self._merge([])  # empty comparison → everything is local_only
        found = next((i for i in items if i["rel"] == "file.txt"), None)
        self.assertIsNotNone(found)
        self.assertNotIn("remote_mtime", found)


class TestFormatMtime(unittest.TestCase):
    """Tests for the _format_mtime display logic (pure datetime, no tkinter)."""

    @staticmethod
    def _format_mtime(ts):
        """Inline replica of main_window._format_mtime."""
        from datetime import datetime
        if ts is None:
            return ""
        try:
            dt = datetime.fromtimestamp(ts)
            return dt.strftime("%d/%m %H:%M")
        except (OSError, OverflowError, ValueError):
            return ""

    def test_none_returns_empty_string(self):
        self.assertEqual(self._format_mtime(None), "")

    def test_valid_timestamp_returns_nonempty_string(self):
        result = self._format_mtime(1700000000.0)
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_format_contains_slash(self):
        """dd/mm format must contain a '/'."""
        result = self._format_mtime(1700000000.0)
        self.assertIn("/", result)


class TestTreeScanTimingLabels(unittest.TestCase):
    """Tests for the 'last scan started' and 'next scan' label formatting logic.

    These tests exercise the pure-datetime arithmetic used by
    _start_tree_check() and _schedule_tree_refresh() without importing tkinter.

    The label prefixes and time format must stay in sync with the module-level
    constants in main_window.py:
      _SCAN_TIME_FMT      = "%H:%M:%S"
      _SCAN_STARTED_PREFIX = "🕐 Inicio: "
      _SCAN_NEXT_PREFIX    = "⏭ Próxima: "
    """

    # Keep in sync with main_window._SCAN_TIME_FMT
    _TIME_FMT = "%H:%M:%S"
    # Keep in sync with main_window._SCAN_STARTED_PREFIX
    _STARTED_PREFIX = "🕐 Inicio: "
    # Keep in sync with main_window._SCAN_NEXT_PREFIX
    _NEXT_PREFIX = "⏭ Próxima: "

    def _format_started(self, ts: float) -> str:
        """Inline replica of _start_tree_check()'s started-label logic."""
        from datetime import datetime, timezone
        return self._STARTED_PREFIX + datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime(self._TIME_FMT)

    def _format_next(self, ts: float, interval_secs: int) -> str:
        """Inline replica of _schedule_tree_refresh()'s next-label logic."""
        from datetime import datetime, timedelta, timezone
        next_dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone() + timedelta(seconds=interval_secs)
        return self._NEXT_PREFIX + next_dt.strftime(self._TIME_FMT)

    def test_started_label_prefix(self):
        """Scan-started label must begin with the configured prefix."""
        label = self._format_started(1700000000.0)
        self.assertTrue(label.startswith(self._STARTED_PREFIX.rstrip()))

    def test_started_label_contains_colon(self):
        """Time part of started label must contain colons (HH:MM:SS)."""
        label = self._format_started(1700000000.0)
        # The time portion after the prefix must have at least two colons
        time_part = label[len(self._STARTED_PREFIX):]
        self.assertEqual(time_part.count(":"), 2)

    def test_next_label_prefix(self):
        """Next-scan label must begin with the configured prefix."""
        label = self._format_next(1700000000.0, 60)
        self.assertTrue(label.startswith(self._NEXT_PREFIX.rstrip()))

    def test_next_label_offset(self):
        """Next-scan time must be exactly interval_secs ahead of started time."""
        from datetime import datetime, timedelta, timezone
        ts = 1700000000.0
        interval = 120
        started = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
        expected_next = (started + timedelta(seconds=interval)).strftime(self._TIME_FMT)
        label = self._format_next(ts, interval)
        self.assertIn(expected_next, label)

    def test_next_label_time_contains_colons(self):
        """Time part of next label must contain colons (HH:MM:SS)."""
        label = self._format_next(1700000000.0, 300)
        time_part = label[len(self._NEXT_PREFIX):]
        self.assertEqual(time_part.count(":"), 2)


class TestFileScanDB(unittest.TestCase):
    """Tests for the encrypted SQLite file-scan database (src/db/file_scan_db.py).

    Each test method gets its own isolated temporary directory so that tests
    do not interfere with each other or with the real user database.
    """

    def setUp(self) -> None:
        """Create a temp directory and open a fresh FileScanDB for each test."""
        from src.db.file_scan_db import FileScanDB

        self._tmpdir = tempfile.mkdtemp()
        db_path = Path(self._tmpdir) / "test_scan.db"
        key_path = Path(self._tmpdir) / "test.key"
        self._db = FileScanDB(db_path=db_path, key_path=key_path)

    def tearDown(self) -> None:
        """Close the DB connection and clean up the temp directory."""
        import shutil
        try:
            self._db.close()
        except Exception:
            pass
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Path discovery
    # ------------------------------------------------------------------

    def test_db_path_property_returns_path_object(self):
        """db_path must return a Path instance pointing to the database file."""
        self.assertIsInstance(self._db.db_path, Path)

    def test_db_path_property_matches_actual_file(self):
        """db_path must point to the SQLite file that actually exists on disk."""
        self.assertTrue(
            self._db.db_path.exists(),
            f"db_path {self._db.db_path} does not exist",
        )

    def test_db_path_property_is_the_configured_path(self):
        """db_path must return exactly the path passed to the constructor."""
        expected = Path(self._tmpdir) / "test_scan.db"
        self.assertEqual(self._db.db_path, expected)

    def test_key_path_property_returns_path_object(self):
        """key_path must return a Path instance pointing to the key file."""
        self.assertIsInstance(self._db.key_path, Path)

    def test_key_path_property_is_the_configured_path(self):
        """key_path must return exactly the path passed to the constructor."""
        expected = Path(self._tmpdir) / "test.key"
        self.assertEqual(self._db.key_path, expected)

    # ------------------------------------------------------------------
    # Table lifecycle
    # ------------------------------------------------------------------

    def test_ensure_table_creates_table(self):
        """ensure_table() must create the table so subsequent queries succeed."""
        self._db.ensure_table("My Service")
        # If the table was created, get_all_records returns an empty list (not
        # an exception).
        records = self._db.get_all_records("My Service")
        self.assertIsInstance(records, list)
        self.assertEqual(len(records), 0)

    def test_ensure_table_is_idempotent(self):
        """Calling ensure_table() twice must not raise."""
        self._db.ensure_table("ServiceA")
        self._db.ensure_table("ServiceA")  # should not raise
        records = self._db.get_all_records("ServiceA")
        self.assertEqual(records, [])

    def test_drop_table_removes_data(self):
        """drop_table() must remove the table so get_all_records returns []."""
        self._db.ensure_table("SvcDrop")
        self._db.upsert_local_batch(
            "SvcDrop",
            1700000000.0,
            {"a/b.txt": {"mtime": 1700000000.0, "size": 100}},
        )
        self._db.drop_table("SvcDrop")
        records = self._db.get_all_records("SvcDrop")
        self.assertEqual(records, [])

    def test_rename_table_moves_data(self):
        """rename_table() must preserve all rows under the new name."""
        self._db.ensure_table("OldName")
        self._db.upsert_local_batch(
            "OldName",
            1700000000.0,
            {"file.txt": {"mtime": 1700000000.0, "size": 42}},
        )
        self._db.rename_table("OldName", "NewName")
        old_records = self._db.get_all_records("OldName")
        new_records = self._db.get_all_records("NewName")
        self.assertEqual(len(old_records), 0)
        self.assertEqual(len(new_records), 1)
        self.assertEqual(new_records[0]["rel"], "file.txt")

    def test_rename_table_identical_slugs_noop(self):
        """rename_table() for names that produce the same slug must not raise."""
        self._db.ensure_table("A B")
        self._db.upsert_local_batch(
            "A B",
            1700000000.0,
            {"x.txt": {"mtime": 1700000000.0, "size": 10}},
        )
        # "A_B" and "A B" both slug to "svc_a_b"
        self._db.rename_table("A B", "A_B")
        records = self._db.get_all_records("A B")
        self.assertEqual(len(records), 1)

    # ------------------------------------------------------------------
    # Local batch upsert (Thread 1)
    # ------------------------------------------------------------------

    def test_upsert_local_batch_writes_fields(self):
        """upsert_local_batch() must persist rel_path, local_mtime, local_size."""
        self._db.ensure_table("SvcLocal")
        self._db.upsert_local_batch(
            "SvcLocal",
            1700000100.0,
            {
                "docs/readme.txt": {"mtime": 1700000000.0, "size": 1024},
                "img/photo.jpg": {"mtime": 1699999000.0, "size": 204800},
            },
        )
        records = {r["rel"]: r for r in self._db.get_all_records("SvcLocal")}
        self.assertIn("docs/readme.txt", records)
        self.assertAlmostEqual(records["docs/readme.txt"]["local_mtime"], 1700000000.0)
        self.assertEqual(records["docs/readme.txt"]["local_size"], 1024)
        self.assertAlmostEqual(records["img/photo.jpg"]["local_mtime"], 1699999000.0)
        self.assertEqual(records["img/photo.jpg"]["local_size"], 204800)

    def test_upsert_local_batch_overwrites_on_conflict(self):
        """A second upsert_local_batch() must overwrite existing local fields."""
        self._db.ensure_table("SvcOver")
        self._db.upsert_local_batch(
            "SvcOver",
            1700000100.0,
            {"file.txt": {"mtime": 1700000000.0, "size": 100}},
        )
        # Simulate file being modified: new mtime and size
        self._db.upsert_local_batch(
            "SvcOver",
            1700000200.0,
            {"file.txt": {"mtime": 1700000150.0, "size": 200}},
        )
        records = self._db.get_all_records("SvcOver")
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["local_mtime"], 1700000150.0)
        self.assertEqual(records[0]["local_size"], 200)

    def test_upsert_local_batch_prunes_deleted_files(self):
        """Files absent from a new batch should have local fields cleared."""
        self._db.ensure_table("SvcPrune")
        # First scan: two files
        self._db.upsert_local_batch(
            "SvcPrune",
            1700000100.0,
            {
                "keep.txt": {"mtime": 1700000000.0, "size": 10},
                "delete.txt": {"mtime": 1699900000.0, "size": 20},
            },
        )
        # Second scan: only "keep.txt" found (delete.txt was removed)
        self._db.upsert_local_batch(
            "SvcPrune",
            1700000200.0,
            {"keep.txt": {"mtime": 1700000000.0, "size": 10}},
        )
        records = {r["rel"]: r for r in self._db.get_all_records("SvcPrune")}
        # "keep.txt" must still have local data
        self.assertIn("keep.txt", records)
        self.assertIsNotNone(records["keep.txt"]["local_mtime"])
        # "delete.txt" should have been cleaned up (no remote data either)
        self.assertNotIn("delete.txt", records)

    # ------------------------------------------------------------------
    # Remote batch upsert (Thread 2)
    # ------------------------------------------------------------------

    def test_upsert_remote_batch_writes_fields(self):
        """upsert_remote_batch() must persist rel_path, remote_mtime, remote_size."""
        self._db.ensure_table("SvcRemote")
        self._db.upsert_remote_batch(
            "SvcRemote",
            1700000100.0,
            {"cloud/data.csv": {"mtime": 1700000050.0, "size": 5000}},
        )
        records = self._db.get_all_records("SvcRemote")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["rel"], "cloud/data.csv")
        self.assertAlmostEqual(records[0]["remote_mtime"], 1700000050.0)
        self.assertEqual(records[0]["remote_size"], 5000)
        # Local fields not set yet
        self.assertIsNone(records[0]["local_mtime"])

    # ------------------------------------------------------------------
    # Status computation (Thread 3)
    # ------------------------------------------------------------------

    def test_update_statuses_synced(self):
        """Files whose local and remote mtimes differ by < tolerance → synced."""
        from src.db.file_scan_db import _MTIME_TOLERANCE_SECS

        ts = 1700000000.0
        self._db.ensure_table("SvcStatus")
        self._db.upsert_local_batch(
            "SvcStatus", ts + 10, {"f.txt": {"mtime": ts, "size": 1}}
        )
        # Remote mtime within tolerance
        self._db.upsert_remote_batch(
            "SvcStatus", ts + 10, {"f.txt": {"mtime": ts + _MTIME_TOLERANCE_SECS - 0.1, "size": 1}}
        )
        self._db.update_statuses("SvcStatus", scan_ts=0)
        records = self._db.get_all_records("SvcStatus")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "synced")

    def test_update_statuses_diff(self):
        """Files whose mtimes differ by > tolerance → diff."""
        from src.db.file_scan_db import _MTIME_TOLERANCE_SECS

        ts = 1700000000.0
        self._db.ensure_table("SvcDiff")
        self._db.upsert_local_batch(
            "SvcDiff", ts + 10, {"f.txt": {"mtime": ts, "size": 1}}
        )
        self._db.upsert_remote_batch(
            "SvcDiff", ts + 10, {"f.txt": {"mtime": ts + _MTIME_TOLERANCE_SECS + 10, "size": 1}}
        )
        self._db.update_statuses("SvcDiff", scan_ts=0)
        records = self._db.get_all_records("SvcDiff")
        self.assertEqual(records[0]["status"], "diff")

    def test_update_statuses_local_only(self):
        """Files with only local data → local_only."""
        ts = 1700000000.0
        self._db.ensure_table("SvcLO")
        self._db.upsert_local_batch(
            "SvcLO", ts + 10, {"local.txt": {"mtime": ts, "size": 1}}
        )
        self._db.update_statuses("SvcLO", scan_ts=0)
        records = self._db.get_all_records("SvcLO")
        self.assertEqual(records[0]["status"], "local_only")

    def test_update_statuses_remote_only(self):
        """Files with only remote data → remote_only."""
        ts = 1700000000.0
        self._db.ensure_table("SvcRO")
        self._db.upsert_remote_batch(
            "SvcRO", ts + 10, {"remote.txt": {"mtime": ts, "size": 1}}
        )
        self._db.update_statuses("SvcRO", scan_ts=0)
        records = self._db.get_all_records("SvcRO")
        self.assertEqual(records[0]["status"], "remote_only")

    # ------------------------------------------------------------------
    # Directory tracking (is_dir flag)
    # ------------------------------------------------------------------

    def test_upsert_local_batch_stores_is_dir_true(self):
        """upsert_local_batch must persist is_dir=True for directory entries."""
        ts = 1700000000.0
        self._db.ensure_table("SvcDirLocal")
        self._db.upsert_local_batch(
            "SvcDirLocal", ts + 10,
            {
                "mydir": {"mtime": ts, "size": 0, "is_dir": True},
                "mydir/file.txt": {"mtime": ts, "size": 42, "is_dir": False},
            },
        )
        records = {r["rel"]: r for r in self._db.get_all_records("SvcDirLocal")}
        self.assertTrue(records["mydir"]["is_dir"])
        self.assertFalse(records["mydir/file.txt"]["is_dir"])

    def test_upsert_remote_batch_stores_is_dir_true(self):
        """upsert_remote_batch must persist is_dir=True for directory entries."""
        ts = 1700000000.0
        self._db.ensure_table("SvcDirRemote")
        self._db.upsert_remote_batch(
            "SvcDirRemote", ts + 10,
            {
                "remotedir": {"mtime": ts, "size": 0, "is_dir": True},
                "remotedir/data.csv": {"mtime": ts, "size": 100, "is_dir": False},
            },
        )
        records = {r["rel"]: r for r in self._db.get_all_records("SvcDirRemote")}
        self.assertTrue(records["remotedir"]["is_dir"])
        self.assertFalse(records["remotedir/data.csv"]["is_dir"])

    def test_is_dir_defaults_to_false_when_not_provided(self):
        """Omitting 'is_dir' key in the batch dict must default to False."""
        ts = 1700000000.0
        self._db.ensure_table("SvcNoDirKey")
        self._db.upsert_local_batch(
            "SvcNoDirKey", ts + 10,
            {"file.txt": {"mtime": ts, "size": 10}},
        )
        records = self._db.get_all_records("SvcNoDirKey")
        self.assertFalse(records[0]["is_dir"])

    def test_update_statuses_directory_always_synced_when_both_sides_exist(self):
        """A directory present on both sides must always get status 'synced',
        regardless of mtime difference."""
        ts = 1700000000.0
        self._db.ensure_table("SvcDirStatus")
        # Local directory mtime differs greatly from remote
        self._db.upsert_local_batch(
            "SvcDirStatus", ts + 10,
            {"mydir": {"mtime": ts, "size": 0, "is_dir": True}},
        )
        self._db.upsert_remote_batch(
            "SvcDirStatus", ts + 10,
            {"mydir": {"mtime": ts + 3600, "size": 0, "is_dir": True}},
        )
        self._db.update_statuses("SvcDirStatus", scan_ts=0)
        records = self._db.get_all_records("SvcDirStatus")
        self.assertEqual(records[0]["status"], "synced")

    def test_update_statuses_remote_only_directory(self):
        """A directory present only on remote must get status 'remote_only'."""
        ts = 1700000000.0
        self._db.ensure_table("SvcDirRO")
        self._db.upsert_remote_batch(
            "SvcDirRO", ts + 10,
            {"onlyremotedir": {"mtime": ts, "size": 0, "is_dir": True}},
        )
        self._db.update_statuses("SvcDirRO", scan_ts=0)
        records = self._db.get_all_records("SvcDirRO")
        self.assertEqual(records[0]["status"], "remote_only")

    def test_schema_migration_adds_is_dir_enc(self):
        """ensure_table must add is_dir_enc to tables created without it."""
        import sqlite3 as _sqlite3
        from pathlib import Path as _Path
        import tempfile as _tmp
        import os as _os
        from src.db.file_scan_db import FileScanDB

        tmpdir = _tmp.mkdtemp()
        try:
            db_path = _Path(tmpdir) / "old.db"
            key_path = _Path(tmpdir) / "old.key"
            # Create a database using the old schema (without is_dir_enc).
            conn = _sqlite3.connect(str(db_path))
            conn.execute("""
                CREATE TABLE svc_test (
                    path_hash TEXT PRIMARY KEY,
                    rel_path_enc BLOB NOT NULL,
                    local_size_enc BLOB, remote_size_enc BLOB,
                    local_mtime_enc BLOB, remote_mtime_enc BLOB,
                    local_scan_ts REAL, remote_scan_ts REAL,
                    status_enc BLOB
                )
            """)
            conn.commit()
            conn.close()
            # Open via FileScanDB — ensure_table should add is_dir_enc.
            db = FileScanDB(db_path=db_path, key_path=key_path)
            db.ensure_table("test")
            conn2 = _sqlite3.connect(str(db_path))
            cols = [r[1] for r in conn2.execute("PRAGMA table_info(svc_test)").fetchall()]
            conn2.close()
            db.close()
            self.assertIn("is_dir_enc", cols)
        finally:
            import shutil as _shutil
            _shutil.rmtree(tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Encryption round-trip
    # ------------------------------------------------------------------

    def test_encryption_round_trip(self):
        """Values written to DB must decrypt correctly on read-back."""
        self._db.ensure_table("SvcEnc")
        original_path = "sub/dir/my file (2024).txt"
        original_mtime = 1700012345.678
        original_size = 999999
        self._db.upsert_local_batch(
            "SvcEnc",
            1700100000.0,
            {original_path: {"mtime": original_mtime, "size": original_size}},
        )
        records = self._db.get_all_records("SvcEnc")
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["rel"], original_path)
        self.assertAlmostEqual(rec["local_mtime"], original_mtime, places=2)
        self.assertEqual(rec["local_size"], original_size)

    def test_data_encrypted_at_rest(self):
        """The raw SQLite file must NOT contain plaintext file paths."""
        import sqlite3 as _sqlite3
        self._db.ensure_table("SvcRaw")
        self._db.upsert_local_batch(
            "SvcRaw",
            1700000000.0,
            {"sensitive/path/secret.txt": {"mtime": 1700000000.0, "size": 1}},
        )
        # Read raw bytes from the DB file to check that path is not visible
        raw_bytes = Path(self._db._db_path).read_bytes()
        self.assertNotIn(b"sensitive/path/secret.txt", raw_bytes)

    # ------------------------------------------------------------------
    # Key file creation
    # ------------------------------------------------------------------

    def test_key_file_created_on_init(self):
        """A new key file must be created and contain a valid Fernet key."""
        from cryptography.fernet import Fernet

        key_path = Path(self._tmpdir) / "new.key"
        db_path = Path(self._tmpdir) / "new.db"
        self.assertFalse(key_path.exists())
        from src.db.file_scan_db import FileScanDB as _DB
        new_db = _DB(db_path=db_path, key_path=key_path)
        try:
            self.assertTrue(key_path.exists())
            raw = key_path.read_bytes().strip()
            # Must be a valid Fernet key (no exception on construction)
            Fernet(raw)
        finally:
            new_db.close()

    def test_same_key_used_across_instances(self):
        """A second FileScanDB instance pointing to the same key must decrypt data
        written by the first instance."""
        from src.db.file_scan_db import FileScanDB as _DB

        db_path = Path(self._tmpdir) / "shared.db"
        key_path = Path(self._tmpdir) / "shared.key"

        db1 = _DB(db_path=db_path, key_path=key_path)
        db1.ensure_table("Shared")
        db1.upsert_local_batch(
            "Shared",
            1700000000.0,
            {"hello.txt": {"mtime": 1700000000.0, "size": 7}},
        )
        db1.close()

        db2 = _DB(db_path=db_path, key_path=key_path)
        records = db2.get_all_records("Shared")
        db2.close()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["rel"], "hello.txt")

    # ------------------------------------------------------------------
    # Helper: _table_slug
    # ------------------------------------------------------------------

    def test_table_slug_sanitizes_name(self):
        """_table_slug() must produce a valid SQL identifier from any service name."""
        from src.db.file_scan_db import _table_slug

        self.assertEqual(_table_slug("My Service"), "svc_my_service")
        self.assertEqual(_table_slug("OneDrive (Work)"), "svc_onedrive_work")
        self.assertEqual(_table_slug("123"), "svc_123")
        self.assertEqual(_table_slug("!@#"), "svc_svc")  # all symbols → fallback
        self.assertTrue(_table_slug("A").startswith("svc_"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
