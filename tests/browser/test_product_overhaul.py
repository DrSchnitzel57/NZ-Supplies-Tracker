"""Product probes: explicit recipients, original drafts and same-user recovery."""

from playwright.sync_api import expect

from apps.issuing.models import Issue
from tests.browser.conftest import staff_login, admin_login, assert_no_internet
from tests.browser.test_workflows import add_item
from tests.factories import make_item, make_missionary, make_staff


def test_typing_never_reuses_a_hidden_recipient(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'recipient')
    first = make_missionary(staff, name='Synthetic Alpha')
    second = make_missionary(staff, name='Synthetic Beta')
    page.goto(f'{live_server.url}/issue/')
    page.fill('#person-filter', 'Alpha')
    assert page.input_value('#person-select') == ''
    page.press('#person-filter', 'Enter')
    assert page.input_value('#person-select') == str(first.pk)
    page.fill('#person-filter', 'Beta')
    assert page.input_value('#person-select') == ''
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
    page.select_option('#person-select', str(first.pk))
    add_item(page, item)
    page.fill('#issue-note', 'Keep until confirmed')
    page.once('dialog', lambda dialog: dialog.dismiss())
    page.select_option('#person-select', str(second.pk))
    assert page.input_value('#person-select') == str(first.pk)
    assert page.locator('.issue-row').count() == 1
    page.once('dialog', lambda dialog: dialog.accept())
    page.select_option('#person-select', str(second.pk))
    assert page.locator('.issue-row').count() == 0
    assert page.input_value('#issue-note') == ''
    assert_no_internet(externals)


def test_shortage_back_retains_original_open_tab_draft(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'short-back')
    person = make_missionary(staff)
    item = make_item(staff, opening_qty=2)
    page.goto(f'{live_server.url}/issue/')
    page.select_option('#person-select', str(person.pk))
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
    page.select_option('#person-select', str(person.pk))
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
    page.select_option('#person-select', str(person.pk))
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
