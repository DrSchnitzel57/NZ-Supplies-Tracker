"""07 search, filters and atomic reviewed batches; synthetic fixtures only."""
import copy
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.common.errors import DomainError
from apps.inventory import services as inventory
from apps.inventory.models import Item, StockMovement
from apps.operations.guards import maintenance_mode
from apps.roster.models import Missionary
from tests.factories import make_item, make_missionary, make_staff


def batch_rows(count=2):
    return [dict(category='Synthetic supplies', name=f'Batch item {i}', size='M', unit='each',
                 low_threshold=2, opening_qty=5, reason='Synthetic opening', row_key=f'row-{i}')
            for i in range(count)]


class SearchAndLists(TestCase):
    def setUp(self):
        self.staff, _ = make_staff()
        self.client.force_login(self.staff)

    def test_finder_matching_priority_archives_bounds_and_no_idle_refresh(self):
        exact = make_missionary(self.staff, external_id='SYN-10', name='Māori Sample')
        other = make_missionary(self.staff, external_id='SYN-100', name='Another Sample')
        hidden = make_missionary(self.staff, external_id='SYN-101', name='Archived Sample')
        hidden.is_archived = True
        hidden.save()
        session = self.client.session
        session['last_activity'] = timezone.now().timestamp() - 900
        session.save()
        before = self.client.session['last_activity']
        response = self.client.get('/api/find/', {'q': ' SYN-10 ', 'scope': 'people'})
        results = response.json()['results']
        self.assertEqual([r['id'] for r in results], [str(exact.pk), str(other.pk)])
        self.assertEqual(self.client.session['last_activity'], before)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.get('/api/find/', {'q': 'MĀORI SYN', 'scope': 'people'}).json()['results'][0]['id'], str(exact.pk))
        for i in range(20):
            make_missionary(self.staff, external_id=f'BOUND-{i}', name=f'Synthetic Bound {i}')
        self.assertEqual(len(self.client.get('/api/find/', {'q': 'Bound', 'scope': 'people'}).json()['results']), 12)
        session = self.client.session
        session['last_activity'] = timezone.now().timestamp() - 1900
        session.save()
        response = self.client.get('/api/find/', {'q': 'Sample'})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('Sample', response.content.decode())

    def test_local_search_uses_all_terms_across_fields(self):
        item = make_item(self.staff, name='Travel towel', category='Hygiene', size='Large')
        response = self.client.get('/inventory/', {'q': 'large hygiene towel'})
        self.assertEqual([i.pk for i in response.context['items']], [item.pk])
        self.assertEqual(self.client.get('/api/find/', {'q': 'large hygiene towel'}).json()['results'][0]['id'], str(item.pk))

    def test_sorted_grouped_pagination_and_blank_roster_filters(self):
        Missionary.objects.bulk_create([Missionary(external_id=f'SORT-{i:03}', name=f'Synthetic {i:03}', title_prefix='Elder' if i % 2 else '') for i in range(95)])
        response = self.client.get('/roster/', {'title': '__blank__', 'sort': '-name'})
        self.assertEqual(response.context['total'], 48)
        self.assertEqual(response.context['missionaries'][0].name, 'Synthetic 094')
        for i in range(65):
            make_item(self.staff, category=f'Group {i // 40}', name=f'Synthetic {i:03}')
        first = self.client.get('/inventory/', {'sort': 'category'})
        second = self.client.get('/inventory/', {'sort': 'category', 'page': 2})
        self.assertEqual(first.context['total'], 65)
        self.assertEqual(second.context['total'], 65)
        a = {i.pk for i in first.context['items']}
        b = {i.pk for i in second.context['items']}
        self.assertFalse(a & b)
        self.assertContains(second, 'Group')

    def test_batch_view_requires_review_and_retains_all_errors(self):
        row = batch_rows(1)[0]
        data = {f'form-0-{key}': value for key, value in row.items()}
        data.update({'form-TOTAL_FORMS': '1', 'form-INITIAL_FORMS': '0', 'form-MIN_NUM_FORMS': '1',
                     'form-MAX_NUM_FORMS': '50', 'idempotency_key': 'view-batch'})
        response = self.client.post('/inventory/batch/', data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['reviewed'])
        self.assertEqual(Item.objects.count(), 0)
        data['confirm'] = '1'
        self.assertEqual(self.client.post('/inventory/batch/', data).status_code, 302)
        self.assertEqual(self.client.post('/inventory/batch/', data).status_code, 302)
        self.assertEqual(Item.objects.count(), 1)
        data['form-0-name'] = 'Changed retry'
        response = self.client.post('/inventory/batch/', data)
        self.assertContains(response, 'different batch', status_code=400)
        self.assertEqual(Item.objects.count(), 1)

    def test_page_fallbacks_and_permissions(self):
        item = make_item(self.staff)
        for suffix in ('edit/', 'stock/', 'count/'):
            response = self.client.get(f'/inventory/{item.pk}/{suffix}')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        self.client.logout()
        self.assertEqual(self.client.get('/inventory/batch/').status_code, 302)
        self.assertEqual(self.client.get('/api/find/?q=synthetic').status_code, 401)

    def test_low_stock_message_is_complete_not_page_limited(self):
        from urllib.parse import unquote
        for i in range(61):
            make_item(self.staff, opening_qty=0, name=f'Synthetic low {i:02}')
        response = self.client.get('/reports/?family=low-stock')
        bullets = [line for line in response.context['low_text'].splitlines() if line.startswith('• ')]
        self.assertEqual(len(bullets), 61)
        self.assertEqual(response.context['page_obj'].paginator.count, 61)
        self.assertIn('Synthetic low 60', response.context['low_text'])
        self.assertIn(response.context['low_text'], unquote(response.context['mail_url']))

    def test_list_return_accepts_only_the_local_list(self):
        from django.test import RequestFactory
        from apps.common.navigation import list_return
        factory = RequestFactory()
        for value in ('https://example.test/inventory/', '//example.test/inventory/',
                      '/inventory/new/', '/inventory/\\evil', 'javascript:alert(1)'):
            request = factory.get('/inventory/', {'return_to': value})
            self.assertEqual(list_return(request, '/inventory/'), '/inventory/')
        request = factory.get('/inventory/', {'return_to': '/inventory/?q=towel&page=2'})
        self.assertEqual(list_return(request, '/inventory/'), '/inventory/?q=towel&page=2')


