"""Build a standard bundle from the local HTML prototype's embedded SEED.

Parses embedded JSON without executing the HTML. Source-negative opening
balances are preserved as correction metadata; only corrected nonnegative
balances go live, with authorized correction audit rows. Output is a
gitignored interchange ZIP. Never logs personal data: only counts.
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.transfers.seed import html_to_bundle


class Command(BaseCommand):
    help = "Convert the local HTML prototype SEED into a standard bundle ZIP."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="Path to the local HTML file")
        parser.add_argument("--output", required=True, help="Destination bundle ZIP path")

    def handle(self, *args, **options):
        src = Path(options["source"])
        out = Path(options["output"])
        if not src.exists():
            raise CommandError(f"source file not found: {src}")
        if src.stat().st_size > 50 * 1024 * 1024:
            raise CommandError("source file is too large (limit 50 MB)")
        try:
            html = src.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CommandError(
                "source HTML must be UTF-8 encoded; re-save it as UTF-8 and retry"
            ) from exc
        try:
            data, counts = html_to_bundle(html, source=f"seed:{src.name}")
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if counts["distributions"]:
            self.stderr.write(
                f"note: source has {counts['distributions']} distribution rows; "
                "history is not carried into the bundle"
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        # Counts only: never log personal roster data.
        self.stdout.write(
            f"wrote {out} roster={counts['roster']} items={counts['items']} "
            f"corrections={counts['corrections']} distributions=0"
        )
