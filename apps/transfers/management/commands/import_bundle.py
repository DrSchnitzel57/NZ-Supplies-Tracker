"""Host installation import: load a bundle ZIP into empty operational storage."""

from __future__ import annotations

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.common.errors import DomainError
from apps.transfers import services as transfer_services


class Command(BaseCommand):
    help = "Import a v1 bundle ZIP (same pipeline as the admin UI)."

    def add_arguments(self, parser):
        parser.add_argument("bundle", help="Path to the bundle ZIP")
        parser.add_argument("--username", required=True, help="Admin username performing the import")

    def handle(self, *args, **options):
        User = get_user_model()
        try:
            actor = User.objects.get(username=options["username"])
        except User.DoesNotExist as exc:
            raise CommandError("admin user not found") from exc
        if not (actor.is_active and actor.is_superuser):
            raise CommandError("user is not an administrator")
        path = Path(options["bundle"])
        if not path.exists():
            raise CommandError(f"bundle not found: {path}")
        if path.stat().st_size > bundle_limits():
            raise CommandError("bundle is too large (limit 10 MB)")
        data = path.read_bytes()
        try:
            result = transfer_services.import_bundle(actor=actor, data=data, filename=path.name)
        except DomainError as exc:
            raise CommandError(str(exc)) from exc
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"imported bundle counts={result.get('counts', {})}")


def bundle_limits() -> int:
    return 10 * 1024 * 1024
