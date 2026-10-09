"""Restore a verified backup under exclusive maintenance (fail closed).

Refuses when the backup fails integrity/schema/release checks, when the
target database has newer work without --allow-newer-data-loss, or when the
backup was created by newer code than is running here. The order is always
verify, restore, re-acquire, migrate, readiness — and only then may the
caller reopen writes with the printed token.

Why re-acquire: restoring a database file replaces the maintenance row
itself, so any window held before the restore no longer exists afterwards.
The command therefore acquires a fresh window after restoring (force-taking
over a stale flag carried inside the backup, audited) and LEAVES it held:
callers release it with ``maintenance --exit --token <printed-token>`` only
after their own readiness gate. On any failure the current window stays
held and the token is printed for deliberate operator recovery.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.operations import backup as backup_mod


class Command(BaseCommand):
    help = "Restore a verified SQLite backup (maintenance-guarded)."

    def add_arguments(self, parser):
        parser.add_argument("backup", help="Verified backup file to restore")
        parser.add_argument("--allow-newer-data-loss", action="store_true",
                            help="Confirm discarding newer live work")
        parser.add_argument("--expected-version", default=None,
                            help="Require running APP_VERSION to match")
        parser.add_argument("--skip-migrate", action="store_true",
                            help="Do not run migrations after restore (testing only)")
        parser.add_argument("--owner", default="restore-db",
                            help="Window owner (must match the outer holder to nest)")
        parser.add_argument("--token", default=None,
                            help="Outer window token for nesting under a script-held window")
        parser.add_argument("--force-takeover", action="store_true",
                            help="Explicit audited takeover of a window held by a "
                                 "failed update (writes stay closed throughout)")

    def handle(self, *args, **options):
        from django.conf import settings

        from apps.common.errors import MaintenanceActiveError
        from apps.operations import guards

        expected = options["expected_version"]
        if expected and getattr(settings, "APP_VERSION", "") != expected:
            raise CommandError(
                "backup/image mismatch: running version does not match "
                "--expected-version; restore the matching release image first.")
        owner = options["owner"]
        reason = str(options["backup"])
        # The pre-restore window is entered directly (not via the
        # contextmanager): the restore below replaces the maintenance row,
        # so this hold is deliberately abandoned and superseded by the
        # post-restore re-acquire.
        try:
            token = guards.enter_maintenance(owner=owner, reason=reason,
                                             token=options["token"],
                                             force=bool(options["force_takeover"]))
        except MaintenanceActiveError as exc:
            raise CommandError(
                f"restore refused: {exc}; "
                "re-run with --force-takeover for an explicit audited takeover "
                "(writes stay closed) after stopping writers")
        try:
            try:
                backup_mod.verify_sqlite_backup(options["backup"])
            except ValueError as exc:
                raise CommandError(f"restore refused (backup invalid): {exc}")
            live = backup_mod.live_db_path()
            if live.exists() and not options["allow_newer_data_loss"]:
                # Any live operational rows newer than an empty check count as
                # "newer work": require explicit confirmation to discard.
                if _live_has_operational_rows():
                    raise CommandError(
                        "restore refused: live database holds newer work; "
                        "re-run with --allow-newer-data-loss to confirm discard.")
        except BaseException:
            # Refused before anything was written: reopen the window.
            guards.exit_maintenance(token)
            raise
        try:
            info = backup_mod.restore_sqlite(
                backup_path=options["backup"], dest_path=live)
        except Exception as exc:
            # Staged restores never swap on failure, so the flag is intact.
            guards.exit_maintenance(token)
            raise CommandError(f"restore failed; live data preserved: {exc}")
        # The restore replaced the maintenance row: re-acquire a fresh
        # window (the backup may even carry a stale held flag, which is
        # taken over explicitly and audited — no live process can own a
        # window across a file restore).
        try:
            token = guards.enter_maintenance(owner=owner,
                                             reason=reason + " (post-restore)")
        except MaintenanceActiveError:
            token = guards.enter_maintenance(
                owner=owner, reason=reason + " (post-restore takeover)",
                force=True)
        try:
            if not options["skip_migrate"]:
                from django.core.management import call_command

                call_command("migrate", "--noinput")
                _assert_ready()
        except BaseException:
            # The current window stays held; print its token for deliberate
            # operator recovery, then re-raise the original failure.
            self.stdout.write(f"maintenance-token: {token}")
            raise
        self.stdout.write(
            f"restore ok: {info['bytes']} bytes sha256={info['sha256'][:16]}…")
        self.stdout.write(f"maintenance-token: {token}")
        return


def _assert_ready() -> None:
    from django.db import connection

    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    except Exception as exc:
        raise CommandError(f"restored database is not ready: {exc}")
    if plan:
        raise CommandError("restored database still has pending migrations; writes stay closed")


def _live_has_operational_rows() -> bool:
    from apps.audit.models import AuditEvent
    from apps.inventory.models import Item, StockMovement
    from apps.issuing.models import Issue, IssueLine
    from apps.operations.models import SystemSetting
    from apps.roster.models import Missionary

    try:
        return bool(Missionary.objects.exists() or Item.objects.exists()
                    or Issue.objects.exists() or IssueLine.objects.exists()
                    or StockMovement.objects.exists() or AuditEvent.objects.exists()
                    or SystemSetting.objects.exists())
    except Exception:
        return True  # fail closed: treat unreadable live DB as holding work
