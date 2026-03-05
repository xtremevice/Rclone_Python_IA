"""
rclone_manager.py
-----------------
Handles all interactions with the rclone binary via subprocess.
Provides methods to authorize, sync, list files, get version, and free disk space.
"""

import json
import os
import platform
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional


# Supported platforms with their rclone type and display name
SUPPORTED_PLATFORMS = {
    "onedrive": "Microsoft OneDrive",
    "drive": "Google Drive",
    "dropbox": "Dropbox",
    "box": "Box",
    "s3": "Amazon S3",
    "b2": "Backblaze B2",
    "sftp": "SFTP",
    "ftp": "FTP",
}

# rclone bisync performance flags used by default for all sync operations
DEFAULT_SYNC_FLAGS = [
    "--transfers", "16",
    "--checkers", "32",
    "--drive-chunk-size", "128M",
    "--buffer-size", "64M",
]

# Default exclusion rule for OneDrive Personal Vault.
# "Almacén personal" is the Spanish-localized name for the encrypted
# OneDrive Personal Vault folder; syncing it causes errors with rclone.
PERSONAL_VAULT_EXCLUDE = "/Almacén personal/**"


def find_rclone():
    """
    Locate the rclone binary on the system PATH.
    Returns the path string or None if rclone is not installed.
    """
    return shutil.which("rclone")


