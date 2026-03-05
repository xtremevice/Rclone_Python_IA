# Copilot Instructions – Rclone Python IA

## Propósito del Proyecto

Aplicación multiplataforma (Windows, Linux, macOS) que actúa como interfaz gráfica para **rclone**, permitiendo a usuarios promedio sincronizar archivos desde múltiples servicios en la nube (OneDrive, Google Drive, Dropbox, etc.) sin configuraciones complejas. La experiencia debe ser tan sencilla como OneDrive en Windows.

---

## Stack Tecnológico

| Componente | Tecnología |
|---|---|
| Lenguaje | Python 3.10+ |
| GUI | PyQt5 |
| Bandeja del sistema | pystray + Pillow |
| Backend de sincronización | rclone (binario externo) |
| Empaquetado | PyInstaller |

Archivos clave de dependencias: `requirements.txt`.

---

## Estructura del Proyecto

```
Rclone_Python_IA/
├── main.py               # Punto de entrada
├── core/
│   ├── config.py         # ConfigManager – persistencia JSON
│   ├── rclone.py         # Wrapper de rclone (subprocess)
│   └── service.py        # Modelo de datos Service
├── ui/
│   ├── wizard.py         # Asistente de nuevo servicio (3 ventanas)
│   ├── main_window.py    # Ventana principal con pestañas
│   └── config_window.py  # Ventana de configuración de servicio
├── resources/            # Iconos, imágenes
├── build_scripts/        # Scripts de empaquetado por plataforma
└── requirements.txt
```

---

## Requisitos de Interfaz Gráfica

### Tamaños de Ventana (porcentaje de la pantalla)
- **Asistente de nuevo servicio**: 70% alto × 60% ancho
- **Ventana principal**: 60% alto × 20% ancho
- **Ventana de configuración**: 60% alto × 70% ancho

### Asistente de Nuevo Servicio (`ui/wizard.py`)
Tres pasos secuenciales:
1. **Paso 1** – Seleccionar directorio local base (explorador de carpetas).
2. **Paso 2** – Elegir plataforma/servicio (OneDrive, Google Drive, Dropbox, etc.).
3. **Paso 3** – Autenticación OAuth: abrir navegador con `rclone authorize`, esperar el token, mostrar confirmación y avanzar a la ventana principal.

### Ventana Principal (`ui/main_window.py`)
- Pestañas superiores: una pestaña por servicio, con el nombre del servicio como título.
- Debajo de las pestañas: etiqueta con nombre del servicio, estado de sincronización (sincronizando / actualizado), frecuencia de sincronización y plataforma vinculada.
- Lista de cambios recientes: últimos 50 archivos modificados con estado (sincronizado / pendiente). Ocupa 100% horizontal × 60% vertical.
- Tres botones en la parte inferior (100% horizontal, ~5% vertical):
  1. **Abrir carpeta** del servicio activo.
  2. **Pausar / Reanudar** sincronización del servicio activo.
  3. **Configuración** del servicio activo.
- Al minimizar → enviar a bandeja del sistema; no existe botón de maximizar.
- Clic en icono de bandeja → restaurar ventana.

### Ventana de Configuración (`ui/config_window.py`)
Menú lateral izquierdo con 7 opciones:
1. **Configuración por defecto**: sincronizar desde raíz `/`, modo VFS bajo demanda, bisync con `--resync`, excluir "Almacén personal" (aplica solo a OneDrive).
2. **Directorios**: cambiar directorio local y directorio remoto dentro del servicio.
3. **Excepciones**: agregar/quitar carpetas excluidas (la exclusión de "Almacén personal" está activa por defecto).
4. **Árbol de carpetas**: vista de árbol de carpetas remotas con checkbox para activar/desactivar sincronización por carpeta.
5. **Frecuencia de sincronización**: opciones cada 1, 5, 15, 30, 60 min; 1, 2, 3, 6, 12, 24 h. También configura inicio con el sistema y retraso inicial.
6. **Espacio en disco**: botón para liberar espacio (poner archivos en modo solo-nube), indicador de uso actualizado cada 10 segundos, botón para eliminar el servicio (con confirmación).
7. **Información del servicio**: cuenta sincronizada, tipo de servicio, directorio, frecuencia, estado activo y versión de rclone.
- Botón **Guardar** que persiste todos los cambios.

