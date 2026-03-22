"""
Native sync manager – uses direct REST APIs for OneDrive and Google Drive
instead of calling rclone.

Supports the same key callback interface as RcloneManager so that the rest
of the application can delegate to this class transparently for services
whose ``sync_provider`` config field is set to ``"nativo"``.

OAuth 2.0 authentication:
  * OneDrive – Authorization Code flow with a local callback server.  The
    rclone OneDrive app (b15665d9-…) is a *confidential* client that requires
    ``client_secret`` in the token exchange (Apache 2.0 licence).
  * Google Drive – Authorization Code + PKCE flow with a local callback server.
    Uses rclone's publicly registered client credentials (Apache 2.0 licence).

Tokens are stored as JSON files in the application config directory:
  ``~/.config/RclonePythonIA/native_token_<remote_name>.json``
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.config.config_manager import ConfigManager, get_config_dir

# ── OneDrive / Microsoft Graph ────────────────────────────────────────────────
# Client ID and secret from the rclone project for OneDrive (Apache 2.0).
# The rclone OneDrive app (b15665d9-…) is registered as a *confidential* client
# in Azure AD, meaning every token-endpoint call must include client_secret.
#
# NOTE: This credential is intentionally public in the rclone source tree.
# It is derived from the obfuscated constant ``rcloneEncryptedClientSecret``
# in rclone/backend/onedrive/onedrive.go by applying rclone's own AES-CTR
# decode (lib/obscure).  Rclone distributes it this way to prevent *accidental*
# leaks while still shipping an open-source, working binary.  It is NOT a
# private secret; do not treat it as one.
_ONEDRIVE_CLIENT_ID = "b15665d9-eda6-4092-8539-0eec376afd59"
# nosec B105 – intentionally public rclone credential; see comment above.
_ONEDRIVE_CLIENT_SECRET = "qtyfaBBYA403=unZUP40~_#"  # noqa: S105
_ONEDRIVE_SCOPES = "Files.ReadWrite offline_access"
_ONEDRIVE_AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
_ONEDRIVE_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
_GRAPH_URL = "https://graph.microsoft.com/v1.0"

# ── Google Drive / Google API ─────────────────────────────────────────────────
# Client credentials from the rclone project for Google Drive (Apache 2.0).
_GDRIVE_CLIENT_ID = "202264815644.apps.googleusercontent.com"
_GDRIVE_CLIENT_SECRET = "X4Z3ca8xfWDb1Voo-F9a7ZxJ"
_GDRIVE_SCOPES = "https://www.googleapis.com/auth/drive"
_GDRIVE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GDRIVE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GDRIVE_API_URL = "https://www.googleapis.com/drive/v3"

# Margin (seconds) before token expiry to proactively refresh
_TOKEN_EXPIRY_MARGIN = 300

# Tolerance for mtime comparison (seconds)
_MTIME_TOLERANCE_SECS = 3.0

# Minimum free local disk space before sync is aborted (10 GiB)
_MIN_FREE_SPACE_BYTES = 10 * 1024 * 1024 * 1024

# Size (bytes) of each chunk used when uploading large files via a session
_UPLOAD_CHUNK_SIZE = 10 * 1024 * 1024  # 10 MiB

# One gibibyte in bytes
_GIB = 1024 * 1024 * 1024

# Maximum URL length to include in log lines (prevents huge next-page tokens)
_MAX_LOG_URL_LENGTH = 120

# Google Workspace native file MIME-type prefix.  Files whose mimeType starts
# with this string (but are not folders) cannot be downloaded via ?alt=media —
# they are Google Docs/Sheets/Slides that live only inside Google's servers.
# These files are skipped during sync (same behavior as rclone --drive-skip-gdocs).
_GDRIVE_WORKSPACE_MIME_PREFIX = "application/vnd.google-apps."


# ── PKCE helpers ──────────────────────────────────────────────────────────────

# OAuth callback port range to search for an available port
_OAUTH_PORT_RANGE = range(53682, 53700)


def _pkce_verifier() -> str:
    """Generate a URL-safe PKCE code verifier (RFC 7636)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def _pkce_challenge(verifier: str) -> str:
    """Derive a PKCE S256 code challenge from the verifier."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


# ── OAuth callback server ─────────────────────────────────────────────────────

class _OAuthCallbackServer:
    """Minimal single-use HTTP server that captures the OAuth redirect code."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.code: Optional[str] = None
        self.error: Optional[str] = None
        self._server: Optional[http.server.HTTPServer] = None

    def start(self) -> bool:
        """Start the server.  Returns False if the port is in use."""
        ref = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                ref.code = (params.get("code", [None]) or [None])[0]
                ref.error = (params.get("error", [None]) or [None])[0]
                body = (
                    b"<html><body><h2>Autenticaci\xc3\xb3n completada</h2>"
                    b"<p>Puedes cerrar esta ventana y volver a la aplicaci\xc3\xb3n.</p>"
                    b"</body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: Any) -> None:
                pass  # silence request logs

        try:
            self._server = http.server.HTTPServer(("127.0.0.1", self.port), _Handler)
            return True
        except OSError:
            return False

    def wait_for_code(self, timeout: float = 120.0) -> Optional[str]:
        """Block until the OAuth callback arrives or *timeout* seconds pass."""
        if self._server is None:
            return None
        self._server.timeout = 1.0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.code is None and self.error is None:
            self._server.handle_request()
        try:
            self._server.server_close()
        except Exception:
            pass
        return self.code


# ── Token storage ─────────────────────────────────────────────────────────────

def _token_path(remote_name: str) -> Path:
    """Return the JSON file path for a service's native OAuth token."""
    safe = remote_name.replace("/", "_").replace("\\", "_")
    return get_config_dir() / f"native_token_{safe}.json"


def load_token(remote_name: str) -> Optional[Dict]:
    """Load the stored OAuth token dict, or None if not present / corrupt."""
    path = _token_path(remote_name)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass
    return None


def save_token(remote_name: str, token: Dict) -> None:
    """Persist an OAuth token dict to disk (mode 0o600)."""
    path = _token_path(remote_name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(token, fh, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def delete_token(remote_name: str) -> None:
    """Remove the stored token for a service (e.g. when service is deleted)."""
    path = _token_path(remote_name)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    data: Optional[bytes] = None,
    timeout: float = 60.0,
    logger: Optional[Callable[[str], None]] = None,
) -> Tuple[int, bytes]:
    """Perform an HTTP request using urllib. Returns (status_code, body_bytes).

    If *logger* is provided it is called once with a formatted line:
    ``"METHOD https://host/path → STATUS"`` so that every API call is
    visible in the error-log panel.
    """
    req = urllib.request.Request(url, data=data, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    # Truncate URL for logging (avoid leaking huge next-page tokens)
    _log_url = url if len(url) <= _MAX_LOG_URL_LENGTH else url[:_MAX_LOG_URL_LENGTH - 3] + "…"
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read()
            if logger:
                logger(f"{method} {_log_url} → {status}")
            return status, body
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read()
        if logger:
            logger(f"{method} {_log_url} → {status} ⚠️")
        return status, body
    except OSError as exc:
        if logger:
            logger(f"{method} {_log_url} → ❌ {exc}")
        raise


def _post_form(
    url: str,
    fields: Dict[str, str],
    timeout: float = 30.0,
    logger: Optional[Callable[[str], None]] = None,
) -> Dict:
    """POST application/x-www-form-urlencoded and parse the JSON response."""
    data = urllib.parse.urlencode(fields).encode()
    status, body = _http_request(
        url,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=data,
        timeout=timeout,
        logger=logger,
    )
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"error": "invalid_json", "raw": body.decode(errors="replace")}


# ── OneDrive provider ─────────────────────────────────────────────────────────

class OneDriveProvider:
    """Direct Microsoft Graph API client for OneDrive personal/business."""

    def __init__(
        self,
        remote_name: str,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._remote_name = remote_name
        self._token: Optional[Dict] = load_token(remote_name)
        # Optional callable(msg) used to record every API call and error.
        # Injected by NativeSyncManager so entries appear in the Errors panel.
        self._logger = logger

    # ── Auth ──────────────────────────────────────────────────────────

    @staticmethod
    def build_auth_url(redirect_uri: str) -> str:
        """Return the Microsoft identity platform authorisation URL.

        The rclone OneDrive app is a confidential client registered with
        ``client_secret``; PKCE is therefore not required.  The token
        endpoint authenticates via client_secret instead.
        """
        params = {
            "client_id": _ONEDRIVE_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": _ONEDRIVE_SCOPES,
            "prompt": "select_account",
        }
        return f"{_ONEDRIVE_AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> bool:
        """Exchange an authorisation code for access + refresh tokens.

        Includes ``client_secret`` because the rclone OneDrive app is
        registered as a confidential client in Azure AD.
        """
        resp = _post_form(
            _ONEDRIVE_TOKEN_URL,
            {
                "client_id": _ONEDRIVE_CLIENT_ID,
                "client_secret": _ONEDRIVE_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            logger=self._logger,
        )
        if "access_token" not in resp:
            if self._logger:
                self._logger(f"❌ exchange_code falló: {resp.get('error', resp)}")
            return False
        resp["obtained_at"] = time.time()
        self._token = resp
        save_token(self._remote_name, resp)
        return True

    def _refresh_token(self) -> bool:
        """Use the refresh_token to obtain a new access_token."""
        if not self._token or "refresh_token" not in self._token:
            if self._logger:
                self._logger("❌ _refresh_token: no hay refresh_token disponible")
            return False
        resp = _post_form(
            _ONEDRIVE_TOKEN_URL,
            {
                "client_id": _ONEDRIVE_CLIENT_ID,
                "client_secret": _ONEDRIVE_CLIENT_SECRET,
                "refresh_token": self._token["refresh_token"],
                "grant_type": "refresh_token",
            },
            logger=self._logger,
        )
        if "access_token" not in resp:
            if self._logger:
                self._logger(f"❌ _refresh_token falló: {resp.get('error', resp)}")
            return False
        resp["obtained_at"] = time.time()
        # Preserve the refresh_token if the new response omits it.
        if "refresh_token" not in resp:
            resp["refresh_token"] = self._token["refresh_token"]
        self._token = resp
        save_token(self._remote_name, resp)
        return True

    def ensure_valid_token(self) -> bool:
        """Refresh the access token if it is about to expire. Returns False on failure."""
        if not self._token:
            return False
        obtained = self._token.get("obtained_at", 0.0)
        expires_in = float(self._token.get("expires_in", 3600))
        if time.time() > obtained + expires_in - _TOKEN_EXPIRY_MARGIN:
            return self._refresh_token()
        return True

    def is_authenticated(self) -> bool:
        """Return True if a valid token is present."""
        return bool(self._token and "access_token" in self._token)

    def _auth_header(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token['access_token']}"}

    # ── File listing ──────────────────────────────────────────────────

    def list_files(self, remote_path: str) -> Dict[str, Dict]:
        """
        Return all files under *remote_path* as ``{rel_path: info_dict}``.

        info_dict keys: ``id``, ``size`` (int), ``mtime`` (float Unix timestamp),
        ``is_dir`` (bool).
        """
        if not self.ensure_valid_token():
            return {}
        # Normalise remote_path: strip leading/trailing slashes
        rp = remote_path.strip("/")
        base_url = (
            f"{_GRAPH_URL}/me/drive/root:/{urllib.parse.quote(rp)}"
            if rp
            else f"{_GRAPH_URL}/me/drive/root"
        )
        return self._list_children_recursive(base_url, "")

    def _list_children_recursive(
        self, folder_url: str, prefix: str
    ) -> Dict[str, Dict]:
        """Recursively list all children of a Drive folder item."""
        results: Dict[str, Dict] = {}
        url = (
            f"{folder_url}/children"
            f"?$select=id,name,file,folder,lastModifiedDateTime,size"
            f"&$top=1000"
        )
        while url:
            status, body = _http_request(
                url, headers=self._auth_header(), timeout=60.0, logger=self._logger
            )
            if status != 200:
                if self._logger:
                    self._logger(
                        f"❌ list_files: respuesta inesperada {status} al listar '{prefix or '/'}'"
                    )
                break
            data = json.loads(body)
            for item in data.get("value", []):
                name = item.get("name", "")
                rel = f"{prefix}/{name}".lstrip("/")
                if "folder" in item:
                    results[rel] = {
                        "id": item["id"],
                        "size": 0,
                        "mtime": _parse_iso8601(item.get("lastModifiedDateTime", "")),
                        "is_dir": True,
                    }
                    # Recurse
                    child_url = f"{_GRAPH_URL}/me/drive/items/{item['id']}"
                    results.update(self._list_children_recursive(child_url, rel))
                else:
                    results[rel] = {
                        "id": item["id"],
                        "size": item.get("size", 0),
                        "mtime": _parse_iso8601(item.get("lastModifiedDateTime", "")),
                        "is_dir": False,
                    }
            url = data.get("@odata.nextLink")
        return results

    # ── Upload / Download ─────────────────────────────────────────────

    def upload_file(self, local_path: str, remote_path: str, rel_path: str) -> bool:
        """Upload a local file to OneDrive. Returns True on success."""
        if not self.ensure_valid_token():
            return False
        rp = remote_path.strip("/")
        drive_path = f"{rp}/{rel_path}".lstrip("/")
        url = f"{_GRAPH_URL}/me/drive/root:/{urllib.parse.quote(drive_path)}:/content"
        try:
            file_size = os.path.getsize(local_path)
        except OSError as exc:
            if self._logger:
                self._logger(f"❌ upload_file: no se pudo leer '{rel_path}': {exc}")
            return False
        if file_size <= 4 * 1024 * 1024:
            # Simple PUT for small files (≤ 4 MiB)
            with open(local_path, "rb") as fh:
                body = fh.read()
            headers = {**self._auth_header(), "Content-Type": "application/octet-stream"}
            status, resp_body = _http_request(
                url, method="PUT", headers=headers, data=body,
                timeout=120.0, logger=self._logger,
            )
            if status not in (200, 201):
                if self._logger:
                    self._logger(
                        f"❌ upload_file: PUT '{rel_path}' devolvió {status}: "
                        f"{resp_body[:200].decode(errors='replace')}"
                    )
                return False
            return True
        else:
            # Upload session for large files
            return self._upload_large_file(url, local_path, file_size)

    def _upload_large_file(self, conflict_url: str, local_path: str, file_size: int) -> bool:
        """Create an upload session and stream the file in chunks."""
        session_url = conflict_url.replace(":/content", ":/createUploadSession")
        status, body = _http_request(
            session_url,
            method="POST",
            headers={**self._auth_header(), "Content-Type": "application/json"},
            data=json.dumps({"item": {"@microsoft.graph.conflictBehavior": "replace"}}).encode(),
            timeout=30.0,
            logger=self._logger,
        )
        if status not in (200, 201):
            if self._logger:
                self._logger(f"❌ _upload_large_file: createUploadSession devolvió {status}")
            return False
        upload_url = json.loads(body).get("uploadUrl")
        if not upload_url:
            if self._logger:
                self._logger("❌ _upload_large_file: respuesta sin uploadUrl")
            return False
        chunk_size = _UPLOAD_CHUNK_SIZE
        with open(local_path, "rb") as fh:
            offset = 0
            while offset < file_size:
                chunk = fh.read(chunk_size)
                end = offset + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{file_size}",
                }
                status, _ = _http_request(
                    upload_url, method="PUT", headers=headers, data=chunk,
                    timeout=120.0, logger=self._logger,
                )
                if status not in (200, 201, 202):
                    if self._logger:
                        self._logger(
                            f"❌ _upload_large_file: chunk {offset}-{end} devolvió {status}"
                        )
                    return False
                offset += len(chunk)
        return True

    def download_file(self, item_id: str, local_path: str) -> bool:
        """Download a Drive item to *local_path*. Returns True on success."""
        if not self.ensure_valid_token():
            return False
        url = f"{_GRAPH_URL}/me/drive/items/{item_id}/content"
        status, body = _http_request(
            url, headers=self._auth_header(), timeout=120.0, logger=self._logger
        )
        if status != 200:
            if self._logger:
                self._logger(f"❌ download_file: GET item {item_id} devolvió {status}")
            return False
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        with open(local_path, "wb") as fh:
            fh.write(body)
        return True

    def create_remote_folder(self, remote_path: str, rel_path: str) -> bool:
        """Create a folder on OneDrive at *remote_path/rel_path*."""
        if not self.ensure_valid_token():
            return False
        rp = remote_path.strip("/")
        parts = rel_path.replace("\\", "/").strip("/").split("/")
        parent = f"{rp}/{'/'.join(parts[:-1])}".strip("/")
        folder_name = parts[-1]
        if parent:
            url = f"{_GRAPH_URL}/me/drive/root:/{urllib.parse.quote(parent)}:/children"
        else:
            url = f"{_GRAPH_URL}/me/drive/root/children"
        body = json.dumps({"name": folder_name, "folder": {}, "@microsoft.graph.conflictBehavior": "replace"}).encode()
        status, resp_body = _http_request(
            url,
            method="POST",
            headers={**self._auth_header(), "Content-Type": "application/json"},
            data=body,
            timeout=30.0,
            logger=self._logger,
        )
        if status not in (200, 201):
            if self._logger:
                self._logger(
                    f"❌ create_remote_folder: POST '{rel_path}' devolvió {status}: "
                    f"{resp_body[:200].decode(errors='replace')}"
                )
            return False
        return True

    # ── Storage info ──────────────────────────────────────────────────

    def get_storage_info(self) -> Optional[str]:
        """Return a human-readable storage usage string, or None on failure."""
        if not self.ensure_valid_token():
            return None
        status, body = _http_request(
            f"{_GRAPH_URL}/me/drive", headers=self._auth_header(),
            timeout=15.0, logger=self._logger,
        )
        if status != 200:
            if self._logger:
                self._logger(f"❌ get_storage_info: GET /me/drive devolvió {status}")
            return None
        data = json.loads(body)
        quota = data.get("quota", {})
        used = quota.get("used", 0)
        total = quota.get("total", 0)
        if total:
            return f"Usado: {_human_size(used)} / {_human_size(total)}"
        return f"Usado: {_human_size(used)}"


# ── Google Drive provider ─────────────────────────────────────────────────────

class GoogleDriveProvider:
    """Direct Google Drive API v3 client."""

    def __init__(
        self,
        remote_name: str,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._remote_name = remote_name
        self._token: Optional[Dict] = load_token(remote_name)
        # Optional callable(msg) used to record every API call and error.
        self._logger = logger

    # ── Auth ──────────────────────────────────────────────────────────

    @staticmethod
    def build_auth_url(redirect_uri: str, verifier: str) -> str:
        """Return the Google OAuth2 authorisation URL (PKCE)."""
        challenge = _pkce_challenge(verifier)
        params = {
            "client_id": _GDRIVE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _GDRIVE_SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{_GDRIVE_AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str, verifier: str) -> bool:
        """Exchange an authorisation code for access+refresh tokens."""
        resp = _post_form(
            _GDRIVE_TOKEN_URL,
            {
                "code": code,
                "client_id": _GDRIVE_CLIENT_ID,
                "client_secret": _GDRIVE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
            logger=self._logger,
        )
        if "access_token" not in resp:
            if self._logger:
                self._logger(f"❌ exchange_code falló: {resp.get('error', resp)}")
            return False
        resp["obtained_at"] = time.time()
        self._token = resp
        save_token(self._remote_name, resp)
        return True

    def _refresh_token(self) -> bool:
        if not self._token or "refresh_token" not in self._token:
            if self._logger:
                self._logger("❌ _refresh_token: no hay refresh_token disponible")
            return False
        resp = _post_form(
            _GDRIVE_TOKEN_URL,
            {
                "client_id": _GDRIVE_CLIENT_ID,
                "client_secret": _GDRIVE_CLIENT_SECRET,
                "refresh_token": self._token["refresh_token"],
                "grant_type": "refresh_token",
            },
            logger=self._logger,
        )
        if "access_token" not in resp:
            if self._logger:
                self._logger(f"❌ _refresh_token falló: {resp.get('error', resp)}")
            return False
        resp["obtained_at"] = time.time()
        if "refresh_token" not in resp:
            resp["refresh_token"] = self._token["refresh_token"]
        self._token = resp
        save_token(self._remote_name, resp)
        return True

    def ensure_valid_token(self) -> bool:
        if not self._token:
            return False
        obtained = self._token.get("obtained_at", 0.0)
        expires_in = float(self._token.get("expires_in", 3600))
        if time.time() > obtained + expires_in - _TOKEN_EXPIRY_MARGIN:
            return self._refresh_token()
        return True

    def is_authenticated(self) -> bool:
        return bool(self._token and "access_token" in self._token)

    def _auth_header(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token['access_token']}"}

    # ── File listing ──────────────────────────────────────────────────

    def _get_folder_id(self, path: str) -> Optional[str]:
        """Resolve a remote path like '/Photos/Vacations' to a Drive folder ID."""
        parts = [p for p in path.strip("/").split("/") if p]
        current_id = "root"
        for part in parts:
            query = (
                f"name='{_gdrive_escape(part)}' and "
                f"'{current_id}' in parents and "
                f"mimeType='application/vnd.google-apps.folder' and trashed=false"
            )
            url = (
                f"{_GDRIVE_API_URL}/files"
                f"?q={urllib.parse.quote(query)}"
                f"&fields=files(id,name)&pageSize=10"
            )
            status, body = _http_request(
                url, headers=self._auth_header(), timeout=30.0, logger=self._logger
            )
            if status != 200:
                if self._logger:
                    self._logger(
                        f"❌ _get_folder_id: no se pudo resolver '{part}' (status {status})"
                    )
                return None
            items = json.loads(body).get("files", [])
            if not items:
                return None
            current_id = items[0]["id"]
        return current_id

    def list_files(self, remote_path: str) -> Dict[str, Dict]:
        """
        Return all files in *remote_path* as ``{rel_path: info_dict}``.

        Builds the path map by starting at the folder resolved from *remote_path*
        and listing all descendants recursively.
        """
        if not self.ensure_valid_token():
            return {}
        root_id = self._get_folder_id(remote_path) or "root"
        return self._list_folder_recursive(root_id, "")

    def _list_folder_recursive(self, folder_id: str, prefix: str) -> Dict[str, Dict]:
        results: Dict[str, Dict] = {}
        page_token: Optional[str] = None
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed=false",
                "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size)",
                "pageSize": "1000",
            }
            if page_token:
                params["pageToken"] = page_token
            url = f"{_GDRIVE_API_URL}/files?{urllib.parse.urlencode(params)}"
            status, body = _http_request(
                url, headers=self._auth_header(), timeout=60.0, logger=self._logger
            )
            if status != 200:
                if self._logger:
                    self._logger(
                        f"❌ list_files: respuesta inesperada {status} al listar '{prefix or '/'}'"
                    )
                break
            data = json.loads(body)
            for item in data.get("files", []):
                name = item.get("name", "")
                rel = f"{prefix}/{name}".lstrip("/")
                mime = item.get("mimeType", "")
                is_dir = mime == "application/vnd.google-apps.folder"
                # Skip Google Workspace native files (Docs, Sheets, Slides, etc.).
                # They cannot be downloaded via ?alt=media and always return 403.
                # This mirrors rclone's --drive-skip-gdocs behavior.
                if (
                    not is_dir
                    and mime.startswith(_GDRIVE_WORKSPACE_MIME_PREFIX)
                ):
                    if self._logger:
                        self._logger(
                            f"⚠️ Omitiendo archivo nativo de Google Workspace "
                            f"(no descargable, tipo: {mime}): {rel}"
                        )
                    continue
                results[rel] = {
                    "id": item["id"],
                    "size": int(item.get("size", 0)) if not is_dir else 0,
                    "mtime": _parse_iso8601(item.get("modifiedTime", "")),
                    "is_dir": is_dir,
                }
                if is_dir:
                    results.update(self._list_folder_recursive(item["id"], rel))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return results

    # ── Upload / Download ─────────────────────────────────────────────

    def _get_or_create_folder(self, remote_path: str, rel_folder: str) -> Optional[str]:
        """Return the Drive ID of *remote_path/rel_folder*, creating it if needed."""
        root_id = self._get_folder_id(remote_path) or "root"
        parts = [p for p in rel_folder.replace("\\", "/").strip("/").split("/") if p]
        current_id = root_id
        for part in parts:
            query = (
                f"name='{_gdrive_escape(part)}' and "
                f"'{current_id}' in parents and "
                f"mimeType='application/vnd.google-apps.folder' and trashed=false"
            )
            url = (
                f"{_GDRIVE_API_URL}/files"
                f"?q={urllib.parse.quote(query)}"
                f"&fields=files(id)&pageSize=1"
            )
            status, body = _http_request(
                url, headers=self._auth_header(), timeout=20.0, logger=self._logger
            )
            if status == 200 and json.loads(body).get("files"):
                current_id = json.loads(body)["files"][0]["id"]
            else:
                # Create the folder
                meta = json.dumps({
                    "name": part,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [current_id],
                }).encode()
                status, body = _http_request(
                    f"{_GDRIVE_API_URL}/files",
                    method="POST",
                    headers={**self._auth_header(), "Content-Type": "application/json"},
                    data=meta,
                    timeout=20.0,
                    logger=self._logger,
                )
                if status not in (200, 201):
                    if self._logger:
                        self._logger(
                            f"❌ _get_or_create_folder: POST '{part}' devolvió {status}"
                        )
                    return None
                current_id = json.loads(body).get("id")
                if not current_id:
                    return None
        return current_id

    def upload_file(self, local_path: str, remote_path: str, rel_path: str) -> bool:
        """Upload a local file to Google Drive. Returns True on success."""
        if not self.ensure_valid_token():
            return False
        rel_parts = rel_path.replace("\\", "/").split("/")
        file_name = rel_parts[-1]
        folder_rel = "/".join(rel_parts[:-1])
        parent_id = self._get_or_create_folder(remote_path, folder_rel)
        if not parent_id:
            parent_id = self._get_folder_id(remote_path) or "root"
        # Check if file already exists (to update vs. create)
        existing_id = self._find_file_id(parent_id, file_name)
        try:
            with open(local_path, "rb") as fh:
                file_body = fh.read()
        except OSError as exc:
            if self._logger:
                self._logger(f"❌ upload_file: no se pudo leer '{rel_path}': {exc}")
            return False
        if existing_id:
            # Update existing file content
            url = (
                f"https://www.googleapis.com/upload/drive/v3/files/{existing_id}"
                f"?uploadType=media"
            )
            status, resp_body = _http_request(
                url,
                method="PATCH",
                headers={**self._auth_header(), "Content-Type": "application/octet-stream"},
                data=file_body,
                timeout=120.0,
                logger=self._logger,
            )
            if status != 200:
                if self._logger:
                    self._logger(
                        f"❌ upload_file: PATCH '{rel_path}' devolvió {status}: "
                        f"{resp_body[:200].decode(errors='replace')}"
                    )
                return False
            return True
        else:
            # Multipart upload (metadata + content)
            meta = json.dumps({"name": file_name, "parents": [parent_id]}).encode()
            boundary = b"----NativeSyncBoundary"
            parts = (
                b"--" + boundary + b"\r\n"
                b"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                + meta + b"\r\n"
                b"--" + boundary + b"\r\n"
                b"Content-Type: application/octet-stream\r\n\r\n"
                + file_body + b"\r\n"
                b"--" + boundary + b"--"
            )
            url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
            status, resp_body = _http_request(
                url,
                method="POST",
                headers={
                    **self._auth_header(),
                    "Content-Type": f"multipart/related; boundary={boundary.decode()}",
                },
                data=parts,
                timeout=120.0,
                logger=self._logger,
            )
            if status not in (200, 201):
                if self._logger:
                    self._logger(
                        f"❌ upload_file: POST (multipart) '{rel_path}' devolvió {status}: "
                        f"{resp_body[:200].decode(errors='replace')}"
                    )
                return False
            return True

    def _find_file_id(self, parent_id: str, name: str) -> Optional[str]:
        """Return the Drive ID of a file by name in *parent_id*, or None."""
        query = (
            f"name='{_gdrive_escape(name)}' and "
            f"'{parent_id}' in parents and trashed=false and "
            f"mimeType!='application/vnd.google-apps.folder'"
        )
        url = (
            f"{_GDRIVE_API_URL}/files"
            f"?q={urllib.parse.quote(query)}"
            f"&fields=files(id)&pageSize=1"
        )
        status, body = _http_request(
            url, headers=self._auth_header(), timeout=20.0, logger=self._logger
        )
        if status == 200:
            files = json.loads(body).get("files", [])
            if files:
                return files[0]["id"]
        return None

    def download_file(self, item_id: str, local_path: str) -> bool:
        """Download a Drive file to *local_path*. Returns True on success."""
        if not self.ensure_valid_token():
            return False
        url = f"{_GDRIVE_API_URL}/files/{item_id}?alt=media"
        status, body = _http_request(
            url, headers=self._auth_header(), timeout=120.0, logger=self._logger
        )
        if status != 200:
            if self._logger:
                self._logger(f"❌ download_file: GET item {item_id} devolvió {status}")
            return False
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        with open(local_path, "wb") as fh:
            fh.write(body)
        return True

    def create_remote_folder(self, remote_path: str, rel_path: str) -> bool:
        """Create a folder on Google Drive. Returns True on success."""
        folder_id = self._get_or_create_folder(remote_path, rel_path)
        return folder_id is not None

    # ── Storage info ──────────────────────────────────────────────────

    def get_storage_info(self) -> Optional[str]:
        if not self.ensure_valid_token():
            return None
        url = f"{_GDRIVE_API_URL}/about?fields=storageQuota"
        status, body = _http_request(
            url, headers=self._auth_header(), timeout=15.0, logger=self._logger
        )
        if status != 200:
            if self._logger:
                self._logger(f"❌ get_storage_info: GET /about devolvió {status}")
            return None
        quota = json.loads(body).get("storageQuota", {})
        used = int(quota.get("usage", 0))
        limit = int(quota.get("limit", 0))
        if limit:
            return f"Usado: {_human_size(used)} / {_human_size(limit)}"
        return f"Usado: {_human_size(used)}"


# ── NativeSyncManager ─────────────────────────────────────────────────────────

class NativeSyncManager:
    """
    Manages native cloud synchronisation for services using the "nativo"
    sync provider (OneDrive and Google Drive direct APIs).

    Exposes the same callback interface as ``RcloneManager`` so that the
    application can route calls from the UI layer to whichever backend is
    configured for each service.
    """

    def __init__(self, config_manager: ConfigManager) -> None:
        self._config = config_manager
        # service_name → background thread
        self._sync_threads: Dict[str, threading.Thread] = {}
        # service_name → stop event
        self._stop_events: Dict[str, threading.Event] = {}
        # service_name → current status string
        self._status: Dict[str, str] = {}

        # Callbacks – same names as RcloneManager
        self.on_status_change: Optional[Callable[[str, str], None]] = None
        self.on_file_synced: Optional[Callable[[str, str, bool], None]] = None
        self.on_error: Optional[Callable[[str, str], None]] = None
        # Callback fired for every API call (and internal errors).
        # Signature: on_api_call(service_name: str, message: str)
        # Forwarded from RcloneManager so entries reach the Errors panel.
        self.on_api_call: Optional[Callable[[str, str], None]] = None

    # ── Provider factory ──────────────────────────────────────────────

    def _make_logger(self, service_name: str) -> Callable[[str], None]:
        """Return a single-argument callable that routes every log message to
        ``on_api_call``, tagged with *service_name*.

        Only ``on_api_call`` is fired here — ``on_error`` is intentionally NOT
        called from this path.  High-level error messages are already emitted
        via ``_emit_error`` (called from ``_upload``, ``_download``, and
        ``_do_sync``), which calls ``on_error`` directly.  Routing ❌ messages
        from the provider logger to ``on_error`` *as well* would cause every
        error to appear twice in the Errores panel.
        """
        def _log(msg: str) -> None:
            if self.on_api_call:
                self.on_api_call(service_name, msg)
        return _log

    def _log_progress(self, service_name: str, msg: str) -> None:
        """Emit a progress message to both output channels so the user can
        follow sync stages and individual file operations in real time.

        * **Errores panel** (``on_api_call``): every message appears here.
          The ``🔗 API |`` prefix is added by the ``_on_native_api_call``
          handler in ``main_window.py``, not here.
        * **Left console** (``on_file_synced``): message appears as an ⏳ entry
          in the service's file-history Listbox (the panel left of the tree).

        The method is intentionally lightweight — it does not call
        ``on_error`` so stage messages never duplicate in the Errores panel.
        Both callbacks are guarded against ``None`` (no-op when not set).
        """
        if self.on_api_call:
            self.on_api_call(service_name, msg)
        if self.on_file_synced:
            self.on_file_synced(service_name, msg, False)

    def _get_provider(self, svc: Dict):
        """Return an OneDriveProvider or GoogleDriveProvider for *svc*."""
        remote_name = svc.get("remote_name", svc.get("name", ""))
        platform = svc.get("platform", "")
        service_name = svc.get("name", remote_name)
        logger = self._make_logger(service_name)
        if platform == "onedrive":
            return OneDriveProvider(remote_name, logger=logger)
        if platform == "drive":
            return GoogleDriveProvider(remote_name, logger=logger)
        return None

    # ── OAuth authentication ──────────────────────────────────────────

    def authenticate(
        self,
        service_name: str,
        platform: str,
        remote_name: str,
        on_done: Optional[Callable[[bool, str], None]] = None,
        timeout: float = 120.0,
    ) -> None:
        """
        Start the native OAuth flow for *service_name* in a background thread.

        Calls ``on_done(success: bool, error_msg: str)`` when complete.
        """
        def _run() -> None:
            ok, msg = self._do_authenticate(platform, remote_name, timeout)
            if on_done:
                on_done(ok, msg)

        threading.Thread(target=_run, daemon=True, name=f"native-auth-{service_name}").start()

    def _do_authenticate(
        self, platform: str, remote_name: str, timeout: float
    ) -> Tuple[bool, str]:
        """Dispatch the native OAuth flow based on *platform*.

        Both OneDrive and Google Drive use the Authorization Code flow with a
        local HTTP callback server.  OneDrive authenticates via ``client_secret``
        (confidential client); Google Drive uses PKCE (installed-app client).

        Returns ``(success, error_msg)``.
        """
        if platform == "onedrive":
            return self._do_auth_code(remote_name, timeout, provider_type="onedrive")
        elif platform == "drive":
            return self._do_auth_code(remote_name, timeout, provider_type="drive")
        else:
            return False, f"Plataforma no compatible para autenticación nativa: {platform}"

    def _do_auth_code(
        self, remote_name: str, timeout: float, provider_type: str
    ) -> Tuple[bool, str]:
        """Perform the OAuth Authorization Code flow with a local callback server.

        OneDrive:   includes ``client_secret`` in the token exchange (confidential
                    client); no PKCE.
        Google Drive: uses PKCE (installed-app client); no client_secret.

        Returns ``(success, error_msg)``.
        """
        # Find a free port for the local callback server.
        port: Optional[int] = None
        server: Optional[_OAuthCallbackServer] = None
        for p in _OAUTH_PORT_RANGE:
            srv = _OAuthCallbackServer(p)
            if srv.start():
                port = p
                server = srv
                break
        if server is None or port is None:
            return False, "No se pudo encontrar un puerto libre para el servidor OAuth."

        # RFC 8252 §7.3: plain HTTP is acceptable for loopback addresses.
        # Rclone registers "http://localhost:<port>/" (root path, no extra segments).
        redirect_uri = f"http://localhost:{port}/"

        if provider_type == "onedrive":
            auth_url = OneDriveProvider.build_auth_url(redirect_uri)
        else:
            verifier = _pkce_verifier()
            auth_url = GoogleDriveProvider.build_auth_url(redirect_uri, verifier)

        webbrowser.open(auth_url)

        code = server.wait_for_code(timeout)
        if not code:
            return False, "Tiempo de espera agotado o autenticación cancelada."

        # Exchange the code for tokens.  Pass a logger so that error details
        # from the provider are captured and returned to the caller.
        auth_errors: List[str] = []
        if provider_type == "onedrive":
            provider: Any = OneDriveProvider(remote_name, logger=auth_errors.append)
            try:
                ok = provider.exchange_code(code, redirect_uri)
            except OSError as exc:
                return False, f"Error de red al intercambiar el código OAuth: {exc}"
        else:
            provider = GoogleDriveProvider(remote_name, logger=auth_errors.append)
            try:
                ok = provider.exchange_code(code, redirect_uri, verifier)
            except OSError as exc:
                return False, f"Error de red al intercambiar el código OAuth: {exc}"

        if not ok:
            detail = "; ".join(auth_errors) if auth_errors else ""
            msg = "No se pudo obtener el token de acceso del servidor."
            if detail:
                msg += f" ({detail})"
            return False, msg
        return True, ""

    def has_token(self, remote_name: str) -> bool:
        """Return True if a native OAuth token exists for *remote_name*."""
        return load_token(remote_name) is not None

    # ── Service lifecycle ─────────────────────────────────────────────

    def start_service(self, service_name: str) -> None:
        """Start the native sync loop for *service_name* if not already running."""
        if service_name in self._sync_threads and self._sync_threads[service_name].is_alive():
            return
        stop_event = threading.Event()
        self._stop_events[service_name] = stop_event
        thread = threading.Thread(
            target=self._sync_loop,
            args=(service_name, stop_event),
            daemon=True,
            name=f"native-sync-{service_name}",
        )
        self._sync_threads[service_name] = thread
        thread.start()

    def stop_service(self, service_name: str) -> None:
        """Signal the native sync loop to stop."""
        event = self._stop_events.get(service_name)
        if event:
            event.set()

    def is_running(self, service_name: str) -> bool:
        thread = self._sync_threads.get(service_name)
        return thread is not None and thread.is_alive()

    def get_status(self, service_name: str) -> str:
        return self._status.get(service_name, "Detenido")

    def start_all(self) -> None:
        """Start native sync for all services with sync_provider='nativo'."""
        for svc in self._config.get_services():
            if (
                svc.get("sync_provider", "rclone") == "nativo"
                and svc.get("sync_enabled", True)
            ):
                self.start_service(svc["name"])

    def stop_all(self) -> None:
        for name in list(self._sync_threads.keys()):
            self.stop_service(name)

    # ── Sync loop ─────────────────────────────────────────────────────

    def _sync_loop(self, service_name: str, stop_event: threading.Event) -> None:
        self._set_status(service_name, "Iniciando…")
        while not stop_event.is_set():
            svc = self._config.get_service(service_name)
            if svc is None or not svc.get("sync_enabled", True):
                break

            is_first = not svc.get("first_sync_done", False)
            self._set_status(
                service_name, "Sincronizando…" if is_first else "Actualizando cambios…"
            )

            success = self._do_sync(svc)
            if success:
                if is_first:
                    self._config.update_service(service_name, {"first_sync_done": True})
                self._set_status(service_name, "Actualizado")
            else:
                self._set_status(service_name, "Error en sincronización")
                self._emit_error(service_name, "Fallo en el ciclo de sincronización nativa")

            interval = svc.get("sync_interval", 900)
            stop_event.wait(timeout=interval)

        self._set_status(service_name, "Detenido")

    def _do_sync(self, svc: Dict) -> bool:
        """
        Perform one full bidirectional sync cycle for *svc* using the native API.

        Algorithm (mirrors rclone bisync behaviour):
          1. Check local free space.
          2. List remote files with mtimes.
          3. Scan local files with mtimes.
          4. Upload local-only files and local-modified files.
          5. Download remote-only files and remote-modified files.
          6. Create missing local directories.
        """
        name = svc.get("name", "?")
        local_path = svc.get("local_path", "")
        remote_path = svc.get("remote_path", "/")

        # Free-space guard
        try:
            free = shutil.disk_usage(local_path).free
        except OSError:
            free = 0
        if free < _MIN_FREE_SPACE_BYTES:
            self._emit_error(
                name,
                f"⛔ Sincronización cancelada: espacio libre insuficiente "
                f"({free / _GIB:.1f} GiB disponible, mínimo 10 GiB).",
            )
            return False

        provider = self._get_provider(svc)
        if provider is None:
            self._emit_error(name, "Proveedor nativo no disponible para esta plataforma.")
            return False

        if not provider.is_authenticated():
            self._emit_error(name, "❌ Sin token de autenticación. Reconecta el servicio.")
            return False

        # ── Stage 1: List remote files ────────────────────────────────────
        self._log_progress(name, "📋 Obteniendo lista de archivos remotos…")
        try:
            remote_files = provider.list_files(remote_path)
        except Exception as exc:
            self._emit_error(name, f"Error al listar archivos remotos ({type(exc).__name__}): {exc}")
            return False
        self._log_progress(name, f"📋 Lista remota: {len(remote_files)} elemento(s)")

        # ── Stage 2: Scan local files ─────────────────────────────────────
        self._log_progress(name, "🔍 Escaneando archivos locales…")
        local_files = _scan_local_files(local_path)
        self._log_progress(name, f"🔍 Archivos locales: {len(local_files)} elemento(s)")

        all_paths = set(remote_files.keys()) | set(local_files.keys())

        # ── Stage 3: Compare and sync ─────────────────────────────────────
        if all_paths:
            self._log_progress(name, f"⚙️ Comparando {len(all_paths)} ruta(s)…")
        errors = 0

        for rel in sorted(all_paths):
            remote_info = remote_files.get(rel)
            local_info = local_files.get(rel)

            if remote_info and remote_info.get("is_dir"):
                # Ensure local directory exists
                local_dir = os.path.join(local_path, rel.replace("/", os.sep))
                os.makedirs(local_dir, exist_ok=True)
                continue

            if local_info and local_info.get("is_dir"):
                # Ensure remote directory exists
                try:
                    provider.create_remote_folder(remote_path, rel)
                except Exception:
                    pass
                continue

            abs_local = os.path.join(local_path, rel.replace("/", os.sep))

            if local_info and not remote_info:
                # Local only → upload
                ok = self._upload(provider, abs_local, remote_path, rel, name)
                if not ok:
                    errors += 1
            elif remote_info and not local_info:
                # Remote only → download
                ok = self._download(provider, remote_info["id"], abs_local, rel, name)
                if not ok:
                    errors += 1
            elif local_info and remote_info:
                # Both exist → compare mtimes
                local_mtime = local_info.get("mtime", 0.0)
                remote_mtime = remote_info.get("mtime", 0.0)
                diff = local_mtime - remote_mtime
                if abs(diff) > _MTIME_TOLERANCE_SECS:
                    if diff > 0:
                        ok = self._upload(provider, abs_local, remote_path, rel, name)
                    else:
                        ok = self._download(
                            provider, remote_info["id"], abs_local, rel, name
                        )
                    if not ok:
                        errors += 1

        return errors == 0

    def _upload(
        self,
        provider: Any,
        abs_local: str,
        remote_path: str,
        rel: str,
        service_name: str,
    ) -> bool:
        self._log_progress(service_name, f"⬆️ Subiendo: '{rel}'")
        try:
            ok = provider.upload_file(abs_local, remote_path, rel)
        except Exception as exc:
            self._emit_error(service_name, f"Error al subir '{rel}' ({type(exc).__name__}): {exc}")
            return False
        if ok and self.on_file_synced:
            self.on_file_synced(service_name, rel, True)
        return ok

    def _download(
        self,
        provider: Any,
        item_id: str,
        abs_local: str,
        rel: str,
        service_name: str,
    ) -> bool:
        self._log_progress(service_name, f"⬇️ Descargando: '{rel}'")
        try:
            ok = provider.download_file(item_id, abs_local)
        except Exception as exc:
            self._emit_error(service_name, f"Error al descargar '{rel}' ({type(exc).__name__}): {exc}")
            return False
        if ok and self.on_file_synced:
            self.on_file_synced(service_name, rel, True)
        return ok

    # ── Remote metadata for UI tree ───────────────────────────────────

    def list_remote_metadata(
        self, service_name: str
    ) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Return ``(metadata_dict, None)`` on success or ``(None, error_msg)``
        on failure.  The metadata dict maps relative path → info dict with
        ``mtime``, ``size``, ``is_dir`` keys (compatible with the format used
        by RcloneManager.list_remote_metadata).
        """
        svc = self._config.get_service(service_name)
        if not svc:
            return None, "Servicio no encontrado."
        provider = self._get_provider(svc)
        if provider is None:
            return None, "Plataforma no compatible con el proveedor nativo."
        if not provider.is_authenticated():
            return None, "Sin token de autenticación."
        try:
            files = provider.list_files(svc.get("remote_path", "/"))
            # Convert to the format expected by the tree view
            # (same structure as rclone lsjson output parsed by RcloneManager)
            meta: Dict[str, Any] = {}
            for rel, info in files.items():
                meta[rel] = {
                    "mtime": info.get("mtime"),
                    "size": info.get("size", 0),
                    "is_dir": info.get("is_dir", False),
                }
            return meta, None
        except Exception as exc:
            return None, str(exc)

    def get_storage_info(self, service_name: str) -> Optional[str]:
        """Return a human-readable storage usage string for the UI."""
        svc = self._config.get_service(service_name)
        if not svc:
            return None
        provider = self._get_provider(svc)
        if provider is None or not provider.is_authenticated():
            return None
        try:
            return provider.get_storage_info()
        except Exception:
            return None

    # ── Internal helpers ──────────────────────────────────────────────

    def _set_status(self, service_name: str, status: str) -> None:
        self._status[service_name] = status
        if self.on_status_change:
            self.on_status_change(service_name, status)

    def _emit_error(self, service_name: str, message: str) -> None:
        if self.on_error:
            self.on_error(service_name, message)


# ── Utility functions ─────────────────────────────────────────────────────────

def _parse_iso8601(s: str) -> float:
    """Parse an ISO 8601 datetime string and return a Unix timestamp.

    Handles the common formats returned by OneDrive (``2024-01-15T10:30:00Z``,
    ``2024-01-15T10:30:00.000Z``) and Google Drive (``2024-01-15T10:30:00.000Z``).
    """
    if not s:
        return 0.0
    import datetime
    # Normalise: replace trailing 'Z' with '+00:00' so fromisoformat() works
    normalised = s.rstrip("Z")
    if "T" in normalised:
        # Truncate fractional seconds beyond microseconds (Python supports up to 6 digits)
        date_part, _, time_part = normalised.partition("T")
        # Split off any timezone offset that may remain
        for sep in ("+", "-"):
            if sep in time_part:
                time_part, _, offset = time_part.partition(sep)
                break
        # Trim fractional seconds to 6 digits max
        if "." in time_part:
            base, _, frac = time_part.partition(".")
            time_part = f"{base}.{frac[:6]}"
        normalised = f"{date_part}T{time_part}+00:00"
    else:
        normalised = f"{normalised}+00:00"
    try:
        dt = datetime.datetime.fromisoformat(normalised)
        return dt.timestamp()
    except (ValueError, OverflowError):
        return 0.0


def _scan_local_files(local_path: str) -> Dict[str, Dict]:
    """
    Return a dict of all files and dirs under *local_path*.

    Keys are POSIX-style relative paths; values contain ``mtime`` (float),
    ``size`` (int), and ``is_dir`` (bool).
    """
    result: Dict[str, Dict] = {}
    base = Path(local_path)
    if not base.is_dir():
        return result
    for entry in base.rglob("*"):
        try:
            rel = entry.relative_to(base).as_posix()
            st = entry.stat()
            result[rel] = {
                "mtime": st.st_mtime,
                "size": st.st_size if entry.is_file() else 0,
                "is_dir": entry.is_dir(),
            }
        except (OSError, ValueError):
            continue
    return result


def _gdrive_escape(name: str) -> str:
    """Escape a name for use inside a Google Drive q-string."""
    return name.replace("\\", "\\\\").replace("'", "\\'")


def _human_size(num_bytes: int) -> str:
    """Return a human-readable file size string."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes //= 1024
    return f"{num_bytes:.1f} PiB"
