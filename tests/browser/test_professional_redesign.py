"""07 behavioural UI coverage and populated rendered evidence, internet blocked."""
from pathlib import Path
import pytest

from playwright.sync_api import expect

from apps.audit.models import AuditEvent
from apps.inventory.models import Item, StockMovement
from apps.issuing.models import Issue
from apps.issuing.services import create_issue
from apps.roster.models import Missionary
from tests.browser.conftest import staff_login, assert_no_internet
from tests.browser.test_workflows import add_item, choose_person
from tests.factories import make_item, make_missionary


def capture(page, name):
    evidence = Path('.local/review/07')
    evidence.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(evidence / name), full_page=True)


def test_sign_in_has_no_false_draft_expiry_or_operational_poll(page, live_server, externals):
    api_requests = []
    page.on('request', lambda request: api_requests.append(request.url) if '/api/' in request.url else None)
    page.goto(live_server.url + '/accounts/login/')
    expect(page.locator('.auth-app')).to_be_visible()
    expect(page.locator('#navigation')).to_be_hidden()
    expect(page.locator('#session-warning')).to_be_hidden()
    expect(page.locator('#connection-status')).to_be_hidden()
    expect(page.locator('.top #themeBtn')).to_be_visible()
    page.click('#themeBtn')
    assert page.locator('html').get_attribute('data-theme') == 'dark'
    assert api_requests == []
    assert_no_internet(externals)


def test_finder_rejects_out_of_order_results_and_keeps_identity_explicit(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'stale07')
    alpha = make_missionary(staff, external_id='ALPHA07', name='Synthetic Alpha')
    beta = make_missionary(staff, external_id='BETA07', name='Synthetic Beta')
    page.goto(live_server.url + '/issue/')
    held = []
    def reorder(route):
        if 'Alpha' in route.request.url:
            held.append(route)
        else:
            route.continue_()
    page.route('**/api/find/**', reorder)
    with page.expect_request('**/api/find/**'):
        page.fill('#person-filter', 'Alpha')
    page.fill('#person-filter', 'Beta')
    expect(page.locator('#person-results')).to_contain_text('Synthetic Beta')
    held[0].fulfill(json={'results':[{'id':str(alpha.pk), 'label':alpha.name,
        'detail':alpha.external_id, 'group':'Missionaries', 'url':f'/roster/{alpha.pk}/'}]})
    expect(page.locator('#person-results')).not_to_contain_text('Synthetic Alpha')
    assert page.input_value('#person-select') == ''
    page.press('#person-filter', 'Enter')
    assert page.input_value('#person-select') == ''  # no highlighted option
    page.press('#person-filter', 'ArrowDown')
    page.press('#person-filter', 'Enter')
    assert page.input_value('#person-select') == str(beta.pk)
    assert_no_internet(externals)


def test_header_finder_keyboard_groups_error_and_all_results(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'finder07')
    person = make_missionary(staff, external_id='FIND-07', name='Synthetic Finder Māori')
    item = make_item(staff, name='Synthetic Finder towel', category='Travel')
    page.reload()
    page.fill('#global-search', 'Finder')
    expect(page.locator('#global-results [role=option]')).to_have_count(2)
    expect(page.locator('#global-results')).to_contain_text('Missionaries')
    expect(page.locator('#global-results')).to_contain_text('Inventory')
    page.press('#global-search', 'ArrowDown')
    page.press('#global-search', 'Enter')
    page.wait_for_url(f'**/roster/{person.pk}/')
    page.fill('#global-search', 'Finder')
    expect(page.locator('#global-results [role=option]')).to_have_count(2)
    page.press('#global-search', 'Escape')
    expect(page.locator('#global-results')).to_be_hidden()
    page.click('.header-finder button')
    page.wait_for_url('**/search/?q=Finder')
    expect(page.locator('main')).to_contain_text(person.name)
    expect(page.locator('main')).to_contain_text(item.name)
    page.route('**/api/find/**', lambda route: route.fulfill(status=503, body='unavailable'))
    page.fill('#global-search', 'Another')
    expect(page.locator('#global-results')).to_contain_text('Search unavailable')
    capture(page, 'search-error.png')
    assert_no_internet(externals)


