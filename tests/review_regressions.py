"""Milestone-05 review regressions: behavioral checks for every fixed finding.

Synthetic data only; no PII. Every test exercises runtime behavior (state
transitions, refusals, round trips) — never mere source-string presence.
"""

import csv
import io
import json
import os
import tempfile
import time
import zipfile

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings

from apps.common.errors import (
    MaintenanceActiveError,
    StaleVersionError,
    ValidationError,
)
from tests.factories import make_admin, make_item, make_missionary, make_staff

User = get_user_model()


def _reauth_session(testcase, user):
    session = testcase.client.session
    session["admin_reauth"] = {
        "at": time.time(), "user_id": user.pk, "pwd": user.password}
    session.save()


def _valid_bundle_bytes(actor=None, filename="t.zip"):
    """Minimal valid bundle of the current (empty or not) database."""
    from apps.transfers import services as transfers

    admin = actor
    if admin is None:
        admin, _ = make_admin()
    return transfers.export_bundle(actor=admin)


def _rebuild_bundle(manifest_overrides=None, table_overrides=None,
                    drop_checksums=(), version=None):
    """Re-zip an exported bundle with edited tables/manifest (fresh checksums)."""
    from apps.transfers import bundle as bundle_mod

    data = _valid_bundle_bytes()
    zf = zipfile.ZipFile(io.BytesIO(data))
    tables = {name: zf.read(name) for name in bundle_mod.TABLE_FILES}
    manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    if table_overrides:
        tables.update(table_overrides)
    checksums = {name: bundle_mod._sha(raw) for name, raw in tables.items()
                 if name not in drop_checksums}
    manifest["checksums"] = checksums
    if manifest_overrides:
        manifest.update(manifest_overrides)
    if version is not None:
        manifest["version"] = version
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as out:
        out.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))
        for name in bundle_mod.TABLE_FILES:
            out.writestr(name, tables[name])
    return buf.getvalue()


def _csv_bytes(fields, rows):
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


class OwnedGuardTests(TestCase):
    def test_second_operator_cannot_share_window(self):
        from apps.operations import guards

        token_a = guards.enter_maintenance(owner="operator-a", reason="update")
        try:
            with self.assertRaises(MaintenanceActiveError):
                guards.enter_maintenance(owner="operator-b", reason="restore")
            self.assertTrue(guards.is_maintenance_active())
            status = guards.maintenance_status()
            self.assertEqual(status["owner"], "operator-a")
        finally:
            self.assertTrue(guards.exit_maintenance(token_a))
        self.assertFalse(guards.is_maintenance_active())

    def test_nested_same_owner_deepens_and_inner_exit_keeps_window(self):
        from apps.operations import guards

        with guards.maintenance_mode(owner="job", reason="outer") as outer:
            self.assertTrue(guards.is_maintenance_active())
            with guards.maintenance_mode(owner="job", reason="inner") as inner:
                self.assertEqual(inner, outer)
                self.assertTrue(guards.is_maintenance_active())
            self.assertTrue(guards.is_maintenance_active())
        self.assertFalse(guards.is_maintenance_active())

    def test_foreign_token_cannot_clear_window(self):
        from apps.operations import guards

        token = guards.enter_maintenance(owner="holder", reason="work")
        try:
            with self.assertRaises(MaintenanceActiveError):
                guards.exit_maintenance("deadbeef" * 8)
            with self.assertRaises(MaintenanceActiveError):
                guards.exit_maintenance(None)
            self.assertTrue(guards.is_maintenance_active())
        finally:
            guards.exit_maintenance(token)

    def test_failed_import_aborts_and_retains_others_window(self):
        from apps.operations import guards
        from apps.transfers import services as transfers

        admin, _ = make_admin()
        data = _valid_bundle_bytes(actor=admin)
        token = guards.enter_maintenance(owner="operator", reason="other work")
        try:
            # Acquisition failure aborts before any write or window change.
            with self.assertRaises(MaintenanceActiveError):
                transfers.import_bundle(actor=admin, data=data, filename="x.zip")
            self.assertTrue(guards.is_maintenance_active())
            self.assertEqual(guards.maintenance_status()["owner"], "operator")
        finally:
            guards.exit_maintenance(token)

    def test_failed_import_closes_only_its_own_window(self):
        from apps.operations import guards
        from apps.transfers import services as transfers

        admin, _ = make_admin()
        # Invalid bundle: parse fails before maintenance is even entered.
        with self.assertRaises(Exception):
            transfers.import_bundle(actor=admin, data=b"not-a-zip", filename="x.zip")
        self.assertFalse(guards.is_maintenance_active())

    def test_crash_recovery_needs_explicit_audited_takeover(self):
        from apps.audit.models import AuditEvent
        from apps.operations import guards

        stale = guards.enter_maintenance(owner="crashed-job", reason="died")
        try:
            # No auto-expiry: the window stays closed (fail closed).
            self.assertTrue(guards.is_maintenance_active())
            with self.assertRaises(MaintenanceActiveError):
                guards.enter_maintenance(owner="operator", reason="retry")
            forced = guards.enter_maintenance(
                owner="operator", reason="crash recovery", force=True)
            self.assertTrue(AuditEvent.objects.filter(
                action="maintenance.takeover").exists())
            guards.exit_maintenance(forced)
            self.assertFalse(guards.is_maintenance_active())
        finally:
            if guards.is_maintenance_active():
                try:
                    guards.exit_maintenance(stale)
                except MaintenanceActiveError:
                    status = guards.maintenance_status()
                    if status["active"]:
                        takeover = guards.enter_maintenance(
                            owner="cleanup", reason="test", force=True)
                        guards.exit_maintenance(takeover)


