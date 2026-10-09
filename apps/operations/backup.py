"""Host-local SQLite backup helpers with verification (fail closed).

Judgment call (visible on review): retention keeps the newest pre-update
``pre-update-*.sqlite3`` recovery backup even when older than 30 days; it is
only superseded by a newer verified pre-update backup. All artifacts are
created permission-restricted (0600 files, 0700 dirs) so host users without
access cannot read operational data from backups.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

RETENTION_DAYS = 30
PRE_UPDATE_PREFIX = "pre-update-"
# Marker naming: the protected recovery backup is the newest verified
# pre-update backup; it is never pruned until superseded.


def _restrict(path: Path) -> None:
    try:
        if path.is_dir():
            os.chmod(path, 0o700)
        else:
            os.chmod(path, 0o600)
    except OSError:
        pass


def backup_sqlite(*, source_path: str | Path, dest_path: str | Path) -> dict:
    """Copy the live SQLite file to dest and verify integrity + checksum."""
    src = Path(source_path)
    dst = Path(dest_path)
    if not src.exists():
        raise ValueError(f"live database not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    _restrict(dst.parent)
    # Use the SQLite backup API so a live WAL database copies consistently.
    # Never copy -wal/-shm files naively; the backup API reads a snapshot.
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    _restrict(dst)
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
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        except Exception as exc:
            raise ValueError(f"backup integrity check failed: {exc}") from exc
    finally:
        conn.close()
    if not rows or rows[0][0] != "ok":
        raise ValueError(f"backup integrity check failed: {rows!r}")
    # Schema compatibility: core Django + app tables must exist so a restore
    # cannot silently produce an empty/unmigrated database.
    required = {"django_migrations", "accounts_user", "inventory_item",
                "roster_missionary"}
    missing = required - tables
    if missing:
        raise ValueError(f"backup schema incomplete, missing: {sorted(missing)}")


def backup_digest(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def list_backups(backup_dir: str | Path) -> list[Path]:
    d = Path(backup_dir)
    if not d.exists():
        return []
    return sorted(d.glob("*.sqlite3"))


def protected_recovery_backup(backup_dir: str | Path) -> Path | None:
    """Newest verified pre-update backup, protected from retention pruning."""
    candidates = sorted(Path(backup_dir).glob(f"{PRE_UPDATE_PREFIX}*.sqlite3")) \
        if Path(backup_dir).exists() else []
    return candidates[-1] if candidates else None


def prune_backups(*, backup_dir: str | Path, retention_days: int = RETENTION_DAYS,
                  now_ts: float | None = None) -> dict:
    """Delete backups older than retention_days, protecting recovery backup.

    Returns {"kept": [...], "deleted": [...], "protected": str|None}.
    Never deletes the newest pre-update recovery backup until superseded
    (a newer pre-update file exists).
    """
    import time

    d = Path(backup_dir)
    now = now_ts if now_ts is not None else time.time()
    cutoff = now - retention_days * 86400
    protected = protected_recovery_backup(d)
    kept: list[str] = []
    deleted: list[str] = []
    for path in list_backups(d):
        if protected is not None and path == protected:
            kept.append(str(path))
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            try:
                path.unlink()
                deleted.append(str(path))
            except OSError:
                kept.append(str(path))
        else:
            kept.append(str(path))
    return {"kept": kept, "deleted": deleted,
            "protected": str(protected) if protected else None}


def restore_sqlite(*, backup_path: str | Path, dest_path: str | Path) -> dict:
    """Restore a verified backup over dest (caller must hold maintenance).

    Verifies integrity + schema first; failed checks leave dest untouched
    (fail closed, usable recovery path preserved).
    """
    src = Path(backup_path)
    dst = Path(dest_path)
    verify_sqlite_backup(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    _restrict(dst.parent)
    # Copy via SQLite backup API in reverse so WAL state stays consistent,
    # then fsync through connection close. Source is read-only here.
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        # Remove WAL sidecars of the destination first so stale frames from a
        # newer database cannot linger after restore.
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                Path(str(dst) + suffix).unlink()
            except OSError:
                pass
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    _restrict(dst)
    verify_sqlite_backup(dst)
    return {"path": str(dst), "sha256": backup_digest(dst),
            "bytes": dst.stat().st_size}


def live_db_path() -> Path:
    from django.conf import settings

    return Path(settings.DATABASES["default"]["NAME"])


def default_backup_dir() -> Path:
    return live_db_path().parent / "backups"