class AtomicBatches(TransactionTestCase):
    def setUp(self):
        self.staff, _ = make_staff()

    def test_retry_once_rejects_changed_payload_and_other_actor(self):
        rows = batch_rows()
        first = inventory.create_batch(actor=self.staff, rows=rows, idempotency_key='retry')
        second = inventory.create_batch(actor=self.staff, rows=rows, idempotency_key='retry')
        self.assertEqual({i.pk for i in first}, {i.pk for i in second})
        changed = copy.deepcopy(rows)
        changed[0]['opening_qty'] += 1
        with self.assertRaises(DomainError):
            inventory.create_batch(actor=self.staff, rows=changed, idempotency_key='retry')
        other, _ = make_staff()
        with self.assertRaises(DomainError):
            inventory.create_batch(actor=other, rows=rows, idempotency_key='retry')
        self.assertEqual(Item.objects.count(), 2)
        self.assertEqual(StockMovement.objects.count(), 2)
        self.assertEqual(AuditEvent.objects.filter(action='inventory.batch_created').count(), 1)
        for item in first:
            self.assertEqual(inventory.ledger_balance(item.pk), item.on_hand)

    def test_bounds_all_row_validation_duplicates_and_archived_match(self):
        for rows in ([], batch_rows(51)):
            with self.assertRaises(DomainError):
                inventory.create_batch(actor=self.staff, rows=rows, idempotency_key='bound')
        rows = batch_rows()
        rows[1]['name'] = '  BATCH   ITEM 0 '
        with self.assertRaises(DomainError):
            inventory.create_batch(actor=self.staff, rows=rows, idempotency_key='duplicate')
        rows = batch_rows()
        rows[1]['opening_qty'] = -1
        with self.assertRaises(DomainError):
            inventory.create_batch(actor=self.staff, rows=rows, idempotency_key='invalid')
        self.assertEqual(Item.objects.count(), 0)
        existing = make_item(self.staff, category='Synthetic supplies', name='Batch item 1', size='M')
        inventory.archive_item(actor=self.staff, item_id=existing.pk)
        with self.assertRaises(DomainError):
            inventory.create_batch(actor=self.staff, rows=batch_rows(), idempotency_key='existing')
        self.assertEqual(Item.objects.count(), 1)

    def test_mid_batch_failure_rolls_back_ledger_items_and_audit(self):
        original = inventory.create_item
        calls = 0
        def fail_second(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise DomainError('Synthetic interrupted batch')
            return original(**kwargs)
        before = AuditEvent.objects.count()
        with patch('apps.inventory.services.create_item', side_effect=fail_second), self.assertRaises(DomainError):
            inventory.create_batch(actor=self.staff, rows=batch_rows(), idempotency_key='rollback')
        self.assertEqual(Item.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), before)

    def test_maintenance_refuses_whole_batch(self):
        with maintenance_mode(owner='batch-test', reason='Synthetic maintenance'), self.assertRaises(DomainError):
            inventory.create_batch(actor=self.staff, rows=batch_rows(), idempotency_key='maintenance')
        self.assertEqual(Item.objects.count(), 0)

    def test_fifty_rows_and_concurrent_same_key(self):
        actor_id = self.staff.pk
        def worker(_):
            close_old_connections()
            try:
                actor = type(self.staff).objects.get(pk=actor_id)
                return {str(i.pk) for i in inventory.create_batch(actor=actor, rows=batch_rows(50), idempotency_key='concurrent')}
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(worker, range(2)))
        self.assertEqual(a, b)
        self.assertEqual(Item.objects.count(), 50)
        self.assertEqual(StockMovement.objects.count(), 50)
        self.assertEqual(AuditEvent.objects.filter(action='item.created').count(), 50)

    def test_concurrent_overlapping_batches_never_partially_save(self):
        from threading import Barrier
        barrier = Barrier(2)
        actor_id = self.staff.pk
        def worker(index):
            close_old_connections()
            try:
                actor = type(self.staff).objects.get(pk=actor_id)
                rows = batch_rows()
                rows[1]['name'] = f'Unique competitor {index}'
                barrier.wait(timeout=20)
                try:
                    inventory.create_batch(actor=actor, rows=rows, idempotency_key=f'competitor-{index}')
                    return 'saved'
                except DomainError:
                    return 'refused'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, range(2)))
        self.assertCountEqual(results, ['saved', 'refused'])
        self.assertEqual(Item.objects.count(), 2)
        self.assertEqual(StockMovement.objects.count(), 2)
        self.assertEqual(AuditEvent.objects.filter(action='inventory.batch_created').count(), 1)
