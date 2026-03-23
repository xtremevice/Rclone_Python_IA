#!/usr/bin/env bash
# build.sh – General multi-platform build script for Rclone Manager
#
# Builds the final distributable executables for one or more target platforms
# by delegating to the dedicated per-platform scripts in the build/ directory.
#
# Usage:
#   ./build.sh                   # Auto-detect host OS and build for it
#   ./build.sh --all             # Build for all platforms (cross-builds via Docker)
#   ./build.sh --linux           # Linux AppImage only
#   ./build.sh --mac             # macOS .app bundle (native arch; macOS host only)
#   ./build.sh --mac-intel       # macOS Intel x86_64 .app (macOS host only)
#   ./build.sh --mac-arm64       # macOS Apple Silicon arm64 .app (macOS host only)
#   ./build.sh --windows         # Windows .exe only
#   ./build.sh --linux --windows # Linux and Windows
#   ./build.sh --help            # Show this help
#
# Cross-platform notes:
#   • Linux builds always run natively on a Linux host, or via a Docker
#     container (python:3.12-slim) when cross-building from macOS.
#   • Windows builds use Docker + the cdrx/pyinstaller-windows image when
#     cross-compiling from Linux or macOS.
#   • macOS builds MUST run on a real macOS machine and cannot be
#     cross-compiled.  Two Apple Silicon (arm64) and Intel (x86_64) .app
#     bundles can only be built on the corresponding native hardware.
#
# Output directories mirror the CI workflow:
#   dist/linux/RcloneManager-x86_64.AppImage
#   dist/mac/RcloneManager.app
#   dist/windows/RcloneManager.exe
#
# Prerequisites (native builds):
#   • Python 3.10+  with pip
#   • python3-tk    (Linux: sudo apt install python3-tk)
#   • appimagetool  (Linux: downloaded automatically if absent)
#   • Xcode CLT     (macOS: optional, for codesign)
#
# Prerequisites (cross-platform builds via Docker):
#   • Docker must be installed and the daemon must be running.

set -euo pipefail

# ---------------------------------------------------------------------------
# Colours & helpers
# ---------------------------------------------------------------------------
_RED='\033[0;31m'
_GREEN='\033[0;32m'
_YELLOW='\033[1;33m'
_CYAN='\033[0;36m'
_BOLD='\033[1m'
_RESET='\033[0m'

info()    { echo -e "${_CYAN}[INFO]${_RESET}  $*"; }
success() { echo -e "${_GREEN}[OK]${_RESET}    $*"; }
warn()    { echo -e "${_YELLOW}[WARN]${_RESET}  $*"; }
error()   { echo -e "${_RED}[ERROR]${_RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

# ---------------------------------------------------------------------------
# Locate the repository root (where this script lives)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR"
cd "$ROOT"

# ---------------------------------------------------------------------------
# Detect host OS
# ---------------------------------------------------------------------------
HOST_OS="$(uname -s)"
case "$HOST_OS" in
    Linux*)   HOST_PLATFORM="linux" ;;
    Darwin*)  HOST_PLATFORM="mac" ;;
    CYGWIN*|MINGW*|MSYS*) HOST_PLATFORM="windows" ;;
    *)        HOST_PLATFORM="unknown" ;;
esac

# ---------------------------------------------------------------------------
# Parse command-line flags
# ---------------------------------------------------------------------------
BUILD_LINUX=false
BUILD_MAC=false
BUILD_WINDOWS=false
AUTO_DETECT=true   # set to false as soon as any explicit platform flag is given

show_help() {
    cat <<EOF

${_BOLD}Rclone Manager – multi-platform build script${_RESET}

Usage:
  $(basename "$0") [OPTIONS]

Options:
  --linux          Build Linux AppImage (dist/linux/RcloneManager-x86_64.AppImage)
  --mac            Build macOS .app bundle – native arch (dist/mac/RcloneManager.app)
  --mac-intel      Alias for --mac on an Intel Mac
  --mac-arm64      Alias for --mac on an Apple Silicon Mac
  --windows        Build Windows executable (dist/windows/RcloneManager.exe)
  --all            Build for all platforms (cross-builds via Docker where needed)
  -h, --help       Show this help and exit

Without options the script auto-detects the host OS and builds for it.

Cross-platform notes:
  • Linux ↔ Windows cross-builds use Docker (cdrx/pyinstaller-windows / python:3.12-slim).
  • macOS builds CANNOT be cross-compiled; they require a real macOS machine.

EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --linux)       BUILD_LINUX=true;   AUTO_DETECT=false ;;
        --mac|--mac-intel|--mac-arm64)
                       BUILD_MAC=true;     AUTO_DETECT=false ;;
        --windows)     BUILD_WINDOWS=true; AUTO_DETECT=false ;;
        --all)         BUILD_LINUX=true; BUILD_MAC=true; BUILD_WINDOWS=true
                       AUTO_DETECT=false ;;
        -h|--help)     show_help; exit 0 ;;
        *)             die "Unknown option: $1  (run $(basename "$0") --help)" ;;
    esac
    shift
