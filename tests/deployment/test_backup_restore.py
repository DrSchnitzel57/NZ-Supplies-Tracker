"""Backup/restore: SQLite-safe ops, retention, failure paths (synthetic)."""

import sqlite3
import time

import pytest

from apps.operations import backup as backup_mod

pytestmark = pytest.mark.django_db


def _synthetic_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE django_migrations (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE accounts_user (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE inventory_item (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE roster_missionary (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO accounts_user VALUES (1)")
    conn.commit()
    conn.close()


def test_backup_verify_round_trip(tmp_path):
    src = tmp_path / "live.sqlite3"
    _synthetic_db(src)
    dest = tmp_path / "backups" / "daily.sqlite3"
    info = backup_mod.backup_sqlite(source_path=src, dest_path=dest)
    assert info["bytes"] > 0 and len(info["sha256"]) == 64
    backup_mod.verify_sqlite_backup(dest)
    # Permission-restricted artifacts.
    assert (dest.stat().st_mode & 0o777) == 0o600


def test_corrupt_backup_fails_closed_and_leaves_dest(tmp_path):
    src = tmp_path / "live.sqlite3"
    _synthetic_db(src)
    good = tmp_path / "good.sqlite3"
    backup_mod.backup_sqlite(source_path=src, dest_path=good)
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not a database at all")
    dest = tmp_path / "restored.sqlite3"
    dest.write_bytes(good.read_bytes())
    before = dest.read_bytes()
    with pytest.raises(ValueError):
        backup_mod.restore_sqlite(backup_path=corrupt, dest_path=dest)
    assert dest.read_bytes() == before  # usable recovery path preserved


def test_incomplete_schema_rejected(tmp_path):
    path = tmp_path / "thin.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE django_migrations (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    with pytest.raises(ValueError):
        backup_mod.verify_sqlite_backup(path)


def test_retention_protects_recovery_backup_until_superseded(tmp_path):
    d = tmp_path / "backups"
    d.mkdir()
    old_daily = d / "daily-old.sqlite3"
    _synthetic_db(old_daily)
    old_time = time.time() - 40 * 86400
    import os
    os.utime(old_daily, (old_time, old_time))
    recovery = d / "pre-update-20260101-000000.sqlite3"
    _synthetic_db(recovery)
    os.utime(recovery, (old_time, old_time))
    result = backup_mod.prune_backups(backup_dir=d, retention_days=30)
    assert str(recovery) in result["kept"]
    assert result["protected"] == str(recovery)
    assert str(old_daily) in result["deleted"]
    # Superseded: a newer pre-update backup releases the old one.
    newer = d / "pre-update-20260201-000000.sqlite3"
    _synthetic_db(newer)
    os.utime(recovery, (old_time, old_time))
    result2 = backup_mod.prune_backups(backup_dir=d, retention_days=30)
    assert result2["protected"] == str(newer)
    assert str(recovery) in result2["deleted"]


def test_restore_requires_verified_backup(tmp_path):
    src = tmp_path / "live.sqlite3"
    _synthetic_db(src)
    dest = tmp_path / "dest.sqlite3"
    info = backup_mod.restore_sqlite(backup_path=src, dest_path=dest)
    assert info["bytes"] > 0
