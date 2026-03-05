"""
service_manager.py
------------------
High-level orchestration layer for managing sync services.
Wraps ConfigManager and RcloneManager to provide start/stop/status logic.
"""

import datetime
import threading
import time
from typing import Callable, Optional

from src.core.config_manager import ConfigManager
from src.core.rclone_manager import RcloneManager, PERSONAL_VAULT_EXCLUDE


class SyncStatus:
    """Constants representing the current sync state of a service."""
    IDLE = "idle"               # Not currently syncing (up to date)
    SYNCING = "syncing"         # Actively running bisync
    PAUSED = "paused"           # User has manually paused sync
    ERROR = "error"             # Last sync ended with an error


class ServiceManager:
    """
    Manages the lifecycle of sync services.
    Each service has its own background scheduler thread that fires bisync
    at the configured interval.
    """

    def __init__(self):
        # Shared config and rclone helpers
        self.config = ConfigManager()
        self.rclone = RcloneManager()
        # Runtime state keyed by service id
        # Each entry: { status, last_lines, changed_files, timer_thread, stop_event }
        self._state = {}
        # Callbacks registered by the UI to receive status updates
        self._status_callbacks = []

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def start_all(self):
        """Start background sync schedulers for all non-paused services."""
        for svc in self.config.get_services():
            if not svc.get("sync_paused", False):
                self.start_service(svc["id"])

    # ------------------------------------------------------------------
    # Service state helpers
    # ------------------------------------------------------------------

    def _get_state(self, service_id):
        """Return (or initialise) the runtime state dict for a service."""
        if service_id not in self._state:
            self._state[service_id] = {
                "status": SyncStatus.IDLE,
                "last_lines": [],        # Last rclone output lines (for debugging)
                "changed_files": [],     # Up to 50 recently synced file records
                "stop_event": threading.Event(),
                "timer_thread": None,
            }
        return self._state[service_id]

    def get_status(self, service_id):
        """Return the current SyncStatus string for the given service."""
        svc = self.config.get_service(service_id)
        if svc and svc.get("sync_paused"):
            return SyncStatus.PAUSED
        return self._get_state(service_id).get("status", SyncStatus.IDLE)

    def get_changed_files(self, service_id):
        """Return the list of recently changed file dicts for a service."""
        return self._get_state(service_id).get("changed_files", [])

    def get_last_sync_time(self, service_id):
        """Return the ISO timestamp string of the last successful sync, or None."""
        svc = self.config.get_service(service_id)
        return svc.get("last_sync") if svc else None

    # ------------------------------------------------------------------
    # Sync lifecycle
    # ------------------------------------------------------------------

    def start_service(self, service_id):
        """
        Launch the periodic sync scheduler for a service.
        If the service is already running, this is a no-op.
        """
        state = self._get_state(service_id)
        # Already running
        if state["timer_thread"] and state["timer_thread"].is_alive():
            return
        # Clear the stop signal so the loop runs
        state["stop_event"].clear()

        def _scheduler_loop():
            """Background loop: sync immediately then repeat at configured interval."""
            while not state["stop_event"].is_set():
                svc = self.config.get_service(service_id)
                if svc is None:
                    # Service was deleted - exit loop
                    break
                if svc.get("sync_paused", False):
                    # Paused - wait 10s and check again
                    state["stop_event"].wait(10)
                    continue
                # Run bisync now
                self._run_sync(service_id)
                # Wait for the configured interval before next sync
                interval_minutes = svc.get("sync_interval", 15)
                state["stop_event"].wait(interval_minutes * 60)

        thread = threading.Thread(target=_scheduler_loop, daemon=True)
        thread.start()
        state["timer_thread"] = thread

    def stop_service(self, service_id):
        """Stop the periodic sync scheduler for a service."""
        state = self._get_state(service_id)
        state["stop_event"].set()
        state["status"] = SyncStatus.IDLE
        self._notify_status_change(service_id)

    def pause_service(self, service_id):
        """Pause sync for a service (marks paused in config, stops the loop)."""
        self.config.update_service(service_id, {"sync_paused": True})
        self.stop_service(service_id)
        state = self._get_state(service_id)
        state["status"] = SyncStatus.PAUSED
        self._notify_status_change(service_id)

    def resume_service(self, service_id):
        """Resume sync for a paused service."""
        self.config.update_service(service_id, {"sync_paused": False})
        state = self._get_state(service_id)
        state["status"] = SyncStatus.IDLE
        self.start_service(service_id)
        self._notify_status_change(service_id)

    def trigger_sync_now(self, service_id):
        """Immediately trigger a single sync cycle outside the normal schedule."""
        def _go():
            self._run_sync(service_id)
        threading.Thread(target=_go, daemon=True).start()

    def _run_sync(self, service_id):
        """
        Execute one bisync cycle for the given service.
        Updates status, collects changed files, handles resync on error.
        """
        svc = self.config.get_service(service_id)
        if svc is None:
            return
        state = self._get_state(service_id)

        # Mark as syncing and notify UI
        state["status"] = SyncStatus.SYNCING
        self._notify_status_change(service_id)

        # Build the list of exclusion rules for this service
        exclude_rules = []
        if svc.get("exclude_personal_vault", True):
            # Default: exclude OneDrive Personal Vault directory
            exclude_rules.append(PERSONAL_VAULT_EXCLUDE)
        # Add any user-defined exclusion patterns
        exclude_rules.extend(svc.get("excluded_paths", []))

        output_lines = []

        def _on_line(line):
            """Accumulate rclone output lines for later analysis."""
            output_lines.append(line)

        # Completion event used to block until bisync finishes
        done_event = threading.Event()
        final_rc = [0]

        def _on_done(rc, last_line):
            """Called by rclone_manager when bisync completes."""
            final_rc[0] = rc
            done_event.set()

        # First bisync attempt (without --resync)
        self.rclone.bisync(
            remote_name=svc["remote_name"],
            local_path=svc["local_path"],
            exclude_rules=exclude_rules,
            resync=svc.get("resync", True),
            on_progress=_on_line,
            on_done=_on_done,
        )
        done_event.wait()

        # If bisync failed, retry with --resync as fallback
        if final_rc[0] != 0:
            output_lines.clear()
            done_event.clear()
            self.rclone.bisync(
                remote_name=svc["remote_name"],
                local_path=svc["local_path"],
                exclude_rules=exclude_rules,
                resync=True,
                on_progress=_on_line,
                on_done=_on_done,
            )
            done_event.wait()

        # Store the last output lines for debugging
        state["last_lines"] = output_lines[-100:]

        # Parse changed files from output (keep last 50)
        new_files = self.rclone.parse_transferred_files(output_lines)
        # Prepend new entries so the most recent appear at the top
        combined = new_files + state["changed_files"]
        # Deduplicate by path while preserving order
        seen = set()
        deduped = []
        for f in combined:
            if f["path"] not in seen:
                seen.add(f["path"])
                deduped.append(f)
        state["changed_files"] = deduped[:50]

        # Update status based on sync result
        if final_rc[0] == 0:
            state["status"] = SyncStatus.IDLE
            now = datetime.datetime.now().isoformat(timespec="seconds")
            self.config.update_service(service_id, {"last_sync": now})
        else:
            state["status"] = SyncStatus.ERROR

        self._notify_status_change(service_id)

    # ------------------------------------------------------------------
    # Status change notifications for UI
    # ------------------------------------------------------------------

    def register_status_callback(self, callback: Callable[[str], None]):
        """Register a function to be called whenever a service status changes."""
        self._status_callbacks.append(callback)

    def _notify_status_change(self, service_id: str):
        """Invoke all registered status callbacks with the changed service id."""
        for cb in self._status_callbacks:
            try:
                cb(service_id)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Service CRUD passthroughs
    # ------------------------------------------------------------------

    def add_service(self, name, platform, local_path, remote_name):
        """Create a new service in config and start its scheduler."""
        svc = self.config.add_service(name, platform, local_path, remote_name)
        self.start_service(svc["id"])
        return svc

    def delete_service(self, service_id):
        """Stop the service scheduler, delete the rclone remote, remove from config."""
        self.stop_service(service_id)
        svc = self.config.get_service(service_id)
        if svc:
            self.rclone.delete_remote(svc["remote_name"])
        self.config.delete_service(service_id)
        # Clean up runtime state
        self._state.pop(service_id, None)

    def get_services(self):
        """Return all configured services."""
        return self.config.get_services()