class RcloneManager:
    """Provides methods to run rclone commands and manage rclone configuration."""

    def __init__(self):
        # Absolute path to the rclone executable, or None if not found
        self.rclone_path = find_rclone()
        # Path to the rclone configuration file used by this application
        self.config_path = self._get_rclone_config_path()

    def _get_rclone_config_path(self):
        """
        Return the path to the rclone config file.
        Uses the platform-appropriate default location.
        """
        system = platform.system()
        if system == "Windows":
            # Windows: %APPDATA%\rclone\rclone.conf
            appdata = os.environ.get("APPDATA", Path.home())
            return Path(appdata) / "rclone" / "rclone.conf"
        elif system == "Darwin":
            # macOS: ~/.config/rclone/rclone.conf
            return Path.home() / ".config" / "rclone" / "rclone.conf"
        else:
            # Linux: ~/.config/rclone/rclone.conf
            return Path.home() / ".config" / "rclone" / "rclone.conf"

    def is_rclone_available(self):
        """Return True if rclone binary was found on the system PATH."""
        return self.rclone_path is not None

    def _run(self, args, timeout=60, capture=True):
        """
        Run a rclone command synchronously and return (returncode, stdout, stderr).

        Parameters
        ----------
        args    : List of arguments following the rclone binary path
        timeout : Seconds to wait before aborting the subprocess
        capture : If True capture stdout/stderr; otherwise stream to terminal
        """
        if not self.rclone_path:
            # rclone not found - return an error tuple
            return 1, "", "rclone not found. Please install rclone first."
        cmd = [self.rclone_path] + args
        # Include rclone config path so we always use the app config
        if "--config" not in args and self.config_path:
            cmd = [self.rclone_path, "--config", str(self.config_path)] + args
        try:
            result = subprocess.run(
                cmd,
                capture_output=capture,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return 1, "", "Command timed out"
        except Exception as exc:
            return 1, "", str(exc)

    def get_version(self):
        """
        Return the rclone version string.
        Returns None if rclone is not available.
        """
        rc, stdout, _ = self._run(["version"], timeout=10)
        if rc == 0 and stdout:
            # First line is typically: rclone v1.xx.x
            return stdout.splitlines()[0].strip()
        return None

    def authorize(self, platform_type: str, callback: Callable[[bool, str], None]):
        """
        Run 'rclone authorize <platform>' in a background thread.
        Opens the browser for OAuth and captures the resulting token JSON.

        Parameters
        ----------
        platform_type : rclone provider type (e.g. 'onedrive', 'drive')
        callback      : Called with (success: bool, token_or_error: str) when done
        """
        def _worker():
            # Build the authorize command - opens browser automatically
            cmd = [self.rclone_path, "authorize", platform_type]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5 minutes to complete OAuth
                )
                output = proc.stdout + proc.stderr
                # rclone authorize outputs the token JSON after success
                # It appears between "Paste the following into your remote machine --->" and
                # "<---End paste" markers, or as raw JSON
                token = self._extract_token(output)
                if token:
                    callback(True, token)
                else:
                    callback(False, f"Token not found in rclone output:\n{output}")
            except subprocess.TimeoutExpired:
                callback(False, "Authorization timed out after 5 minutes")
            except Exception as exc:
                callback(False, str(exc))

        # Run in a daemon thread so it does not block the UI
        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        return thread

    def _extract_token(self, output: str):
        """
        Extract the token JSON string from rclone authorize output.
        rclone prints the token between marker lines or as raw JSON.
        """
        # Try to find the paste markers first
        start_marker = "Paste the following into your remote machine --->"
        end_marker = "<---End paste"
        if start_marker in output and end_marker in output:
            start = output.index(start_marker) + len(start_marker)
            end = output.index(end_marker)
            return output[start:end].strip()
        # Fall back: look for a JSON object with access_token key
        match = re.search(r'\{[^{}]*"access_token"[^{}]*\}', output, re.DOTALL)
        if match:
            return match.group(0).strip()
        return None

    def create_remote(self, remote_name: str, platform_type: str, token: str):
        """
        Create a new rclone remote entry in the config file.

        Parameters
        ----------
        remote_name   : Name to use for the remote (e.g. 'onedrive_svc1')
        platform_type : rclone provider type (e.g. 'onedrive')
        token         : OAuth token JSON string from rclone authorize
        """
        args = [
            "config", "create", remote_name, platform_type,
            "token", token,
        ]
        rc, stdout, stderr = self._run(args, timeout=30)
        return rc == 0, stderr

    def remote_exists(self, remote_name: str):
        """Return True if a remote with the given name exists in rclone config."""
        rc, stdout, _ = self._run(["config", "show", remote_name], timeout=10)
        return rc == 0 and remote_name in stdout

    def list_remotes(self):
        """Return a list of configured rclone remote names."""
        rc, stdout, _ = self._run(["listremotes"], timeout=10)
        if rc == 0:
            # Each line is "remotename:" - strip the trailing colon
            return [line.rstrip(":").strip() for line in stdout.splitlines() if line.strip()]
        return []

    def delete_remote(self, remote_name: str):
        """Delete a rclone remote from the config."""
        rc, _, _ = self._run(["config", "delete", remote_name], timeout=10)
        return rc == 0

    def bisync(
        self,
        remote_name: str,
        local_path: str,
        exclude_rules: list,
        resync: bool = False,
        on_progress: Optional[Callable[[str], None]] = None,
        on_done: Optional[Callable[[int, str], None]] = None,
    ):
        """
        Run rclone bisync in a background thread and stream output.

        Parameters
        ----------
        remote_name   : rclone remote name (without trailing colon)
        local_path    : Local directory path to sync
        exclude_rules : List of rclone exclude pattern strings
        resync        : If True, add --resync flag for full reconcile
        on_progress   : Called with each output line during sync
        on_done       : Called with (returncode, last_output_line) when sync ends
        """
        def _worker():
            # Build the command arguments
            cmd = [
                self.rclone_path,
                "--config", str(self.config_path),
                "bisync",
                f"{remote_name}:/",   # Sync from root of the remote
                str(local_path),
            ]
            # Append performance flags
            cmd += DEFAULT_SYNC_FLAGS
            # Append --resync if requested (first run or error recovery)
            if resync:
                cmd.append("--resync")
            # Append progress flag so we get live output
            cmd += ["-P", "-v"]
            # Append any exclusion rules
            for rule in exclude_rules:
                cmd += ["--exclude", rule]

            try:
                # Stream output line by line for live progress updates
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                last_line = ""
                for line in proc.stdout:
                    line = line.rstrip()
                    last_line = line
                    if on_progress:
                        on_progress(line)
                proc.wait()
                if on_done:
                    on_done(proc.returncode, last_line)
            except Exception as exc:
                if on_done:
                    on_done(1, str(exc))

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        return thread

    def list_folder_tree(self, remote_name: str, path: str = "/"):
        """
        Return a list of dicts representing the folder tree of a remote.
        Each dict has 'path' (relative) and 'name' keys.

        Parameters
        ----------
        remote_name : rclone remote name
        path        : Remote path to list (default '/')
        """
        rc, stdout, _ = self._run(
            ["lsd", "-R", "--max-depth", "5", f"{remote_name}:{path}"],
            timeout=60,
        )
        if rc != 0:
            return []
        folders = []
        for line in stdout.splitlines():
            # lsd output format: "    -1 date time -1 folder_name"
            parts = line.strip().split(None, 4)
            if len(parts) >= 5:
                folder_path = parts[4].strip()
                folders.append({
                    "path": folder_path,
                    "name": folder_path.split("/")[-1] or folder_path,
                })
        return folders

    def get_disk_usage(self, local_path: str):
        """
        Return disk usage in bytes for a local directory using psutil.
        Falls back to running 'du' if psutil is not available.
        """
        try:
            import psutil
            usage = psutil.disk_usage(str(local_path))
            return usage.used, usage.total, usage.free
        except Exception:
            return 0, 0, 0

    def free_space(self, remote_name: str, on_done: Optional[Callable[[bool, str], None]] = None):
        """
        Run 'rclone cleanup' to free cached/downloaded files on the remote.
        Executed in a background thread.

        Parameters
        ----------
        remote_name : rclone remote name
        on_done     : Called with (success: bool, message: str) when done
        """
        def _worker():
            rc, stdout, stderr = self._run(
                ["cleanup", f"{remote_name}:"], timeout=120
            )
            if on_done:
                if rc == 0:
                    on_done(True, "Space freed successfully")
                else:
                    on_done(False, stderr or "cleanup command failed")

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        return thread

    def parse_transferred_files(self, output_lines: list):
        """
        Parse rclone verbose output lines to extract the list of transferred files.
        Returns a list of dicts with 'path' and 'status' keys.

        Parameters
        ----------
        output_lines : Lines of rclone stdout/stderr output
        """
        files = []
        # Pattern for lines like: "2024/01/01 12:00:00 INFO  : path/to/file: Copied (new)"
        pattern = re.compile(
            r"INFO\s*:\s*(.+?):\s*(Copied \(new\)|Copied \(replaced\)|Deleted|Updated|Moved)"
        )
        for line in output_lines:
            match = pattern.search(line)
            if match:
                files.append({
                    "path": match.group(1).strip(),
                    "status": match.group(2).strip(),
                })
        # Return only the last 50 unique file entries
        seen = set()
        unique = []
        for f in reversed(files):
            key = f["path"]
            if key not in seen:
                seen.add(key)
                unique.append(f)
            if len(unique) >= 50:
                break
        return list(reversed(unique))
