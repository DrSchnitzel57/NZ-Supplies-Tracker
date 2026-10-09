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

    def handle(self, *args, **options):
        dest = options["dest"]
        if not dest:
            stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
            dest = str(backup_mod.default_backup_dir() / f"{options['prefix']}{stamp}.sqlite3")
        try:
            info = backup_mod.backup_sqlite(
                source_path=backup_mod.live_db_path(), dest_path=dest)
        except ValueError as exc:
            raise CommandError(f"backup failed: {exc}")
        self.stdout.write(f"backup ok: {info['bytes']} bytes sha256={info['sha256'][:16]}…")
