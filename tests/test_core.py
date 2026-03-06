"""
test_core.py
------------
Unit tests for the core modules: ConfigManager, RcloneManager.
Run with:  python3 -m pytest tests/test_core.py -v
"""

import json
import os
import sys
import tempfile
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
    _bisync_cache_dir,
    _clear_bisync_stale_files,
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
        """clear_bisync_locks() should remove lock files matching the service remote."""
        self.config.add_service("LockSvc", "onedrive", "/tmp/lock_test")
        self.config.update_service("LockSvc", {"remote_name": "lockremote"})

        # Create a fake lock file in a temp cache dir
        fake_cache = Path(self._tmpdir) / "fake_bisync_cache"
        fake_cache.mkdir()
        lock = fake_cache / "lockremote_..tmp_lock_test.lck"
        lock.write_text("pid")

        errors = []
        self.rclone.on_error = lambda name, msg: errors.append(msg)

        with patch("src.rclone.rclone_manager._bisync_cache_dir", return_value=fake_cache):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
