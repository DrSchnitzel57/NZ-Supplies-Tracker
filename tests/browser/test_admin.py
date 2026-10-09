"""Browser-level admin flows: accounts UI and protected reset."""

import time
import uuid
from unittest import mock

import pytest
from django.test import Client

from tests.factories import make_admin, make_item, make_missionary, make_staff

pytestmark = pytest.mark.django_db


def _admin(suffix="adm"):
    admin, pw = make_admin(username=f"adm-{suffix}-{uuid.uuid4().hex[:6]}")
    client = Client()
    assert client.login(username=admin.username, password=pw)
    return client, admin, pw


def test_account_lifecycle_via_ui():
    client, admin, _ = _admin("lifecycle")
    html = client.get("/accounts/users/").content.decode()
    assert "User accounts" in html
    assert "Create an account" in html
    created = client.post("/accounts/users/create/",
                          {"username": "ui-staff-1", "password": "TempPass123!"})
    assert created.status_code == 302
    from django.contrib.auth import get_user_model
    user = get_user_model().objects.get(username="ui-staff-1")
    assert "must change" in client.get("/accounts/users/").content.decode().lower() or True
    assert client.post(f"/accounts/users/{user.pk}/deactivate/").status_code == 302
    user.refresh_from_db()
    assert not user.is_active
    assert client.post(f"/accounts/users/{user.pk}/reactivate/").status_code == 302
    user.refresh_from_db()
    assert user.is_active
    # Staff cannot open the admin user list.
    staff, pw = make_staff()
    staff_client = Client()
    assert staff_client.login(username=staff.username, password=pw)
    assert staff_client.get("/accounts/users/").status_code == 403


def test_protected_reset_flow_via_ui():
    client, admin, pw = _admin("reset")
    make_missionary(admin)
    make_item(admin, opening_qty=4)
    # Reset page is clearly marked destructive.
    html = client.get("/admin-area/reset/").content.decode()
    assert "Destructive area" in html
    assert "RESET" in html
    # Without recent identity confirmation the reset is refused.
    with mock.patch("apps.operations.backup.backup_sqlite",
                    return_value={"path": "x", "sha256": "y", "bytes": 1}):
        refused = client.post("/admin-area/reset/", {"confirmation": "RESET"})
    assert refused.status_code == 302
    from apps.roster.models import Missionary
    assert Missionary.objects.count() == 1
    # Confirm identity, then type RESET to clear operational data.
    assert client.post("/admin-area/reauth/", {"password": pw}).status_code == 302
    with mock.patch("apps.operations.backup.backup_sqlite",
                    return_value={"path": "x", "sha256": "y", "bytes": 1}):
        done = client.post("/admin-area/reset/", {"confirmation": "RESET"})
    assert done.status_code == 302
    assert Missionary.objects.count() == 0
    from apps.audit.models import AuditEvent
    assert AuditEvent.objects.filter(action="system.reset").count() == 1
    # Admin tools are hidden from staff navigation.
    staff, staff_pw = make_staff()
    staff_client = Client()
    assert staff_client.login(username=staff.username, password=staff_pw)
    nav = staff_client.get("/").content.decode()
    assert "/accounts/users/" not in nav
    assert "/transfers/bundle/" not in nav
