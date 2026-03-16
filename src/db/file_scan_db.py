"""Encrypted SQLite database for per-service file scan metadata.

All data values (file paths, sizes, modification times, scan timestamps, sync
status) are encrypted at rest using :class:`cryptography.fernet.Fernet`
(AES-128-CBC + HMAC-SHA256).  A stable SHA-256 hash of each file's relative
path is stored unencrypted as the primary key so that rows can be looked up and
upserted without decrypting all records first.

The Fernet key is generated once and saved in::

    ~/.config/RclonePythonIA/db.key   (mode 0o600)

The database file itself is stored alongside it::

    ~/.config/RclonePythonIA/file_scan_cache.db   (mode 0o600)

Each synchronisation service gets its own table whose name is derived from the
service's slug (``svc_<slug>``).  This keeps table-level SQLite locks
independent so Thread 1 / Thread 2 / Thread 3 for service A never block the
equivalent threads for service B.

Schema (one table per service)::

    path_hash        TEXT PRIMARY KEY   -- SHA-256 hex of rel_path (unencrypted)
    rel_path_enc     BLOB NOT NULL      -- encrypted POSIX relative path
    local_size_enc   BLOB               -- encrypted local file size (integer)
    remote_size_enc  BLOB               -- encrypted remote file size (integer)
    local_mtime_enc  BLOB               -- encrypted local mtime (float seconds UTC)
    remote_mtime_enc BLOB               -- encrypted remote mtime (float seconds UTC)
    local_scan_ts    REAL               -- UTC epoch of last local scan (unencrypted)
    remote_scan_ts   REAL               -- UTC epoch of last remote scan (unencrypted)
    status_enc       BLOB               -- encrypted status string

``local_scan_ts`` and ``remote_scan_ts`` are kept unencrypted because they are
used in WHERE clauses to prune stale records efficiently (range comparisons on
encrypted values would require decrypting every row).
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional

from cryptography.fernet import Fernet

from src.config.config_manager import get_config_dir

# Mtime tolerance used when computing "synced" vs "diff" status (seconds).
# Mirrors _MTIME_TOLERANCE_SECS from rclone_manager to keep behaviour consistent.
_MTIME_TOLERANCE_SECS: float = 2.0

_DB_FILENAME = "file_scan_cache.db"
_KEY_FILENAME = "db.key"


def _table_slug(service_name: str) -> str:
    """Return a filesystem/SQL-safe slug for *service_name*.

    Non-alphanumeric characters → underscores.  Falls back to ``"svc"`` for
    an all-symbol input.  The result is always lower-cased and prefixed with
    ``svc_`` to ensure the name is a valid SQL identifier even when the service
    name starts with a digit.
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", service_name).strip("_").lower()
    return "svc_" + (slug or "svc")


def _path_hash(rel_path: str) -> str:
    """Return a hex SHA-256 digest of *rel_path* for use as the primary key."""
    return hashlib.sha256(rel_path.encode()).hexdigest()


