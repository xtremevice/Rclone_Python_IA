# Rclone Manager

**Rclone Manager** es una aplicación de escritorio multiplataforma (Windows, Linux, macOS) escrita en Python que usa [rclone](https://rclone.org) como motor de sincronización. Ofrece una experiencia similar a OneDrive de Windows: pocos clics para configurar múltiples servicios en la nube y sincronización automática en segundo plano.

---

## Capturas de pantalla

| Ventana Principal | Configuración | Asistente de nuevo servicio |
|---|---|---|
| ![Main Window](https://github.com/user-attachments/assets/b02bae44-a1aa-4587-8546-891112200864) | ![Config Window](https://github.com/user-attachments/assets/d3035685-11c0-495d-8980-a4a07fa54230) | ![Wizard Step 1](https://github.com/user-attachments/assets/86092998-d04c-42df-81bd-3ea47913838c) |

---

## Características

- **Asistente de configuración** en 3 pasos: carpeta local → servicio en la nube → autenticación OAuth en el navegador.
- **Ventana principal** con pestañas por servicio mostrando estado, intervalo, plataforma y lista de los últimos 50 archivos modificados.
- **Minimización al área de notificaciones** (bandeja del sistema) con ícono de acceso rápido.
- **Ventana de configuración** con 7 secciones:
  1. Configuración rclone por defecto
  2. Directorios local/remoto
  3. Patrones de exclusión (incluye "Almacén personal/**" de OneDrive por defecto)
  4. Árbol de carpetas del servicio con toggle de sincronización
  5. Intervalo de sincronización (1 min a 24 horas) y arranque automático
  6. Uso de disco y liberación de caché local
  7. Información del servicio y versión de rclone
- **Sincronización con `rclone bisync`**: primer arranque con `--resync`, siguientes sin él.
- **Servicios soportados**: OneDrive, Google Drive, Dropbox, Box, S3, Backblaze B2, MEGA, SFTP, FTP, WebDAV.

## Prerrequisitos

1. [Python 3.10+](https://python.org)
2. [rclone](https://rclone.org/downloads/) instalado y en el PATH
3. Dependencias Python: `pip install -r requirements.txt`

## Uso

```bash
pip install -r requirements.txt
python main.py
```

Si no hay servicios configurados, el asistente se abrirá automáticamente.

## Construcción de ejecutables

| Plataforma | Script | Resultado |
|---|---|---|
| Windows | `build\build_windows.bat` | `dist\windows\RcloneManager.exe` |
| Linux   | `build/build_linux.sh`    | `dist/linux/RcloneManager-x86_64.AppImage` |
| macOS   | `build/build_mac.sh`      | `dist/mac/RcloneManager.app` |

Todos los scripts requieren `pyinstaller` (incluido en `requirements.txt`). Para el AppImage de Linux también se necesita [`appimagetool`](https://github.com/AppImage/AppImageKit/releases).

## Estructura del proyecto

```
├── main.py                   # Punto de entrada y controlador de la aplicación
├── requirements.txt
├── assets/
│   ├── icon.png              # Ícono de la aplicación
│   └── create_icon.py        # Generador del ícono
├── app/
│   ├── config.py             # Gestión de configuración (JSON)
│   ├── rclone_manager.py     # Wrapper de comandos rclone
│   ├── sync_manager.py       # Planificador de sincronizaciones periódicas
│   ├── tray.py               # Ícono de bandeja del sistema (pystray)
│   ├── utils.py              # Utilidades de UI compartidas
│   └── windows/
│       ├── wizard.py         # Asistente de nuevo servicio (3 pasos)
│       ├── main_window.py    # Ventana principal con pestañas
│       └── config_window.py  # Ventana de configuración (7 secciones)
└── build/
    ├── rclone_manager.spec   # Spec de PyInstaller
    ├── build_windows.bat
    ├── build_linux.sh
    └── build_mac.sh
```
