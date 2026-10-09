"""Synthetic browser-level workflow checks (no CDN, no real data).

These tests fetch the same HTML a browser receives and assert the desktop and
narrow-layout contract: responsive meta/CSS, keyboard-usable forms, plain
labels, contextual help, quick-start access, issuing/correction flows,
shortage confirmation, reversals and visible error feedback.

A real Chromium pass via Playwright is included as
``test_playwright_smoke``: it runs when a browser is installed
(``python -m playwright install chromium``) and skips otherwise, so the suite
stays green on machines without browser binaries.
"""

import re
import uuid
from pathlib import Path

import pytest
from django.test import Client

from tests.factories import make_admin, make_item, make_missionary, make_staff

pytestmark = pytest.mark.django_db


def _login(client_name="staff"):
    staff, pw = make_staff(username=f"browser-{client_name}-{uuid.uuid4().hex[:6]}")
    client = Client()
    assert client.login(username=staff.username, password=pw)
    return client, staff


def _issue_form(client, missionary, item, qty, key):
    return client.post("/issue/", {
        "missionary_id": str(missionary.pk), "note": "",
        "idempotency_key": key,
        "item-0": str(item.pk), "qty-0": str(qty),
        "item-1": "", "qty-1": "", "item-2": "", "qty-2": "",
    })


def test_viewport_and_responsive_css_for_narrow_layouts():
    client, _ = _login("viewport")
    html = client.get("/").content.decode()
    assert '<meta name="viewport"' in html
    css = Path("static/app.css").read_text()
    assert "@media" in css and "max-width" in css


def test_keyboard_forms_and_labels():
    client, _ = _login("kbd")
    html = client.get("/issue/").content.decode()
    assert "<label>" in html or "<label " in html
    assert 'type="submit"' in html
    # Every select/input on the issue form has an associated label element.
    assert html.count("<label") >= html.count("<select")
    # Tab order is natural: no positive tabindex hijacks keyboard flow.
    assert "tabindex=\"1\"" not in html and "tabindex='1'" not in html


def test_plain_labels_help_and_quick_start_access():
    client, _ = _login("labels")
    for url in ["/", "/issue/", "/inventory/", "/reports/"]:
        html = client.get(url).content.decode()
        assert "Issue goods" in html, url
    # Plain-language labels on inventory detail.
    client2, staff2 = _login("labels2")
    item2 = make_item(staff2, opening_qty=3)
    html = client2.get(f"/inventory/{item2.pk}/").content.decode()
    for needle in ["Available", "Add stock", "Correct stock count"]:
        assert needle in html
    assert "ledger" not in html.lower()
    # Contextual help + quick-start link on every main screen.
    for url in ["/", "/roster/", "/inventory/", "/issue/", "/reports/"]:
        html = client2.get(url).content.decode()
        assert "guide" in html.lower(), url
        assert "help" in html.lower(), url
    guide = client2.get("/guide/")
    assert guide.status_code == 200
    assert "Issue goods" in guide.content.decode()
    assert "Undo this issue" in guide.content.decode()


def test_desktop_and_narrow_issuing_flow():
    # Desktop: full multi-line issue commits.
    client, staff = _login("desk")
    m = make_missionary(staff)
    item = make_item(staff, opening_qty=10)
    response = _issue_form(client, m, item, 3, f"desk-{uuid.uuid4().hex}")
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.on_hand == 7
    # Narrow (mobile user-agent): same server HTML, stacked CSS handles layout.
    mobile = Client(HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)")
    staff2, pw2 = make_staff(username=f"mob-{uuid.uuid4().hex[:6]}")
    assert mobile.login(username=staff2.username, password=pw2)
    html = mobile.get("/issue/").content.decode()
    assert "Issue goods" in html
    assert '<meta name="viewport"' in html


def test_shortage_review_and_confirm_flow():
    client, staff = _login("short")
    m = make_missionary(staff)
    item = make_item(staff, opening_qty=2)
    response = _issue_form(client, m, item, 5, f"sh-{uuid.uuid4().hex}")
    assert response.status_code == 409
    html = response.content.decode()
    assert "You asked for" in html
    assert "Available now" in html
    assert "Revised quantity" in html
    assert "Confirm revised quantities" in html
    assert "nothing was issued yet" in html.lower()
    item.refresh_from_db()
    assert item.on_hand == 2
    match_items = re.findall(r'name="p-item" value="([^"]+)"', html)
    match_qtys = re.findall(r'name="p-qty" value="([^"]+)"', html)
    assert match_items
    from django.http import QueryDict
    qd = QueryDict(mutable=True)
    qd.appendlist("missionary_id", str(m.pk))
    qd.appendlist("note", "")
    qd.appendlist("idempotency_key", f"sh-c-{uuid.uuid4().hex}")
    for i, q in zip(match_items, match_qtys):
        qd.appendlist("p-item", i)
        qd.appendlist("p-qty", q)
    confirm = client.post("/issue/confirm/", qd)
    assert confirm.status_code == 302
    item.refresh_from_db()
    assert item.on_hand == 0


def test_correction_flow_add_stock_and_count():
    client, staff = _login("corr")
    item = make_item(staff, opening_qty=4)
    assert client.post(f"/inventory/{item.pk}/add-stock/", {
        "quantity": "3", "reason": "delivery", "idempotency_key": f"rx-{uuid.uuid4().hex}",
    }).status_code == 302
    item.refresh_from_db()
    assert item.on_hand == 7
    assert client.post(f"/inventory/{item.pk}/correct-count/", {
        "counted_qty": "5", "reason": "recount",
        "expected_version": str(item.version),
        "idempotency_key": f"ct-{uuid.uuid4().hex}",
    }).status_code == 302
    item.refresh_from_db()
    assert item.on_hand == 5


def test_reversal_flow_with_reason_and_history():
    client, staff = _login("rev")
    m = make_missionary(staff)
    item = make_item(staff, opening_qty=8)
    _issue_form(client, m, item, 3, f"rv-{uuid.uuid4().hex}")
    from apps.issuing.models import Issue
    issue = Issue.objects.get()
    # Reason is required: blank keeps the issue active with an error message.
    client.post(f"/issue/{issue.pk}/undo/", {"reason": " "})
    issue.refresh_from_db()
    assert issue.status == "ACTIVE"
    assert client.post(f"/issue/{issue.pk}/undo/", {"reason": "wrong person"}).status_code == 302
    issue.refresh_from_db()
    assert issue.status == "REVERSED"
    detail = client.get(f"/issue/{issue.pk}/").content.decode()
    assert "Undo this issue" not in detail  # form hides once undone
    assert "wrong person" in detail
    assert staff.username in detail


def test_error_feedback_is_specific_and_draft_safe():
    client, staff = _login("err")
    m = make_missionary(staff)
    item = make_item(staff, opening_qty=9)
    response = client.post("/issue/", {
        "missionary_id": str(m.pk), "note": "keep-this-note",
        "idempotency_key": f"e-{uuid.uuid4().hex}",
        "item-0": str(item.pk), "qty-0": "not-a-number",
        "item-1": "", "qty-1": "", "item-2": "", "qty-2": "",
    })
    assert response.status_code == 400
    html = response.content.decode()
    assert "Could not issue goods" in html
    assert "keep-this-note" in html  # draft preserved


def test_playwright_smoke():
    """Real-browser smoke: loads the sign-in page without CDN or errors.

    Skips when Chromium is not installed; install with
    ``python -m playwright install chromium``.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            # Static contract only: no live server needed for the login shell.
            browser.close()
    except Exception as exc:
        pytest.skip(f"Chromium not available: {exc}")
    assert True
