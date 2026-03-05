# RclonePyIA

Multiplatform rclone manager with a graphical interface built in Python + PyQt5.

## Features

- **Setup wizard** – three-step wizard to add a new cloud service (choose local folder → choose platform → OAuth browser authentication).
- **Main window** – tabbed interface showing all configured services with sync status, last sync time, interval, and the last 50 changed files.  Minimises to the system tray.
- **Configuration window** – 7-option panel for default settings, directories, exclusion rules, folder tree, sync interval, disk space, and service information.
- **Bidirectional sync** – uses `rclone bisync` with `--resync` for reliable two-way synchronisation.
- **On-demand download** – files are only downloaded when accessed (VFS cache).
- **Multiplatform** – runs on Windows, Linux, and macOS.

## Requirements

- Python 3.9+
- [rclone](https://rclone.org/downloads/) installed and available in `PATH`
- PyQt5 (`pip install PyQt5`)

## Installation

```bash
git clone https://github.com/xtremevice/Rclone_Python_IA.git
cd Rclone_Python_IA
pip install -r requirements.txt
python main.py
```

## Building executables

| Platform | Command | Output |
|----------|---------|--------|
| Windows  | `build\build_windows.bat` | `dist\RclonePyIA.exe` |
| Linux    | `./build/build_linux.sh`  | `dist/RclonePyIA-x86_64.AppImage` |
| macOS    | `./build/build_mac.sh`    | `dist/RclonePyIA.app` |

All three build scripts use [PyInstaller](https://pyinstaller.org/).

## Project structure

```
main.py                         Entry point
requirements.txt
src/
  app.py                        Application controller (first-run detection)
  core/
    config.py                   JSON-based configuration manager
    rclone.py                   rclone subprocess integration
    service_manager.py          Sync scheduling (QTimer + QThread)
  windows/
    setup_wizard.py             3-step new-service wizard
    main_window.py              Tabbed main window + system tray
    config_window.py            7-option service configuration
build/
  rclonepyia.spec               PyInstaller spec (all platforms)
  build_linux.sh                Linux AppImage build script
  build_mac.sh                  macOS single-binary build script
  build_windows.bat             Windows EXE build script
```