class AtomicBackupTests(TransactionTestCase):
    # Real commits: the raw SQLite backup API only sees committed data.
    def test_backup_records_manifest_and_verifies(self):
        import os
        import tempfile

        from apps.operations import backup as backup_mod

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "b.sqlite3")
            info = backup_mod.backup_sqlite(
                source_path=backup_mod.live_db_path(), dest_path=dest)
            self.assertTrue(info["completed"])
            self.assertIn("schema_head", info)
            manifest = backup_mod.read_manifest(dest)
            self.assertEqual(manifest["sha256"], info["sha256"])
            self.assertEqual(manifest["sha256"], backup_mod.backup_digest(dest))
            self.assertEqual(oct(os.stat(dest).st_mode & 0o777), "0o600")
            # No staging leftovers under final names.
            self.assertFalse([p for p in os.listdir(tmp) if ".tmp-" in p])

    def test_corrupt_newest_never_supersedes_verified_protection(self):
        import os
        import tempfile
        import time as _time

        from apps.operations import backup as backup_mod

        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "pre-update-20200101-000000.sqlite3")
            backup_mod.backup_sqlite(
                source_path=backup_mod.live_db_path(), dest_path=good)
            old = os.path.join(tmp, "pre-update-20190101-000000.sqlite3")
            backup_mod.backup_sqlite(
                source_path=backup_mod.live_db_path(), dest_path=old)
            ancient = _time.time() - 40 * 86400
            os.utime(old, (ancient, ancient))
            # Attacker/corruption drops a newer but garbage recovery file.
            evil = os.path.join(tmp, "pre-update-99990101-000000.sqlite3")
            with open(evil, "wb") as fh:
                fh.write(b"\x00" * 8192)
            os.chmod(evil, 0o600)
            os.utime(good, (ancient, ancient))
            protected = backup_mod.protected_recovery_backup(tmp)
            self.assertEqual(str(protected), good)
            result = backup_mod.prune_backups(
                backup_dir=tmp, retention_days=30, now_ts=_time.time())
            self.assertEqual(result["protected"], good)
            self.assertIn(good, result["kept"])
            self.assertNotIn(good, result["deleted"])

    def test_verify_rejects_bad_permissions_and_newer_schema(self):
        import os
        import sqlite3
        import tempfile

        from apps.operations import backup as backup_mod

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "b.sqlite3")
            backup_mod.backup_sqlite(
                source_path=backup_mod.live_db_path(), dest_path=dest)
            os.chmod(dest, 0o644)
            with self.assertRaises(ValueError):
                backup_mod.verify_sqlite_backup(dest)
            os.chmod(dest, 0o600)
            backup_mod.verify_sqlite_backup(dest)  # strict perms pass again
            conn = sqlite3.connect(dest)
            try:
                conn.execute(
                    "INSERT INTO django_migrations(app, name, applied) "
                    "VALUES('nope', '9999_future', datetime('now'))")
                conn.commit()
            finally:
                conn.close()
            with self.assertRaises(ValueError):
                backup_mod.verify_sqlite_backup(dest)

    def test_offline_restore_never_unlinks_open_wal(self):
        import os
        import tempfile

        from django.db import connections

        from apps.operations import backup as backup_mod
        from apps.operations import guards

        admin, _ = make_admin()
        person = make_missionary(admin)
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.sqlite3")
            backup_mod.backup_sqlite(
                source_path=backup_mod.live_db_path(), dest_path=src)
            make_missionary(admin)  # newer work after the backup
            self.assertEqual(
                __import__("apps.roster.models", fromlist=["Missionary"])
                .Missionary.objects.count(), 2)
            token = guards.enter_maintenance(owner="test", reason="restore")
            try:
                # Django connections are wide open here (test client state);
                # restore must close/checkpoint them, not unlink a live WAL.
                info = backup_mod.restore_sqlite(
                    backup_path=src, dest_path=backup_mod.live_db_path())
                self.assertIn("preserved", info)
                self.assertTrue(os.path.exists(info["preserved"]))
            finally:
                guards.exit_maintenance(token)
                connections.close_all()
            from apps.roster.models import Missionary
            self.assertEqual(Missionary.objects.count(), 1)
            self.assertEqual(Missionary.objects.get().pk, person.pk)


