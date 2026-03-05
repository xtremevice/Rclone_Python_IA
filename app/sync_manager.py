"""
Synchronisation scheduling manager.

Runs periodic bisync operations for each enabled service in background
threads and keeps an in-memory record of recent file changes and sync
status for display in the UI.
"""

import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Tuple

from app.rclone_manager import RcloneManager


# Maximum number of file change entries kept per service
MAX_CHANGES = 50


class ServiceState:
    """Holds the runtime state for a single service."""

    def __init__(self, name: str) -> None:
        """Initialise state for service *name* with defaults."""
        self.name: str = name
        # 'idle' | 'syncing' | 'error' | 'paused'
        self.status: str = "idle"
        self.last_sync: Optional[str] = None
        self.last_error: Optional[str] = None
        # Deque capped at MAX_CHANGES; each entry is (filename, timestamp)
        self.recent_changes: Deque[Tuple[str, str]] = deque(maxlen=MAX_CHANGES)
        # Background timer for periodic sync
        self._timer: Optional[threading.Timer] = None
        # Running subprocess handle
        self._process = None
        # Whether the service has ever completed a successful sync
        self.first_sync_done: bool = False


class SyncManager:
    """Manages periodic sync operations for all configured services."""

    def __init__(
        self,
        rclone: RcloneManager,
        on_status_change: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """
        Initialise the sync manager.

        Args:
            rclone: A :class:`RcloneManager` instance for running bisync.
            on_status_change: Optional callback(service_name, new_status)
                invoked whenever a service's status changes.
        """
        self.rclone = rclone
        self._on_status_change = on_status_change
        self._states: Dict[str, ServiceState] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _get_state(self, name: str) -> ServiceState:
        """Return (creating if needed) the ServiceState for *name*."""
        with self._lock:
            if name not in self._states:
                self._states[name] = ServiceState(name)
            return self._states[name]

    def _set_status(self, name: str, status: str) -> None:
        """Update the status for *name* and fire the callback."""
        state = self._get_state(name)
        state.status = status
        if self._on_status_change:
            self._on_status_change(name, status)

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------

    def start_service(self, service_cfg: Dict) -> None:
        """
        Schedule periodic sync for *service_cfg* and trigger an initial sync.

        If the service is already running this is a no-op.
        """
        name = service_cfg["name"]
        state = self._get_state(name)
        if state.status == "syncing":
            return
        state.first_sync_done = service_cfg.get("first_sync_done", False)
        self._trigger_sync(service_cfg)

    def stop_service(self, name: str) -> None:
        """Stop periodic sync and any in-progress sync for *name*."""
        state = self._get_state(name)
        # Cancel pending timer
        if state._timer is not None:
            state._timer.cancel()
            state._timer = None
        # Terminate running subprocess
        if state._process is not None:
            try:
                state._process.terminate()
            except OSError:
                pass
            state._process = None
        self._set_status(name, "paused")

    def resume_service(self, service_cfg: Dict) -> None:
        """Resume sync for a previously paused service."""
        name = service_cfg["name"]
        state = self._get_state(name)
        if state.status == "paused":
            self.start_service(service_cfg)

    def remove_service(self, name: str) -> None:
        """Stop and remove all state for *name*."""
        self.stop_service(name)
        with self._lock:
            self._states.pop(name, None)

    def stop_all(self) -> None:
        """Stop all running services."""
        for name in list(self._states.keys()):
            self.stop_service(name)

    # ------------------------------------------------------------------
    # Status / change queries
    # ------------------------------------------------------------------

    def get_status(self, name: str) -> str:
        """Return the current status string for *name*."""
        return self._get_state(name).status

    def get_last_sync(self, name: str) -> Optional[str]:
        """Return a human-readable timestamp for the last successful sync."""
        return self._get_state(name).last_sync

    def get_recent_changes(self, name: str) -> List[Tuple[str, str]]:
        """Return the recent file-change list for *name* (newest first)."""
        state = self._get_state(name)
        return list(state.recent_changes)

    # ------------------------------------------------------------------
    # Internal sync logic
    # ------------------------------------------------------------------

    def _trigger_sync(self, service_cfg: Dict) -> None:
        """Start a bisync subprocess and schedule the next run afterwards."""
        name = service_cfg["name"]
        state = self._get_state(name)

        # Don't stack up syncs
        if state.status == "syncing":
            return

        self._set_status(name, "syncing")

        def _on_line(line: str) -> None:
            """Process a single output line from rclone."""
            # rclone progress lines often contain file paths; we capture them
            if line and not line.startswith(" ") and "/" in line:
                import datetime
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                state.recent_changes.appendleft((line[:120], ts))

        def _on_complete(success: bool, output: str) -> None:
            """Handle bisync completion: update state and schedule next run."""
            import datetime

            if success:
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                state.last_sync = ts
                state.last_error = None
                # Mark first sync as done so subsequent runs skip --resync
                state.first_sync_done = True
                # Refresh recent changes from local filesystem
                local_path = service_cfg.get("local_path", "")
                if local_path:
                    fresh = self.rclone.list_recent_changes(
                        local_path, MAX_CHANGES
                    )
                    state.recent_changes.clear()
                    state.recent_changes.extend(fresh)
                self._set_status(name, "idle")
            else:
                state.last_error = output[-500:] if output else "Error desconocido"
                self._set_status(name, "error")

            state._process = None

            # Schedule next periodic sync (unless stopped/paused)
            if state.status != "paused":
                interval_min = service_cfg.get("sync_interval", 15)
                state._timer = threading.Timer(
                    interval_min * 60,
                    self._trigger_sync,
                    args=(service_cfg,),
                )
                state._timer.daemon = True
                state._timer.start()

        # Decide whether to use --resync
        needs_resync = not state.first_sync_done

        proc = self.rclone.run_bisync(
            remote_name=service_cfg["name"],
            remote_path=service_cfg.get("remote_path", "/"),
            local_path=service_cfg["local_path"],
            exclude_patterns=service_cfg.get("exclude_patterns", []),
            rclone_options=service_cfg.get("rclone_options", {}),
            resync=needs_resync,
            on_output=_on_line,
            on_complete=_on_complete,
        )
        state._process = proc
