"""
Integración con rclone para Rclone Python IA.
Maneja la instalación, configuración y ejecución de rclone.
"""

import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from datetime import datetime
from typing import Callable, List, Optional

from core.service import Service


def get_rclone_executable() -> str:
    """
    Retorna la ruta al ejecutable de rclone.
    Busca primero en el PATH del sistema, luego en el directorio de la app.
    """
    # Buscar en el PATH del sistema
    rclone_path = shutil.which("rclone")
    if rclone_path:
        return rclone_path

    # Buscar en el directorio de la aplicación
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if sys.platform == "win32":
        local_rclone = os.path.join(app_dir, "rclone.exe")
    else:
        local_rclone = os.path.join(app_dir, "rclone")

    if os.path.exists(local_rclone):
        return local_rclone

    return "rclone"


def get_rclone_version() -> str:
    """
    Obtiene la versión instalada de rclone.
    Retorna la cadena de versión o mensaje de error.
    """
    try:
        result = subprocess.run(
            [get_rclone_executable(), "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # Extraer primera línea con la versión
            first_line = result.stdout.strip().split("\n")[0]
            return first_line
        return "rclone no disponible"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "rclone no instalado"


def is_rclone_installed() -> bool:
    """Verifica si rclone está instalado y disponible."""
    version = get_rclone_version()
    return "rclone" in version.lower() and "no" not in version.lower()


def get_rclone_config_path() -> str:
    """
    Retorna la ruta al archivo de configuración de rclone.
    Crea el directorio si no existe.
    """
    try:
        result = subprocess.run(
            [get_rclone_executable(), "config", "file"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # La salida contiene la ruta al archivo de config
            lines = result.stdout.strip().split("\n")
            for line in lines:
                if line.strip() and not line.startswith("Configuration"):
                    return line.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Ruta por defecto si no se puede obtener de rclone
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", ""), "rclone", "rclone.conf")
    elif sys.platform == "darwin":
        return os.path.expanduser("~/.config/rclone/rclone.conf")
    else:
        return os.path.expanduser("~/.config/rclone/rclone.conf")


def create_service_remote(service: Service, token: Optional[str] = None) -> bool:
    """
    Crea o actualiza la configuración del remote de rclone para un servicio.
    Retorna True si fue exitoso.
    """
    remote_name = service.get_rclone_remote_name()
    rclone_exe = get_rclone_executable()

    try:
        if service.platform == "onedrive":
            # Configuración específica para OneDrive
            cmd = [
                rclone_exe, "config", "create",
                remote_name, "onedrive",
                "token", token if token else "",
            ]
        elif service.platform == "googledrive":
            # Configuración para Google Drive
            cmd = [
                rclone_exe, "config", "create",
                remote_name, "drive",
                "token", token if token else "",
            ]
        elif service.platform == "dropbox":
            # Configuración para Dropbox
            cmd = [
                rclone_exe, "config", "create",
                remote_name, "dropbox",
                "token", token if token else "",
            ]
        else:
            # Configuración genérica para otros servicios
            cmd = [
                rclone_exe, "config", "create",
                remote_name, service.platform,
                "token", token if token else "",
            ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(f"Error creando remote rclone: {e}")
        return False


def delete_service_remote(service: Service) -> bool:
    """
    Elimina la configuración del remote de rclone para un servicio.
    Retorna True si fue exitoso.
    """
    remote_name = service.get_rclone_remote_name()
    rclone_exe = get_rclone_executable()

    try:
        result = subprocess.run(
            [rclone_exe, "config", "delete", remote_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def authorize_service(service: Service, callback: Callable[[bool, str], None]):
    """
    Inicia el proceso de autorización OAuth de rclone en un hilo separado.
    Abre el navegador para que el usuario inicie sesión.
    callback(success: bool, token_or_error: str) se llama cuando termina.
    """
    def run_auth():
        """Ejecuta el proceso de autorización en segundo plano."""
        rclone_exe = get_rclone_executable()

        # Mapear plataforma al tipo de rclone
        platform_type_map = {
            "onedrive": "onedrive",
            "googledrive": "drive",
            "dropbox": "dropbox",
            "box": "box",
            "mega": "mega",
            "pcloud": "pcloud",
            "yandex": "yandex",
        }
        rclone_type = platform_type_map.get(service.platform, service.platform)

        try:
            # Ejecutar rclone authorize para iniciar el flujo OAuth
            result = subprocess.run(
                [rclone_exe, "authorize", rclone_type],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutos para que el usuario inicie sesión
            )

            if result.returncode == 0:
                # Extraer el token del output
                token = extract_token_from_output(result.stdout + result.stderr)
                if token:
                    callback(True, token)
                else:
                    callback(True, result.stdout.strip())
            else:
                callback(False, result.stderr.strip())

        except subprocess.TimeoutExpired:
            callback(False, "Tiempo de espera agotado para la autorización")
        except FileNotFoundError:
            callback(False, "rclone no está instalado. Por favor instale rclone primero.")
        except OSError as e:
            callback(False, f"Error al ejecutar rclone: {e}")

    # Ejecutar en hilo separado para no bloquear la UI
    thread = threading.Thread(target=run_auth, daemon=True)
    thread.start()


def extract_token_from_output(output: str) -> Optional[str]:
    """
    Extrae el token JSON del output de rclone authorize.
    Busca el patrón de token en la salida estándar de rclone.
    """
    # Buscar patrón de token JSON en la salida
    token_pattern = r'\{[^{}]*"access_token"[^{}]*\}'
    match = re.search(token_pattern, output)
    if match:
        return match.group(0)

    # Buscar patrón alternativo de paste token
    paste_pattern = r'Paste the following into your remote machine --->(.*?)<---'
    match = re.search(paste_pattern, output, re.DOTALL)
    if match:
        return match.group(1).strip()

    return None


class SyncManager:
    """
    Gestor de sincronización para un servicio específico.
    Maneja la ejecución y monitoreo de rclone bisync/resync.
    """

    def __init__(self, service: Service, config_manager=None):
        """Inicializa el gestor con el servicio a sincronizar."""
        # Referencia al servicio
        self.service = service
        # Referencia al gestor de configuración para actualizar estado
        self.config_manager = config_manager
        # Proceso de rclone en ejecución
        self.process: Optional[subprocess.Popen] = None
        # Hilo de monitoreo
        self.monitor_thread: Optional[threading.Thread] = None
        # Si la sincronización está activa
        self.is_running = False
        # Callback para notificar cambios de estado
        self.status_callback: Optional[Callable] = None
        # Callback para notificar archivos procesados
        self.file_callback: Optional[Callable] = None

    def build_sync_command(self) -> List[str]:
        """
        Construye el comando rclone para sincronizar el servicio.
        Usa bisync con resync si está habilitado, con todas las opciones configuradas.
        """
        rclone_exe = get_rclone_executable()
        remote_name = self.service.get_rclone_remote_name()

        # Construir ruta remota completa
        remote_path = self.service.remote_path.lstrip("/")
        remote_source = f"{remote_name}:{remote_path}"
        local_dest = self.service.local_path

        # Determinar comando base (bisync o sync)
        if self.service.use_resync:
            cmd = [rclone_exe, "bisync", remote_source, local_dest]
        else:
            cmd = [rclone_exe, "sync", remote_source, local_dest]

        # Agregar flag de verbose para capturar archivos procesados
        cmd.extend(["--verbose", "--stats=1s"])

        # Agregar filtros de exclusión
        if self.service.exclude_personal_vault and self.service.platform == "onedrive":
            cmd.extend(["--exclude", "Personal Vault/**"])

        # Agregar otras carpetas excluidas
        for folder in self.service.excluded_folders:
            if folder:
                cmd.extend(["--exclude", f"{folder}/**"])

        # Modo on-demand (VFS) para descarga solo cuando se necesita
        if self.service.on_demand:
            cmd.append("--drive-on-demand-streaming")

        return cmd

    def start(self):
        """
        Inicia la sincronización del servicio.
        Actualiza el estado del servicio y lanza el proceso rclone.
        """
        if self.is_running:
            return

        self.is_running = True
        self.service.is_syncing = True

        # Actualizar estado en el gestor de configuración
        if self.config_manager:
            self.config_manager.update_service(self.service)

        # Notificar cambio de estado
        if self.status_callback:
            self.status_callback("Sincronizando...")

        # Lanzar monitoreo en hilo separado
        self.monitor_thread = threading.Thread(target=self._run_sync, daemon=True)
        self.monitor_thread.start()

    def stop(self):
        """
        Detiene la sincronización del servicio.
        Termina el proceso rclone si está corriendo.

        La terminación del proceso se realiza en un hilo secundario para no
        bloquear el hilo de la interfaz gráfica mientras se espera al proceso.
        """
        self.is_running = False

        # Capture the current process reference and clear self.process so that
        # a subsequent start() cannot accidentally re-terminate the same instance.
        process = self.process
        self.process = None
        if process and process.poll() is None:
            def _terminate():
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

            threading.Thread(target=_terminate, daemon=True).start()

        self.service.is_syncing = False

        # Actualizar estado en el gestor de configuración
        if self.config_manager:
            self.config_manager.update_service(self.service)

        # Notificar cambio de estado
        if self.status_callback:
            self.status_callback("Detenido")

    def _run_sync(self):
        """
        Método interno que ejecuta el proceso de sincronización.
        Se ejecuta en un hilo separado para no bloquear la UI.
        """
        try:
            cmd = self.build_sync_command()

            # Crear directorio local si no existe
            os.makedirs(self.service.local_path, exist_ok=True)

            # Ejecutar rclone con captura de salida en tiempo real
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            # Leer línea por línea para detectar archivos procesados
            for line in iter(self.process.stdout.readline, ""):
                if not self.is_running:
                    break
                self._process_rclone_output(line.strip())

            # Esperar que termine el proceso
            self.process.wait()

            # Actualizar timestamp de última sincronización
            self.service.last_sync = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.service.is_syncing = False

            # Actualizar estado en gestor de configuración
            if self.config_manager:
                self.config_manager.update_service(self.service)

            # Notificar que terminó
            if self.status_callback and self.is_running:
                self.status_callback("Actualizado")

        except FileNotFoundError:
            self.service.is_syncing = False
            if self.status_callback:
                self.status_callback("Error: rclone no instalado")
        except OSError as e:
            self.service.is_syncing = False
            if self.status_callback:
                self.status_callback(f"Error: {e}")

    def _process_rclone_output(self, line: str):
        """
        Procesa una línea de salida de rclone para extraer información de archivos.
        Identifica archivos transferidos y actualiza la lista de recientes.
        """
        if not line:
            return

        # Detectar archivos copiados/transferidos
        transfer_patterns = [
            r"Copied\s+(.+)",
            r"Updated\s+(.+)",
            r"Deleted\s+(.+)",
            r"Moved\s+(.+)",
        ]

        for pattern in transfer_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                file_name = match.group(1).strip()
                status = pattern.split(r"\s")[0].replace("\\", "")

                # Crear entrada de archivo
                file_entry = {
                    "file": file_name,
                    "status": status,
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "synced": True,
                }

                # Notificar nuevo archivo procesado
                if self.file_callback:
                    self.file_callback(file_entry)

                # Actualizar lista de recientes en el servicio
                if self.config_manager:
                    self.config_manager.update_service_recent_files(
                        self.service.service_id, file_entry
                    )
                break


def get_remote_storage_info(service: Service) -> Optional[str]:
    """
    Obtiene información de cuota del almacenamiento remoto usando ``rclone about``.

    Retorna una cadena legible con Total, Usado y Libre, por ejemplo:
    ``"Total: 1.024 TiB  |  Usado: 125.3 GiB  |  Libre: 898.7 GiB"``.

    Retorna ``None`` si el servicio no soporta ``about`` (ej. S3, SFTP) o si
    rclone no está disponible.
    """
    rclone_exe = get_rclone_executable()
    rclone_config = get_rclone_config_path()
    remote_name = service.get_rclone_remote_name()

    try:
        result = subprocess.run(
            [rclone_exe, "--config", rclone_config, "about", f"{remote_name}:"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return None

        # Parse output lines like "Total:   1.024 TiB", "Used:    125.3 GiB"
        info: dict = {}
        for line in result.stdout.strip().splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                info[key.strip().lower()] = value.strip()

        parts = []
        if "total" in info:
            parts.append(f"Total: {info['total']}")
        if "used" in info:
            parts.append(f"Usado: {info['used']}")
        if "free" in info:
            parts.append(f"Libre: {info['free']}")

        return "  |  ".join(parts) if parts else None

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def get_remote_folders(service: Service) -> List[dict]:
    """
    Obtiene la lista de carpetas del remote de rclone para un servicio.
    Retorna lista de dicts con 'name', 'path' e 'is_dir'.
    """
    rclone_exe = get_rclone_executable()
    remote_name = service.get_rclone_remote_name()

    try:
        # Ejecutar rclone lsd para listar solo directorios
        result = subprocess.run(
            [rclone_exe, "lsd", f"{remote_name}:", "--max-depth=1"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        folders = []
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    # Parsear salida de lsd: tamaño fecha hora nombre
                    parts = line.strip().split(None, 4)
                    if len(parts) >= 5:
                        folder_name = parts[4]
                        folders.append({
                            "name": folder_name,
                            "path": f"/{folder_name}",
                            "is_dir": True,
                            "synced": folder_name not in service.excluded_folders,
                        })

        return folders

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


def get_disk_usage(local_path: str) -> dict:
    """
    Obtiene el uso de disco de la carpeta local del servicio.
    Retorna dict con 'used', 'total' y 'free' en bytes.
    """
    try:
        stat = shutil.disk_usage(local_path)
        return {
            "total": stat.total,
            "used": stat.used,
            "free": stat.free,
        }
    except (OSError, FileNotFoundError):
        return {"total": 0, "used": 0, "free": 0}


def format_bytes(size_bytes: int) -> str:
    """
    Formatea un tamaño en bytes a una cadena legible.
    Ejemplo: 1024 -> '1.0 KB'
    """
    if size_bytes == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)

    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1

    return f"{size:.1f} {units[i]}"


def open_folder(path: str):
    """
    Abre la carpeta en el explorador de archivos del sistema.
    Compatible con Windows, macOS y Linux.
    """
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def free_disk_space(service: Service) -> bool:
    """
    Libera el espacio en disco del servicio usando rclone vfs/forget.
    Pone los archivos de vuelta en modo solo-nube.
    Retorna True si fue exitoso.
    """
    rclone_exe = get_rclone_executable()
    remote_name = service.get_rclone_remote_name()

    try:
        # Usar rclone rc vfs/forget para limpiar la caché VFS
        result = subprocess.run(
            [rclone_exe, "rc", "vfs/forget", f"--fs={remote_name}:"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
