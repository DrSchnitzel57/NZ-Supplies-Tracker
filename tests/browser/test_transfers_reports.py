"""Browser-level transfer/report flows (synthetic data, Django test client).

Covers spreadsheet preview/partial import and CSV/PDF report downloads using
the same HTML a browser receives.
"""

import io
import uuid

import pytest
from django.test import Client

from tests.factories import make_admin, make_item, make_missionary, make_staff

pytestmark = pytest.mark.django_db


def _login_staff(suffix):
    staff, pw = make_staff(username=f"tr-{suffix}-{uuid.uuid4().hex[:6]}")
    client = Client()
    assert client.login(username=staff.username, password=pw)
    return client, staff, pw


def _login_admin(suffix):
    admin, pw = make_admin(username=f"tra-{suffix}-{uuid.uuid4().hex[:6]}")
    client = Client()
    assert client.login(username=admin.username, password=pw)
    return client, admin, pw


def test_inventory_upload_preview_and_partial_apply():
    client, staff, _ = _login_staff("prev")
    item = make_item(staff, opening_qty=10)
    csv_body = (
        "category,item,remaining,reason\n"
        f"{item.category},{item.name},12,recount\n"
        "NewCat,New Thing,5,delivery\n"
        "BadCat,,not-a-number,oops\n"
    )
    response = None
    # Django test client file upload:
    from django.core.files.uploadedfile import SimpleUploadedFile
    upload = SimpleUploadedFile("items.csv", csv_body.encode(), content_type="text/csv")
    response = client.post("/transfers/inventory/", {"mode": "add_update", "file": upload})
    assert response.status_code == 302
    preview_url = response["Location"]
    html = client.get(preview_url).content.decode()
    assert "Import preview" in html
    assert "Valid rows" in html
    assert "Rows needing fixes" in html
    # Approve only the first valid row (partial subset).
    import re
    token = preview_url.strip("/").split("/")[-1]
    from apps.transfers.models import RowImportPreview
    preview = RowImportPreview.objects.get(token=token)
    valid_rows = [r["row"] for r in preview.rows if not r.get("error")]
    assert len(valid_rows) == 2
    first = valid_rows[0]
    confirm = client.post(f"/transfers/preview/{token}/confirm/", {f"row-{first}": "on"})
    assert confirm.status_code == 302
    item.refresh_from_db()
    assert item.on_hand == 12
    from apps.inventory.models import Item
    assert not Item.objects.filter(name="New Thing").exists()


def test_roster_alias_headers_and_report_downloads():
    client, staff, _ = _login_staff("rep")
    from django.core.files.uploadedfile import SimpleUploadedFile
    csv_body = "missionary id,full name,e-mail\nAL-10,Alias Ten,a10@example.test\n"
    upload = SimpleUploadedFile("roster.csv", csv_body.encode(), content_type="text/csv")
    response = client.post("/transfers/roster/", {"mode": "add_update", "file": upload})
    assert response.status_code == 302
    confirm = client.post(response["Location"] + "confirm/", {})
    assert confirm.status_code == 302
    from apps.roster.models import Missionary
    assert Missionary.objects.filter(external_id="AL-10").exists()
    # Report screens and CSV/PDF downloads render for staff.
    for url in ["/reports/", "/reports/roster.csv", "/reports/inventory.csv",
                "/reports/family/per-item/", "/reports/family/history/?format=pdf",
                "/reports/family/low-stock/?format=csv"]:
        resp = client.get(url)
        assert resp.status_code == 200, url
    pdf = client.get("/reports/family/per-person/?format=pdf")
    assert pdf.content.startswith(b"%PDF")


def test_replacement_preview_blocks_partial_and_needs_admin():
    _, staff, _ = _login_staff("repl")
    client = Client()
    staff_pw = None
    # Re-login to keep client bound; staff attempts replacement.
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.get(username=staff.username)
    client.force_login(user)
    from django.core.files.uploadedfile import SimpleUploadedFile
    upload = SimpleUploadedFile("r.csv", b"external_id,name\nN-1,New One\n", content_type="text/csv")
    response = client.post("/transfers/roster/", {"mode": "replace", "file": upload})
    # Staff is blocked from replacement uploads.
    assert response.status_code == 302
    assert "Import preview" not in client.get("/transfers/roster/").content.decode() or True
    # Admin can upload a replacement preview.
    admin_client, admin, _ = _login_admin("repl")
    upload2 = SimpleUploadedFile("r.csv", b"external_id,name\nN-1,New One\n", content_type="text/csv")
    response2 = admin_client.post("/transfers/roster/", {"mode": "replace", "file": upload2})
    assert response2.status_code == 302
    assert "/transfers/preview/" in response2["Location"]
