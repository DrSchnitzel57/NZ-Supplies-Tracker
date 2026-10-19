"""Full-bundle and spreadsheet-import cases (synthetic data only)."""

import io
import json
import uuid
import zipfile

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.common.errors import StaleVersionError, ValidationError
from apps.inventory.models import Item
from apps.roster.models import Missionary
from apps.transfers import bundle as bundle_mod
from apps.transfers import services as transfers
from apps.transfers.models import RowImportPreview
from apps.transfers.row_imports import build_preview, current_revision, new_preview_token, read_table
from tests.factories import make_admin, make_item, make_missionary, make_staff

User = get_user_model()


def _csv_bytes(rows, header):
    import csv

    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


class BundleRoundTripTests(TestCase):
    def setUp(self):
        self.admin, _ = make_admin()

    def test_export_import_round_trip_preserves_counts(self):
        m = make_missionary(self.admin)
        item = make_item(self.admin, opening_qty=7)
        data = transfers.export_bundle(actor=self.admin)
        parsed = bundle_mod.parse_bundle(data)
        # v2 added roster departure_date; v1 bundles still import (see below).
        self.assertEqual(parsed["manifest"]["version"], 2)
        self.assertEqual(parsed["manifest"]["counts"]["roster"], 1)
        # Import into a fresh empty DB: wipe operational tables first.
        from apps.audit.models import AuditEvent
        from apps.inventory.models import StockMovement
        from apps.issuing.models import Issue
        StockMovement.objects.all().delete()
        Issue.objects.all().delete()
        Item.objects.all().delete()
        Missionary.objects.all().delete()
        AuditEvent.objects.all().delete()
        result = transfers.import_bundle(actor=self.admin, data=data)
        self.assertEqual(result["counts"]["roster"], 1)
        self.assertEqual(Missionary.objects.count(), 1)
        self.assertEqual(Item.objects.count(), 1)
        restored = Item.objects.get()
        self.assertEqual(restored.on_hand, 7)
        self.assertTrue(User.objects.filter(username=self.admin.username).exists())

    def test_version_1_bundle_without_departure_column_still_imports(self):
        """Bundles made before departure_date existed stay importable."""
        import io
        import json
        import zipfile

        make_missionary(self.admin)
        make_item(self.admin, opening_qty=3)
        data = transfers.export_bundle(actor=self.admin)
        src = zipfile.ZipFile(io.BytesIO(data))
        manifest = json.loads(src.read("manifest.json"))
        tables = {name: src.read(name) for name in bundle_mod.TABLE_FILES}
        rows = bundle_mod.parse_bundle(data)["tables"]["roster.csv"]
        tables["roster.csv"] = bundle_mod._csv_bytes(bundle_mod.ROSTER_FIELDS_V1, rows)
        manifest["version"] = 1
        manifest["checksums"] = {n: bundle_mod._sha(raw) for n, raw in tables.items()}
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            for name, raw in tables.items():
                zf.writestr(name, raw)
        parsed = bundle_mod.parse_bundle(out.getvalue())
        self.assertEqual(parsed["tables"]["roster.csv"][0]["departure_date"], "")
        from apps.audit.models import AuditEvent
        from apps.inventory.models import StockMovement
        StockMovement.objects.all().delete()
        Item.objects.all().delete()
        Missionary.objects.all().delete()
        AuditEvent.objects.all().delete()
        transfers.import_bundle(actor=self.admin, data=out.getvalue())
        self.assertEqual(Missionary.objects.count(), 1)
        self.assertIsNone(Missionary.objects.get().departure_date)

    def test_non_admin_cannot_export_or_import(self):
        staff, _ = make_staff()
        with self.assertRaises(Exception):
            transfers.export_bundle(actor=staff)
        data = transfers.export_bundle(actor=self.admin)
        with self.assertRaises(Exception):
            transfers.import_bundle(actor=staff, data=data)

    def test_import_requires_empty_operational_data(self):
        make_missionary(self.admin)
        data = transfers.export_bundle(actor=self.admin)
        with self.assertRaises(ValidationError):
            transfers.import_bundle(actor=self.admin, data=data)
        self.assertEqual(Missionary.objects.count(), 1)

    def test_bad_checksum_rejected_atomically(self):
        make_missionary(self.admin)
        data = transfers.export_bundle(actor=self.admin)
        raw = bytearray(data)
        # Corrupt the manifest checksum for roster.csv.
        zf_in = zipfile.ZipFile(io.BytesIO(data))
        manifest = json.loads(zf_in.read("manifest.json"))
        manifest["checksums"]["roster.csv"] = "0" * 64
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name in zf_in.namelist():
                if name == "manifest.json":
                    zf.writestr(name, json.dumps(manifest).encode())
                else:
                    zf.writestr(name, zf_in.read(name))
        bad = buf.getvalue()
        from apps.audit.models import AuditEvent
        from apps.inventory.models import StockMovement
        from apps.issuing.models import Issue
        StockMovement.objects.all().delete()
        Issue.objects.all().delete()
        Item.objects.all().delete()
        Missionary.objects.all().delete()
        AuditEvent.objects.all().delete()
        with self.assertRaises(ValueError):
            transfers.import_bundle(actor=self.admin, data=bad)
        self.assertEqual(Missionary.objects.count(), 0)

    def test_unsupported_version_rejected(self):
        data = transfers.export_bundle(actor=self.admin)
        zf_in = zipfile.ZipFile(io.BytesIO(data))
        manifest = json.loads(zf_in.read("manifest.json"))
        manifest["version"] = 999
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name in zf_in.namelist():
                zf.writestr(name, json.dumps(manifest).encode() if name == "manifest.json" else zf_in.read(name))
        with self.assertRaises(ValueError):
            bundle_mod.parse_bundle(buf.getvalue())

    def test_safe_encoding_round_trip(self):
        self.assertEqual(bundle_mod.safe_decode(bundle_mod.safe_encode("=1+1")), "=1+1")
        self.assertEqual(bundle_mod.safe_encode("plain"), "plain")