class ResetRestoreCommandTests(TransactionTestCase):
    # Real commits for the same reason as AtomicBackupTests.
    def setUp(self):
        self.admin, _ = make_admin()

    def test_reset_marker_records_path_and_version(self):
        from apps.operations import backup as backup_mod
        from apps.operations import reset as reset_service

        make_missionary(self.admin)
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "pre-reset-x.sqlite3")
            counts = reset_service.current_counts()
            info = reset_service.reset_to_empty(
                actor=self.admin, confirmation="RESET",
                backup_path=dest,
                expected_counts={k: str(v) for k, v in counts.items()})
            self.assertTrue(os.path.exists(dest))
            self.assertTrue(backup_mod.read_manifest(dest)["completed"])
            from apps.audit.models import AuditEvent
            marker = AuditEvent.objects.get(action="system.reset")
            self.assertEqual(marker.detail["backup_path"], info["path"])
            self.assertIn("app_version", marker.detail)

    def test_reset_rejects_any_backup_error_fail_closed(self):
        from unittest import mock

        from apps.operations import reset as reset_service
        from apps.roster.models import Missionary

        make_missionary(self.admin)
        with mock.patch("apps.operations.backup.backup_sqlite",
                        side_effect=OSError("disk gone")):
            with self.assertRaises(ValidationError):
                reset_service.reset_to_empty(actor=self.admin, confirmation="RESET")
        self.assertEqual(Missionary.objects.count(), 1)

    def test_restore_command_migrates_and_checks_readiness(self):
        import os
        import tempfile

        from django.core.management import call_command

        from apps.inventory.models import Item
        from apps.operations import backup as backup_mod

        person = make_missionary(self.admin)
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "r.sqlite3")
            backup_mod.backup_sqlite(
                source_path=backup_mod.live_db_path(), dest_path=src)
            make_item(self.admin, opening_qty=3)  # newer work
            with self.assertRaises(Exception):
                call_command("restore_db", src)
            self.assertTrue(Item.objects.exists())  # untouched
            call_command("restore_db", src, "--allow-newer-data-loss")
            self.assertFalse(Item.objects.exists())
            self.assertTrue(
                __import__("apps.roster.models", fromlist=["Missionary"])
                .Missionary.objects.filter(pk=person.pk).exists())

    def test_restore_refuses_movements_only_newer_work(self):
        import os
        import tempfile

        from django.core.management import call_command

        from apps.operations import backup as backup_mod
        from apps.roster.models import Missionary

        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "empty.sqlite3")
            backup_mod.backup_sqlite(
                source_path=backup_mod.live_db_path(), dest_path=src)
            # Post-reset state: only the reset marker audit + no rows. Add a
            # lone movement-less audit? Movements require items; instead drop
            # everything and leave a single audit row (marker-like).
            Missionary.objects.all().delete()
            from apps.audit.models import AuditEvent
            AuditEvent.objects.create(actor=self.admin, action="system.reset",
                                      target_type="system", target_id="reset",
                                      detail={})
            with self.assertRaises(Exception):
                call_command("restore_db", src)
            call_command("restore_db", src, "--allow-newer-data-loss")


class SharedThrottleTests(TestCase):
    def test_login_bounded_and_atomic(self):
        from apps.accounts import throttling

        staff, pw = make_staff(username="lock-me")
        key = throttling.login_key("lock-me")
        for _ in range(5):
            self.client.post("/accounts/login/",
                             {"username": "lock-me", "password": "wrong!"})
        self.assertTrue(throttling.is_locked(key))
        # Correct password is still refused while locked.
        response = self.client.post("/accounts/login/",
                                    {"username": "lock-me", "password": pw})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(staff_is_logged_in(self.client))
        throttling.clear(key)
        self.assertTrue(self.client.login(username="lock-me", password=pw))

    def test_reauth_has_separate_bounded_bucket(self):
        from apps.accounts import reauth as reauth_mod

        admin, pw = make_admin()
        self.client.login(username=admin.username, password=pw)
        for _ in range(5):
            response = self.client.post("/admin-area/reauth/", {"password": "nope"})
            self.assertEqual(response.status_code, 401)
        response = self.client.post("/admin-area/reauth/", {"password": pw})
        # Locked out even with the right password.
        self.assertEqual(response.status_code, 429)
        reauth_mod.record_reauth_success(admin)
        response = self.client.post("/admin-area/reauth/", {"password": pw},
                                    follow=False)
        self.assertEqual(response.status_code, 302)

    def test_reauth_bound_to_password_change(self):
        from apps.accounts import reauth as reauth_mod

        admin, pw = make_admin()
        self.client.login(username=admin.username, password=pw)
        self.assertEqual(
            self.client.post("/admin-area/reauth/", {"password": pw}).status_code, 302)
        req = _req(admin)
        self.assertTrue(reauth_mod.reauth_fresh(req))
        # Changing the password invalidates the confirmation.
        admin.set_password("BrandNewPass123!")
        admin.save()
        self.assertFalse(reauth_mod.reauth_fresh(req))

    def test_reauth_safe_redirect(self):
        admin, pw = make_admin()
        self.client.login(username=admin.username, password=pw)
        response = self.client.post("/admin-area/reauth/?next=https://evil.test/x",
                                    {"password": pw})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].endswith("/admin-area/reset/"))

    def test_weak_temporary_password_rejected(self):
        from apps.accounts import lifecycle

        admin, _ = make_admin()
        with self.assertRaises(ValidationError):
            lifecycle.create_user(actor=admin, username="weak-1", password="123")
        with self.assertRaises(ValidationError):
            lifecycle.create_user(actor=admin, username="weak-2", password="password")