class FileScanDB:
    """Thread-safe, Fernet-encrypted SQLite database for file scan metadata.

    One instance should be shared across the whole application (typically
    owned by ``MainWindow``).  All public methods are safe to call from
    any thread — a single :class:`threading.Lock` serialises all SQLite
    operations so that WAL-mode concurrent writes from multiple service
    threads do not corrupt each other.

    Parameters
    ----------
    db_path:
        Optional override for the database file path.  Defaults to
        ``<config_dir>/file_scan_cache.db``.
    key_path:
        Optional override for the Fernet key file path.  Defaults to
        ``<config_dir>/db.key``.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        key_path: Optional[Path] = None,
    ) -> None:
        config_dir = get_config_dir()
        self._db_path: Path = db_path or (config_dir / _DB_FILENAME)
        self._key_path: Path = key_path or (config_dir / _KEY_FILENAME)

        self._fernet: Fernet = self._load_or_create_key()

        # check_same_thread=False: we protect access with self._lock instead
        self._conn: sqlite3.Connection = sqlite3.connect(
            str(self._db_path), check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row

        # WAL mode allows one writer + multiple readers concurrently, which is
        # the typical usage pattern (Thread 1 and Thread 2 write from separate
        # threads, Thread 3 reads).
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL

        # Restrict file permissions so other OS users cannot read the DB.
        for path in (self._db_path, self._key_path):
            try:
                os.chmod(str(path), 0o600)
            except OSError:
                pass

        # Single lock to serialise all SQLite access from multiple threads.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Path discovery
    # ------------------------------------------------------------------

    @property
    def db_path(self) -> Path:
        """Absolute path of the SQLite database file.

        Useful for locating the database during development and testing::

            from src.db.file_scan_db import FileScanDB
            db = FileScanDB()
            print(db.db_path)   # ~/.config/RclonePythonIA/file_scan_cache.db
        """
        return self._db_path

    @property
    def key_path(self) -> Path:
        """Absolute path of the Fernet encryption key file (``db.key``)."""
        return self._key_path

    # ------------------------------------------------------------------
    # Key management
    # ------------------------------------------------------------------

    def _load_or_create_key(self) -> Fernet:
        """Load the Fernet key from disk, creating it if it does not exist."""
        if self._key_path.exists():
            raw = self._key_path.read_bytes().strip()
        else:
            raw = Fernet.generate_key()
            self._key_path.write_bytes(raw)
            try:
                os.chmod(str(self._key_path), 0o600)
            except OSError:
                pass
        return Fernet(raw)

    # ------------------------------------------------------------------
    # Encryption helpers
    # ------------------------------------------------------------------

    def _enc(self, value: Optional[str]) -> Optional[bytes]:
        """Encrypt *value* and return the Fernet token, or ``None``."""
        if value is None:
            return None
        return self._fernet.encrypt(value.encode())

    def _dec(self, blob: Optional[bytes]) -> Optional[str]:
        """Decrypt a Fernet token and return the plaintext, or ``None``."""
        if blob is None:
            return None
        try:
            return self._fernet.decrypt(bytes(blob)).decode()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Table lifecycle
    # ------------------------------------------------------------------

    def ensure_table(self, service_name: str) -> None:
        """Create the scan-metadata table for *service_name* if it does not exist."""
        tbl = _table_slug(service_name)
        with self._lock:
            self._conn.execute(f"""
                CREATE TABLE IF NOT EXISTS "{tbl}" (
                    path_hash        TEXT PRIMARY KEY,
                    rel_path_enc     BLOB NOT NULL,
                    local_size_enc   BLOB,
                    remote_size_enc  BLOB,
                    local_mtime_enc  BLOB,
                    remote_mtime_enc BLOB,
                    local_scan_ts    REAL,
                    remote_scan_ts   REAL,
                    status_enc       BLOB
                )
            """)
            self._conn.commit()

    def drop_table(self, service_name: str) -> None:
        """Drop the scan-metadata table for a deleted service."""
        tbl = _table_slug(service_name)
        with self._lock:
            self._conn.execute(f'DROP TABLE IF EXISTS "{tbl}"')
            self._conn.commit()

    def rename_table(self, old_name: str, new_name: str) -> None:
        """Rename the table when a service is renamed.

        If the slugs are identical (e.g. both normalise to the same string)
        this is a no-op.  The target table is created if it does not yet exist
        so that subsequent :meth:`ensure_table` calls are idempotent.
        """
        old_tbl = _table_slug(old_name)
        new_tbl = _table_slug(new_name)
        if old_tbl == new_tbl:
            return
        with self._lock:
            row = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (old_tbl,),
            ).fetchone()
            if row is not None:
                self._conn.execute(f'ALTER TABLE "{old_tbl}" RENAME TO "{new_tbl}"')
                self._conn.commit()
        # Guarantee the destination table exists even when the source did not.
        self.ensure_table(new_name)

    # ------------------------------------------------------------------
    # Batch write operations (called by Threads 1 and 2)
    # ------------------------------------------------------------------

    def _prune_stale_and_orphaned(
        self, tbl: str, scan_ts: float, side: str
    ) -> None:
        """Clear stale single-side fields and delete fully-orphaned rows.

        Called at the end of :meth:`upsert_local_batch` and
        :meth:`upsert_remote_batch` (while the caller already holds
        :attr:`_lock`) to remove data for files that were not present in
        the latest scan.

        Parameters
        ----------
        tbl:
            Already-quoted table name (from :func:`_table_slug`).
        scan_ts:
            UTC epoch timestamp of the current scan batch.  Any row whose
            ``<side>_scan_ts`` column is older than this value (and > 0)
            was not visited during the current scan, which means the file
            was deleted on that side.
        side:
            Either ``"local"`` or ``"remote"``.  Determines which
            ``{side}_size_enc``, ``{side}_mtime_enc``, and
            ``{side}_scan_ts`` columns are cleared.
        """
        # Clear stale side-specific fields.
        self._conn.execute(
            f"""
            UPDATE "{tbl}"
            SET {side}_size_enc=NULL, {side}_mtime_enc=NULL, {side}_scan_ts=0
            WHERE {side}_scan_ts < ? AND {side}_scan_ts > 0
            """,
            (scan_ts,),
        )
        # Remove rows that have no data on either side (fully orphaned).
        self._conn.execute(
            f"""
            DELETE FROM "{tbl}"
            WHERE local_mtime_enc IS NULL AND remote_mtime_enc IS NULL
            """,
        )

    def upsert_local_batch(
        self,
        service_name: str,
        scan_ts: float,
        files: Dict[str, Dict],
    ) -> None:
        """Write local-scan results for all *files* in a single transaction.

        Called by **Thread 1** after the local filesystem walk completes.

        Parameters
        ----------
        service_name:
            The service whose table should be updated.
        scan_ts:
            UTC epoch timestamp marking when this scan batch started.  Stored
            in the unencrypted ``local_scan_ts`` column so that stale records
            can be pruned without decrypting every row.
        files:
            Mapping of ``rel_path → {"size": int, "mtime": float}``.
        """
        tbl = _table_slug(service_name)
        with self._lock:
            self._conn.executemany(
                f"""
                INSERT INTO "{tbl}"
                    (path_hash, rel_path_enc, local_size_enc, local_mtime_enc, local_scan_ts)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(path_hash) DO UPDATE SET
                    rel_path_enc   = excluded.rel_path_enc,
                    local_size_enc = excluded.local_size_enc,
                    local_mtime_enc = excluded.local_mtime_enc,
                    local_scan_ts  = excluded.local_scan_ts
                """,
                [
                    (
                        _path_hash(rel),
                        self._enc(rel),
                        self._enc(str(meta["size"])),
                        self._enc(str(meta["mtime"])),
                        scan_ts,
                    )
                    for rel, meta in files.items()
                ],
            )
            # Clear local fields for files not found in this scan batch and
            # delete orphaned records via the shared pruning helper.
            self._prune_stale_and_orphaned(tbl, scan_ts, "local")
            self._conn.commit()

    def upsert_remote_batch(
        self,
        service_name: str,
        scan_ts: float,
        files: Dict[str, Dict],
    ) -> None:
        """Write remote-scan results for all *files* in a single transaction.

        Called by **Thread 2** after the remote listing completes.

        Parameters
        ----------
        service_name:
            The service whose table should be updated.
        scan_ts:
            UTC epoch timestamp marking when this scan batch started.
        files:
            Mapping of ``rel_path → {"size": int, "mtime": float}``.
        """
        tbl = _table_slug(service_name)
        with self._lock:
            self._conn.executemany(
                f"""
                INSERT INTO "{tbl}"
                    (path_hash, rel_path_enc, remote_size_enc, remote_mtime_enc, remote_scan_ts)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(path_hash) DO UPDATE SET
                    rel_path_enc    = excluded.rel_path_enc,
                    remote_size_enc = excluded.remote_size_enc,
                    remote_mtime_enc = excluded.remote_mtime_enc,
                    remote_scan_ts  = excluded.remote_scan_ts
                """,
                [
                    (
                        _path_hash(rel),
                        self._enc(rel),
                        self._enc(str(meta["size"])),
                        self._enc(str(meta["mtime"])),
                        scan_ts,
                    )
                    for rel, meta in files.items()
                ],
            )
            # Clear remote fields for files no longer present on the remote
            # and delete orphaned records via the shared pruning helper.
            self._prune_stale_and_orphaned(tbl, scan_ts, "remote")
            self._conn.commit()

    def update_statuses(self, service_name: str, scan_ts: float) -> None:
        """Recompute and persist ``status_enc`` for every record in the table.

        Called by **Thread 3** after both Thread 1 and Thread 2 have finished
        writing to the DB for the current scan generation.

        Status assignment rules:

        * Both local and remote mtime present, ``|Δ| ≤ tolerance`` → ``"synced"``
        * Both present, ``|Δ| > tolerance`` → ``"diff"``
        * Only local mtime → ``"local_only"``
        * Only remote mtime → ``"remote_only"``
        * Neither → ``"unknown"``
        """
        records = self._read_raw_rows(service_name)
        updates: List[tuple] = []
        for row in records:
            l_ts = self._to_float(self._dec(row["local_mtime_enc"]))
            r_ts = self._to_float(self._dec(row["remote_mtime_enc"]))
            if l_ts is not None and r_ts is not None:
                status = "synced" if abs(l_ts - r_ts) <= _MTIME_TOLERANCE_SECS else "diff"
            elif l_ts is not None:
                status = "local_only"
            elif r_ts is not None:
                status = "remote_only"
            else:
                status = "unknown"
            updates.append((self._enc(status), row["path_hash"]))

        tbl = _table_slug(service_name)
        with self._lock:
            self._conn.executemany(
                f'UPDATE "{tbl}" SET status_enc=? WHERE path_hash=?',
                updates,
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Read operation (called by Thread 3)
    # ------------------------------------------------------------------

    def get_all_records(self, service_name: str) -> List[Dict]:
        """Return all records for *service_name* as a list of plain-Python dicts.

        Each dict contains:

        * ``"rel"``           – POSIX relative path (str)
        * ``"local_size"``    – local file size in bytes (:class:`int` or ``None``)
        * ``"remote_size"``   – remote file size in bytes (:class:`int` or ``None``)
        * ``"local_mtime"``   – local last-modified UTC timestamp (:class:`float` or ``None``)
        * ``"remote_mtime"``  – remote last-modified UTC timestamp (:class:`float` or ``None``)
        * ``"last_local_scan"``  – UTC epoch of the last local scan (:class:`float` or ``None``)
        * ``"last_remote_scan"`` – UTC epoch of the last remote scan (:class:`float` or ``None``)
        * ``"status"``        – sync status string (``"synced"``, ``"diff"``, etc.)

        Returns an empty list if the table does not exist.
        """
        rows = self._read_raw_rows(service_name)
        result: List[Dict] = []
        for row in rows:
            rel = self._dec(row["rel_path_enc"])
            if not rel:
                continue
            result.append({
                "rel": rel,
                "local_size": self._to_int(self._dec(row["local_size_enc"])),
                "remote_size": self._to_int(self._dec(row["remote_size_enc"])),
                "local_mtime": self._to_float(self._dec(row["local_mtime_enc"])),
                "remote_mtime": self._to_float(self._dec(row["remote_mtime_enc"])),
                "last_local_scan": row["local_scan_ts"],
                "last_remote_scan": row["remote_scan_ts"],
                "status": self._dec(row["status_enc"]) or "unknown",
            })
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read_raw_rows(self, service_name: str) -> List[sqlite3.Row]:
        """Return all raw SQLite rows for *service_name*, or ``[]`` if missing."""
        tbl = _table_slug(service_name)
        with self._lock:
            # Check table exists before querying to avoid SQLite errors.
            exists = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (tbl,),
            ).fetchone()
            if exists is None:
                return []
            return self._conn.execute(f'SELECT * FROM "{tbl}"').fetchall()

    @staticmethod
    def _to_float(value: Optional[str]) -> Optional[float]:
        """Convert a decrypted string to :class:`float`, or ``None`` on failure."""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _to_int(value: Optional[str]) -> Optional[int]:
        """Convert a decrypted string to :class:`int`, or ``None`` on failure."""
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()