def test_low_stock_copy_and_mail_app_share_filtered_rows(page, live_server, externals):
    from urllib.parse import unquote
    staff, _ = staff_login(page, live_server, 'copy07')
    included = make_item(staff, opening_qty=0, category='Synthetic hygiene', name='Māori travel towel')
    excluded = make_item(staff, opening_qty=0, category='Synthetic hygiene', name='Soap')
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    page.goto(live_server.url + '/reports/?family=low-stock&q=towel')
    page.click('#copy-low-stock')
    expect(page.locator('#copy-feedback')).to_contain_text('Copied all matching items')
    copied = page.evaluate('navigator.clipboard.readText()')
    assert included.name in copied and excluded.name not in copied
    assert copied in unquote(page.locator('a[href^="mailto:"]').get_attribute('href'))
    assert_no_internet(externals)


def test_panel_dirty_cancel_focus_save_preserves_list_state(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'panel07')
    items = [make_item(staff, name=f'Panel item {i:02}', category='Panel supplies') for i in range(65)]
    page.goto(f'{live_server.url}/inventory/?q=Panel&sort=name&page=2')
    original_url = page.url
    opener = page.locator('a[data-panel][href$="/edit/"]').last
    opener.scroll_into_view_if_needed()
    position = page.evaluate('scrollY')
    href = opener.get_attribute('href')
    opener.click()
    panel = page.frame_locator('.side-dialog iframe')
    expect(panel.locator('input[name=category]')).to_be_focused()
    panel.locator('input[name=unit]').fill('packs')
    page.once('dialog', lambda dialog: dialog.dismiss())
    page.locator('body > .side-dialog > button').click()
    expect(page.locator('body > .side-dialog')).to_be_visible()
    capture(page, 'panel-dirty.png')
    page.once('dialog', lambda dialog: dialog.accept())
    page.locator('body > .side-dialog > button').click()
    expect(opener).to_be_focused()
    assert page.url == original_url
    assert abs(page.evaluate('scrollY') - position) <= 1
    opener.click()
    panel.locator('input[name=unit]').fill('packs')
    panel.locator('main button[type=submit]').click()
    expect(page.locator('body > .side-dialog')).not_to_be_visible()
    expect(page.locator(f'a[data-panel][href="{href}"]')).to_be_focused()
    assert page.url == original_url
    assert Item.objects.get(pk=href.split('/')[2]).unit == 'packs'
    assert_no_internet(externals)


def test_panel_uncertain_receipt_cannot_close_or_duplicate(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'panel-retry07')
    item = make_item(staff, category='Panel', name='Retry supply')
    page.goto(f'{live_server.url}/inventory/')
    page.locator(f'a[data-panel][href="/inventory/{item.pk}/stock/"]').click()
    panel = page.frame_locator('.side-dialog iframe')
    panel.locator('input[name=quantity]').fill('4')
    panel.locator('input[name=reason]').fill('Synthetic delivery')
    def lose_reply(route):
        if route.request.method == 'POST':
            route.fetch()
            route.abort()
        else:
            route.continue_()
    page.route('**/inventory/*/add-stock/', lose_reply)
    panel.locator('main button[type=submit]').click()
    expect(panel.locator('main form [role=status]')).to_contain_text('success is not confirmed')
    capture(page, 'panel-uncertain.png')
    expect(panel.locator('input[name=quantity]')).to_be_disabled()
    page.once('dialog', lambda dialog: dialog.accept())
    page.locator('body > .side-dialog > button').click()
    expect(page.locator('body > .side-dialog')).to_be_visible()
    page.unroute('**/inventory/*/add-stock/', lose_reply)
    panel.locator('main button[type=submit]').click()
    expect(page.locator('body > .side-dialog')).not_to_be_visible()
    item.refresh_from_db()
    assert item.on_hand == 14
    assert StockMovement.objects.filter(item=item, kind=StockMovement.RECEIPT).count() == 1
    assert_no_internet(externals)


