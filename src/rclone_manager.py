"""
rclone_manager.py — Integración con rclone.
Gestiona la instalación, configuración, autenticación OAuth y sincronización
de servicios usando rclone como backend.
"""

import os
import subprocess
import threading
import shutil
import json
import platform
import urllib.request
import zipfile
import tarfile
import stat
import time
from typing import Callable, Optional

# Directorio donde se almacena el ejecutable de rclone local
RCLONE_DIR = os.path.join(os.path.expanduser("~"), ".rclone_python_ia", "rclone")

# Mapeo de plataformas a sus nombres de remote en rclone
PLATFORM_TYPES = {
    "OneDrive": "onedrive",
    "Google Drive": "drive",
    "Dropbox": "dropbox",
    "Box": "box",
    "SFTP": "sftp",
    "FTP": "ftp",
    "Amazon S3": "s3",
    "Backblaze B2": "b2",
    "pCloud": "pcloud",
    "Mega": "mega",
}

# URLs de descarga de rclone según plataforma
RCLONE_DOWNLOAD_URLS = {
    "Windows": "https://downloads.rclone.org/rclone-current-windows-amd64.zip",
    "Linux": "https://downloads.rclone.org/rclone-current-linux-amd64.zip",
    "Darwin": "https://downloads.rclone.org/rclone-current-osx-amd64.zip",
}


def get_rclone_path() -> str:
    """
    Devuelve la ruta al ejecutable de rclone.
    Primero busca en el PATH del sistema, luego en el directorio local.
    """
    # Buscar rclone en el PATH del sistema
    system_rclone = shutil.which("rclone")
    if system_rclone:
        return system_rclone

    # Buscar en el directorio local de la aplicación
    exe = "rclone.exe" if platform.system() == "Windows" else "rclone"
    local_path = os.path.join(RCLONE_DIR, exe)
    if os.path.isfile(local_path):
        return local_path

    return ""


def is_rclone_installed() -> bool:
    """
    Verifica si rclone está disponible en el sistema o en el directorio local.
    """
    return bool(get_rclone_path())


def install_rclone(progress_callback: Optional[Callable[[str], None]] = None) -> bool:
    """
    Descarga e instala rclone localmente para la aplicación.
    Devuelve True si la instalación fue exitosa, False en caso contrario.
    """
    # Crear directorio de rclone si no existe
    os.makedirs(RCLONE_DIR, exist_ok=True)

    # Obtener la URL de descarga según el sistema operativo
    system = platform.system()
    url = RCLONE_DOWNLOAD_URLS.get(system)
    if not url:
        if progress_callback:
            progress_callback(f"Sistema operativo no soportado: {system}")
        return False

    # Ruta temporal para el archivo descargado
    zip_path = os.path.join(RCLONE_DIR, "rclone_download.zip")

    try:
        # Notificar inicio de descarga
        if progress_callback:
            progress_callback("Descargando rclone...")

        # Descargar el archivo zip de rclone
        urllib.request.urlretrieve(url, zip_path)

        # Notificar inicio de extracción
        if progress_callback:
            progress_callback("Extrayendo rclone...")

        # Extraer el ejecutable del zip descargado
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                # Buscar el ejecutable de rclone dentro del zip
                basename = os.path.basename(member)
                exe = "rclone.exe" if system == "Windows" else "rclone"
                if basename == exe:
                    # Leer el contenido del ejecutable
                    data = zf.read(member)
                    # Escribir el ejecutable en el directorio local
                    dest = os.path.join(RCLONE_DIR, exe)
                    with open(dest, "wb") as out:
                        out.write(data)
                    # Dar permisos de ejecución en sistemas Unix
                    if system != "Windows":
                        os.chmod(dest, os.stat(dest).st_mode | stat.S_IEXEC)
                    break

        # Eliminar el archivo zip temporal
        os.remove(zip_path)

        if progress_callback:
            progress_callback("rclone instalado correctamente.")
        return True

    except Exception as e:
        if progress_callback:
            progress_callback(f"Error instalando rclone: {e}")
        return False