done

# If no explicit flags, build for the host platform.
if [[ "$AUTO_DETECT" == "true" ]]; then
    case "$HOST_PLATFORM" in
        linux)   BUILD_LINUX=true ;;
        mac)     BUILD_MAC=true ;;
        windows) BUILD_WINDOWS=true ;;
        *)       die "Unsupported host OS '$HOST_OS'. Use --linux / --mac / --windows explicitly." ;;
    esac
    info "Auto-detected host platform: ${_BOLD}$HOST_PLATFORM${_RESET}"
fi

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo ""
echo -e "${_BOLD}════════════════════════════════════════════════════════${_RESET}"
echo -e "${_BOLD}         Rclone Manager – Build for all platforms        ${_RESET}"
echo -e "${_BOLD}════════════════════════════════════════════════════════${_RESET}"
printf "  %-20s %s\n" "Host OS:"     "$HOST_OS ($HOST_PLATFORM)"
printf "  %-20s %s\n" "Build Linux:" "$BUILD_LINUX"
printf "  %-20s %s\n" "Build macOS:" "$BUILD_MAC"
printf "  %-20s %s\n" "Build Windows:" "$BUILD_WINDOWS"
echo -e "${_BOLD}════════════════════════════════════════════════════════${_RESET}"
echo ""

# Track per-platform result for the summary table
RESULT_LINUX="skipped"
RESULT_MAC="skipped"
RESULT_WINDOWS="skipped"

# ---------------------------------------------------------------------------
# Helper: run a build in Docker
# ---------------------------------------------------------------------------

# Cache the Docker availability check so the daemon is contacted only once.
_DOCKER_AVAILABLE=""
_docker_available() {
    if [[ -z "$_DOCKER_AVAILABLE" ]]; then
        if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
            _DOCKER_AVAILABLE="yes"
        else
            _DOCKER_AVAILABLE="no"
        fi
    fi
    [[ "$_DOCKER_AVAILABLE" == "yes" ]]
}

_require_docker() {
    if ! _docker_available; then
        die "Docker is required for cross-platform builds but is not available.\n" \
            "       Install Docker Desktop or Docker Engine and make sure the daemon is running."
    fi
}

# ---------------------------------------------------------------------------
# Linux build
# ---------------------------------------------------------------------------
build_linux() {
    echo -e "\n${_BOLD}──── Building Linux AppImage ────${_RESET}"
    if [[ "$HOST_PLATFORM" == "linux" ]]; then
        info "Running natively on Linux..."
        chmod +x build/build_linux.sh
        build/build_linux.sh
    else
        info "Cross-building Linux AppImage via Docker (python:3.12-slim)..."
        _require_docker
        # We mount the repo into the container and run the linux build script.
        # The container must have python3-tk; we install it inside the script.
        docker run --rm \
            -v "$ROOT:/workspace" \
            -w /workspace \
            --env APPIMAGE_EXTRACT_AND_RUN=1 \
            python:3.12-slim \
            bash -c "
                set -euo pipefail
                apt-get update -qq
                apt-get install -y --no-install-recommends python3-tk wget fuse libfuse2 2>/dev/null || \
                    apt-get install -y --no-install-recommends python3-tk wget 2>/dev/null
                chmod +x build/build_linux.sh
                bash build/build_linux.sh
            "
    fi
    success "Linux AppImage → dist/linux/RcloneManager-x86_64.AppImage"
    RESULT_LINUX="success"
}

