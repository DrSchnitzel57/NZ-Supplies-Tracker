"""Product probes: explicit recipients, original drafts and same-user recovery."""

from playwright.sync_api import expect

from apps.issuing.models import Issue
from tests.browser.conftest import staff_login, admin_login, assert_no_internet
from tests.browser.test_workflows import add_item, choose_person
from tests.factories import make_item, make_missionary, make_staff


def test_typing_never_reuses_a_hidden_recipient(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'recipient')
    first = make_missionary(staff, name='Synthetic Alpha')
    second = make_missionary(staff, name='Synthetic Beta')
    page.goto(f'{live_server.url}/issue/')
    page.fill('#person-filter', 'Alpha')
    assert page.input_value('#person-select') == ''
    expect(page.locator('#person-results [role=option]')).to_have_count(1)
    page.press('#person-filter', 'ArrowDown')
    page.press('#person-filter', 'Enter')
    assert page.input_value('#person-select') == str(first.pk)
    page.click('#change-person')
    page.fill('#person-filter', 'Beta')
    assert page.input_value('#person-select') == ''
    expect(page.locator('#person-results [role=option]')).to_have_count(1)
    page.press('#person-filter', 'ArrowDown')
    page.press('#person-filter', 'Enter')
    assert page.input_value('#person-select') == str(second.pk)
    expect(page.locator('#recipient-summary')).to_contain_text('Synthetic Beta')
    assert_no_internet(externals)


def test_recipient_change_confirm_clears_items_and_note(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'recipient-change')
    first = make_missionary(staff)
    second = make_missionary(staff)
    item = make_item(staff)
    page.goto(f'{live_server.url}/issue/')
    choose_person(page, first)
    add_item(page, item)
    page.fill('#issue-note', 'Keep until confirmed')
    page.once('dialog', lambda dialog: dialog.dismiss())
    page.click('#change-person')
    assert page.input_value('#person-select') == str(first.pk)
    assert page.locator('.issue-row').count() == 1
    page.once('dialog', lambda dialog: dialog.accept())
    page.click('#change-person')
    choose_person(page, second)
    assert page.locator('.issue-row').count() == 0
    assert page.input_value('#issue-note') == ''
    assert_no_internet(externals)


def test_shortage_back_retains_original_open_tab_draft(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'short-back')
    person = make_missionary(staff)
    item = make_item(staff, opening_qty=2)
    page.goto(f'{live_server.url}/issue/')
    choose_person(page, person)
    add_item(page, item, '8')
    page.fill('#issue-note', 'Original requested note')
    key = page.input_value('input[name=idempotency_key]')
    page.click('#issue-form button[type=submit]')
    expect(page.locator('#shortage-review')).to_be_visible()
    page.click('#shortage-review .back-draft')
    expect(page.locator('#issue-form')).to_be_visible()
    assert page.input_value('input[name=qty-0]') == '8'
    assert page.input_value('#issue-note') == 'Original requested note'
    assert page.input_value('input[name=idempotency_key]') == key
    assert not Issue.objects.exists()
    assert_no_internet(externals)


def test_other_tab_login_repairs_csrf_but_never_inherits_identity(page, live_server, externals):
    staff, password = staff_login(page, live_server, 'recover')
    person = make_missionary(staff)
    item = make_item(staff)
    page.goto(f'{live_server.url}/issue/')
    choose_person(page, person)
    add_item(page, item, '2')
    page.fill('#issue-note', 'Recovered original')
    other = page.context.new_page()
    other.goto(f'{live_server.url}/')
    other.click("form[action='/accounts/logout/'] button")
    other.fill('#id_username', staff.username)
    other.fill('#id_password', password)
    other.click('main button[type=submit]')
    other.wait_for_url(f'{live_server.url}/')
    page.click('#issue-form button[type=submit]')
    page.wait_for_url('**/issue/*/')
    assert Issue.objects.get().issued_by_id == staff.pk
    assert Issue.objects.get().note == 'Recovered original'
    assert_no_internet(externals)


def test_different_user_login_leaves_draft_unsent(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'identity')
    person = make_missionary(staff)
    item = make_item(staff)
    page.goto(f'{live_server.url}/issue/')
    choose_person(page, person)
    add_item(page, item)
    other_user, password = make_staff()
    other = page.context.new_page()
    other.goto(f'{live_server.url}/')
    other.click("form[action='/accounts/logout/'] button")
    other.fill('#id_username', other_user.username)
    other.fill('#id_password', password)
    other.click('main button[type=submit]')
    other.wait_for_url(f'{live_server.url}/')
    page.click('#issue-form button[type=submit]')
    expect(page.locator('#issue-feedback')).to_contain_text('different account')
    assert not Issue.objects.exists()
    assert page.locator('.issue-row').count() == 1
    assert_no_internet(externals)


def test_complete_narrow_menu_and_dark_button_contrast(mobile_page, live_server, externals):
    admin, _ = admin_login(mobile_page, live_server, 'menu')
    mobile_page.click('#menu-toggle')
    mobile_page.click('.admin-nav summary')
    for path in ('/guide/', '/accounts/users/', '/admin-area/settings/', '/transfers/bundle/', '/admin-area/reset/'):
        expect(mobile_page.locator(f'#navigation a[href="{path}"]')).to_be_visible()
    mobile_page.click('#themeBtn')
    ratio = mobile_page.evaluate('''() => {
      const button = document.querySelector('.primary-action');
      const style = getComputedStyle(button);
      const luminance = color => {
        const c = color.match(/[0-9.]+/g).slice(0,3).map(Number).map(x => {
          x /= 255; return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
        });
        return c[0]*0.2126 + c[1]*0.7152 + c[2]*0.0722;
      };
      const a = luminance(style.color), b = luminance(style.backgroundColor);
      return (Math.max(a,b)+0.05)/(Math.min(a,b)+0.05);
    }''')
    assert ratio >= 4.5
    mobile_page.press('#themeBtn', 'Escape')
    expect(mobile_page.locator('#menu-toggle')).to_be_focused()
    assert_no_internet(externals)


