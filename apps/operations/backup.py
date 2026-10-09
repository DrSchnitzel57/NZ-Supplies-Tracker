"""Host-local SQLite backup helpers with verification (fail closed)."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


def backup_sqlite(*, source_path: str | Path, dest_path: str | Path) -> dict:
    """Copy the live SQLite file to dest and verify integrity + checksum."""
    src = Path(source_path)
    dst = Path(dest_path)
    if not src.exists():
        raise ValueError(f"live database not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Use the SQLite backup API so a live WAL database copies consistently.
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    verify_sqlite_backup(dst)
    digest = hashlib.sha256(dst.read_bytes()).hexdigest()
    return {"path": str(dst), "sha256": digest, "bytes": dst.stat().st_size}


def verify_sqlite_backup(path: str | Path) -> None:
    """Raise unless the backup passes integrity_check and has core tables."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError("backup is missing or empty")
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    finally:
        conn.close()
    if not rows or rows[0][0] != "ok":
        raise ValueError(f"backup integrity check failed: {rows!r}")


def live_db_path() -> Path:
    from django.conf import settings

    return Path(settings.DATABASES["default"]["NAME"])