---

## Lógica de Sincronización (core/rclone.py)

Parámetros de rclone por defecto:
```
--transfers 16 --checkers 32 --drive-chunk-size 128M --buffer-size 64M -P
```

Flujo `bisync`:
1. Ejecutar `rclone bisync <remote>:/ <local_path> <opts> --exclude "<exclusion>"`.
2. Si falla (código ≠ 0), reintentar con `--resync`.
3. Registrar los últimos 50 archivos modificados en el estado del servicio.

Modo VFS (solo bajo demanda):
```
--vfs-cache-mode full --vfs-cache-max-size 10G --dir-cache-time 60s
--poll-interval 10s --vfs-read-ahead 128M
```

---

## Modelo de Datos (`core/service.py`)

Cada servicio tiene:
```python
{
    "name": str,          # Nombre asignado por el usuario
    "platform": str,      # "onedrive", "gdrive", "dropbox", etc.
    "remote": str,        # Nombre del remoto en rclone (ej. "onedrive:")
    "local_path": str,    # Directorio local absoluto
    "remote_path": str,   # Directorio remoto (por defecto "/")
    "sync_interval": int, # Segundos entre sincronizaciones
    "exclusions": list,   # Carpetas excluidas
    "tree_disabled_folders": list,  # Carpetas desactivadas desde el árbol (opción 4)
    "autostart": bool,    # Iniciar con el sistema
    "autostart_delay": int,    # Segundos de retraso al iniciar
    "is_syncing": bool,   # Estado actual
    "last_synced": str,   # ISO 8601 timestamp
    "recent_files": list  # Últimos 50 archivos (máx.)
}
```

Persistencia: `ConfigManager` guarda y carga desde un archivo JSON en el directorio de configuración del usuario.

---

## Empaquetado

| Plataforma | Script | Salida |
|---|---|---|
| Windows | `build_scripts/build_windows.bat` | `RclonePythonIA.exe` (un solo ejecutable) |
| Linux | `build_scripts/build_linux.sh` | AppImage |
| macOS | `build_scripts/build_mac.sh` | Ejecutable único |

---

## Convenciones de Código

1. **Comentarios obligatorios**: toda función debe tener un comentario justo encima describiendo su propósito.
2. **Funciones largas**: si una función supera las 50 líneas, agregar comentarios en cada línea o bloque lógico relevante.
3. **Idioma**: comentarios y variables internas en español; identificadores de código en inglés (snake_case).
4. **Sin maximizar**: la ventana principal no debe tener botón de maximizar (`Qt.WindowMaximizeButtonHint` desactivado).
5. **Bandeja del sistema**: usar `pystray` con un icono PNG de 64×64 px ubicado en `resources/`.
6. **Gestión de hilos**: cada servicio corre su sincronización en un `QThread` independiente para no bloquear la UI.

---

## Flujo de Inicio

```
main.py
  └─ ConfigManager.has_services()?
       ├─ NO  → ServiceWizard (3 pasos) → MainWindow
       └─ SÍ  → MainWindow directamente
```

---

## Notas Importantes

- Rclone debe estar instalado en el sistema. La aplicación verifica su presencia al iniciar y muestra un mensaje de error si no se encuentra.
- El archivo de configuración de rclone se ubica en la ruta estándar de la plataforma (`~/.config/rclone/rclone.conf` en Linux/macOS, `%APPDATA%\rclone\rclone.conf` en Windows).
- La exclusión de "Almacén personal" (`/Almacén personal/**`) está activa **solo para OneDrive** porque causa errores durante la sincronización; no aplica a Google Drive, Dropbox u otros servicios. Se puede desactivar desde la opción 3 de configuración.
- El modo `bisync --resync` se usa como recuperación automática ante conflictos o primera sincronización.