def get_rclone_version() -> str:
    """
    Devuelve la versión de rclone instalada, o cadena vacía si no está disponible.
    """
    rclone = get_rclone_path()
    if not rclone:
        return ""
    try:
        # Ejecutar 'rclone version' y capturar la salida
        result = subprocess.run(
            [rclone, "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # La primera línea contiene la versión
        first_line = result.stdout.strip().split("\n")[0]
        return first_line
    except Exception:
        return ""


def authorize_service(
    platform_type: str,
    remote_name: str,
    result_callback: Callable[[bool, str], None],
) -> None:
    """
    Inicia el flujo de autorización OAuth para un servicio.
    Abre el navegador para que el usuario inicie sesión.
    Llama a result_callback(success, message) cuando termina.
    Este proceso se ejecuta en un hilo separado para no bloquear la UI.
    """

    def _run_auth():
        """Función interna que ejecuta la autorización en un hilo."""
        rclone = get_rclone_path()
        if not rclone:
            result_callback(False, "rclone no está instalado.")
            return

        # Obtener el tipo de remote para rclone
        rclone_type = PLATFORM_TYPES.get(platform_type, platform_type.lower())

        try:
            # Construir el comando de autorización
            # rclone authorize abre el navegador y espera el token
            cmd = [rclone, "authorize", rclone_type]

            # Ejecutar el proceso de autorización
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutos máximo para autorizar
            )

            if proc.returncode == 0:
                # Extraer el token de la salida de rclone
                token = _extract_token_from_output(proc.stdout)
                if token:
                    result_callback(True, token)
                else:
                    result_callback(False, "No se pudo extraer el token.")
            else:
                # Reportar error con el mensaje de rclone
                error_msg = proc.stderr.strip() or "Error desconocido."
                result_callback(False, error_msg)

        except subprocess.TimeoutExpired:
            result_callback(False, "Tiempo de espera agotado para la autorización.")
        except Exception as e:
            result_callback(False, str(e))

    # Ejecutar en hilo separado para no bloquear la interfaz
    thread = threading.Thread(target=_run_auth, daemon=True)
    thread.start()


def _extract_token_from_output(output: str) -> str:
    """
    Extrae el token JSON de la salida del comando rclone authorize.
    Busca la línea que contiene el token en formato JSON.
    """
    # Buscar la línea con el token (comienza con '{' y termina con '}')
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                # Verificar que es JSON válido
                json.loads(line)
                return line
            except json.JSONDecodeError:
                continue
    return ""


def configure_remote(
    remote_name: str,
    platform_type: str,
    token: str,
) -> bool:
    """
    Configura un remote en rclone con el token OAuth obtenido.
    Crea la entrada correspondiente en el archivo de configuración de rclone.
    Devuelve True si la configuración fue exitosa.
    """
    rclone = get_rclone_path()
    if not rclone:
        return False

    # Obtener el tipo de remote para rclone
    rclone_type = PLATFORM_TYPES.get(platform_type, platform_type.lower())

    try:
        # Crear la configuración usando rclone config create
        cmd = [
            rclone,
            "config",
            "create",
            remote_name,
            rclone_type,
            "token",
            token,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0

    except Exception:
        return False


def delete_remote(remote_name: str) -> bool:
    """
    Elimina un remote de la configuración de rclone.
    Devuelve True si la eliminación fue exitosa.
    """
    rclone = get_rclone_path()
    if not rclone:
        return False

    try:
        # Eliminar el remote con rclone config delete
        result = subprocess.run(
            [rclone, "config", "delete", remote_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def list_remote_folders(
    remote_name: str,
    path: str = "",
    callback: Optional[Callable[[list], None]] = None,
) -> list:
    """
    Lista las carpetas de un remote de rclone en la ruta especificada.
    Si se proporciona callback, se ejecuta en un hilo separado.
    Devuelve la lista de carpetas o lista vacía si hay error.
    """
    rclone = get_rclone_path()
    if not rclone:
        return []

    # Construir la ruta remota
    remote_path = f"{remote_name}:{path}"

    def _run_list():
        """Función interna que ejecuta el listado en un hilo."""
        try:
            # Listar directorios usando rclone lsd
            result = subprocess.run(
                [rclone, "lsd", remote_path],
                capture_output=True,
                text=True,
                timeout=60,
            )
            folders = []
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    # Formato: "          -1 fecha hora -1 nombre_carpeta"
                    parts = line.strip().split()
                    if parts:
                        folders.append(parts[-1])
            if callback:
                callback(folders)
            return folders
        except Exception:
            if callback:
                callback([])
            return []

    if callback:
        # Ejecutar en hilo separado si se proporcionó callback
        thread = threading.Thread(target=_run_list, daemon=True)
        thread.start()
        return []
    else:
        return _run_list()


def get_disk_usage(local_folder: str) -> int:
    """
    Calcula el espacio en disco usado por la carpeta local en bytes.
    Devuelve 0 si la carpeta no existe o hay un error.
    """
    if not os.path.exists(local_folder):
        return 0

    total_size = 0
    try:
        # Recorrer todos los archivos del directorio
        for dirpath, dirnames, filenames in os.walk(local_folder):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                # Obtener tamaño del archivo (ignorar errores de permisos)
                try:
                    total_size += os.path.getsize(filepath)
                except OSError:
                    pass
    except Exception:
        pass
    return total_size


def free_disk_space(
    remote_name: str,
    local_folder: str,
    callback: Optional[Callable[[bool, str], None]] = None,
) -> None:
    """
    Libera el espacio en disco convirtiendo los archivos locales a solo-nube.
    Usa 'rclone move' para mover archivos a la nube y eliminar las copias locales.
    Llama a callback(success, message) cuando termina.
    """
    rclone = get_rclone_path()
    if not rclone:
        if callback:
            callback(False, "rclone no está instalado.")
        return

    def _run_free():
        """Función interna que ejecuta la liberación de espacio en un hilo."""
        try:
            # Usar rclone vfs/forget para marcar archivos como no cacheados
            remote_path = f"{remote_name}:/"
            cmd = [
                rclone,
                "move",
                local_folder,
                remote_path,
                "--delete-empty-src-dirs",
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                if callback:
                    callback(True, "Espacio liberado correctamente.")
            else:
                if callback:
                    callback(False, result.stderr.strip() or "Error al liberar espacio.")
        except Exception as e:
            if callback:
                callback(False, str(e))

    # Ejecutar en hilo separado
    thread = threading.Thread(target=_run_free, daemon=True)
    thread.start()


class SyncWorker:
    """
    Gestiona la sincronización continua de un servicio usando rclone bisync.
    Ejecuta rclone en un proceso separado y notifica los cambios.
    """

    def __init__(self, service: dict, log_callback: Callable[[str, bool], None]):
        """
        Inicializa el trabajador de sincronización.

        :param service: Diccionario con la configuración del servicio.
        :param log_callback: Función que recibe (archivo, sincronizado) para actualizar la UI.
        """
        # Configuración del servicio
        self.service = service
        # Función de callback para registrar cambios en la UI
        self.log_callback = log_callback
        # Proceso de rclone actualmente en ejecución
        self._process: Optional[subprocess.Popen] = None
        # Hilo que supervisa el proceso
        self._thread: Optional[threading.Thread] = None
        # Bandera para detener el ciclo de sincronización
        self._running = False
        # Temporizador para la próxima sincronización
        self._timer: Optional[threading.Timer] = None

    @property
    def is_running(self) -> bool:
        """Indica si el worker de sincronización está activo."""
        return self._running

    def start(self) -> None:
        """
        Inicia el ciclo de sincronización periódica.
        """
        if self._running:
            return
        self._running = True
        # Ejecutar la primera sincronización inmediatamente
        self._schedule_sync()

    def stop(self) -> None:
        """
        Detiene la sincronización y el proceso de rclone activo.
        """
        self._running = False
        # Cancelar el temporizador pendiente
        if self._timer:
            self._timer.cancel()
            self._timer = None
        # Terminar el proceso de rclone si está corriendo
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()

    def _schedule_sync(self) -> None:
        """
        Programa la próxima sincronización según el intervalo configurado.
        """
        if not self._running:
            return
        # Ejecutar la sincronización en este ciclo
        self._run_sync()
        # Obtener el intervalo en segundos (convertir de minutos)
        interval_sec = self.service.get("sync_interval", 5) * 60
        # Programar la próxima ejecución
        if self._running:
            self._timer = threading.Timer(interval_sec, self._schedule_sync)
            self._timer.daemon = True
            self._timer.start()

    def _run_sync(self) -> None:
        """
        Ejecuta rclone bisync para sincronizar el servicio.
        Registra los archivos modificados mediante el callback.
        """
        rclone = get_rclone_path()
        if not rclone:
            self.log_callback("rclone no disponible", False)
            return

        # Construir la ruta remota
        remote_name = self.service.get("rclone_remote", "")
        remote_path = self.service.get("remote_path", "/")
        local_folder = self.service.get("local_folder", "")
        excluded = self.service.get("excluded_folders", [])

        if not remote_name or not local_folder:
            return

        # Asegurar que la carpeta local existe
        os.makedirs(local_folder, exist_ok=True)

        remote_full = f"{remote_name}:{remote_path}"

        # Construir el comando bisync de rclone
        cmd = [
            rclone,
            "bisync",
            local_folder,
            remote_full,
            "--resync",    # Forzar resincronización completa (ignora estado previo)
            "--verbose",   # Salida detallada para el log
        ]

        # Agregar exclusiones de carpetas configuradas
        for folder in excluded:
            cmd.extend(["--exclude", f"/{folder}/**"])

        try:
            # Ejecutar rclone y capturar la salida línea por línea
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            # Procesar la salida en tiempo real
            for line in self._process.stdout:
                line = line.strip()
                if line:
                    # Detectar si la línea indica un archivo modificado
                    synced = "copied" in line.lower() or "updated" in line.lower()
                    self.log_callback(line, synced)

            self._process.wait()

        except Exception as e:
            self.log_callback(f"Error de sincronización: {e}", False)