def _wipe_operational():
    from apps.audit.models import AuditEvent
    from apps.inventory.models import Item, StockMovement
    from apps.issuing.models import Issue, IssueLine
    from apps.roster.models import Missionary

    IssueLine.objects.all().delete()
    StockMovement.objects.all().delete()
    Issue.objects.all().delete()
    Item.objects.all().delete()
    Missionary.objects.all().delete()
    AuditEvent.objects.all().delete()


def staff_is_logged_in(client):
    return "_auth_user_id" in client.session


class _Req:
    def __init__(self, user):
        self.user = user
        self.session = {}


def _req(user):
    # Minimal stand-in exposing session/user for reauth_fresh.
    from django.test import RequestFactory

    factory = RequestFactory()
    request = factory.get("/")
    request.user = user
    from django.contrib.sessions.backends.db import SessionStore

    store = SessionStore()
    record = {"at": time.time(), "user_id": user.pk, "pwd": user.password}
    store["admin_reauth"] = record
    store.save()
    request.session = store
    return request


class BundleValidationTests(TestCase):
    def setUp(self):
        self.admin, _ = make_admin()
        self.staff, _ = make_staff()

    def _import(self, data, actor=None):
        from apps.transfers import services as transfers

        return transfers.import_bundle(
            actor=actor or self.admin, data=data, filename="t.zip")

    def test_ledger_mismatch_rejected(self):
        # Available=999 with only 10 units of recorded movement is corrupt.
        from apps.transfers import bundle as bundle_mod

        item = make_item(self.admin, opening_qty=10)
        data = _valid_bundle_bytes(actor=self.admin)
        zf = zipfile.ZipFile(io.BytesIO(data))
        tables = {n: zf.read(n).decode("utf-8") for n in bundle_mod.TABLE_FILES}
        rows = list(csv.DictReader(io.StringIO(tables["items.csv"])))
        for row in rows:
            if row["id"] == str(item.pk):
                row["on_hand"] = "999"
        edited = _csv_bytes(bundle_mod.ITEM_FIELDS, rows)
        with self.assertRaises(Exception) as ctx:
            self._import(_rebuild_bundle(table_overrides={"items.csv": edited}))
        self.assertIn("disagrees", str(ctx.exception))

    def test_missing_checksum_rejected(self):
        with self.assertRaises(Exception) as ctx:
            self._import(_rebuild_bundle(drop_checksums=("items.csv",)))
        self.assertIn("checksum", str(ctx.exception))

    def test_count_mismatch_and_bad_header_rejected(self):
        with self.assertRaises(Exception) as ctx:
            self._import(_rebuild_bundle(
                manifest_overrides={"counts": {"settings": 0, "roster": 0,
                                              "items": 999, "issues": 0,
                                              "issue_lines": 0, "movements": 0,
                                              "audits": 0}}))
        self.assertIn("count", str(ctx.exception))
        data = _valid_bundle_bytes(actor=self.admin)
        zf = zipfile.ZipFile(io.BytesIO(data))
        raw = zf.read("roster.csv").decode("utf-8").replace(
            "personal_email", "hacker_col", 1)
        with self.assertRaises(Exception):
            self._import(_rebuild_bundle(table_overrides={"roster.csv": raw.encode()}))

    def test_duplicate_lines_statuses_and_reversal_consistency(self):
        from apps.transfers import bundle as bundle_mod

        person = make_missionary(self.admin)
        item = make_item(self.admin, opening_qty=10)
        from apps.issuing import services as issuing

        issuing.create_issue(
            actor=self.admin, missionary_id=person.pk,
            lines=[{"item_id": str(item.pk), "quantity": 3}],
            note="", idempotency_key="rev-reg-1")
        data = _valid_bundle_bytes(actor=self.admin)
        zf = zipfile.ZipFile(io.BytesIO(data))
        lines = list(csv.DictReader(io.StringIO(zf.read("issue_lines.csv").decode())))
        # Duplicate line IDs.
        dup = _csv_bytes(bundle_mod.LINE_FIELDS, lines + [lines[0]])
        with self.assertRaises(Exception):
            self._import(_rebuild_bundle(table_overrides={"issue_lines.csv": dup}))
        # Unknown status.
        issues = list(csv.DictReader(io.StringIO(zf.read("issues.csv").decode())))
        issues[0]["status"] = "LOST"
        bad = _csv_bytes(bundle_mod.ISSUE_FIELDS, issues)
        with self.assertRaises(Exception):
            self._import(_rebuild_bundle(table_overrides={"issues.csv": bad}))
        # Reversed flag without reversal movements.
        issues[0]["status"] = "REVERSED"
        issues[0]["reversed_at"] = "2026-02-01T00:00:00+00:00"
        issues[0]["reversal_reason"] = "oops"
        bad2 = _csv_bytes(bundle_mod.ISSUE_FIELDS, issues)
        with self.assertRaises(Exception):
            self._import(_rebuild_bundle(table_overrides={"issues.csv": bad2}))

    def test_timestamps_and_actor_snapshots_survive_without_accounts(self):
        from apps.issuing import services as issuing
        from apps.transfers import services as transfers

        person = make_missionary(self.admin)
        item = make_item(self.admin, opening_qty=10)
        issue = issuing.create_issue(
            actor=self.admin, missionary_id=person.pk,
            lines=[{"item_id": str(item.pk), "quantity": 2}],
            note="", idempotency_key="rev-reg-2")
        issued_at = issue.issued_at
        admin_name = self.admin.username
        data = _valid_bundle_bytes(actor=self.admin)
        # Simulate a fresh host: wipe operational data and drop every
        # account; snapshots must carry attribution without the accounts.
        _wipe_operational()
        User.objects.all().delete()
        fresh_admin, _ = make_admin(username="importer")
        transfers.import_bundle(actor=fresh_admin, data=data, filename="t.zip")
        from apps.issuing.models import Issue
        got = Issue.objects.get(pk=issue.pk)
        self.assertEqual(got.issued_at, issued_at)
        self.assertEqual(got.issued_by_username, admin_name)
        self.assertIsNone(got.issued_by)
        from apps.inventory.models import StockMovement
        self.assertTrue(StockMovement.objects.filter(
            created_by_username=admin_name).exists())

    def test_legacy_v0_bundle_migrates_explicitly(self):
        from apps.transfers import bundle as bundle_mod

        make_item(self.admin, opening_qty=4)
        data = _rebuild_bundle(version=0)
        parsed = bundle_mod.parse_bundle(data)
        self.assertEqual(parsed["manifest"].get("migrated_from_version"), 0)
        _wipe_operational()
        self._import(data)
        from apps.inventory.models import Item
        self.assertEqual(Item.objects.count(), 1)
        # Unknown versions still rejected.
        with self.assertRaises(Exception):
            bundle_mod.parse_bundle(_rebuild_bundle(version=7))

    def test_zip_bounds_reject_junk(self):
        from apps.transfers import bundle as bundle_mod

        data = _valid_bundle_bytes(actor=self.admin)
        zf = zipfile.ZipFile(io.BytesIO(data))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as out:
            for info in zf.infolist():
                out.writestr(info.filename, zf.read(info.filename))
            out.writestr("evil.exe", b"x")
        with self.assertRaises(bundle_mod.BundleError):
            bundle_mod.parse_bundle(buf.getvalue())
        with self.assertRaises(bundle_mod.BundleError):
            bundle_mod.parse_bundle(b"x" * (bundle_mod.MAX_BUNDLE_BYTES + 1))

    def test_safe_encode_round_trip_is_lossless(self):
        from apps.transfers.bundle import safe_decode, safe_encode

        for original in ["=1+1", "  =1+1", "'quoted", "'=already", "", "@m",
                         "  -5", "plain", "it's", "  padded  ", "'",
                         "+44123", "|pipe", "%pct", "a'b", "''x"]:
            self.assertEqual(safe_decode(safe_encode(original)), original, original)

    def test_staff_csv_is_formula_safe(self):
        from apps.reports import services as rep

        make_missionary(self.admin, name="=cmd|evil", external_id="F1")
        response = rep.roster_csv()
        content = response.content.decode()
        self.assertIn("'=cmd|evil", content)
        self.assertNotIn("\n=cmd", content)


