"""
Rclone Python IA – Multiplatform Rclone Manager
Entry point for the application.

Behaviour:
  - If no services are configured → open the new-service wizard first.
  - Otherwise → open the main window directly.
"""

import sys
import tkinter as tk

from src.config.config_manager import ConfigManager
from src.rclone.rclone_manager import RcloneManager


def main() -> None:
    """Application entry point."""
    # Initialise the config manager (loads or creates the JSON config file)
    config = ConfigManager()
    # Initialise the rclone manager (does not start any threads yet)
    rclone = RcloneManager(config)

    services = config.get_services()

    if not services:
        # No services configured → run the setup wizard as the root window
        _run_wizard_first(config, rclone)
    else:
        # Services exist → open the main window directly
        _run_main_window(config, rclone)


def _run_wizard_first(config: ConfigManager, rclone: RcloneManager) -> None:
    """
    Run a minimal Tk root (hidden) that hosts the setup wizard.
    After the wizard finishes, launch the main window.
    """
    root = tk.Tk()
    root.withdraw()  # Hide the empty root window

    from src.gui.setup_wizard import SetupWizard

    def on_wizard_complete(service_name: str) -> None:
        # Destroy the hidden root and open the main window
        root.destroy()
        _run_main_window(config, rclone)

    SetupWizard(
        parent=root,
        config_manager=config,
        rclone_manager=rclone,
        on_complete=on_wizard_complete,
    )

    root.mainloop()


def _run_main_window(config: ConfigManager, rclone: RcloneManager) -> None:
    """Create and run the main application window."""
    from src.gui.main_window import MainWindow

    window = MainWindow(config_manager=config, rclone_manager=rclone)
    window.run()


if __name__ == "__main__":
    main()