@pytest.mark.parametrize('_attempt', range(3))
def test_mobile_panel_preserves_horizontal_list_position(mobile_page, live_server, externals, _attempt):
    staff, _ = staff_login(mobile_page, live_server, 'mobile-panel07')
    item = make_item(staff, category='Mobile', name='Synthetic mobile supply')
    mobile_page.goto(live_server.url + '/inventory/?q=mobile')
    opener = mobile_page.locator(f'a[data-panel][href="/inventory/{item.pk}/stock/"]')
    opener.scroll_into_view_if_needed()
    left = mobile_page.locator('main .tablewrap').evaluate('(node) => node.scrollLeft')
    assert left > 0
    opener.click()
    panel = mobile_page.frame_locator('.side-dialog iframe')
    panel.locator('input[name=quantity]').fill('2')
    panel.locator('input[name=reason]').fill('Synthetic mobile delivery')
    panel.locator('main button[type=submit]').click()
    expect(mobile_page.locator('body > .side-dialog')).not_to_be_visible()
    assert mobile_page.locator('main .tablewrap').evaluate('(node) => node.scrollLeft') == left
    expect(mobile_page.locator(f'a[href="/inventory/{item.pk}/stock/"]')).to_be_focused()
    assert mobile_page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert_no_internet(externals)


def test_panel_same_user_recovery_repairs_parent_shell_csrf(page, live_server, externals):
    staff, password = staff_login(page, live_server, 'panel-csrf07')
    item = make_item(staff)
    page.goto(live_server.url + '/inventory/')
    page.locator(f'a[data-panel][href="/inventory/{item.pk}/stock/"]').click()
    panel = page.frame_locator('.side-dialog iframe')
    panel.locator('input[name=quantity]').fill('2')
    panel.locator('input[name=reason]').fill('Synthetic recovered delivery')
    other = page.context.new_page()
    other.goto(live_server.url + '/')
    other.click("form[action='/accounts/logout/'] button")
    other.fill('#id_username', staff.username)
    other.fill('#id_password', password)
    other.click('main button[type=submit]')
    other.wait_for_url(live_server.url + '/')
    panel.locator('main button[type=submit]').click()
    expect(page.locator('body > .side-dialog')).not_to_be_visible()
    page.click(".userbar form[action='/accounts/logout/'] button")
    page.wait_for_url('**/accounts/login/')
    item.refresh_from_db()
    assert item.on_hand == 12
    assert_no_internet(externals)


def test_panel_hides_form_until_recovery_and_completion_handlers_are_ready(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'panel-ready07')
    item = make_item(staff)
    page.goto(live_server.url + '/inventory/')
    held = []
    page.route('**/static/issuing.js', lambda route: held.append(route))
    with page.expect_request('**/static/issuing.js'):
        page.locator(f'a[data-panel][href="/inventory/{item.pk}/stock/"]').click()
    panel = page.frame_locator('.side-dialog iframe')
    expect(panel.locator('input[name=quantity]')).to_have_count(1)
    expect(page.locator('body > .side-dialog iframe')).to_be_hidden()
    held[0].continue_()
    expect(panel.locator('input[name=quantity]')).to_be_visible()
    expect(panel.locator('main form')).to_have_attribute('data-recovery-ready', '1')
    expect(panel.locator('.page-back')).to_be_hidden()
    panel.locator('input[name=quantity]').fill('2')
    panel.locator('input[name=reason]').fill('Synthetic fast interaction')
    panel.locator('main button[type=submit]').click()
    expect(page.locator('body > .side-dialog')).not_to_be_visible()
    item.refresh_from_db()
    assert item.on_hand == 12
    assert_no_internet(externals)


def test_expired_panel_load_has_safe_fallback_and_can_close(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'panel-expiry07')
    item = make_item(staff)
    page.goto(live_server.url + '/inventory/')
    other = page.context.new_page()
    other.goto(live_server.url + '/')
    other.click("form[action='/accounts/logout/'] button")
    page.locator(f'a[data-panel][href="/inventory/{item.pk}/stock/"]').click()
    expect(page.locator('body > .side-dialog > p')).to_contain_text('Unable to open this panel')
    expect(page.locator('body > .side-dialog > a')).to_have_attribute('target', '_blank')
    page.locator('body > .side-dialog > button').click()
    expect(page.locator('body > .side-dialog')).not_to_be_visible()
    assert StockMovement.objects.filter(item=item, kind=StockMovement.RECEIPT).count() == 0
    assert_no_internet(externals)


def fill_batch_row(page, index, name):
    for field, value in {'category':'Synthetic batch', 'name': name, 'size':'M',
                         'unit':'each', 'low_threshold':'2', 'opening_qty':'7', 'reason':'Synthetic opening'}.items():
        page.fill(f'[name="form-{index}-{field}"]', value)


