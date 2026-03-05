"""
src/core/service_manager.py

Manages the lifecycle and scheduling of all configured rclone services.
Each service gets its own QTimer that triggers a SyncWorker/QThread at the
configured interval.  The manager emits Qt signals so the UI can react to
sync events without polling.
"""
from datetime import datetime
from typing import Dict, Optional

from PyQt5.QtCore import QObject, QTimer, QThread, pyqtSignal

from src.core.config import AppConfig, ServiceConfig
from src.core.rclone import SyncWorker


class ServiceManager(QObject):
    """Central coordinator for all service sync operations.

    Signals
    -------
    sync_started(str)           Service ID whose sync just began.
    sync_progress(str, str)     (service_id, log_line) during a sync.
    sync_file(str, str, bool)   (service_id, filename, success) per transferred file.
    sync_finished(str, bool, str) (service_id, success, message) when done.
    services_changed()          Any service was added, removed, or modified.
    """

    sync_started = pyqtSignal(str)
    sync_progress = pyqtSignal(str, str)
    sync_file = pyqtSignal(str, str, bool)
    sync_finished = pyqtSignal(str, bool, str)
    services_changed = pyqtSignal()

    def __init__(self, config: AppConfig, parent: Optional[QObject] = None) -> None:
        """Initialise the manager with the loaded application configuration."""
        super().__init__(parent)
        self.config = config
        # Maps service_id → QTimer
        self._timers: Dict[str, QTimer] = {}
        # Maps service_id → (QThread, SyncWorker) while a sync is running
        self._active_syncs: Dict[str, tuple] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_all(self) -> None:
        """Start schedulers for all services that have sync_active=True."""
        for service in self.config.services:
            if service.sync_active:
                self._schedule_service(service)

    def stop_all(self) -> None:
        """Stop all schedulers and cancel any in-progress syncs."""
        for service_id in list(self._timers.keys()):
            self._stop_timer(service_id)
        for service_id in list(self._active_syncs.keys()):
            self.cancel_sync(service_id)

    def toggle_sync(self, service_id: str) -> None:
        """Toggle the sync_active state of a service.

        If the service was running, its timer is stopped (and any active sync
        is cancelled).  If it was stopped, a new timer is started.
        """
        service = self.config.get_service(service_id)
        if not service:
            return
        service.sync_active = not service.sync_active
        self.config.update_service(service)

        if service.sync_active:
            self._schedule_service(service)
        else:
            self._stop_timer(service_id)
            self.cancel_sync(service_id)
        self.services_changed.emit()

    def trigger_sync_now(self, service_id: str) -> None:
        """Immediately trigger a sync for the given service (outside the schedule)."""
        service = self.config.get_service(service_id)
        if service and service_id not in self._active_syncs:
            self._run_sync(service)

    def cancel_sync(self, service_id: str) -> None:
        """Request cancellation of the currently running sync for a service."""
        if service_id in self._active_syncs:
            _, worker = self._active_syncs[service_id]
            worker.cancel()

    def update_schedule(self, service_id: str) -> None:
        """Restart the timer for a service after its sync_interval has changed."""
        self._stop_timer(service_id)
        service = self.config.get_service(service_id)
        if service and service.sync_active:
            self._schedule_service(service)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _schedule_service(self, service: ServiceConfig) -> None:
        """Create and start a QTimer for the given service."""
        self._stop_timer(service.id)   # remove any existing timer first

        timer = QTimer(self)
        # Convert minutes → milliseconds
        interval_ms = service.sync_interval * 60 * 1000
        timer.setInterval(interval_ms)
        # Capture service_id in a local variable so that the lambda closure
        # binds the *value* at this point in time rather than a late-bound
        # reference to `service.id`, which could change if `service` is
        # mutated after this method returns.
        service_id = service.id
        timer.timeout.connect(lambda: self._on_timer(service_id))
        self._timers[service.id] = timer
        timer.start()

    def _stop_timer(self, service_id: str) -> None:
        """Stop and remove the QTimer for a service if one exists."""
        timer = self._timers.pop(service_id, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def _on_timer(self, service_id: str) -> None:
        """Called by the QTimer when it fires.  Starts a sync if none is running."""
        if service_id in self._active_syncs:
            # A sync is already in progress – skip this tick
            return
        service = self.config.get_service(service_id)
        if service and service.sync_active:
            self._run_sync(service)

    def _run_sync(self, service: ServiceConfig) -> None:
        """Spin up a QThread + SyncWorker pair for the given service."""
        thread = QThread(self)
        worker = SyncWorker(
            remote_name=service.remote_name,
            local_path=service.local_path,
            remote_path=service.remote_path,
            exclude_rules=service.exclude_rules,
            use_resync=service.use_resync,
            download_on_demand=service.download_on_demand,
        )
        worker.moveToThread(thread)

        # Connect worker signals
        service_id = service.id
        worker.progress.connect(lambda line: self.sync_progress.emit(service_id, line))
        worker.file_synced.connect(
            lambda fn, ok: self._on_file_synced(service_id, fn, ok)
        )
        worker.finished.connect(lambda ok, msg: self._on_finished(service_id, ok, msg))

        # Worker runs when thread starts
        thread.started.connect(worker.run)

        self._active_syncs[service_id] = (thread, worker)

        # Mark service as syncing
        service.is_syncing = True
        self.config.update_service(service)
        self.sync_started.emit(service_id)

        thread.start()

    def _on_file_synced(self, service_id: str, filename: str, ok: bool) -> None:
        """Handle a file-synced event from the worker."""
        service = self.config.get_service(service_id)
        if service:
            service.add_recent_file(filename, ok)
            self.config.update_service(service)
        self.sync_file.emit(service_id, filename, ok)

    def _on_finished(self, service_id: str, success: bool, message: str) -> None:
        """Handle sync-finished event: update state, clean up thread/worker."""
        service = self.config.get_service(service_id)
        if service:
            service.is_syncing = False
            if success:
                service.last_sync = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.config.update_service(service)

        # Clean up thread
        pair = self._active_syncs.pop(service_id, None)
        if pair:
            thread, worker = pair
            thread.quit()
            thread.wait()
            worker.deleteLater()
            thread.deleteLater()

        self.sync_finished.emit(service_id, success, message)