# ---------------------------------------------------------------------------
# macOS build
# ---------------------------------------------------------------------------
build_mac() {
    echo -e "\n${_BOLD}──── Building macOS .app bundle ────${_RESET}"
    if [[ "$HOST_PLATFORM" != "mac" ]]; then
        warn "macOS builds require a real macOS machine and cannot be cross-compiled."
        warn "Skipping macOS build on $HOST_OS."
        warn "To build for macOS:"
        warn "  • Run this script on macOS: ./build.sh --mac"
        warn "  • Or push a tag to trigger the GitHub Actions CI workflow,"
        warn "    which uses native macos-13 (Intel) and macos-14 (Apple Silicon) runners."
        RESULT_MAC="skipped (macOS host required)"
        return 0
    fi
    ARCH="$(uname -m)"
    info "Running natively on macOS ($ARCH)..."
    chmod +x build/build_mac.sh
    build/build_mac.sh
    success "macOS .app bundle → dist/mac/RcloneManager.app  ($ARCH)"
    RESULT_MAC="success ($ARCH)"
}

# ---------------------------------------------------------------------------
# Windows build
# ---------------------------------------------------------------------------
build_windows() {
    echo -e "\n${_BOLD}──── Building Windows .exe ────${_RESET}"
    if [[ "$HOST_PLATFORM" == "windows" ]]; then
        info "Running on Windows (WSL/MSYS)..."
        # Prefer the .bat file from a proper Windows shell; here we call it
        # via cmd.exe if available, otherwise use Wine+Python in Docker.
        if command -v cmd.exe &>/dev/null; then
            cmd.exe /c "build\\build_windows.bat"
        else
            warn "cmd.exe not available in this shell environment."
            warn "Falling back to Docker-based Windows build..."
            _build_windows_docker
        fi
    else
        info "Cross-building Windows .exe via Docker (cdrx/pyinstaller-windows)..."
        _require_docker
        _build_windows_docker
    fi
    success "Windows executable → dist/windows/RcloneManager.exe"
    RESULT_WINDOWS="success"
}

_build_windows_docker() {
    # cdrx/pyinstaller-windows is a community image that ships Python + Wine +
    # PyInstaller pre-installed, making it well-suited for cross-compiling
    # Windows executables from Linux or macOS hosts.
    #
    # The image entrypoint runs PyInstaller; we override it to install
    # dependencies first, then run the spec file.
    docker run --rm \
        -v "$ROOT:/src" \
        -w /src \
        cdrx/pyinstaller-windows:python3 \
        bash -c "
            set -euo pipefail
            pip install -r requirements.txt
            pyinstaller build/rclone_manager.spec \
                --distpath dist/windows \
                --workpath build/work_windows \
                --clean
        "
}

# ---------------------------------------------------------------------------
# Run requested builds
# ---------------------------------------------------------------------------
BUILD_FAILED=false

if [[ "$BUILD_LINUX" == "true" ]]; then
    if build_linux; then
        true
    else
        error "Linux build FAILED."
        RESULT_LINUX="FAILED"
        BUILD_FAILED=true
    fi
fi

if [[ "$BUILD_MAC" == "true" ]]; then
    if build_mac; then
        true
    else
        error "macOS build FAILED."
        RESULT_MAC="FAILED"
        BUILD_FAILED=true
    fi
fi

if [[ "$BUILD_WINDOWS" == "true" ]]; then
    if build_windows; then
        true
    else
        error "Windows build FAILED."
        RESULT_WINDOWS="FAILED"
        BUILD_FAILED=true
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo -e "${_BOLD}════════════════════════════════════════════════════════${_RESET}"
echo -e "${_BOLD}                    Build Summary                       ${_RESET}"
echo -e "${_BOLD}════════════════════════════════════════════════════════${_RESET}"

_status() {
    local result="$1"
    case "$result" in
        success*)   echo -e "${_GREEN}✔  $result${_RESET}" ;;
        skipped*)   echo -e "${_YELLOW}–  $result${_RESET}" ;;
        FAILED*)    echo -e "${_RED}✘  $result${_RESET}" ;;
        *)          echo -e "   $result" ;;
    esac
}

printf "  %-22s " "Linux AppImage:";  _status "$RESULT_LINUX"
printf "  %-22s " "macOS .app:";      _status "$RESULT_MAC"
printf "  %-22s " "Windows .exe:";    _status "$RESULT_WINDOWS"

echo -e "${_BOLD}════════════════════════════════════════════════════════${_RESET}"

if [[ "$BUILD_FAILED" == "true" ]]; then
    echo ""
    die "One or more builds failed.  See output above for details."
fi

echo ""
success "All requested builds completed successfully."