class UploadBoundTests(TestCase):
    def test_oversize_rejected(self):
        with self.assertRaises(ValueError):
            read_table(filename="a.csv", content=b"x" * (2 * 1024 * 1024 + 1))

    def test_macro_and_legacy_formats_rejected(self):
        for name in ["a.xlsm", "a.xls", "a.xlsb", "a.zip"]:
            with self.assertRaises(ValueError, msg=name):
                read_table(filename=name, content=b"a,b\n1,2\n")

    def test_too_many_rows_rejected(self):
        header = "category,name,remaining\n"
        body = "".join(f"c{i},n{i},1\n" for i in range(2001))
        with self.assertRaises(ValueError):
            read_table(filename="a.csv", content=(header + body).encode())

    def test_formula_cells_are_row_errors_never_evaluated(self):
        content = b"external_id,name\nE1,=1+1\n"
        rows = read_table(filename="a.csv", content=content)
        preview = build_preview(kind="roster", rows=rows, mode="add_update")
        self.assertEqual(len(preview["errors"]), 1)
        self.assertIn("formula", preview["errors"][0]["problems"][0].lower())

    def test_plain_xlsx_first_worksheet_read_with_aliases(self):
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["missionary id", "full name"])
        ws.append(["X-1", "Xlsx Person"])
        buf = io.BytesIO()
        wb.save(buf)
        rows = read_table(filename="roster.xlsx", content=buf.getvalue())
        preview = build_preview(kind="roster", rows=rows, mode="add_update")
        self.assertEqual(len(preview["valid"]), 1)
        self.assertEqual(preview["valid"][0]["external_id"], "X-1")


