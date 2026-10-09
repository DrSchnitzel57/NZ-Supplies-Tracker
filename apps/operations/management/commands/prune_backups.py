"""Prune backups older than retention, protecting the recovery backup."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.operations import backup as backup_mod


class Command(BaseCommand):
    help = "Delete backups older than 30 days; keep the recovery backup."

    def add_arguments(self, parser):
        parser.add_argument("--dir", default=None)
        parser.add_argument("--days", type=int, default=backup_mod.RETENTION_DAYS)

    def handle(self, *args, **options):
        d = options["dir"] or str(backup_mod.default_backup_dir())
        result = backup_mod.prune_backups(backup_dir=d, retention_days=options["days"])
        self.stdout.write(
            f"prune ok: kept={len(result['kept'])} "
            f"deleted={len(result['deleted'])} protected={result['protected']}")
