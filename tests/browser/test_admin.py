"""Real administrator flows in Chromium (synthetic data, LAN-only)."""

from __future__ import annotations

import io
import zipfile

from django.contrib.auth import get_user_model

from apps.audit.models import AuditEvent
from apps.roster.models import Missionary
from playwright.sync_api import expect

from tests.browser.conftest import assert_no_internet, admin_login, staff_login, tag
from tests.factories import make_item, make_missionary, make_staff

User = get_user_model()


def _reauth(page, live_server, password, next_path="/admin-area/reset/"):
    page.goto(f"{live_server.url}/admin-area/reauth/?next={next_path}")
    page.fill("input[name=password]", password)
    with page.expect_navigation():
        page.click("main button[type=submit]")


def test_account_lifecycle_with_fresh_identity(page, live_server, externals):
    admin, pw = admin_login(page, live_server, "life")
    page.goto(f"{live_server.url}/accounts/users/")
    assert "Staff accounts" in page.content()
    # Account changes need fresh identity: the page asks for it first, on the
    # same screen, so nobody types a new account only to have it dropped.
    assert "Confirm your password" in page.content()
    assert page.locator("input[name=username]").count() == 0
    page.fill("#reauth-password", pw)
    with page.expect_navigation():
        page.click("form[action^='/admin-area/reauth/'] button[type=submit]")
    page.wait_for_url(f'{live_server.url}/accounts/users/')
    page.fill("input[name=display_name]", "Sister Synthetic")
    page.fill("input[name=username]", "ui-staff-1")
    page.fill("main form[action='/accounts/users/create/'] input[name=password]", "TempPass123!")
    with page.expect_navigation():
        page.click("text=Create account")
    user = User.objects.get(username="ui-staff-1")
    assert user.must_change_password
    steps = page.locator(".next-steps").inner_text()
    assert "Next steps for Sister Synthetic" in steps and "ui-staff-1" in steps and live_server.url in steps
    assert "TempPass123!" not in page.content()
    expect(page.locator(f"#account-{user.pk}.just-added")).to_be_visible()
    row = page.locator(f"text={user.username}")
    assert row.count() >= 1
    deactivate = page.locator(f"form[action='/accounts/users/{user.pk}/deactivate/'] button")
    with page.expect_navigation():
        deactivate.click()
    user.refresh_from_db()
    assert not user.is_active
    reactivate = page.locator(f"form[action='/accounts/users/{user.pk}/reactivate/'] button")
    with page.expect_navigation():
        reactivate.click()
    user.refresh_from_db()
    assert user.is_active
    # Staff navigation hides administrator tools.
    staff, staff_pw = make_staff(username=f"nav-{tag()}")
    from tests.browser.conftest import logout, make_login
    logout(page, live_server)
    make_login(page, live_server, staff.username, staff_pw)
    nav = page.content()
    assert "/accounts/users/" not in nav
    assert "/transfers/bundle/" not in nav
    assert_no_internet(externals)


def test_protected_reset_with_counts(page, live_server, externals):
    admin, pw = admin_login(page, live_server, "reset")
    make_missionary(admin)
    make_item(admin, opening_qty=4)
    page.goto(f"{live_server.url}/admin-area/reset/")
    assert "Protected reset" in page.content()
    # Without recent identity confirmation the button stays disabled, and a
    # forged POST is refused with nothing deleted.
    assert page.locator("main button[type=submit]").is_disabled()
    refused = page.evaluate(
        """fetch('/admin-area/reset/', {method: 'POST',
            headers: {'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)[1],
                      'Content-Type': 'application/x-www-form-urlencoded'},
            body: new URLSearchParams({confirmation: 'RESET'})})
            .then(r => r.text()).then(t => t.includes('Confirm your password'))""")
    assert refused
    assert Missionary.objects.count() == 1
    _reauth(page, live_server, pw)
    page.goto(f"{live_server.url}/admin-area/reset/")
    page.fill("input[name=confirmation]", "RESET")
    with page.expect_navigation():
        page.click("main button[type=submit]")
    assert Missionary.objects.count() == 0
    assert AuditEvent.objects.filter(action="system.reset").count() == 1
    assert_no_internet(externals)


