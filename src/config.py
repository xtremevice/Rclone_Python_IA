"""
config.py — Gestión de configuración de servicios.
Carga y guarda la configuración de servicios en un archivo JSON.
"""

import json
import os

# Directorio y archivo de configuración en el directorio de datos del usuario
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".rclone_python_ia")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def load_config() -> dict:
    """
    Carga la configuración desde el archivo JSON.
    Si el archivo no existe o está corrupto, devuelve una estructura por defecto.
    """
    # Crear directorio de configuración si no existe
    os.makedirs(CONFIG_DIR, exist_ok=True)

    # Si el archivo no existe, devolver configuración vacía
    if not os.path.exists(CONFIG_FILE):
        return {"services": []}

    try:
        # Leer y parsear el archivo JSON
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Asegurar que la clave 'services' exista
        if "services" not in data:
            data["services"] = []
        return data
    except (json.JSONDecodeError, OSError):
        # En caso de error, devolver configuración vacía
        return {"services": []}


def save_config(config: dict) -> None:
    """
    Guarda la configuración en el archivo JSON.
    Crea el directorio de configuración si no existe.
    """
    # Asegurar que el directorio exista antes de escribir
    os.makedirs(CONFIG_DIR, exist_ok=True)

    # Escribir el archivo JSON con formato legible
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


def get_services() -> list:
    """
    Devuelve la lista de servicios configurados.
    """
    return load_config().get("services", [])


def add_service(service: dict) -> None:
    """
    Agrega un nuevo servicio a la configuración.
    El servicio es un diccionario con la información del servicio.
    """
    config = load_config()
    config["services"].append(service)
    save_config(config)


def remove_service(service_name: str) -> None:
    """
    Elimina un servicio de la configuración por su nombre.
    """
    config = load_config()
    # Filtrar la lista excluyendo el servicio con el nombre dado
    config["services"] = [
        s for s in config["services"] if s.get("name") != service_name
    ]
    save_config(config)


def update_service(service_name: str, updated: dict) -> None:
    """
    Actualiza los datos de un servicio existente identificado por su nombre.
    """
    config = load_config()
    # Recorrer la lista y reemplazar el servicio que coincida
    for i, s in enumerate(config["services"]):
        if s.get("name") == service_name:
            config["services"][i] = updated
            break
    save_config(config)


def default_service_config() -> dict:
    """
    Devuelve la configuración por defecto para un nuevo servicio.
    Incluye los parámetros rclone recomendados.
    """
    return {
        # Nombre identificador del servicio
        "name": "",
        # Plataforma de nube (onedrive, gdrive, dropbox, etc.)
        "platform": "",
        # Carpeta local donde se sincronizará el servicio
        "local_folder": "",
        # Directorio remoto (por defecto raíz '/')
        "remote_path": "/",
        # Token OAuth obtenido durante la configuración
        "token": "",
        # Nombre interno de rclone para este remote
        "rclone_remote": "",
        # Intervalo de sincronización en minutos
        "sync_interval": 5,
        # Si está activo o pausado
        "active": True,
        # Si el servicio se inicia con el sistema operativo
        "start_with_system": False,
        # Retraso en segundos antes de iniciar la sincronización al arrancar
        "startup_delay": 0,
        # Carpetas excluidas de la sincronización
        "excluded_folders": [],
        # Carpetas con sincronización selectiva habilitada
        "selective_folders": [],
        # Tiempo de la última sincronización
        "last_sync": None,
        # Estado actual: 'syncing', 'idle', 'error', 'paused'
        "status": "idle",
    }
