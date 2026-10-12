"""Real import/export/report flows in Chromium (synthetic data, LAN-only)."""

from __future__ import annotations

import csv
import io

from apps.inventory.models import Item
from apps.roster.models import Missionary
from tests.browser.conftest import assert_no_internet, staff_login, tag
from tests.factories import make_item, make_missionary


def test_roster_import_preview_subset_confirm(page, live_server, externals):
    staff, _ = staff_login(page, live_server, "imp")
    page.goto(f"{live_server.url}/transfers/roster/")
    csv_bytes = b"external_id,name,email\nB1,Browser One,a@example.test\nB2,Browser Two,\n"
    with page.expect_file_chooser() as chooser:
        page.click("input[type=file]")
    chooser.value.set_files(
        {"name": "roster.csv", "mimeType": "text/csv", "buffer": csv_bytes})
    page.click("main button[type=submit]")
    page.wait_for_selector("text=Import preview")
    assert "B1" in page.content()
    # Uncheck the second row: only the approved subset applies.
    page.uncheck("input[name=row-3]")
    with page.expect_navigation():
        page.click("form[action$='/confirm/'] button:not([name])")
    assert "Import applied" in page.content()
    assert Missionary.objects.filter(external_id="B1").exists()
    assert not Missionary.objects.filter(external_id="B2").exists()
    assert_no_internet(externals)


def test_apply_none_writes_nothing(page, live_server, externals):
    staff, _ = staff_login(page, live_server, "none")
    page.goto(f"{live_server.url}/transfers/roster/")
    csv_bytes = b"external_id,name\nN9,Nobody Nine\n"
    with page.expect_file_chooser() as chooser:
        page.click("input[type=file]")
    chooser.value.set_files(
        {"name": "r.csv", "mimeType": "text/csv", "buffer": csv_bytes})
    page.click("main button[type=submit]")
    page.wait_for_selector("text=Import preview")
    with page.expect_navigation():
        page.click("form[action$='/confirm/'] button[value='none']")
    assert "Import applied" in page.content()
    assert "0 added" in page.content()
    assert not Missionary.objects.filter(external_id="N9").exists()
    assert_no_internet(externals)


def test_inventory_stock_minus_out_and_exports(page, live_server, externals):
    staff, _ = staff_login(page, live_server, "inv")
    item = make_item(staff, opening_qty=10)
    page.goto(f"{live_server.url}/transfers/inventory/")
    csv_bytes = (
        f"category,name,stock,out,reason\n{item.category},{item.name},12,4,recount\n"
    ).encode()
    with page.expect_file_chooser() as chooser:
        page.click("input[type=file]")
    chooser.value.set_files(
        {"name": "inv.csv", "mimeType": "text/csv", "buffer": csv_bytes})
    page.click("main button[type=submit]")
    page.wait_for_selector("text=Import preview")
    assert "recount" in page.content()
    assert "count 8" in page.content()  # stock-minus-out delta shown, not raw cells
    with page.expect_navigation():
        page.click("form[action$='/confirm/'] button:not([name])")
    item.refresh_from_db()
    assert item.on_hand == 8
    from apps.issuing.models import Issue
    assert Issue.objects.count() == 0  # never fabricates issues
    # CSV + PDF exports download real content.
    page.goto(f"{live_server.url}/reports/?family=inventory")
    with page.expect_download() as dl:
        page.click('[data-report-csv]')
    path = dl.value.path()
    rows = list(csv.DictReader(io.StringIO(open(path).read())))
    assert any(r["available"] == "8" for r in rows)
    with page.expect_download() as dl2:
        page.click('[data-report-pdf]')
    assert open(dl2.value.path(), "rb").read(4) == b"%PDF"
    assert_no_internet(externals)


def test_report_families_serve_csv_and_pdf(page, live_server, externals):
    staff_login(page, live_server, "rep")
    for family in ("per-item", "per-person", "low-stock", "stock",
                   "category", "intake", "history"):
        page.goto(f"{live_server.url}/reports/?family={family}")
        with page.expect_download() as dl:
            page.click('[data-report-csv]')
        data = open(dl.value.path(), "rb").read()
        assert len(data) > 0
        with page.expect_download() as dl2:
            page.click('[data-report-pdf]')
        assert open(dl2.value.path(), "rb").read(4) == b"%PDF"
    page.goto(f"{live_server.url}/reports/")
    assert "Available stock" in page.content()
    page.goto(f"{live_server.url}/reports/?family=intake")
    assert "Arrivals overview" in page.content()
    assert_no_internet(externals)
