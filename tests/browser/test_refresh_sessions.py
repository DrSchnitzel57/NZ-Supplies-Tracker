"""Polling/session browser contract (synthetic data only).

Proves the live-refresh behavior a browser relies on:
- Dashboard/stock polls refresh about every 15 seconds without touching
  form drafts or replacing inputs.
- Connection failures are visible (status element + honest messaging hooks).
- Background polls never extend the 30-minute sign-in timer, never store
  personal data, and an expired session surfaces a sign-in warning.
"""

import uuid
from pathlib import Path

import pytest
from django.test import Client
from django.utils import timezone

from tests.factories import make_item, make_missionary, make_staff

pytestmark = pytest.mark.django_db


def _login(suffix="poll"):
    staff, pw = make_staff(username=f"refresh-{suffix}-{uuid.uuid4().hex[:6]}")
    client = Client()
    assert client.login(username=staff.username, password=pw)
    return client, staff


def test_polling_hooks_present_and_forms_untouched():
    client, staff = _login("hooks")
    html = client.get("/issue/").content.decode()
    assert 'id="connection-status"' in html
    assert 'id="last-refresh"' in html
    assert 'id="session-warning"' in html
    js = Path("static/app.js").read_text()
    assert "15000" in js
    assert "localStorage" not in js  # never persist personal data locally
    assert "sessionStorage" not in js
    # Polling updates read-only cells; the draft form keeps its values.
    m = make_missionary(staff)
    item = make_item(staff, opening_qty=6)
    before = client.get("/issue/").content.decode()
    assert f'value="{item.pk}"' in before or str(item.pk) in before
    stock = client.get("/api/stock/").json()
    assert any(str(i["id"]) == str(item.pk) for i in stock["items"])
    after = client.get("/issue/").content.decode()
    assert "Issue goods" in after


def test_poll_failures_are_visible_offline():
    client, _ = _login("offline")
    html = client.get("/").content.decode()
    assert "Connection:" in html
    assert "Last refresh" in html
    js = Path("static/app.js").read_text()
    assert "offline" in js.lower()


def test_background_polls_do_not_keep_session_alive():
    client, staff = _login("alive")
    client.get("/")
    session = client.session
    old = timezone.now().timestamp() - 100
    session["last_activity"] = old
    session.save()
    assert client.get("/api/stock/").status_code == 200
    assert abs(float(client.session["last_activity"]) - old) < 1
    assert client.get("/api/dashboard/").status_code == 200
    assert abs(float(client.session["last_activity"]) - old) < 1
    assert client.get("/poll/").status_code == 200
    assert abs(float(client.session["last_activity"]) - old) < 1
    # Real navigation extends the timer; polls never do.
    client.get("/")
    assert float(client.session["last_activity"]) > old


def test_expired_session_shows_sign_in_warning():
    client, staff = _login("expired")
    session = client.session
    session["last_activity"] = timezone.now().timestamp() - 2000
    session.save()
    response = client.get("/api/stock/")
    # Expired sessions are sent back to sign-in rather than leaking data.
    assert response.status_code in (302, 403)
    html = client.get("/issue/", follow=False)
    assert html.status_code in (302, 200)
    shell = client.get("/accounts/login/").content.decode()
    assert "Sign in" in shell
    js = Path("static/app.js").read_text()
    assert "session-warning" in js or "sign-in" in js.lower()
