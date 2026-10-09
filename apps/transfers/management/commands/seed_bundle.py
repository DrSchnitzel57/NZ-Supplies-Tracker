"""Build a standard v1 bundle from the local HTML prototype's embedded SEED.

Parses embedded JSON without executing the HTML. Source-negative opening
balances are preserved as correction metadata; only corrected nonnegative
balances go live, with authorized correction audit rows. Output is a
gitignored interchange ZIP. Never logs personal data: only counts.
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.transfers import bundle as bundle_mod
from apps.transfers.seed import extract_seed_json, seed_to_bundle_rows


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
        html = src.read_text(encoding="utf-8", errors="strict")
        try:
            seed = extract_seed_json(html)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        rows = seed_to_bundle_rows(seed)
        if rows["distributions"]:
            self.stderr.write(
                f"note: source has {len(rows['distributions'])} distribution rows; "
                "history is not carried into the bundle"
            )
        data = bundle_mod.build_bundle(
            source=f"seed:{src.name}",
            settings_rows=rows["settings"],
            roster_rows=rows["roster"],
            item_rows=rows["items"],
            issue_rows=rows["issues"],
            line_rows=rows["lines"],
            movement_rows=rows["movements"],
            audit_rows=rows["audits"],
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        corrections = sum(1 for r in rows["items"] if r.get("source_corrected") == "true")
        # Counts only: never log personal roster data.
        self.stdout.write(
            f"wrote {out} roster={len(rows['roster'])} items={len(rows['items'])} "
            f"corrections={corrections} distributions=0"
        )
