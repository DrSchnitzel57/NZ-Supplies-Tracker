"""Seed-bundle converter cases using synthetic equivalent seeds only."""

import io
import json
import zipfile

from django.core.management import call_command
from django.test import TestCase

from apps.transfers import bundle as bundle_mod
from apps.transfers.seed import extract_seed_json, normalize_seed, seed_to_bundle_rows
from tests.factories import make_admin


def _synthetic_html(*, negatives=2, roster_n=5, items_n=6) -> str:
    roster = [{"id": f"SYN-{i}", "name": f"Synthetic Person {i}",
               "email": f"syn-{i}@example.test"} for i in range(roster_n)]
    items = []
    for i in range(items_n):
        remaining = -(i + 1) if i < negatives else (10 + i)
        items.append({"category": "Cat", "name": f"Syn Item {i}",
                      "size": "", "remaining": remaining})
    seed = {"missionaries": roster, "items": items, "distributions": []}
    return f"<html><head><script>const SEED = {json.dumps(seed)};</script></head></html>"


class SeedConversionTests(TestCase):
    def test_extract_and_normalize_synthetic_shapes(self):
        html = _synthetic_html()
        seed = extract_seed_json(html)
        roster, items, dists = normalize_seed(seed)
        self.assertEqual(len(roster), 5)
        self.assertEqual(len(items), 6)
        self.assertEqual(dists, [])
        # Alternate key spellings.
        alt = {"people": [{"extId": "A1", "fullName": "Alt One"}],
               "inventory": [{"cat": "C", "item": "I1", "stock": 4}],
               "history": []}
        roster2, items2, _ = normalize_seed(alt)
        self.assertEqual(roster2[0]["external_id"], "A1")
        self.assertEqual(items2[0]["remaining"], 4)

    def test_source_negatives_become_correction_metadata(self):
        rows = seed_to_bundle_rows(extract_seed_json(_synthetic_html()))
        corrected = [r for r in rows["items"] if r["source_corrected"] == "true"]
        self.assertEqual(len(corrected), 2)
        for row in rows["items"]:
            self.assertGreaterEqual(int(row["on_hand"]), 0)
        for row in corrected:
            self.assertTrue(int(row["source_remaining"]) < 0)
        self.assertEqual(len(rows["audits"]), 2)
        self.assertTrue(all("seed.correction" in json.dumps(a) for a in rows["audits"]))
        self.assertEqual(rows["distributions"], [])

    def test_seed_bundle_command_writes_valid_bundle(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "source.html"
            out = Path(tmp) / "initial.zip"
            src.write_text(_synthetic_html(negatives=4, roster_n=9, items_n=12), encoding="utf-8")
            with io.StringIO() as buf:
                call_command("seed_bundle", source=str(src), output=str(out), stdout=buf)
                output = buf.getvalue()
            self.assertTrue(out.exists())
            # Counts only in logs: no personal data leaked.
            self.assertIn("roster=9", output)
            self.assertIn("corrections=4", output)
            self.assertNotIn("Synthetic Person", output)
            parsed = bundle_mod.parse_bundle(out.read_bytes())
            self.assertEqual(parsed["manifest"]["counts"]["roster"], 9)
            self.assertEqual(parsed["manifest"]["counts"]["items"], 12)
            live = [int(r["on_hand"]) for r in parsed["tables"]["items.csv"]]
            self.assertTrue(all(v >= 0 for v in live))
            corrections = [r for r in parsed["tables"]["items.csv"]
                           if r["source_corrected"] == "true"]
            self.assertEqual(len(corrections), 4)

    def test_import_of_seed_bundle_records_corrections(self):
        import tempfile
        from pathlib import Path

        from apps.audit.models import AuditEvent
        from apps.inventory.models import Item

        admin, _ = make_admin()
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "s.html"
            out = Path(tmp) / "b.zip"
            src.write_text(_synthetic_html(negatives=1, roster_n=2, items_n=2), encoding="utf-8")
            call_command("seed_bundle", source=str(src), output=str(out))
            from apps.transfers import services as transfers
            transfers.import_bundle(actor=admin, data=out.read_bytes())
        self.assertTrue(all(i.on_hand >= 0 for i in Item.objects.all()))
        self.assertTrue(AuditEvent.objects.filter(action="seed.correction").exists())