def test_settings_notice_and_threshold(page, live_server, externals):
    from apps.inventory.models import Item

    admin, pw = admin_login(page, live_server, "set")
    _reauth(page, live_server, pw, "/admin-area/settings/")
    page.goto(f"{live_server.url}/admin-area/settings/")
    page.fill("input[name=site_notice]", "Browser notice 123")
    page.fill("input[name=default_low_threshold]", "6")
    page.click("main button[type=submit]")
    from playwright.sync_api import expect
    expect(page.locator('main')).to_contain_text('Saved system settings')
    page.goto(f"{live_server.url}/")
    assert "Browser notice 123" in page.content()
    assert AuditEvent.objects.filter(action="settings.updated").count() == 1
    assert_no_internet(externals)


def test_bundle_export_reset_import_round_trip(page, live_server, externals):
    from apps.inventory.models import Item

    admin, pw = admin_login(page, live_server, "bun")
    person = make_missionary(admin)
    make_item(admin, opening_qty=4)
    _reauth(page, live_server, pw, "/transfers/bundle/")
    page.goto(f"{live_server.url}/transfers/bundle/")
    with page.expect_download() as dl:
        page.click("a[href='/transfers/bundle/export/']")
    bundle_bytes = open(dl.value.path(), "rb").read()
    assert zipfile.is_zipfile(io.BytesIO(bundle_bytes))
    # Full reset, then import the same bundle: data comes back.
    _reauth(page, live_server, pw)
    page.goto(f"{live_server.url}/admin-area/reset/")
    page.fill("input[name=confirmation]", "RESET")
    with page.expect_navigation():
        page.click("main button[type=submit]")
    assert Missionary.objects.count() == 0
    page.goto(f"{live_server.url}/transfers/bundle/")
    with page.expect_file_chooser() as chooser:
        page.click("input[name=bundle]")
    chooser.value.set_files(
        {"name": "bundle.zip", "mimeType": "application/zip", "buffer": bundle_bytes})
    with page.expect_navigation():
        page.click("form[action='/transfers/bundle/import/'] button[type=submit]")
    assert "Imported bundle" in page.content()
    assert Missionary.objects.filter(external_id=person.external_id).count() == 1
    assert Item.objects.count() == 1
    assert_no_internet(externals)


def test_original_tracker_html_imports_from_the_admin_screen(page, live_server, externals):
    """No command line needed: an administrator uploads the old HTML file."""
    import json

    from apps.inventory.models import Item

    admin, pw = admin_login(page, live_server, "orig")
    seed = {"missionaries": [{"id": f"SYN-{i}", "name": f"Synthetic, Person {i}", "type": "Sister",
                              "arrived": True} for i in range(3)],
            "inventory": [{"cat": "Hygiene", "item": "Soap", "remain": 5},
                          {"cat": "Hygiene", "item": "Lip Balm", "remain": -2}]}
    html = f"<html><script>const SEED = {json.dumps(seed)};</script></html>".encode()
    _reauth(page, live_server, pw, "/transfers/bundle/")
    page.goto(f"{live_server.url}/transfers/bundle/")
    page.set_input_files("input[name=original]", files=[
        {"name": "MTC Missionary Supplies Tracker.html", "mimeType": "text/html", "buffer": html}])
    with page.expect_navigation():
        page.click("form[action='/transfers/bundle/import-original/'] button[type=submit]")
    text = page.locator("main").inner_text()
    assert "Imported the original tracker: 3 missionaries and 2 stock items" in text
    assert "1 item had a negative count" in text
    assert "Synthetic" not in page.locator(".messages").inner_text()
    assert Missionary.objects.count() == 3 and Item.objects.count() == 2
    page.goto(f"{live_server.url}/transfers/bundle/")
    assert page.locator("input[name=original]").count() == 0  # only into an empty app
    assert_no_internet(externals)