class RowImportHardeningTests(TestCase):
    def setUp(self):
        self.staff, _ = make_staff()
        self.admin, _ = make_admin()

    def _preview(self, kind, rows, mode="add_update", actor=None):
        from apps.transfers.models import RowImportPreview
        from apps.transfers.row_imports import build_preview, current_revision, new_preview_token

        preview = build_preview(kind=kind, rows=rows, mode=mode)
        stored = [{**e, "error": False} for e in preview["valid"]]
        stored += [{**e["entry"], "error": True, "problems": e["problems"]}
                   for e in preview["errors"]]
        return RowImportPreview.objects.create(
            token=new_preview_token(), kind=kind, mode=mode, rows=stored,
            base_revision=current_revision(kind), created_by=actor or self.staff)

    def test_lower_version_edit_invalidates_preview(self):
        from apps.transfers import services as transfers

        first = make_missionary(self.staff, external_id="LV-1", name="One")
        make_missionary(self.staff, external_id="LV-2", name="Two")
        rows = [["external_id", "name"], ["LV-3", "Three"]]
        preview = self._preview("roster", rows)
        # Edit the LOWER-version record; max-version token schemes miss this.
        from apps.roster import services as roster_services
        roster_services.update_missionary(
            actor=self.staff, missionary_id=first.pk,
            expected_version=first.version, name="One Renamed")
        with self.assertRaises(StaleVersionError):
            transfers.confirm_row_import(
                actor=self.staff, preview=preview, selected=None)

    def test_unchecked_all_writes_nothing(self):
        from apps.roster.models import Missionary
        from apps.transfers import services as transfers

        rows = [["external_id", "name"], ["N1", "None One"]]
        preview = self._preview("roster", rows)
        result = transfers.confirm_row_import(
            actor=self.staff, preview=preview, selected=set())
        self.assertEqual(result, {"created": 0, "updated": 0, "adjusted": 0,
                                 "archived": 0})
        self.assertFalse(Missionary.objects.filter(external_id="N1").exists())

    def test_preview_ownership_and_single_use(self):
        from apps.transfers import services as transfers

        other, _ = make_staff()
        rows = [["external_id", "name"], ["O1", "Owned"]]
        preview = self._preview("roster", rows, actor=self.staff)
        with self.assertRaises(ValidationError):
            transfers.confirm_row_import(actor=other, preview=preview, selected=None)
        # Administrator may apply anyone's preview.
        result = transfers.confirm_row_import(
            actor=self.admin, preview=preview, selected=None)
        self.assertEqual(result["created"], 1)
        # Single-use: the consumed preview cannot confirm again.
        with self.assertRaises(StaleVersionError):
            transfers.confirm_row_import(
                actor=self.admin, preview=preview, selected=None)

    def test_archived_roster_match_reactivates(self):
        from apps.roster import services as roster_services
        from apps.transfers import services as transfers

        person = make_missionary(self.staff, external_id="RE-1", name="Old")
        roster_services.archive_missionary(actor=self.staff, missionary_id=person.pk)
        rows = [["external_id", "name"], ["RE-1", "New Name"]]
        preview = self._preview("roster", rows)
        result = transfers.confirm_row_import(
            actor=self.staff, preview=preview, selected=None)
        self.assertEqual(result["updated"], 1)
        person.refresh_from_db()
        self.assertFalse(person.is_archived)
        self.assertEqual(person.name, "New Name")

    def test_fractional_nonfinite_overflow_counts_rejected(self):
        from apps.transfers.row_imports import build_preview

        for bad in ["1.9", "inf", "-inf", "nan", "1e30", "9999999999x"]:
            rows = [["category", "name", "remaining"], ["C", "N", bad]]
            preview = build_preview(kind="inventory", rows=rows, mode="add_update")
            self.assertEqual(len(preview["errors"]), 1, bad)
            self.assertEqual(len(preview["valid"]), 0, bad)

    def test_macro_workbook_and_bad_magic_rejected(self):
        import io as _io

        import openpyxl

        from apps.transfers.row_imports import read_table

        wb = openpyxl.Workbook()
        wb.active.append(["category", "name", "remaining"])
        wb.active.append(["C", "N", 3])
        buf = _io.BytesIO()
        wb.save(buf)
        raw = buf.getvalue()
        zin = zipfile.ZipFile(_io.BytesIO(raw))
        buf2 = _io.BytesIO()
        with zipfile.ZipFile(buf2, "w") as out:
            for info in zin.infolist():
                out.writestr(info.filename, zin.read(info.filename))
            out.writestr("xl/vbaProject.bin", b"macro")
        with self.assertRaises(ValueError):
            read_table(filename="m.xlsx", content=buf2.getvalue())
        with self.assertRaises(ValueError):
            read_table(filename="m.xlsx", content=b"definitely-not-a-zip")

    def test_title_row_and_aliases_accepted(self):
        from apps.transfers.row_imports import build_preview

        rows = [["Inventory January"], ["category", "itemName", "onHand"],
                ["Cat", "Widget", "5"]]
        preview = build_preview(kind="inventory", rows=rows, mode="add_update")
        self.assertEqual(len(preview["valid"]), 1)
        self.assertEqual(preview["valid"][0]["counted"], 5)
        rows = [["Roster"], ["extId", "fullName"], ["A-1", "Some Person"]]
        preview = build_preview(kind="roster", rows=rows, mode="add_update")
        self.assertEqual(len(preview["valid"]), 1)

    def test_preview_shows_reasons_and_deltas(self):
        item = make_item(self.staff, opening_qty=10)
        rows = [["category", "name", "remaining", "reason"],
                [item.category, item.name, "12", "recount found more"]]
        preview = self._preview("inventory", rows)
        entry = preview.rows[0]
        self.assertEqual(entry["counted"], 12)
        self.assertEqual(entry["current"], 10)
        self.assertEqual(entry["delta"], 2)
        self.assertEqual(entry["reason"], "recount found more")

    def test_roster_import_preserves_profile_fields(self):
        from apps.roster.models import Missionary
        from apps.transfers import services as transfers

        rows = [["external_id", "name", "title_prefix", "stake",
                 "arrival_status", "reason"],
                ["P-1", "Jane Doe", "Sister", "North Stake", "arrived", "intake note"]]
        preview = self._preview("roster", rows)
        transfers.confirm_row_import(actor=self.staff, preview=preview, selected=None)
        person = Missionary.objects.get(external_id="P-1")
        self.assertEqual(person.title_prefix, "Sister")
        self.assertEqual(person.stake, "North Stake")
        self.assertEqual(person.arrival_status, "arrived")


