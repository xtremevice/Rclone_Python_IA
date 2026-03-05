"""
Rclone Manager – application entry point.

Boots the GUI, sets up the tray icon and orchestrates all windows:
  - If no services are configured, opens the new-service wizard first.
  - Otherwise, opens the main window directly.
  - Minimising the main window sends it to the notification tray.
"""

import sys
import tkinter as tk
from typing import Dict

from app.config import AppConfig
from app.rclone_manager import RcloneManager
from app.sync_manager import SyncManager
from app.tray import TrayIcon
from app.utils import apply_theme
from app.windows.config_window import ConfigWindow
from app.windows.main_window import MainWindow
from app.windows.wizard import NewServiceWizard


class Application:
    """
    Top-level application controller.

    Owns the shared configuration, rclone manager, sync manager and tray icon.
    Coordinates opening and closing of all windows.
    """

    def __init__(self) -> None:
        """Initialise shared services and decide which window to show first."""
        self.config = AppConfig()
        self.rclone = RcloneManager()
        self.sync_manager = SyncManager(
            self.rclone,
            on_status_change=self._on_sync_status_change,
        )

        # Placeholder – main window is created below
        self.main_window: MainWindow | None = None
        self._tray: TrayIcon | None = None

    # ------------------------------------------------------------------
    # Application lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the application event loop."""
        # Check rclone availability (non-fatal warning)
        if not self.rclone.is_available():
            import tkinter.messagebox as mb
            # Need a hidden root just for the dialog
            _tmp = tk.Tk()
            _tmp.withdraw()
            mb.showwarning(
                "rclone no encontrado",
                "No se encontró rclone en el PATH.\n\n"
                "Descárguelo desde https://rclone.org/downloads/ e "
                "instálelo para que la sincronización funcione.",
            )
            _tmp.destroy()

        # Build the main window (this creates the Tk root)
        self.main_window = MainWindow(
            app_config=self.config,
            sync_manager=self.sync_manager,
            on_add_service=self._open_wizard,
            on_open_config=self._open_config,
            on_quit=self._quit,
        )

        apply_theme(self.main_window)

        # Override the iconify (minimise) behaviour to hide to tray instead
        self.main_window.bind("<Unmap>", self._on_main_minimised)

        # Set up tray icon
        self._tray = TrayIcon(
            on_show=self._restore_main_window,
            on_quit=self._quit,
        )
        self._tray.start()

        # Start syncing all enabled services
        self._start_all_services()

        # If no services exist, open the wizard automatically
        if not self.config.services:
            self.main_window.after(200, self._open_wizard)

        # Enter the Tk event loop
        self.main_window.mainloop()

    def _quit(self) -> None:
        """Stop all syncs, remove tray icon and exit."""
        self.sync_manager.stop_all()
        if self._tray:
            self._tray.stop()
        if self.main_window and self.main_window.winfo_exists():
            self.main_window.destroy()

    # ------------------------------------------------------------------
    # Service management
    # ------------------------------------------------------------------

    def _start_all_services(self) -> None:
        """Launch sync workers for every enabled service in config."""
        for svc in self.config.services:
            if svc.get("enabled", True):
                self.sync_manager.start_service(svc)

    def _on_sync_status_change(self, name: str, status: str) -> None:
        """
        Forward sync-status updates to the main window from a background thread.

        Uses `after` to marshal the call to the UI thread safely.
        """
        if self.main_window and self.main_window.winfo_exists():
            self.main_window.after(0, lambda: self.main_window.refresh_service_tab(name))  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Window coordination
    # ------------------------------------------------------------------

    def _open_wizard(self) -> None:
        """Open the new-service wizard."""
        if self.main_window is None:
            return
        NewServiceWizard(
            parent=self.main_window,
            app_config=self.config,
            rclone=self.rclone,
            on_finish=self._on_wizard_finish,
        )

    def _on_wizard_finish(self, service: Dict) -> None:
        """
        Called after the wizard successfully adds a new service.

        Starts the sync worker and updates the main window.
        """
        # Start syncing the newly added service
        self.sync_manager.start_service(service)

        # Rebuild tabs in the main window to include the new service
        if self.main_window and self.main_window.winfo_exists():
            self.main_window._rebuild_tabs()  # noqa: SLF001 – intentional

    def _open_config(self, service_name: str) -> None:
        """Open the configuration window for *service_name*."""
        if self.main_window is None:
            return
        ConfigWindow(
            parent=self.main_window,
            service_name=service_name,
            app_config=self.config,
            rclone=self.rclone,
            sync_manager=self.sync_manager,
            on_service_deleted=self._on_service_deleted,
            on_saved=self._on_config_saved,
        )

    def _on_service_deleted(self, name: str) -> None:
        """Rebuild the main window tabs after a service is deleted."""
        if self.main_window and self.main_window.winfo_exists():
            self.main_window._rebuild_tabs()  # noqa: SLF001
            if not self.config.services:
                self.main_window._show_empty_state()  # noqa: SLF001

    def _on_config_saved(self, name: str) -> None:
        """Restart the sync worker after configuration changes."""
        svc = self.config.get_service(name)
        if svc:
            self.sync_manager.stop_service(name)
            self.sync_manager.start_service(svc)

    # ------------------------------------------------------------------
    # Tray / minimise behaviour
    # ------------------------------------------------------------------

    def _on_main_minimised(self, event: tk.Event) -> None:
        """
        Intercept the window unmap event.

        On most platforms the window-manager sends an Unmap event when the
        user minimises the window.  We hide it completely so it only appears
        in the notification tray.
        """
        if self.main_window and self.main_window.state() == "iconic":
            self.main_window.after(10, self.main_window.minimize_to_tray)

    def _restore_main_window(self) -> None:
        """Restore the main window from the tray."""
        if self.main_window and self.main_window.winfo_exists():
            self.main_window.after(0, self.main_window.restore_from_tray)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Create and run the Application."""
    app = Application()
    app.run()


if __name__ == "__main__":
    main()
