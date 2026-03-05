"""
main.py — Punto de entrada de la aplicación Rclone Python IA.
Determina si hay servicios configurados para mostrar el wizard o la ventana principal.
"""

import sys
import os

# Asegurar que el directorio src esté en el path para importaciones relativas
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

import config as cfg
from windows.wizard import run_wizard
from windows.main_window import MainWindow


def main():
    """
    Función principal de la aplicación.
    Crea la aplicación Qt, decide si mostrar el wizard o la ventana principal,
    y ejecuta el bucle de eventos.
    """
    # Habilitar escala de alta densidad de píxeles (HiDPI)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    # Crear la instancia de la aplicación Qt
    app = QApplication(sys.argv)
    app.setApplicationName("Rclone Python IA")
    app.setOrganizationName("xtremevice")

    # Evitar que la aplicación se cierre al cerrar la última ventana
    # (para que el ícono de bandeja siga activo)
    app.setQuitOnLastWindowClosed(False)

    # Cargar los servicios configurados
    services = cfg.get_services()

    if not services:
        # Sin servicios: mostrar el asistente de configuración inicial
        added = run_wizard()
        if not added:
            # Si el usuario cancela el wizard sin configurar nada, salir
            sys.exit(0)

    # Mostrar la ventana principal con los servicios configurados
    main_win = MainWindow()
    main_win.show()

    # Ejecutar el bucle de eventos de Qt
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