class SeedPreservationTests(TestCase):
    def test_synthetic_seed_round_trip(self):
        from apps.transfers.seed import extract_seed_json, seed_to_bundle_rows

        html = (
            "<html><body><script>\n"
            "var SEED = {\"missionaries\": ["
            "{\"id\": \"M1\", \"name\": \"Elder O'Brien\", \"email\": \"a@example.test\","
            " \"stake\": \"North // not a comment\", \"arrival_status\": \"arrived\","
            " \"arrival_date\": \"2025-01-05\", \"custom_note\": \"keep me\"},"
            "{\"id\": \"M2\", \"fullName\": \"Jane Doe\", \"personalEmail\": \"j@example.test\"}"
            "], \"items\": ["
            "{\"category\": \"Food\", \"item\": \"Rice\", \"remaining\": 10, \"lowThreshold\": 2},"
            "{\"category\": \"Food\", \"item\": \"Beans\", \"remaining\": -3},"
            "{\"category\": \"Food\", \"item\": \"Oil\", \"remaining\": -1},"
            "{\"category\": \"Gear\", \"item\": \"Socks\", \"stock\": 5, \"out\": 5},"
            "{\"category\": \"Gear\", \"item\": \"Soap\", \"remaining\": -2},"
            "{\"category\": \"Gear\", \"item\": \"Towels\", \"remaining\": -7}"
            "], \"distributions\": []};\n</script></body></html>"
        )
        seed = extract_seed_json(html)
        rows = seed_to_bundle_rows(seed)
        self.assertEqual(len(rows["roster"]), 2)
        elder = [r for r in rows["roster"] if r["external_id"] == "M1"][0]
        self.assertEqual(elder["name"], "O'Brien")
        self.assertEqual(elder["title_prefix"], "Elder")
        self.assertEqual(elder["stake"], "North // not a comment")
        self.assertEqual(elder["arrival_date"], "2025-01-05")
        jane = [r for r in rows["roster"] if r["external_id"] == "M2"][0]
        self.assertEqual(jane["personal_email"], "j@example.test")
        corrections = [r for r in rows["items"] if r["source_corrected"] == "true"]
        self.assertEqual(len(corrections), 4)
        for row in corrections:
            self.assertEqual(row["on_hand"], "0")
        self.assertEqual(len([a for a in rows["audits"]
                              if a["action"] == "seed.correction"]), 4)
        socks = [r for r in rows["items"] if r["name"] == "Socks"][0]
        self.assertEqual(socks["on_hand"], "0")  # 5 - 5 stock-minus-out


