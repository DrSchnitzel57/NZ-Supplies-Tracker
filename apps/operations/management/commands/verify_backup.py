"""Verify a SQLite backup file (initialized Django entrypoint for scripts).

Calls backup.verify_sqlite_backup with Django settings and the app registry
initialized, so the schema/release checks that need DATABASES and the
migration loader work. Used by scripts/restore.sh before touching live data.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Verify a SQLite backup's integrity, schema and release."

    def add_arguments(self, parser):
        parser.add_argument("backup", help="Backup file to verify")

    def handle(self, *args, **options):
        from apps.operations import backup as backup_mod

        try:
            backup_mod.verify_sqlite_backup(options["backup"])
        except ValueError as exc:
            raise CommandError(f"backup invalid: {exc}")
        except Exception as exc:
            raise CommandError(f"backup invalid: {exc}")
        self.stdout.write("backup verified")
