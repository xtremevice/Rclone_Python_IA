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
Punto de entrada principal para Rclone Python IA.
Determina si mostrar el asistente de nuevo servicio o la ventana principal
según si existen servicios configurados o no.
"""

import sys
import os

# Agregar el directorio raíz al path para importaciones correctas
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QMessageBox

from core.config import ConfigManager
from ui.wizard import ServiceWizard
from ui.main_window import MainWindow


def main():
    """
    Función principal de la aplicación.
    Inicializa Qt, carga la configuración y decide qué ventana mostrar primero.
    """
    # Crear la aplicación Qt con soporte de DPI alta
    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    # Información de la aplicación para el sistema operativo
    app.setApplicationName("Rclone Python IA")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("RclonePythonIA")
    app.setOrganizationDomain("rclone-python-ia.local")

    # Evitar que la aplicación salga al cerrar todas las ventanas
    # (necesario para que quede en la bandeja del sistema)
    app.setQuitOnLastWindowClosed(False)

    # Cargar la configuración guardada
    config_manager = ConfigManager()

    # Variable para mantener referencia a la ventana principal
    main_window = None

    def show_main_window():
        """Muestra la ventana principal y la hace visible."""
        nonlocal main_window
        main_window = MainWindow(config_manager)
        main_window.show()

    def on_service_created(service):
        """
        Callback llamado cuando el asistente crea un nuevo servicio.
        Muestra la ventana principal con el servicio recién creado.
        """
        # La ventana principal se crea y el servicio se agrega a ella
        show_main_window()
        main_window.add_service(service)

    if config_manager.has_services():
        # Si ya hay servicios configurados, mostrar la ventana principal
        show_main_window()
    else:
        # Si no hay servicios, mostrar el asistente de configuración inicial
        wizard = ServiceWizard()
        wizard.service_created.connect(on_service_created)

        # Si el usuario cierra el asistente sin crear un servicio, salir
        result = wizard.exec_()
        if result != ServiceWizard.Accepted:
            # Verificar si se creó algún servicio antes de cerrar el wizard
            if not config_manager.has_services() and main_window is None:
                sys.exit(0)

        # Si el usuario completó el wizard pero la ventana no se abrió aún
        if main_window is None and config_manager.has_services():
            show_main_window()

    # Ejecutar el loop de eventos de Qt
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