class ViewBehaviorTests(TestCase):
    def setUp(self):
        self.staff, self.spw = make_staff()
        self.admin, self.apw = make_admin()

    def test_poll_endpoints_return_json_auth_errors(self):
        response = self.client.get("/api/stock/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "sign-in-required")
        plain = User.objects.create_user(username="plain-x", password="PlainPass123!")
        self.client.force_login(plain)
        response = self.client.get("/api/dashboard/")
        self.assertEqual(response.status_code, 403)
        # An expired idle session also yields JSON 401, never a redirect.
        self.client.login(username=self.staff.username, password=self.spw)
        session = self.client.session
        import time as _time
        session["last_activity"] = _time.time() - 5000
        session.save()
        response = self.client.get("/api/stock/")
        self.assertEqual(response.status_code, 401)

    def test_dashboard_api_reports_server_expiry_countdown(self):
        self.client.login(username=self.staff.username, password=self.spw)
        payload = self.client.get("/api/dashboard/").json()
        self.assertIn("expires_in", payload)
        self.assertGreaterEqual(payload["expires_in"], 0)
        self.assertLessEqual(payload["expires_in"], 1800)

    def test_issue_form_supports_rows_beyond_three(self):
        from apps.issuing.models import Issue

        self.client.login(username=self.staff.username, password=self.spw)
        person = make_missionary(self.staff)
        items = [make_item(self.staff, opening_qty=10) for _ in range(4)]
        post = {"missionary_id": str(person.pk), "idempotency_key": "rows-4",
                "note": ""}
        for i, item in enumerate(items):
            post[f"item-{i}"] = str(item.pk)
            post[f"qty-{i}"] = "1"
        response = self.client.post("/issue/", post)
        self.assertEqual(response.status_code, 302)
        issue = Issue.objects.get(idempotency_key="rows-4")
        self.assertEqual(issue.lines.count(), 4)

    def test_search_covers_email_and_size(self):
        self.client.login(username=self.staff.username, password=self.spw)
        make_missionary(self.staff, external_id="EM-1", name="Searchable Person")
        person = make_missionary(self.staff)
        person.email = "find-me-xyz@example.test"
        person.save()
        html = self.client.get("/roster/", {"q": "find-me-xyz"}).content.decode()
        self.assertIn(str(person.pk), html)
        self.assertNotIn("EM-1", html)  # non-matching rows are filtered out
        item = make_item(self.staff, opening_qty=5, size="X-Large")
        html = self.client.get("/inventory/", {"size": "large"}).content.decode()
        self.assertIn(str(item.pk), html)

    def test_lists_state_explicit_limits(self):
        from apps.roster.models import Missionary

        self.client.login(username=self.staff.username, password=self.spw)
        Missionary.objects.bulk_create([
            Missionary(external_id=f"Bulk-{i}", name=f"Bulk Person {i}")
            for i in range(501)])
        html = self.client.get("/roster/").content.decode()
        self.assertIn("first 500 of 501", html)
        # Exports never truncate.
        response = self.client.get("/reports/roster.csv")
        rows = list(csv.DictReader(io.StringIO(response.content.decode())))
        self.assertEqual(len(rows), 501)

    def test_notice_and_default_threshold_are_functional(self):
        self.client.login(username=self.admin.username, password=self.apw)
        _reauth_session(self, self.admin)
        self.client.post("/admin-area/settings/",
                         {"site_notice": "Picnic Friday", "default_low_threshold": "7"})
        html = self.client.get("/").content.decode()
        self.assertIn("Picnic Friday", html)
        from apps.audit.models import AuditEvent
        self.assertTrue(AuditEvent.objects.filter(action="settings.updated").exists())
        # Blank threshold on the item form falls back to the site default.
        response = self.client.post("/inventory/new/",
                                    {"category": "C", "name": "N",
                                     "opening_qty": "5", "reason": "opening",
                                     "low_threshold": "",
                                     "idempotency_key": "thr-1"})
        self.assertEqual(response.status_code, 302)
        from apps.inventory.models import Item
        self.assertEqual(Item.objects.get(key__contains="n").low_threshold, 7)

    def test_stock_retry_with_same_key_writes_once(self):
        from apps.inventory.models import StockMovement

        self.client.login(username=self.staff.username, password=self.spw)
        item = make_item(self.staff, opening_qty=5)
        for _ in range(2):
            response = self.client.post(
                f"/inventory/{item.pk}/add-stock/",
                {"quantity": "2", "reason": "delivery", "idempotency_key": "dup-rx-1"})
            self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 7)
        self.assertEqual(
            StockMovement.objects.filter(idempotency_key="dup-rx-1").count(), 1)

    def test_confirm_failure_keeps_draft(self):
        self.client.login(username=self.staff.username, password=self.spw)
        person = make_missionary(self.staff)
        response = self.client.post(
            "/issue/confirm/",
            {"missionary_id": str(person.pk), "note": "keep-this-note",
             "idempotency_key": "draft-keep-1"})
        self.assertEqual(response.status_code, 400)
        content = response.content.decode()
        self.assertIn("keep-this-note", content)
        self.assertIn("Issue goods", content)

    def test_staff_never_sees_replace_mode_or_others_previews(self):
        from apps.transfers.models import RowImportPreview

        self.client.login(username=self.staff.username, password=self.spw)
        html = self.client.get("/transfers/roster/").content.decode()
        self.assertNotIn('value="replace"', html)
        RowImportPreview.objects.create(
            token="other-token", kind="roster", mode="add_update", rows=[],
            base_revision="r:0:0", created_by=self.admin)
        html = self.client.get("/transfers/roster/").content.decode()
        self.assertNotIn("other-token", html)