class RowImportTests(TestCase):
    def setUp(self):
        self.staff, _ = make_staff()
        self.admin, _ = make_admin()

    def _make_preview(self, kind, rows, mode="add_update", actor=None):
        preview = build_preview(kind=kind, rows=rows, mode=mode)
        stored = [{**e, "error": False} for e in preview["valid"]]
        stored += [{**e["entry"], "error": True, "problems": e["problems"]} for e in preview["errors"]]
        return RowImportPreview.objects.create(
            token=new_preview_token(), kind=kind, mode=mode, rows=stored,
            base_revision=current_revision(kind), created_by=actor or self.staff)

    def test_roster_add_update_and_partial_subset(self):
        rows = [["external_id", "name", "email"],
                ["E1", "Person One", "a@example.test"],
                ["E2", "Person Two", ""],
                ["", "Missing ID", ""]]
        preview = self._make_preview("roster", rows)
        # Only approve row 2 (valid subset allowed for add/update).
        result = transfers.confirm_row_import(actor=self.staff, preview=preview, selected=[2])
        self.assertEqual(result["created"], 1)
        self.assertTrue(Missionary.objects.filter(external_id="E1").exists())
        self.assertFalse(Missionary.objects.filter(external_id="E2").exists())

    def test_stale_preview_rejected(self):
        rows = [["external_id", "name"], ["E9", "Person Nine"]]
        preview = self._make_preview("roster", rows)
        make_missionary(self.staff)  # changes revision after preview
        with self.assertRaises(StaleVersionError):
            transfers.confirm_row_import(actor=self.staff, preview=preview, selected=None)

    def test_inventory_stock_minus_out_becomes_count_adjustment(self):
        item = make_item(self.staff, opening_qty=10)
        rows = [["category", "item", "stock", "out", "reason"],
                [item.category, item.name, "12", "4", "recount"]]
        preview = self._make_preview("inventory", rows)
        result = transfers.confirm_row_import(actor=self.staff, preview=preview, selected=None)
        self.assertEqual(result["adjusted"], 1)
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 8)  # 12 - 4
        from apps.issuing.models import Issue
        self.assertEqual(Issue.objects.count(), 0)  # never fabricates issues

    def test_negative_counts_rejected(self):
        rows = [["category", "name", "remaining"], ["Cat", "Thing", "-3"]]
        preview_obj = build_preview(kind="inventory", rows=rows, mode="add_update")
        self.assertEqual(len(preview_obj["errors"]), 1)

    def test_replacement_requires_all_valid_and_admin(self):
        make_missionary(self.staff, external_id="KEEP-1", name="Keep Me")
        rows = [["external_id", "name"], ["NEW-1", "New Person"], ["", "Bad Row"]]
        preview = self._make_preview("roster", rows, mode="replace", actor=self.admin)
        with self.assertRaises(ValidationError):
            transfers.confirm_row_import(actor=self.admin, preview=preview, selected=None)
        self.assertTrue(Missionary.objects.filter(external_id="KEEP-1").exists())
        # Staff cannot run replacement even when valid.
        rows2 = [["external_id", "name"], ["NEW-2", "New Two"]]
        preview2 = self._make_preview("roster", rows2, mode="replace")
        with self.assertRaises(Exception):
            transfers.confirm_row_import(actor=self.staff, preview=preview2, selected=None)

    def test_replacement_archives_missing_records(self):
        keep = make_missionary(self.admin, external_id="KEEP-9", name="Keep Nine")
        gone = make_missionary(self.admin, external_id="GONE-9", name="Gone Nine")
        rows = [["external_id", "name"], ["KEEP-9", "Keep Nine"]]
        preview = self._make_preview("roster", rows, mode="replace", actor=self.admin)
        result = transfers.confirm_row_import(actor=self.admin, preview=preview, selected=None)
        self.assertEqual(result["archived"], 1)
        gone.refresh_from_db()
        self.assertTrue(gone.is_archived)
        keep.refresh_from_db()
        self.assertFalse(keep.is_archived)

    def test_header_aliases_accepted(self):
        rows = [["missionary id", "full name", "e-mail"], ["AL-1", "Alias Person", "x@example.test"]]
        preview = build_preview(kind="roster", rows=rows, mode="add_update")
        self.assertEqual(len(preview["valid"]), 1)
