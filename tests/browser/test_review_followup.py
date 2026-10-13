"""Real-browser review regressions; no internet, storage or automatic replay."""
import pytest
from playwright.sync_api import expect

from apps.inventory.models import StockMovement
from apps.inventory import services as inventory
from tests.browser.conftest import staff_login, assert_no_internet
from tests.factories import make_item


def test_uncertain_receipt_blocks_other_form_and_retries_original_once(page, live_server, externals):
    staff, _ = staff_login(page, live_server, 'uncertain-receipt')
    item = make_item(staff, opening_qty=10)
    page.goto(f'{live_server.url}/inventory/{item.pk}/')
    receipt = page.locator("form[action$='/add-stock/']")
    count = page.locator("form[action$='/correct-count/']")
    receipt.locator('[name=quantity]').fill('3')
    receipt.locator('[name=reason]').fill('Synthetic delivery only once')
    key = receipt.locator('[name=idempotency_key]').input_value()
    count.locator('[name=counted_qty]').fill('13')
    count.locator('[name=reason]').fill('Conflicting recount')
    count_posts = []
    page.on('request', lambda request: count_posts.append(request.url)
        if request.method == 'POST' and request.url.endswith('/correct-count/') else None)

    def lose_reply(route):
        route.fetch()  # The receipt really commits, but its reply is lost.
        route.abort()

    page.route('**/add-stock/', lose_reply)
    receipt.locator('button[type=submit]').click()
    expect(receipt).to_contain_text('success is not confirmed')
    item.refresh_from_db()
    assert item.on_hand == 13
    count.locator('button[type=submit]').click()
    expect(count).to_contain_text('Resolve the pending request')
    assert count_posts == []
    expect(receipt.locator('[name=quantity]')).to_be_disabled()
    assert receipt.locator('[name=idempotency_key]').input_value() == key
    assert receipt.locator('[name=reason]').input_value() == 'Synthetic delivery only once'
    page.unroute('**/add-stock/', lose_reply)
    receipt.locator('button[type=submit]').click()
    expect(page.locator('main')).to_contain_text('Added 3')
    item.refresh_from_db()
    assert item.on_hand == 13
    assert StockMovement.objects.filter(item=item, kind=StockMovement.RECEIPT).count() == 1
    assert_no_internet(externals)


@pytest.mark.parametrize('action', ['add-stock', 'correct-count'])
def test_stock_error_keeps_get_url_for_pagination_and_reload(page, live_server, externals, action):
    staff, _ = staff_login(page, live_server, f'canonical-{action}')
    item = make_item(staff, opening_qty=10)
    for i in range(51):
        inventory.receive_stock(actor=staff, item_id=item.pk, quantity=1,
            reason='Synthetic page history', idempotency_key=f'canonical-rx-{i}')
    url = f'{live_server.url}/inventory/{item.pk}/'
    page.goto(url)
    form = page.locator(f"form[action$='/{action}/']")
    form.locator('[name=reason]').fill(' ')
    if action == 'add-stock':
        form.locator('[name=quantity]').fill('1')
    form.locator('button[type=submit]').click()
    expect(page.locator('main')).to_contain_text('This field is required')
    assert page.url == url
    page.get_by_role('link', name='Next', exact=True).click()
    assert page.url.startswith(url + '?')
    expect(page.locator('.pagination')).to_contain_text('Page 2 of 2')
    response = page.reload()
    assert response.status == 200
    expect(page.locator('h1')).to_contain_text(item.name)
    assert_no_internet(externals)
