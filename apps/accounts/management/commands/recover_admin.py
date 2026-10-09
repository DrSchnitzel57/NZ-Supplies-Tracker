"""Host admin recovery command.

Interactive only: the password is read with getpass, never taken as a CLI
argument and never written to logs. Creates or promotes the named user to
administrator and forces a temporary-password change on next login.
"""

from __future__ import annotations

import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


class Command(BaseCommand):
    help = "Recover host administrator access (interactive; never logs credentials)."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="Admin username")
        parser.add_argument(
            "--create",
            action="store_true",
            help="Create the user if it does not exist.",
        )

    def handle(self, *args, **options):
        username = options["username"].strip()
        if not username:
            raise CommandError("username is required")
        try:
            user = User.objects.get(username=username)
            created = False
        except User.DoesNotExist:
            if not options["create"]:
                raise CommandError(f"user {username!r} does not exist (use --create)")
            user = User(username=username)
            created = True
        # Interactive prompt only; password never appears in argv or logs.
        temporary = getpass.getpass("Temporary password: ")
        confirm = getpass.getpass("Confirm temporary password: ")
        if not temporary:
            raise CommandError("password may not be empty")
        if temporary != confirm:
            raise CommandError("passwords do not match")
        user.set_password(temporary)
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.must_change_password = True
        user.password_changed_at = None
        user.save()
        # Invalidate existing sessions without logging secrets.
        from apps.accounts.lifecycle import invalidate_sessions

        invalidate_sessions(user)
        if created:
            self.stdout.write(f"Created administrator {username!r}.")
        else:
            self.stdout.write(f"Recovered administrator {username!r}.")
        self.stdout.write("Temporary password must be changed on next login.")
