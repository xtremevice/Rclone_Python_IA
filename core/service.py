"""
Modelo de servicio para Rclone Python IA.
Define la estructura de datos de un servicio de sincronización.
"""

import uuid
from dataclasses import dataclass, field
from typing import Optional


# Intervalos de sincronización disponibles (en segundos)
SYNC_INTERVALS = {
    "1 minuto": 60,
    "5 minutos": 300,
    "15 minutos": 900,
    "30 minutos": 1800,
    "1 hora": 3600,
    "2 horas": 7200,
    "3 horas": 10800,
    "6 horas": 21600,
    "12 horas": 43200,
    "24 horas": 86400,
}

# Plataformas soportadas por rclone
SUPPORTED_PLATFORMS = [
    "onedrive",
    "googledrive",
    "dropbox",
    "box",
    "s3",
    "sftp",
    "ftp",
    "mega",
    "pcloud",
    "yandex",
    "nextcloud",
    "owncloud",
]

# Nombres de visualización para cada plataforma
PLATFORM_DISPLAY_NAMES = {
    "onedrive": "Microsoft OneDrive",
    "googledrive": "Google Drive",
    "dropbox": "Dropbox",
    "box": "Box",
    "s3": "Amazon S3",
    "sftp": "SFTP",
    "ftp": "FTP",
    "mega": "MEGA",
    "pcloud": "pCloud",
    "yandex": "Yandex Disk",
    "nextcloud": "Nextcloud",
    "owncloud": "ownCloud",
}


@dataclass
class Service:
    """Clase que representa un servicio de sincronización configurado."""

    # Nombre único del servicio asignado por el usuario
    name: str = ""
    # Tipo de plataforma (onedrive, googledrive, etc.)
    platform: str = ""
    # Carpeta local donde se sincronizarán los datos
    local_path: str = ""
    # Ruta remota dentro del servicio (por defecto raíz)
    remote_path: str = "/"
    # Identificador único del servicio
    service_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # Nombre del remote en rclone (generado automáticamente)
    rclone_remote: str = ""
    # Intervalo de sincronización en segundos
    sync_interval: int = 300
    # Si la sincronización está activa
    is_syncing: bool = False
    # Si el servicio está pausado
    is_paused: bool = False
    # Lista de carpetas excluidas de la sincronización
    excluded_folders: list = field(default_factory=list)
    # Si se excluye la carpeta "Archivo personal" de OneDrive por defecto
    exclude_personal_vault: bool = True
    # Si se inicia con el sistema
    start_with_system: bool = False
    # Tiempo de retraso al iniciar (en segundos)
    startup_delay: int = 0
    # Timestamp de la última sincronización
    last_sync: Optional[str] = None
    # Lista de los últimos archivos sincronizados (máximo 50)
    recent_files: list = field(default_factory=list)
    # Si se descargan solo archivos usados (on-demand)
    on_demand: bool = True
    # Si se usa resync para sincronización bidireccional
    use_resync: bool = True

    def get_display_name(self) -> str:
        """Retorna el nombre de visualización del servicio."""
        return self.name if self.name else f"Servicio {self.service_id[:8]}"

    def get_platform_display_name(self) -> str:
        """Retorna el nombre de visualización de la plataforma."""
        return PLATFORM_DISPLAY_NAMES.get(self.platform, self.platform.capitalize())

    def get_sync_interval_display(self) -> str:
        """Retorna el intervalo de sincronización como texto legible."""
        for name, seconds in SYNC_INTERVALS.items():
            if seconds == self.sync_interval:
                return name
        # Si no coincide exactamente, convertir manualmente
        if self.sync_interval < 3600:
            return f"{self.sync_interval // 60} minuto(s)"
        return f"{self.sync_interval // 3600} hora(s)"

    def get_status_display(self) -> str:
        """Retorna el estado del servicio como texto."""
        if self.is_paused:
            return "Pausado"
        if self.is_syncing:
            return "Sincronizando..."
        return "Actualizado"

    def get_rclone_remote_name(self) -> str:
        """Retorna el nombre del remote de rclone para este servicio."""
        if self.rclone_remote:
            return self.rclone_remote
        # Generar nombre seguro basado en service_id
        return f"rclone_ia_{self.service_id[:8]}"

    def to_dict(self) -> dict:
        """Serializa el servicio a un diccionario para guardar en JSON."""
        return {
            "name": self.name,
            "platform": self.platform,
            "local_path": self.local_path,
            "remote_path": self.remote_path,
            "service_id": self.service_id,
            "rclone_remote": self.rclone_remote,
            "sync_interval": self.sync_interval,
            "is_paused": self.is_paused,
            "excluded_folders": self.excluded_folders,
            "exclude_personal_vault": self.exclude_personal_vault,
            "start_with_system": self.start_with_system,
            "startup_delay": self.startup_delay,
            "last_sync": self.last_sync,
            "recent_files": self.recent_files,
            "on_demand": self.on_demand,
            "use_resync": self.use_resync,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Service":
        """Crea un servicio desde un diccionario cargado de JSON."""
        service = cls()
        service.name = data.get("name", "")
        service.platform = data.get("platform", "")
        service.local_path = data.get("local_path", "")
        service.remote_path = data.get("remote_path", "/")
        service.service_id = data.get("service_id", str(uuid.uuid4()))
        service.rclone_remote = data.get("rclone_remote", "")
        service.sync_interval = data.get("sync_interval", 300)
        service.is_paused = data.get("is_paused", False)
        service.excluded_folders = data.get("excluded_folders", [])
        service.exclude_personal_vault = data.get("exclude_personal_vault", True)
        service.start_with_system = data.get("start_with_system", False)
        service.startup_delay = data.get("startup_delay", 0)
        service.last_sync = data.get("last_sync", None)
        service.recent_files = data.get("recent_files", [])
        service.on_demand = data.get("on_demand", True)
        service.use_resync = data.get("use_resync", True)
        return service
