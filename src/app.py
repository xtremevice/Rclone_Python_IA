"""
src/app.py

Application controller.  Decides whether to show the setup wizard (first run)
or the main window, and wires together the configuration, service manager, and
all windows.
"""
from typing import Optional

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

from src.core.config import AppConfig
from src.core.service_manager import ServiceManager


class RcloneApp:
    """Top-level application controller.

    Responsibilities:
      • Load the application configuration.
      • Initialise the ServiceManager.
      • Show the setup wizard when no services are configured, otherwise
        show the main window directly.
      • Wire service-creation events so that a new service immediately
        transitions to the main window.
    """

    def __init__(self, qt_app: QApplication) -> None:
        """Initialise the controller with a reference to the Qt application."""
        self.qt_app = qt_app
        self.config = AppConfig()
        self.manager = ServiceManager(self.config)
        self._main_window = None

    def start(self) -> None:
        """Determine the first screen and display it."""
        if self.config.services:
            # Services already exist – go straight to the main window
            self._show_main_window()
        else:
            # First run (or all services deleted) – open the setup wizard
            self._show_setup_wizard()

    # ------------------------------------------------------------------

    def _show_main_window(self) -> None:
        """Create and display the main window, then start all sync schedulers."""
        from src.windows.main_window import MainWindow
        self._main_window = MainWindow(
            config=self.config,
            manager=self.manager,
        )
        self._main_window.show()
        # Start sync schedulers with a small delay so the UI renders first
        QTimer.singleShot(500, self.manager.start_all)

    def _show_setup_wizard(self) -> None:
        """Open the first-run setup wizard."""
        from src.windows.setup_wizard import SetupWizard
        wizard = SetupWizard(config=self.config)
        # When a service is successfully created, transition to the main window
        wizard.service_created.connect(self._on_first_service_created)
        # If the user cancels the wizard, exit the application
        wizard.rejected.connect(QApplication.quit)
        wizard.show()

    def _on_first_service_created(self, service) -> None:
        """After the first service is added, open the main window."""
        self._show_main_window()
        # Trigger an immediate sync for the newly created service
        QTimer.singleShot(1000, lambda: self.manager.trigger_sync_now(service.id))