def test_offline_submission_keeps_original_draft_without_replay(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'offline-draft')
    person = make_missionary(staff)
    item = make_item(staff)
    page.goto(f'{live_server.url}/issue/')
    choose_person(page, person)
    add_item(page, item, '2')
    page.fill('#issue-note', 'No offline queue')
    page.context.set_offline(True)
    page.click('#issue-form button[type=submit]')
    expect(page.locator('#issue-feedback')).to_contain_text('Nothing was sent')
    assert not Issue.objects.exists()
    assert page.input_value('input[name=qty-0]') == '2'
    page.context.set_offline(False)
    # Reconnection itself never replays a mutation.
    page.evaluate("fetch('/api/dashboard/').then(r => r.json())")
    assert not Issue.objects.exists()
    page.click('#issue-form button[type=submit]')
    page.wait_for_url('**/issue/*/')
    assert Issue.objects.get().note == 'No offline queue'
    assert_no_internet(externals)


def test_uncertain_success_deliberate_retry_uses_original_key_once(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'uncertain')
    person = make_missionary(staff)
    item = make_item(staff)
    page.goto(f'{live_server.url}/issue/')
    choose_person(page, person)
    add_item(page, item, '2')

    def lose_reply(route):
        if route.request.method == 'POST':
            route.fetch()  # The real server commits, but the browser loses the reply.
            route.abort()
        else:
            route.continue_()

    page.route('**/issue/', lose_reply)
    page.click('#issue-form button[type=submit]')
    expect(page.locator('#issue-feedback')).to_contain_text('success is not confirmed')
    assert Issue.objects.count() == 1
    expect(page.locator('input[name=qty-0]')).to_be_disabled()
    page.unroute('**/issue/', lose_reply)
    page.click('#issue-form button[type=submit]')
    page.wait_for_url('**/issue/*/')
    assert Issue.objects.count() == 1
    item.refresh_from_db()
    assert item.on_hand == 8
    assert_no_internet(externals)


def test_leave_warning_reload_discards_and_no_personal_browser_storage(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'lifetime')
    person = make_missionary(staff)
    item = make_item(staff)
    page.goto(f'{live_server.url}/issue/')
    choose_person(page, person)
    add_item(page, item)
    page.fill('#issue-note', 'Temporary tab data')
    assert page.evaluate('Object.keys(localStorage).filter(k => k !== "mtc-theme")') == []
    assert page.evaluate('Object.keys(sessionStorage)') == []
    page.once('dialog', lambda dialog: dialog.dismiss())
    page.click('.navlink[href="/inventory/"]')
    assert page.url.endswith('/issue/')
    page.once('dialog', lambda dialog: dialog.accept())
    page.reload()
    assert page.locator('.issue-row').count() == 0
    assert page.input_value('#issue-note') == ''
    assert not Issue.objects.exists()
    assert_no_internet(externals)


def test_source_scale_keyboard_50_item_workspace_limit(page, live_server, externals):
    from apps.roster.models import Missionary
    staff, _ = staff_login(page, live_server, 'scale')
    Missionary.objects.bulk_create([Missionary(external_id=f'SYN-{i:03}', name=f'Synthetic person {i:03}') for i in range(95)])
    items = [make_item(staff, opening_qty=3, category='Synthetic supplies', name=f'Item {i:03}') for i in range(224)]
    person = Missionary.objects.get(external_id='SYN-094')
    page.goto(f'{live_server.url}/issue/')
    choose_person(page, person)
    assert page.input_value('#person-select') == str(person.pk)
    page.fill('#item-filter', 'Item 223')
    expect(page.locator('.catalogue-row:visible')).to_have_count(1)
    page.press('#item-filter', 'Tab')
    page.keyboard.press('Enter')
    page.fill('#item-filter', '')
    for item in items[:49]:
        add_item(page, item)
    assert page.locator('.issue-row').count() == 50
    add_item(page, items[50])
    expect(page.locator('#issue-feedback')).to_contain_text('at most 50')
    assert page.locator('.issue-row').count() == 50
    page.click('#issue-form button[type=submit]')
    page.wait_for_url('**/issue/*/')
    assert Issue.objects.get().lines.count() == 50
    assert_no_internet(externals)


def test_live_stock_does_not_rewrite_count_version_or_focused_draft(page, live_server, externals):
    from apps.inventory import services as inventory
    staff, _ = staff_login(page, live_server, 'live-count')
    item = make_item(staff, opening_qty=2)
    page.goto(f'{live_server.url}/inventory/{item.pk}/count/')
    count = page.locator('input[name=counted_qty]')
    count.fill('4')
    page.fill('#correct_reason', 'Original recount')
    count.focus()
    version = page.input_value('input[name=expected_version]')
    inventory.receive_stock(actor=staff, item_id=item.pk, quantity=2,
                            reason='Other staff delivery', idempotency_key='live-count-delivery')
    page.wait_for_function("document.querySelector('[data-available-for]').textContent === '4'", timeout=25000)
    expect(count).to_be_focused()
    assert page.input_value('input[name=counted_qty]') == '4'
    assert page.input_value('input[name=expected_version]') == version
    page.click("form[action$='/correct-count/'] button[type=submit]")
    expect(page.locator('main')).to_contain_text('stale')
    assert page.input_value('input[name=counted_qty]') == '4'
    assert page.input_value('#correct_reason') == 'Original recount'
    assert_no_internet(externals)