def test_rendered_shortage_offline_expiry_retains_draft(page, live_server, externals):
    import time
    from django.contrib.sessions.models import Session
    from django.contrib.sessions.backends.db import SessionStore
    staff, _ = staff_login(page, live_server, 'failures07')
    person = make_missionary(staff, external_id='FAIL-07', name='Synthetic failure example')
    item = make_item(staff, opening_qty=2, name='Synthetic shortage supplies')
    page.goto(live_server.url + '/issue/')
    choose_person(page, person)
    add_item(page, item, '5')
    page.fill('#issue-note', 'Keep this synthetic draft')
    page.click('#issue-submit')
    expect(page.locator('#shortage-review')).to_be_visible()
    capture(page, 'issue-shortage.png')
    page.click('#shortage-review .back-draft')
    page.context.set_offline(True)
    page.click('#issue-submit')
    expect(page.locator('#issue-feedback')).to_contain_text('Nothing was sent')
    capture(page, 'issue-offline.png')
    page.context.set_offline(False)
    key = next(cookie['value'] for cookie in page.context.cookies() if cookie['name'] == 'sessionid')
    record = Session.objects.get(session_key=key)
    data = record.get_decoded()
    data['last_activity'] = time.time() - 4000
    record.session_data = SessionStore().encode(data)
    record.save()
    page.click('#issue-submit')
    expect(page.locator('#issue-feedback')).to_contain_text('Sign in again')
    capture(page, 'issue-expired.png')
    assert page.input_value('#issue-note') == 'Keep this synthetic draft'
    assert page.input_value('[name=qty-0]') == '5'
    assert not Issue.objects.exists()
    assert_no_internet(externals)


def test_batch_review_retained_errors_atomic_unknown_success_retry(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'batch07')
    page.goto(f'{live_server.url}/inventory/batch/')
    fill_batch_row(page, 0, 'Synthetic batch towel')
    remove_box = page.locator('.batch-remove').bounding_box()
    assert 44 <= remove_box['height'] <= 60 and remove_box['width'] < 200
    assert page.locator('[name="form-0-name"]').bounding_box()['width'] >= 200
    page.click('#batch-add')
    fill_batch_row(page, 1, '  SYNTHETIC   BATCH TOWEL ')
    page.click('#batch-form button[type=submit]')
    expect(page.locator('main')).to_contain_text('same item')
    capture(page, 'batch-duplicate.png')
    assert Item.objects.count() == 0
    expect(page.locator('[name="form-0-name"]')).to_have_value('Synthetic batch towel')
    page.fill('[name="form-1-name"]', 'Synthetic batch soap')
    page.click('#batch-form button[type=submit]')
    expect(page.locator('main h1')).to_have_text('Review new items')
    assert Item.objects.count() == 0
    def lose_reply(route):
        if route.request.method == 'POST':
            route.fetch()
            route.abort()
        else:
            route.continue_()
    page.route('**/inventory/batch/', lose_reply)
    page.click('#batch-form button[type=submit]')
    expect(page.locator('#batch-form [role=status]')).to_contain_text('success is not confirmed')
    capture(page, 'batch-uncertain.png')
    assert Item.objects.count() == 2
    expect(page.locator('[name="form-0-name"]')).to_be_disabled()
    page.unroute('**/inventory/batch/', lose_reply)
    page.click('#batch-form button[type=submit]')
    expect(page.locator('main h1')).to_have_text('Inventory')
    assert Item.objects.count() == 2
    assert StockMovement.objects.count() == 2
    assert AuditEvent.objects.filter(action='inventory.batch_created').count() == 1
    assert_no_internet(externals)


def test_mobile_batch_fields_and_actions_are_usable(mobile_page, live_server, externals):
    staff, _ = staff_login(mobile_page, live_server, 'mobile-batch07')
    mobile_page.goto(live_server.url + '/inventory/batch/')
    fill_batch_row(mobile_page, 0, 'Synthetic mobile towel')
    mobile_page.click('#batch-add')
    fill_batch_row(mobile_page, 1, 'Synthetic mobile soap')
    assert mobile_page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert mobile_page.locator('[name="form-1-name"]').bounding_box()['width'] >= 250
    assert mobile_page.locator('.batch-remove').last.bounding_box()['height'] <= 60
    mobile_page.click('#batch-form button[type=submit]')
    expect(mobile_page.locator('main h1')).to_have_text('Review new items')
    assert Item.objects.count() == 0
    mobile_page.click('#batch-form button[type=submit]')
    expect(mobile_page.locator('main h1')).to_have_text('Inventory')
    assert Item.objects.count() == 2
    assert_no_internet(externals)


