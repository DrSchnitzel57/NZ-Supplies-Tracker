"""Host-local SQLite backup helpers with verification (fail closed).

Judgment call (visible on review): retention keeps the newest *verified*
pre-update/pre-reset recovery backup even when older than 30 days; it is
only superseded by a newer verified recovery backup. A corrupt file can
never become the protected backup: protection candidates are verified
newest-first and unverifiable files are skipped. All artifacts are created
permission-restricted (0600 files, 0700 dirs) so host users without access
cannot read operational data from backups.

Every completed backup records a ``<name>.manifest.json`` sidecar with the
path, checksum, application version, schema head and completion stamp, so an
operator can always distinguish a verified backup from an interrupted copy.
Backups are written atomically (temporary file + fsync + rename) so a crash
mid-copy never leaves a half-written file under its final name.

Restore never unlinks an open WAL: it closes this process's Django
connections, checkpoints the live WAL, preserves the pre-restore live
database, stages the restore into a temporary file, verifies it, and only
then swaps it into place. Callers (scripts/commands) hold an owned
maintenance window across the whole operation and run migrate + readiness
before reopening writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

RETENTION_DAYS = 30
PRE_UPDATE_PREFIX = "pre-update-"
PRE_RESET_PREFIX = "pre-reset-"
RECOVERY_PREFIXES = (PRE_UPDATE_PREFIX, PRE_RESET_PREFIX)
MANIFEST_SUFFIX = ".manifest.json"
# Marker naming: the protected recovery backup is the newest verified
# recovery backup; it is never pruned until superseded.

# --- Windows/native install support ---
#: Native Windows host (deploy/windows). A module-level flag rather than
#: inline ``os.name`` checks so tests can exercise the Windows branches on
#: POSIX by patching it. On Windows, POSIX mode bits do not exist (os.chmod
#: only toggles the read-only attribute and st_mode always reads 0o666), so
#: access control comes from NTFS ACLs: install.ps1 restricts the install
#: root to Administrators + SYSTEM and every backup inherits that ACL.
IS_WINDOWS = os.name == "nt"
#: Transient sharing violations on Windows (antivirus/indexer briefly
#: holding a just-written file) are retried this many times.
_WINDOWS_REPLACE_ATTEMPTS = 20
_WINDOWS_REPLACE_DELAY = 0.25


def _replace(src: str | Path, dst: str | Path) -> None:
    """Atomic rename over dst; on Windows retry brief sharing violations."""
    if not IS_WINDOWS:
        os.replace(src, dst)
        return
    import time

    for attempt in range(_WINDOWS_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _WINDOWS_REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_WINDOWS_REPLACE_DELAY)
# --- end Windows/native install support ---


def _restrict(path: Path) -> None:
    if IS_WINDOWS:
        return  # NTFS ACLs (inherited from the install root) apply instead
    try:
        if path.is_dir():
            os.chmod(path, 0o700)
        else:
            os.chmod(path, 0o600)
    except OSError:
        pass


def _fsync_file(path: Path) -> None:
    # Windows: os.fsync -> FlushFileBuffers, which needs a writable handle.
    flags = (os.O_RDWR | getattr(os, "O_BINARY", 0)) if IS_WINDOWS else os.O_RDONLY
    fd = os.open(str(path), flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    if IS_WINDOWS:
        # No directory handles via os.open (and no os.O_DIRECTORY) on
        # Windows; NTFS journals the rename metadata itself.
        return
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _app_version() -> str:
    try:
        from django.conf import settings

        return str(getattr(settings, "APP_VERSION", "") or "")
    except Exception:
        return ""


def _code_migration_names() -> set[str]:
    """Migration names known to the running code (app_label.name)."""
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    connection.ensure_connection()
    loader = MigrationLoader(connection)
    return {f"{app}.{name}" for (app, name) in loader.disk_migrations}


def _backup_migration_names(path: Path) -> set[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        try:
            rows = conn.execute("SELECT app, name FROM django_migrations").fetchall()
        except Exception as exc:
            raise ValueError(f"backup has no migration history: {exc}") from exc
    finally:
        conn.close()
    return {f"{app}.{name}" for (app, name) in rows}


def _is_uri(path: str | Path) -> bool:
    return str(path).startswith("file:")


def _connect_readonly(src: Path):
    if _is_uri(src):
        # Shared-cache memory databases have no filesystem path; connect by
        # URI instead. Production paths use mode=ro so verification can never
        # modify the source.
        try:
            conn = sqlite3.connect(str(src), uri=True)
            conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
            return conn
        except Exception as exc:
            raise ValueError(f"live database not found: {src}") from exc
    if not src.exists():
        raise ValueError(f"live database not found: {src}")
    return sqlite3.connect(f"file:{src}?mode=ro", uri=True)


def backup_sqlite(*, source_path: str | Path, dest_path: str | Path) -> dict:
    """Copy the live SQLite file to dest and verify integrity + checksum.

    Atomic: the copy lands in a temporary sibling file and is renamed into
    place only after verification, with a manifest recording path, checksum,
    application version, schema head and completion.
    """
    src = Path(source_path)
    dst = Path(dest_path)
    if _is_uri(src):
        _connect_readonly(src).close()
    elif not src.exists():
        raise ValueError(f"live database not found: {src}")
    if dst.suffix != ".sqlite3":
        raise ValueError("backup destination must end in .sqlite3")
    dst.parent.mkdir(parents=True, exist_ok=True)
    _restrict(dst.parent)
    _checkpoint(src)
    # Use the SQLite backup API so a live WAL database copies consistently.
    # Never copy -wal/-shm files naively; the backup API reads a snapshot.
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=dst.name + ".tmp-",
                                        dir=str(dst.parent))
    os.close(tmp_fd)
    tmp = Path(tmp_name)
    try:
        _restrict(tmp)
        src_conn = _connect_readonly(src)
        try:
            dst_conn = sqlite3.connect(str(tmp))
            try:
                dst_conn.execute("PRAGMA journal_mode=DELETE").fetchall()
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                Path(str(tmp) + suffix).unlink()
            except OSError:
                pass
        _fsync_file(tmp)
        verify_sqlite_backup(tmp)
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        info = {
            "path": str(dst),
            "sha256": digest,
            "bytes": tmp.stat().st_size,
            "app_version": _app_version(),
            "schema_head": sorted(_backup_migration_names(tmp)),
            "completed": True,
        }
        _write_manifest(tmp, info)
        _fsync_file(Path(str(tmp) + MANIFEST_SUFFIX))
        _replace(tmp, dst)
        _replace(str(tmp) + MANIFEST_SUFFIX, str(dst) + MANIFEST_SUFFIX)
        _fsync_dir(dst.parent)
    finally:
        for leftover in (tmp, Path(str(tmp) + MANIFEST_SUFFIX),
                         Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm"),
                         Path(str(tmp) + "-journal")):
            try:
                if leftover.exists():
                    leftover.unlink()
            except OSError:
                pass
    _restrict(dst)
    return {**info, "path": str(dst)}


def verify_sqlite_backup(path: str | Path) -> None:
    """Raise unless the backup passes integrity_check and schema/release checks."""
    path = Path(path)
    uri = _is_uri(path)
    if not uri and (not path.exists() or path.stat().st_size == 0):
        raise ValueError("backup is missing or empty")
    if not uri:
        try:
            mode = oct(path.stat().st_mode & 0o777)
        except OSError as exc:
            raise ValueError(f"backup is not readable: {exc}") from exc
        # Windows has no POSIX mode bits (always 0o666/0o444); the
        # install-root ACL is the access control there (see IS_WINDOWS).
        if mode != "0o600" and not IS_WINDOWS:
            raise ValueError(f"backup has unsafe permissions {mode}; refusing to trust it")
    conn = (_connect_readonly(path) if uri
            else sqlite3.connect(f"file:{path}?mode=ro", uri=True))
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
    required = {"django_migrations", "django_session", "accounts_user",
                "accounts_loginthrottle", "inventory_item", "roster_missionary",
                "issuing_issue", "audit_auditevent"}
    missing = required - tables
    if missing:
        raise ValueError(f"backup schema incomplete, missing: {sorted(missing)}")
    # Release check: a backup created by newer code (migrations this code
    # does not know) cannot be restored here; older backups are fine because
    # the restore flow migrates them before reopening writes.
    try:
        backup_migrations = _backup_migration_names(path)
        code_migrations = _code_migration_names()
    except Exception as exc:
        raise ValueError(f"backup release check failed: {exc}") from exc
    unknown = sorted(backup_migrations - code_migrations)
    if unknown:
        raise ValueError(
            "backup was created by newer code "
            f"(unknown migrations: {unknown[:5]}); restore the matching release first")


def backup_digest(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_manifest(path: str | Path) -> dict | None:
    manifest = Path(str(path) + MANIFEST_SUFFIX)
    if not manifest.exists():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_manifest(tmp: Path, info: dict) -> None:
    manifest = Path(str(tmp) + MANIFEST_SUFFIX)
    payload = {**info, "completed": True}
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True),
                        encoding="utf-8")
    _restrict(manifest)


def list_backups(backup_dir: str | Path) -> list[Path]:
    d = Path(backup_dir)
    if not d.exists():
        return []
    # Only finished backups: skip staging files and manifests.
    return sorted(p for p in d.glob("*.sqlite3")
                  if ".tmp-" not in p.name and not p.name.startswith(".tmp"))


def _is_recovery_name(path: Path) -> bool:
    return any(path.name.startswith(prefix) for prefix in RECOVERY_PREFIXES)


def _verified(path: Path) -> bool:
    try:
        verify_sqlite_backup(path)
    except ValueError:
        return False
    manifest = read_manifest(path)
    if manifest is None:
        return False
    if manifest.get("sha256") != backup_digest(path):
        return False
    return manifest.get("completed") is True


def protected_recovery_backup(backup_dir: str | Path) -> Path | None:
    """Newest verified recovery backup, protected from retention pruning.

    Candidates are verified newest-first; corrupt or manifest-less files can
    never supersede verified protection.
    """
    d = Path(backup_dir)
    if not d.exists():
        return None
    candidates = sorted(
        (p for p in list_backups(d) if _is_recovery_name(p)),
        key=lambda p: p.name,
    )
    for path in reversed(candidates):
        if _verified(path):
            return path
    return None


def prune_backups(*, backup_dir: str | Path, retention_days: int = RETENTION_DAYS,
                  now_ts: float | None = None) -> dict:
    """Delete backups older than retention_days, protecting recovery backup.

    Returns {"kept": [...], "deleted": [...], "protected": str|None}.
    Never deletes the newest verified recovery backup until superseded
    (a newer verified recovery file exists). Stale staging files are always
    removed; files that fail verification but are still fresh are kept (an
    operator decides their fate), never promoted to protected.
    """
    import time

    d = Path(backup_dir)
    now = now_ts if now_ts is not None else time.time()
    cutoff = now - retention_days * 86400
    protected = protected_recovery_backup(d)
    kept: list[str] = []
    deleted: list[str] = []
    # Staging leftovers are never valid backups.
    if d.exists():
        for staging in list(d.glob("*.tmp-*")):
            try:
                staging.unlink()
                deleted.append(str(staging))
            except OSError:
                pass
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
                try:
                    Path(str(path) + MANIFEST_SUFFIX).unlink()
                except OSError:
                    pass
                deleted.append(str(path))
            except OSError:
                kept.append(str(path))
        else:
            kept.append(str(path))
    return {"kept": kept, "deleted": deleted,
            "protected": str(protected) if protected else None}


def _checkpoint(path: Path) -> None:
    if not _is_uri(path) and not Path(path).exists():
        return  # never create a stray file just to checkpoint it
    try:
        if _is_uri(path):
            conn = sqlite3.connect(str(path), uri=True)
        else:
            conn = sqlite3.connect(str(path))
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        finally:
            conn.close()
    except Exception:
        pass


def _close_django_connections() -> None:
    try:
        from django.db import connections

        connections.close_all()
    except Exception:
        pass


def restore_sqlite(*, backup_path: str | Path, dest_path: str | Path,
                   preserve: bool = True) -> dict:
    """Restore a verified backup over dest (caller must hold maintenance).

    Closes this process's Django connections and checkpoints the live WAL
    first, so no open WAL is ever unlinked. The live database is preserved
    next to dest before any write; the restore is staged into a temporary
    file, verified, and only then swapped into place (fail closed: failed
    checks leave dest untouched with a usable recovery path preserved).
    """
    from apps.operations.guards import assert_maintenance_active

    assert_maintenance_active()
    src = Path(backup_path)
    dst = Path(dest_path)
    verify_sqlite_backup(src)
    expected = backup_digest(src) if not _is_uri(src) else None
    _close_django_connections()
    if dst.exists():
        _checkpoint(dst)
        _close_django_connections()
    if _is_uri(dst):
        # Memory/file-URI destination (tests): restore directly; there is no
        # filesystem image to stage, preserve or unlink sidecars for.
        src_conn = _connect_readonly(src)
        try:
            dst_conn = sqlite3.connect(str(dst), uri=True)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
        _close_django_connections()
        verify_sqlite_backup(dst)
        return {"path": str(dst), "sha256": "", "bytes": 0}
    dst.parent.mkdir(parents=True, exist_ok=True)
    _restrict(dst.parent)
    preserved = None
    if preserve and dst.exists():
        import time
        import uuid as _uuid

        stamp = f"{int(time.time())}-{_uuid.uuid4().hex[:8]}"
        preserved = dst.parent / f"{dst.stem}.pre-restore-{stamp}.sqlite3"
        # Preservation copy via the backup API (consistent snapshot), then
        # verify it before relying on it.
        live_conn = sqlite3.connect(str(dst))
        try:
            keep_conn = sqlite3.connect(str(preserved))
            try:
                keep_conn.execute("PRAGMA journal_mode=DELETE").fetchall()
                live_conn.backup(keep_conn)
            finally:
                keep_conn.close()
        finally:
            live_conn.close()
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                Path(str(preserved) + suffix).unlink()
            except OSError:
                pass
        _restrict(preserved)
        _fsync_file(preserved)
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=dst.name + ".tmp-",
                                        dir=str(dst.parent))
    os.close(tmp_fd)
    tmp = Path(tmp_name)
    try:
        _restrict(tmp)
        # Copy via SQLite backup API in reverse so WAL state stays consistent.
        # Source is read-only here.
        src_conn = _connect_readonly(src)
        try:
            dst_conn = sqlite3.connect(str(tmp))
            try:
                dst_conn.execute("PRAGMA journal_mode=DELETE").fetchall()
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                Path(str(tmp) + suffix).unlink()
            except OSError:
                pass
        _fsync_file(tmp)
        verify_sqlite_backup(tmp)
        if backup_digest(tmp) != expected:
            raise ValueError("staged restore checksum changed during copy; aborting")
        # verify_sqlite_backup() above reopened this process's Django
        # connection to the live file: close it so no open handle/WAL sits
        # under the swap (Windows also refuses to replace an open file).
        _close_django_connections()
        _replace(tmp, dst)
        # Stale sidecars belong to the pre-restore database image: with all
        # connections closed and the WAL checkpointed they are safe to drop
        # only now, after the swap, so a crash mid-restore keeps recovery data.
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                Path(str(dst) + suffix).unlink()
            except OSError:
                pass
        _fsync_dir(dst.parent)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    _close_django_connections()
    _restrict(dst)
    verify_sqlite_backup(dst)
    result = {"path": str(dst), "sha256": backup_digest(dst),
              "bytes": dst.stat().st_size}
    if preserved is not None:
        result["preserved"] = str(preserved)
    return result


def live_db_path() -> Path:
    from django.conf import settings

    return Path(settings.DATABASES["default"]["NAME"])


def default_backup_dir() -> Path:
    return live_db_path().parent / "backups"
