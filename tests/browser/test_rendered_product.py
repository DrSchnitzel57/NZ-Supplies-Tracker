"""Rendered synthetic help evidence and retained-input maintenance probes."""
from pathlib import Path

from playwright.sync_api import expect

from apps.issuing.models import Issue
from apps.operations.guards import maintenance_mode
from tests.browser.conftest import staff_login, assert_no_internet
from tests.browser.test_workflows import add_item
from tests.factories import make_item, make_missionary


def test_rendered_guide_examples_keyboard_and_reduced_motion(page, live_server, externals):
    evidence = Path('.local/review')
    evidence.mkdir(parents=True, exist_ok=True)
    def screenshot(name, full_page=False):
        page.evaluate('window.scrollTo(0, 0)')
        page.wait_for_function('window.scrollY === 0')
        page.screenshot(path=str(evidence / name), full_page=full_page)
    page.goto(f'{live_server.url}/accounts/login/')
    screenshot('01-sign-in.png')
    staff, _ = staff_login(page, live_server, 'illustration')
    person = make_missionary(staff, external_id='SYN-GUIDE', name='Synthetic example missionary')
    item = make_item(staff, category='Synthetic hygiene', name='Soap — Māori example', opening_qty=12)
    page.reload()
    screenshot('02-dashboard.png')
    page.goto(f'{live_server.url}/roster/{person.pk}/')
    page.click(f'a[href="/issue/?missionary={person.pk}"]')
    expect(page.locator('#recipient-summary')).to_contain_text(person.name)
    add_item(page, item, '2')
    screenshot('03-issue-goods.png', full_page=True)
    page.once('dialog', lambda dialog: dialog.accept())
    page.goto(f'{live_server.url}/inventory/{item.pk}/')
    screenshot('04-stock-detail.png', full_page=True)
    page.goto(f'{live_server.url}/reports/?family=stock')
    screenshot('05-reports.png', full_page=True)
    # Actual keyboard focus and reduced-motion rendering, not source-string checks.
    page.emulate_media(reduced_motion='reduce')
    page.press('#report-family', 'Tab')
    expect(page.locator('#id_q')).to_be_focused()
    assert page.evaluate("getComputedStyle(document.querySelector('input')).transitionDuration") == '0s'
    page.goto(f'{live_server.url}/guide/')
    assert page.locator('.guide-img').count() == 4
    assert_no_internet(externals)


def test_maintenance_rejects_issue_without_losing_draft_or_replaying(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'maintenance')
    person = make_missionary(staff)
    item = make_item(staff)
    page.goto(f'{live_server.url}/issue/')
    page.select_option('#person-select', str(person.pk))
    add_item(page, item, '2')
    page.fill('#issue-note', 'Keep through maintenance')
    key = page.input_value('input[name=idempotency_key]')
    with maintenance_mode(owner='synthetic-browser', reason='Synthetic maintenance test'):
        page.click('#issue-form button[type=submit]')
        expect(page.locator('#issue-feedback')).to_contain_text('maintenance')
        assert not Issue.objects.exists()
        assert page.input_value('input[name=qty-0]') == '2'
        assert page.input_value('#issue-note') == 'Keep through maintenance'
        assert page.input_value('input[name=idempotency_key]') == key
    assert not Issue.objects.exists()
    page.click('#issue-form button[type=submit]')
    page.wait_for_url('**/issue/*/')
    assert Issue.objects.count() == 1
    assert_no_internet(externals)


def test_record_form_cross_tab_security_recovery_retains_nonpassword_fields(page, live_server, externals):
    staff, password = staff_login(page, live_server, 'record-recovery')
    person = make_missionary(staff)
    page.goto(f'{live_server.url}/roster/{person.pk}/edit/')
    page.fill('input[name=mission]', 'Recovered synthetic mission')
    page.fill('input[name=arrival_date]', '2026-10-12')
    other = page.context.new_page()
    other.goto(f'{live_server.url}/')
    other.click("form[action='/accounts/logout/'] button")
    other.fill('#id_username', staff.username)
    other.fill('#id_password', password)
    other.click('main button[type=submit]')
    other.wait_for_url(f'{live_server.url}/')
    page.click('main button[type=submit]')
    expect(page.locator('main')).to_contain_text('Saved changes')
    person.refresh_from_db()
    assert person.mission == 'Recovered synthetic mission'
    assert person.arrival_date.isoformat() == '2026-10-12'
    assert_no_internet(externals)


def test_narrow_dark_workspace_has_no_page_overflow(mobile_page, live_server, externals):
    evidence = Path('.local/review')
    evidence.mkdir(parents=True, exist_ok=True)
    staff, _ = staff_login(mobile_page, live_server, 'narrow-evidence')
    person = make_missionary(staff, name='Synthetic narrow-screen missionary')
    item = make_item(staff, name='Synthetic narrow-screen supplies', opening_qty=12)
    mobile_page.goto(f'{live_server.url}/issue/')
    mobile_page.select_option('#person-select', str(person.pk))
    add_item(mobile_page, item, '2')
    mobile_page.click('#menu-toggle')
    mobile_page.click('#themeBtn')
    mobile_page.press('#themeBtn', 'Escape')
    assert mobile_page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    mobile_page.evaluate('window.scrollTo(0, 0)')
    mobile_page.screenshot(path=str(evidence / 'narrow-dark-issue.png'), full_page=True)
    expect(mobile_page.locator('#add-item')).to_be_visible()
    assert_no_internet(externals)
