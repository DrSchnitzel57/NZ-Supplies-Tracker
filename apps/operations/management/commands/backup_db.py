"""Create a verified host-local SQLite backup (counts only in logs)."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.operations import backup as backup_mod


class Command(BaseCommand):
    help = "Write a verified SQLite backup via the backup API (never naive copy)."

    def add_arguments(self, parser):
        parser.add_argument("--dest", default=None,
                            help="Destination file (default: backups/<stamp>.sqlite3)")
        parser.add_argument("--prefix", default="manual-")
        parser.add_argument("--quiesce", action="store_true",
                            help="Hold an owned maintenance window during the copy")

    def handle(self, *args, **options):
        dest = options["dest"]
        if not dest:
            stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
            dest = str(backup_mod.default_backup_dir() / f"{options['prefix']}{stamp}.sqlite3")
        if options["quiesce"]:
            from apps.operations.guards import maintenance_mode

            with maintenance_mode(owner="backup", reason=str(dest)):
                info = self._take(dest)
        else:
            info = self._take(dest)
        self.stdout.write(
            f"backup ok: {info['bytes']} bytes sha256={info['sha256'][:16]}… "
            f"version={info.get('app_version', '') or 'dev'} manifest={dest}.manifest.json")

    def _take(self, dest: str) -> dict:
        try:
            return backup_mod.backup_sqlite(
                source_path=backup_mod.live_db_path(), dest_path=dest)
        except ValueError as exc:
            raise CommandError(f"backup failed: {exc}")
        except Exception as exc:
            raise CommandError(f"backup failed: {exc}")