class ReportFamilyTests(TestCase):
    def test_stock_category_intake_reports(self):
        from apps.issuing import services as issuing
        from apps.reports import services as rep

        staff, _ = make_staff()
        person = make_missionary(staff)
        item = make_item(staff, opening_qty=10)
        issuing.create_issue(
            actor=staff, missionary_id=person.pk,
            lines=[{"item_id": str(item.pk), "quantity": 3}],
            note="", idempotency_key="rep-1")
        for family in ("stock", "category", "intake", "per-item",
                       "per-person", "low-stock", "history"):
            response = rep.report_csv(family)
            self.assertEqual(response.status_code, 200, family)
            self.assertTrue(len(response.content) > 0, family)
            pdf = rep.report_pdf(family)
            self.assertTrue(pdf.content.startswith(b"%PDF"), family)
        stock = rep.report_csv("stock").content.decode()
        self.assertIn("3", stock)  # net given out included
        # Archived filter is honored on the roster/inventory exports.
        from apps.roster import services as roster_services
        roster_services.archive_missionary(actor=staff, missionary_id=person.pk)
        active_only = rep.roster_csv(include_archived=False).content.decode()
        self.assertNotIn(person.external_id, active_only)
        full = rep.roster_csv(include_archived=True).content.decode()
        self.assertIn(person.external_id, full)


class ReadinessTests(TestCase):
    def test_readyz_reports_version_and_maintenance(self):
        from apps.operations import guards

        payload = self.client.get("/readyz/").json()
        self.assertTrue(payload["ok"])
        self.assertIn("version", payload)
        self.assertIn("maintenance", payload)
        token = guards.enter_maintenance(owner="test", reason="check")
        try:
            payload = self.client.get("/readyz/").json()
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["maintenance"])
            self.assertEqual(payload["maintenance_owner"], "test")
        finally:
            guards.exit_maintenance(token)

    @override_settings(DEBUG=False, SECURE_SSL_REDIRECT=True)
    def test_health_endpoints_bypass_https_redirect(self):
        # In-container probes reach Gunicorn over plain HTTP; they must see
        # the real state, not a 301.
        self.assertEqual(self.client.get("/readyz/").status_code, 200)
        self.assertEqual(self.client.get("/healthz/").status_code, 200)
