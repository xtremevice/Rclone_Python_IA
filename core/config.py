"""
Gestión de configuración persistente para Rclone Python IA.
Guarda y carga la configuración de servicios en un archivo JSON.
"""

import json
import os
import sys
from typing import List, Optional

from core.service import Service


def get_config_dir() -> str:
    """
    Retorna el directorio donde se almacena la configuración de la aplicación.
    Usa directorios estándar según el sistema operativo.
    """
    # Determinar directorio de configuración según el SO
    if sys.platform == "win32":
        # En Windows usar APPDATA
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin":
        # En macOS usar ~/Library/Application Support
        base = os.path.expanduser("~/Library/Application Support")
    else:
        # En Linux usar ~/.config
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))

    # Crear subdirectorio para la aplicación
    config_dir = os.path.join(base, "RclonePythonIA")
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


def get_config_file() -> str:
    """Retorna la ruta completa al archivo de configuración JSON."""
    return os.path.join(get_config_dir(), "config.json")


class ConfigManager:
    """
    Gestor de configuración de la aplicación.
    Maneja la persistencia de servicios y ajustes globales.
    """

    def __init__(self):
        """Inicializa el gestor y carga la configuración existente."""
        # Ruta al archivo de configuración
        self.config_file = get_config_file()
        # Lista de servicios configurados
        self.services: List[Service] = []
        # Configuración global de la aplicación
        self.global_settings: dict = {}
        # Cargar configuración al iniciar
        self.load()

    def load(self):
        """
        Carga la configuración desde el archivo JSON.
        Si el archivo no existe, inicializa con valores vacíos.
        """
        if not os.path.exists(self.config_file):
            # No hay configuración previa, iniciar vacío
            self.services = []
            self.global_settings = {}
            return

        try:
            # Leer y parsear el archivo JSON
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Cargar lista de servicios desde el JSON
            services_data = data.get("services", [])
            self.services = [Service.from_dict(s) for s in services_data]

            # Cargar ajustes globales
            self.global_settings = data.get("global_settings", {})

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            # Si hay error al leer, iniciar con valores vacíos y registrar error
            print(f"Error cargando configuración: {e}")
            self.services = []
            self.global_settings = {}

    def save(self):
        """
        Guarda la configuración actual en el archivo JSON.
        Crea el directorio si no existe.
        """
        try:
            # Preparar datos para serialización
            data = {
                "services": [s.to_dict() for s in self.services],
                "global_settings": self.global_settings,
            }

            # Escribir el archivo JSON con indentación para legibilidad
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        except (OSError, IOError) as e:
            print(f"Error guardando configuración: {e}")

    def add_service(self, service: Service) -> bool:
        """
        Agrega un nuevo servicio a la lista y guarda la configuración.
        Retorna True si fue exitoso, False si ya existe un servicio con ese nombre.
        """
        # Verificar que no exista un servicio con el mismo nombre
        for existing in self.services:
            if existing.name.lower() == service.name.lower():
                return False

        # Agregar el servicio y guardar
        self.services.append(service)
        self.save()
        return True

    def update_service(self, service: Service):
        """Actualiza un servicio existente por su service_id y guarda."""
        for i, existing in enumerate(self.services):
            if existing.service_id == service.service_id:
                self.services[i] = service
                self.save()
                return

    def remove_service(self, service_id: str) -> bool:
        """
        Elimina un servicio por su service_id.
        Retorna True si fue eliminado, False si no se encontró.
        """
        for i, service in enumerate(self.services):
            if service.service_id == service_id:
                del self.services[i]
                self.save()
                return True
        return False

    def get_service(self, service_id: str) -> Optional[Service]:
        """Busca y retorna un servicio por su service_id."""
        for service in self.services:
            if service.service_id == service_id:
                return service
        return None

    def has_services(self) -> bool:
        """Retorna True si hay al menos un servicio configurado."""
        return len(self.services) > 0

    def update_service_recent_files(self, service_id: str, file_entry: dict):
        """
        Agrega un archivo a la lista de archivos recientes del servicio.
        Mantiene un máximo de 50 archivos en la lista.
        """
        service = self.get_service(service_id)
        if service is None:
            return

        # Agregar al inicio de la lista (más reciente primero)
        service.recent_files.insert(0, file_entry)

        # Mantener límite de 50 archivos
        if len(service.recent_files) > 50:
            service.recent_files = service.recent_files[:50]

        # Guardar cambios
        self.save()
