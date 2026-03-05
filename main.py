"""
main.py
-------
Entry point for Rclone Manager.

Start-up logic:
  • If no services are configured → open the new-service setup wizard first.
  • If services exist → open the main window directly.
  • After the wizard completes, proceed to the main window.
"""

import sys
import tkinter as tk
from tkinter import messagebox

from src.core.service_manager import ServiceManager
from src.ui.utils import apply_theme


def _show_rclone_missing_error(root: tk.Tk):
    """Display an informative error when rclone is not installed and exit."""
    messagebox.showerror(
        "rclone no encontrado",
        "rclone no está instalado o no se encuentra en el PATH del sistema.\n\n"
        "Por favor instala rclone desde https://rclone.org/downloads/ y "
        "asegúrate de que esté disponible en el PATH antes de ejecutar esta aplicación.",
        master=root,
    )


def main():
    """
    Application entry point.
    Creates the root Tk instance, decides which window to show first,
    and starts the event loop.
    """
    # Create an invisible root window that will be hidden while we determine
    # which real window to show first.
    root = tk.Tk()
    root.withdraw()   # Hide the root; we show either wizard or main window

    apply_theme(root)

    # Instantiate the service manager (loads config from disk)
    svc_manager = ServiceManager()

    # Warn if rclone is not available (non-fatal - user can still manage config)
    if not svc_manager.rclone.is_rclone_available():
        _show_rclone_missing_error(root)

    services = svc_manager.get_services()

    if not services:
        # --- No services configured: show the setup wizard first ---
        from src.ui.setup_wizard import SetupWizard

        def _on_wizard_complete(name, platform, local_path, remote_name, token):
            """After the wizard finishes, launch the main window."""
            svc_manager.add_service(name, platform, local_path, remote_name)
            _open_main_window(root, svc_manager)

        SetupWizard(root, on_complete=_on_wizard_complete)
    else:
        # --- Services exist: open main window directly ---
        _open_main_window(root, svc_manager)

    root.mainloop()


def _open_main_window(root: tk.Tk, svc_manager: ServiceManager):
    """
    Replace the hidden root window with the real MainWindow,
    then start background sync schedulers for all services.
    """
    from src.ui.main_window import MainWindow

    # Destroy the invisible root and use MainWindow as the new root widget
    # (MainWindow extends tk.Tk, so we destroy the placeholder first)
    root.destroy()

    window = MainWindow(svc_manager)
    # Start periodic sync for all configured services
    svc_manager.start_all()
    # Bind minimize event to tray (Linux/macOS: <Unmap>, Windows: handled differently)
    window.bind("<Unmap>", window._on_iconify)


if __name__ == "__main__":
    main()
