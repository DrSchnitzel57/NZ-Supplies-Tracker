"""Operator maintenance-window control (used by update/restore scripts).

Entering an already-held window fails unless --force is passed; exiting
requires no token here because the script owns the whole host operation,
but a foreign token-held window is never cleared silently: --force takes
over explicitly and audits the takeover. --check reports JSON status.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.common.errors import MaintenanceActiveError
from apps.operations import guards


class Command(BaseCommand):
    help = "Enter, exit, or inspect the owned maintenance window."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--enter", action="store_true")
        group.add_argument("--exit", action="store_true")
        group.add_argument("--check", action="store_true")
        parser.add_argument("--owner", default="operator")
        parser.add_argument("--reason", default="")
        parser.add_argument("--token", default="",
                            help="Ownership token returned by --enter (for --exit)")
        parser.add_argument("--force", action="store_true",
                            help="Take over a window held by someone else (audited)")

    def handle(self, *args, **options):
        if options["check"]:
            self.stdout.write(json.dumps(guards.maintenance_status()))
            return
        if options["enter"]:
            try:
                token = guards.enter_maintenance(
                    owner=options["owner"], reason=options["reason"],
                    force=options["force"],
                    token=options["token"] or None)
            except MaintenanceActiveError as exc:
                raise CommandError(f"maintenance entry refused: {exc}")
            self.stdout.write(f"maintenance on owner={options['owner']} token={token}")
            return
        # --exit: release this host operation's window using the token from
        # --enter. Without the token (e.g. after a crashed script lost it),
        # only --force releases — via an audited takeover.
        if options["token"]:
            try:
                closed = guards.exit_maintenance(options["token"])
            except MaintenanceActiveError as exc:
                raise CommandError(f"maintenance exit refused: {exc}")
            self.stdout.write("maintenance off" if closed else "maintenance hold released")
            return
        status = guards.maintenance_status()
        if not status["active"]:
            self.stdout.write("maintenance already off")
            return
        if options["force"]:
            try:
                token = guards.enter_maintenance(
                    owner=options["owner"], reason="forced release", force=True)
            except MaintenanceActiveError as exc:
                raise CommandError(f"maintenance takeover refused: {exc}")
            guards.exit_maintenance(token)
            self.stdout.write("maintenance off (forced takeover released)")
            return
        raise CommandError(
            "refusing to clear another holder's maintenance window without --token/--force; "
            f"held by {status['owner'] or 'another operation'}")
