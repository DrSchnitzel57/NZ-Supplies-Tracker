"""Real desktop/mobile issue flows in Chromium (synthetic data, LAN-only)."""

from __future__ import annotations

import re

from apps.issuing.models import Issue
from tests.browser.conftest import (
    admin_login,
    assert_no_internet,
    staff_login,
    tag,
)
from tests.factories import make_item, make_missionary


def _seed(staff):
    person = make_missionary(staff, external_id=f"P-{tag()}", name=f"Person {tag()}")
    item = make_item(staff, opening_qty=10)
    return person, item


def add_item(page, item, quantity='1'):
    page.select_option('#catalogue-select', str(item.pk))
    page.fill('#catalogue-qty', quantity)
    page.click('#add-item')


def test_issue_goods_desktop(page, live_server, externals):
    staff, _ = staff_login(page, live_server, "issue")
    person, item = _seed(staff)
    page.goto(f"{live_server.url}/issue/")
    page.select_option("select[name=missionary_id]", str(person.pk))
    add_item(page, item, '3')
    page.fill("input[name=note]", "browser issue")
    with page.expect_navigation():
        page.click("#issue-form button[type=submit]")
    assert "/issue/" in page.url
    assert "Issued goods" in page.content()
    issue = Issue.objects.get(missionary=person)
    assert issue.lines.count() == 1
    assert issue.lines.get().quantity == 3
    item.refresh_from_db()
    assert item.on_hand == 7
    assert_no_internet(externals)


def test_issue_goods_mobile_layout(mobile_page, live_server, externals):
    staff, _ = staff_login(mobile_page, live_server, "mob")
    person, item = _seed(staff)
    mobile_page.goto(f"{live_server.url}/issue/")
    assert "Issue goods" in mobile_page.content()
    overflow = mobile_page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, f"horizontal overflow on narrow screen: {overflow}px"
    mobile_page.select_option("select[name=missionary_id]", str(person.pk))
    add_item(mobile_page, item)
    with mobile_page.expect_navigation():
        mobile_page.click("#issue-form button[type=submit]")
    assert "Issued goods" in mobile_page.content()
    assert Issue.objects.filter(missionary=person).count() == 1
    assert_no_internet(externals)


def test_shortage_review_then_confirm_rechecks(page, live_server, externals):
    staff, _ = staff_login(page, live_server, "short")
    person, item = _seed(staff)
    page.goto(f"{live_server.url}/issue/")
    page.select_option("select[name=missionary_id]", str(person.pk))
    add_item(page, item, '999')
    page.click("form[action='/issue/confirm/'] button[type=submit], #issue-form button[type=submit]")
    page.wait_for_selector('#shortage-review:not([hidden])')
    assert "revised" in page.content().lower()
    with page.expect_navigation():
        page.click("form[action='/issue/confirm/'] button[type=submit]")
    assert "Issued revised quantities" in page.content()
    item.refresh_from_db()
    assert item.on_hand == 0
    assert Issue.objects.get(missionary=person).lines.get().quantity == 10
    assert_no_internet(externals)


def test_undo_issue_restores_stock_once(page, live_server, externals):
    from apps.issuing import services as issuing

    staff, _ = staff_login(page, live_server, "undo")
    person, item = _seed(staff)
    issue = issuing.create_issue(
        actor=staff, missionary_id=person.pk,
        lines=[{"item_id": str(item.pk), "quantity": 4}],
        note="", idempotency_key=f"undo-{tag()}")
    page.goto(f"{live_server.url}/issue/{issue.pk}/")
    page.fill("input[name=reason], textarea[name=reason]", "wrong person")
    page.click("form[action$='/undo/'] button[type=submit]")
    from playwright.sync_api import expect
    expect(page.locator('main')).to_contain_text('Undone')
    item.refresh_from_db()
    assert item.on_hand == 10
    # The undone issue offers no second undo: the form is gone, history kept.
    page.goto(f"{live_server.url}/issue/{issue.pk}/")
    assert "Undone" in page.content()
    assert page.locator("form[action$='/undo/']").count() == 0
    # A forged second undo is refused and changes nothing.
    # A forged second undo is refused (message on the redirect target).
    refused = page.evaluate(
        f"""fetch('/issue/{issue.pk}/undo/', {{method: 'POST',
            headers: {{'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)[1]}},
            body: new URLSearchParams({{reason: 'again'}})}})
            .then(r => r.text()).then(t => t.toLowerCase().includes('already undone'))""")
    assert refused
    item.refresh_from_db()
    assert item.on_hand == 10
    assert_no_internet(externals)


def test_add_remove_rows_and_filter(page, live_server, externals):
    staff, _ = staff_login(page, live_server, "rows")
    person = make_missionary(staff)
    items = [make_item(staff, opening_qty=10) for _ in range(5)]
    page.goto(f"{live_server.url}/issue/")
    page.select_option("select[name=missionary_id]", str(person.pk))
    page.fill("#item-filter", items[0].name)
    hidden = page.evaluate(
        """Array.from(document.querySelector('#catalogue-select').options)
            .filter(o => o.value && o.hidden).length""")
    assert hidden > 0
    page.fill("#item-filter", "")
    for i, item in enumerate(items):
        add_item(page, item)
    add_item(page, items[0])
    assert page.locator('#issue-rows .issue-row').count() == 5
    assert page.input_value('input[name=qty-0]') == '2'
    page.click('#issue-rows .remove-row:last-child >> nth=4')
    assert page.locator('#issue-rows .issue-row').count() == 4
    add_item(page, items[-1])
    with page.expect_navigation():
        page.click("#issue-form button[type=submit]")
    assert "Issued goods" in page.content()
    assert Issue.objects.get(missionary=person).lines.count() == 5
    assert_no_internet(externals)


def test_double_submit_issues_once(page, live_server, externals):
    staff, _ = staff_login(page, live_server, "dbl")
    person, item = _seed(staff)
    page.goto(f"{live_server.url}/issue/")
    page.select_option("select[name=missionary_id]", str(person.pk))
    add_item(page, item, '2')
    page.evaluate(
        "const f = document.querySelector('#issue-form');"
        "f.requestSubmit(); f.requestSubmit();")
    page.wait_for_url(re.compile(r"/issue/[^/]+/"))
    assert Issue.objects.filter(missionary=person).count() == 1
    item.refresh_from_db()
    assert item.on_hand == 8
    assert_no_internet(externals)