def test_populated_rendered_design_desktop_mobile_zoom_and_exports(page, live_server, externals):
    evidence = Path('.local/review/07')
    evidence.mkdir(parents=True, exist_ok=True)
    def screenshot(name):
        page.evaluate('window.scrollTo(0,0)')
        page.wait_for_function('window.scrollY === 0')
        page.screenshot(path=str(evidence / name), full_page=True)
    staff, _ = staff_login(page, live_server, 'populated07')
    Missionary.objects.bulk_create([Missionary(external_id=f'SYN07-{i:03}',
        name=f'Synthetic Māori missionary {i:03}', mission='Synthetic Auckland', language='Māori',
        arrival_status='Arrived' if i % 2 else 'Expected', title_prefix='Sister' if i % 2 else 'Elder') for i in range(95)])
    people = list(Missionary.objects.order_by('external_id'))
    items = [make_item(staff, category=f'Synthetic category {i // 32 + 1}',
             name=f'Synthetic supply {i:03} — Māori long description for practical wrapping',
             size=('Small', 'Medium', 'Large')[i % 3], opening_qty=0 if i > 220 else 1000) for i in range(224)]
    Item.objects.filter(pk__in=[items[i].pk for i in range(0, 210, 37)]).update(low_threshold=1000)
    for i in range(210):
        create_issue(actor=staff, missionary_id=people[i % 95].pk,
            lines=[{'item_id': str(items[i % 224].pk), 'quantity': 2}], note='Synthetic long-history example',
            idempotency_key=f'render07-{i}')
    for width in (1440, 1280, 390):
        page.set_viewport_size({'width':width, 'height':900})
        for theme in ('light', 'dark'):
            page.evaluate('(theme) => {document.documentElement.dataset.theme = theme; localStorage.setItem("mtc-theme",theme);}', theme)
            for name, url in (('overview','/'), ('roster','/roster/'), ('inventory','/inventory/'),
                              ('history','/issue/history/?page=5'), ('reports','/reports/?family=category')):
                page.goto(live_server.url + url)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                screenshot(f'{width}-{theme}-{name}.png')
            page.goto(live_server.url + '/issue/')
            choose_person(page, people[-1])
            add_item(page, items[-1], '3')
            page.evaluate('document.getElementById("catalogue-rows").scrollTop = 0')
            screenshot(f'{width}-{theme}-issue.png')
            page.once('dialog', lambda dialog: dialog.accept())
            page.goto(live_server.url + '/inventory/')
    page.set_viewport_size({'width':1280, 'height':900})
    page.goto(live_server.url + '/issue/')
    # 200% CSS zoom at desktop width exercises the real responsive workspace.
    page.evaluate('document.documentElement.style.zoom = "2"')
    page.emulate_media(reduced_motion='reduce')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    catalogue_box = page.locator('[aria-label="Supply catalogue"]').bounding_box()
    summary_box = page.locator('.issue-summary').bounding_box()
    assert catalogue_box['width'] >= 280
    assert catalogue_box['y'] + catalogue_box['height'] <= summary_box['y'] + 1
    screenshot('200-percent-zoom.png')
    page.evaluate('document.documentElement.style.zoom = "1"')
    for family in ('stock', 'history', 'roster'):
        response = page.context.request.get(live_server.url + f'/reports/family/{family}/?format=pdf')
        assert response.status == 200
        (evidence / f'{family}.pdf').write_bytes(response.body())
        import json
        import subprocess
        import sys
        probe = subprocess.run([sys.executable, 'tests/pdf_probe.py', str(evidence / f'{family}.pdf'),
            str(evidence / f'{family}-pdf.png')], capture_output=True, text=True, check=True)
        pages = json.loads(probe.stdout)
        assert pages and all(p['noto_embedded'] and p['pixels'] > 0 for p in pages)
        assert 'Māori' in ''.join(p['text'] for p in pages)
        for pdfpage in pages:
            assert abs(min(pdfpage['width'], pdfpage['height']) - 595.28) < 2
            for word in pdfpage['words']:
                assert 25 <= word[0] <= word[2] <= pdfpage['width'] - 25
                assert 25 <= word[1] <= word[3] <= pdfpage['height'] - 25
    assert Missionary.objects.count() == 95
    assert Item.objects.count() == 224
    assert Issue.objects.count() == 210
    assert_no_internet(externals)
