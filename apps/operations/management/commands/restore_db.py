"""Restore a verified backup under exclusive maintenance (fail closed).

Refuses when the backup fails integrity/schema checks, when the target
database has newer work without --allow-newer-data-loss, or when the backup
predates migrations the live code needs (schema compatibility is checked by
verifying required tables exist; image/backup pairing is enforced by the
operator passing --expected-version matching the running APP_VERSION).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.operations import backup as backup_mod
from apps.operations.guards import maintenance_mode


class Command(BaseCommand):
    help = "Restore a verified SQLite backup (maintenance-guarded)."

    def add_arguments(self, parser):
        parser.add_argument("backup", help="Verified backup file to restore")
        parser.add_argument("--allow-newer-data-loss", action="store_true",
                            help="Confirm discarding newer live work")
        parser.add_argument("--expected-version", default=None,
                            help="Require running APP_VERSION to match")

    def handle(self, *args, **options):
        from django.conf import settings

        expected = options["expected_version"]
        if expected and getattr(settings, "APP_VERSION", "") != expected:
            raise CommandError(
                "backup/image mismatch: running version does not match "
                "--expected-version; restore the matching release image first.")
        with maintenance_mode(owner="restore-db", reason=str(options["backup"])):
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
            try:
                info = backup_mod.restore_sqlite(
                    backup_path=options["backup"], dest_path=live)
            except ValueError as exc:
                raise CommandError(f"restore failed; live data untouched: {exc}")
        self.stdout.write(f"restore ok: {info['bytes']} bytes sha256={info['sha256'][:16]}…")


def _live_has_operational_rows() -> bool:
    from apps.inventory.models import Item
    from apps.issuing.models import Issue
    from apps.roster.models import Missionary

    try:
        return bool(Missionary.objects.exists() or Item.objects.exists()
                    or Issue.objects.exists())
    except Exception:
        return True  # fail closed: treat unreadable live DB as holding work
