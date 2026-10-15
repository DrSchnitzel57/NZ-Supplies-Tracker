"""Real polling/session behavior in Chromium (synthetic data, LAN-only)."""

from __future__ import annotations

from django.contrib.sessions.models import Session

from tests.browser.conftest import assert_no_internet, staff_login
from tests.factories import make_item, make_missionary
from tests.browser.test_workflows import add_item, choose_person


def _last_activity(staff) -> float | None:
    for session in Session.objects.all().iterator():
        data = session.get_decoded()
        if str(data.get("_auth_user_id")) == str(staff.pk):
            return data.get("last_activity")
    return None


def test_poll_preserves_drafts_and_spares_idle_timer(page, live_server, externals):
    staff, _ = staff_login(page, live_server, "draft")
    person = make_missionary(staff)
    item = make_item(staff, opening_qty=6)
    page.goto(f"{live_server.url}/issue/")
    choose_person(page, person)
    add_item(page, item, '2')
    page.fill("input[name=note]", "draft-note-keep")
    page.fill("input[name=qty-0]", "2")
    before = _last_activity(staff)
    assert before is not None
    # Let real 15-second polls run at least once.
    page.wait_for_function(
        "!document.getElementById('last-refresh').textContent.includes('not yet')",
        timeout=25000)
    assert page.inner_text("#connection-status").startswith("Connection: connected")
    assert "draft-note-keep" in page.input_value("input[name=note]")
    assert page.input_value("input[name=qty-0]") == "2"
    # Background polls never extend the idle timer.
    after = _last_activity(staff)
    assert after is not None
    assert abs(after - before) < 5
    assert_no_internet(externals)


def test_expired_session_warns_and_polls_json_401(page, live_server, externals):
    import time

    staff, _ = staff_login(page, live_server, "exp")
    page.goto(f"{live_server.url}/issue/")
    # Age the session server-side past the 30-minute idle limit.
    from django.contrib.sessions.backends.db import SessionStore

    cookies = {c["name"]: c["value"] for c in page.context.cookies()}
    key = cookies.get("sessionid")
    assert key, "browser has no session cookie after login"
    record = Session.objects.get(session_key=key)
    data = record.get_decoded()
    assert str(data.get("_auth_user_id")) == str(staff.pk)
    data["last_activity"] = time.time() - 4000
    record.session_data = SessionStore().encode(data)
    record.save()
    status = page.evaluate(
        "fetch('/api/stock/', {headers: {'X-Requested-With': 'fetch'}})"
        ".then(r => r.status).catch(() => 'network-error')")
    assert status == 401
    page.reload()
    assert "Sign in" in page.content()
    assert_no_internet(externals)


def test_offline_poll_failure_is_visible_then_recovers(page, live_server, externals):
    staff, _ = staff_login(page, live_server, "off")
    page.goto(f"{live_server.url}/")
    page.wait_for_function(
        "document.getElementById('connection-status').textContent.includes('connected')",
        timeout=25000)
    context = page.context
    context.set_offline(True)
    page.wait_for_function(
        "document.getElementById('connection-status').textContent.includes('offline')",
        timeout=25000)
    context.set_offline(False)
    page.wait_for_function(
        "document.getElementById('connection-status').textContent.includes('connected')",
        timeout=25000)
    assert_no_internet(externals)
