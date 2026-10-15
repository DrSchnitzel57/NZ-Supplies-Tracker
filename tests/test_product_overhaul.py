"""Milestone 06 behavioral regressions; synthetic records only."""

from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.common.errors import DomainError
from apps.inventory.models import Item
from apps.issuing import services as issuing
from apps.issuing.models import Issue
from apps.operations.models import SystemSetting
from apps.roster.models import Missionary
from apps.transfers.models import RowImportPreview
from apps.transfers.row_imports import build_preview, current_revision, new_preview_token
from apps.transfers.services import confirm_row_import
from tests.factories import make_admin, make_item, make_missionary


class ProductSafetyTests(TestCase):
    def setUp(self):
        self.admin, self.password = make_admin()
        self.client.force_login(self.admin)

    def fresh(self):
        self.client.post('/admin-area/reauth/', {'password': self.password})

    def preview(self, rows, mode='add_update', kind='roster'):
        result = build_preview(kind=kind, rows=rows, mode=mode)
        return RowImportPreview.objects.create(
            token=new_preview_token(), kind=kind, mode=mode,
            rows=[{**r, 'error': False} for r in result['valid']],
            base_revision=current_revision(kind), created_by=self.admin)

    def test_optional_import_blanks_preserve_in_both_modes(self):
        person = make_missionary(self.admin, external_id='SYN-1')
        Missionary.objects.filter(pk=person.pk).update(
            mission='Synthetic mission', language='Te reo', arrival_date=date(2026, 10, 12))
        for mode in ('add_update', 'replace'):
            for rows in ([['id', 'name'], ['SYN-1', 'New name']],
                         [['id', 'name', 'email', 'mission', 'language', 'arrival_date'],
                          ['SYN-1', 'New name', '', '', '', '']]):
                with self.subTest(mode=mode, rows=rows):
                    preview = self.preview(rows, mode)
                    confirm_row_import(actor=self.admin, preview=preview, selected=None)
                    person.refresh_from_db()
                    self.assertTrue(person.email)
                    self.assertEqual(person.mission, 'Synthetic mission')
                    self.assertEqual(person.language, 'Te reo')
                    self.assertEqual(person.arrival_date, date(2026, 10, 12))

    def test_new_import_saves_both_dates(self):
        preview = self.preview([['id', 'name', 'arrival_date', 'release_date'],
                                ['SYN-2', 'New synthetic person', '2026-10-12', '2027-10-12']])
        confirm_row_import(actor=self.admin, preview=preview, selected=None)
        person = Missionary.objects.get(external_id='SYN-2')
        self.assertEqual(person.arrival_date, date(2026, 10, 12))
        self.assertEqual(person.release_date, date(2027, 10, 12))

    def test_legacy_preview_requires_reupload(self):
        preview = self.preview([['id', 'name'], ['SYN-3', 'Synthetic legacy']])
        rows = preview.rows
        for row in rows:
            row.pop('present_fields', None)
        preview.rows = rows
        preview.save()
        with self.assertRaisesMessage(DomainError, 're-upload'):
            confirm_row_import(actor=self.admin, preview=preview, selected=None)
        self.assertFalse(Missionary.objects.exists())

    def test_report_bundle_alias_requires_recent_password(self):
        for url in ('/reports/bundle/export/', '/transfers/bundle/export/'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/admin-area/reauth/', response.url)

    def test_post_only_account_action_returns_to_gettable_screen(self):
        response = self.client.post('/accounts/users/create/', {
            'username': 'synthetic-new-user', 'password': 'NeverKeepThis123!'})
        response = self.client.post(response.url, {'password': self.password}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request['PATH_INFO'], '/accounts/users/')
        self.assertNotContains(response, 'NeverKeepThis123!')

    def test_settings_audit_failure_rolls_back_settings(self):
        self.fresh()
        SystemSetting.objects.create(key='site_notice', value='Before')
        with patch('apps.audit.models.AuditEvent.objects.create', side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):
                self.client.post('/admin-area/settings/', {
                    'site_notice': 'After', 'default_low_threshold': '7'})
        self.assertEqual(SystemSetting.objects.get(key='site_notice').value, 'Before')
        self.assertFalse(SystemSetting.objects.filter(key='default_low_threshold').exists())

    def test_settings_guard_runs_inside_transaction(self):
        self.fresh()
        from apps.operations.guards import assert_writes_allowed

        def check():
            # TestCase itself is atomic; the guarded service needs its own block.
            self.assertGreater(len(connection.atomic_blocks), 2)
            assert_writes_allowed()

        with patch('apps.operations.guards.assert_writes_allowed', side_effect=check):
            response = self.client.post('/admin-area/settings/', {
                'site_notice': 'Synthetic', 'default_low_threshold': '7'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AuditEvent.objects.filter(action='settings.updated').exists())

    def test_invalid_opening_and_threshold_never_create_item(self):
        for field in ('opening_qty', 'low_threshold'):
            with self.subTest(field=field):
                payload = {'category': 'Synthetic', 'name': field,
                           'opening_qty': '4', 'low_threshold': '2', field: 'not a number'}
                response = self.client.post('/inventory/new/', payload)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(Item.objects.filter(name=field).exists())

    def test_local_day_and_uncapped_low_stock_count(self):
        person = make_missionary(self.admin)
        item = make_item(self.admin, opening_qty=1)
        issue = issuing.create_issue(actor=self.admin, missionary_id=person.pk,
            lines=[{'item_id': str(item.pk), 'quantity': 1}], idempotency_key='local-day')
        at = datetime(2026, 10, 11, 12, 30, tzinfo=dt_timezone.utc)
        Issue.objects.filter(pk=issue.pk).update(issued_at=at)
        for i in range(21):
            make_item(self.admin, opening_qty=0, name=f'Low synthetic {i}')
        with patch('django.utils.timezone.now', return_value=at):
            payload = self.client.get('/api/dashboard/').json()
            response = self.client.get('/')
        self.assertEqual(timezone.get_default_timezone_name(), 'Pacific/Auckland')
        self.assertEqual(payload['issues_today'], 1)
        self.assertEqual(response.context['counts']['low_stock'], 22)

    def test_roster_bound_errors_keep_all_context_and_original_version(self):
        person = make_missionary(self.admin)
        payload = {'external_id': person.external_id, 'name': '', 'mission': 'Kept mission',
                   'arrival_date': '2026-11-01', 'personal_email': 'invalid',
                   'expected_version': str(person.version)}
        response = self.client.post(f'/roster/{person.pk}/edit/', payload)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'value="Kept mission"', status_code=400)
        self.assertContains(response, 'value="2026-11-01"', status_code=400)
        self.assertContains(response, 'value="invalid"', status_code=400)
        self.assertEqual(response.context['form']['name'].value(), '')
        person.refresh_from_db()
        self.assertNotEqual(person.name, '')

    def test_roster_complete_edit_can_deliberately_clear_optional_field(self):
        person = make_missionary(self.admin)
        payload = {'external_id': person.external_id, 'name': 'Edited synthetic',
                   'mission': 'Mission', 'language': 'Māori', 'stake': 'Home stake',
                   'title_prefix': 'Sister', 'arrival_status': 'Arrived',
                   'arrival_date': '2026-10-12', 'release_date': '2027-10-12',
                   'email': '', 'personal_email': 'synthetic@example.test',
                   'expected_version': str(person.version)}
        response = self.client.post(f'/roster/{person.pk}/edit/', payload)
        self.assertEqual(response.status_code, 302)
        person.refresh_from_db()
        self.assertEqual(person.email, '')
        self.assertEqual(person.language, 'Māori')
        self.assertEqual(person.arrival_date, date(2026, 10, 12))

    def test_stock_failure_retains_reason_count_and_key(self):
        item = make_item(self.admin)
        response = self.client.post(f'/inventory/{item.pk}/add-stock/', {
            'quantity': 'bad', 'reason': 'Keep delivery context', 'idempotency_key': 'keep-rx'})
        self.assertEqual(response.status_code, 400)
        form = response.context['form']
        self.assertEqual(form['quantity'].value(), 'bad')
        self.assertEqual(form['reason'].value(), 'Keep delivery context')
        self.assertEqual(form['idempotency_key'].value(), 'keep-rx')
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 10)

    def test_50_distinct_items_commit_and_51_never_partially_write(self):
        person = make_missionary(self.admin)
        items = [make_item(self.admin, opening_qty=2) for _ in range(51)]
        lines = [{'item_id': str(item.pk), 'quantity': 1} for item in items]
        with self.assertRaisesMessage(DomainError, '50'):
            issuing.create_issue(actor=self.admin, missionary_id=person.pk, lines=lines,
                                 idempotency_key='too-many-service')
        payload = {'missionary_id': str(person.pk), 'idempotency_key': 'too-many-view'}
        for i, item in enumerate(items):
            payload[f'item-{i}'] = str(item.pk)
            payload[f'qty-{i}'] = '1'
        response = self.client.post('/issue/', payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Issue.objects.exists())
        self.assertTrue(all(item.on_hand == 2 for item in Item.objects.all()))
        payload.pop('item-50')
        payload.pop('qty-50')
        response = self.client.post('/issue/', payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Issue.objects.get().lines.count(), 50)

    def test_shortage_back_keeps_original_request_and_key(self):
        person = make_missionary(self.admin)
        item = make_item(self.admin, opening_qty=2)
        payload = {'missionary_id': str(person.pk), 'idempotency_key': 'original-short',
                   'item-0': str(item.pk), 'qty-0': '7', 'note': 'Original note'}
        response = self.client.post('/issue/', payload)
        self.assertEqual(response.status_code, 409)
        payload['edit_draft'] = '1'
        response = self.client.post('/issue/', payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['draft_key'], 'original-short')
        self.assertEqual(response.context['draft_lines'][0]['quantity'], 7)
        self.assertEqual(response.context['note'], 'Original note')
        self.assertFalse(Issue.objects.exists())

    def test_draft_cannot_be_submitted_as_a_different_user(self):
        person = make_missionary(self.admin)
        item = make_item(self.admin)
        response = self.client.post('/issue/', {
            'missionary_id': str(person.pk), 'draft_user_id': str(self.admin.pk + 1),
            'item-0': str(item.pk), 'qty-0': '1', 'idempotency_key': 'wrong-identity'})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Issue.objects.exists())

    def test_session_refresh_supplies_current_csrf_without_activity(self):
        response = self.client.get('/api/session/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user_id'], str(self.admin.pk))
        self.assertTrue(response.json()['csrf_token'])
        before = self.client.session.get('last_activity')
        self.client.get('/api/session/')
        self.assertEqual(self.client.session.get('last_activity'), before)

    def test_account_error_keeps_username_but_never_password(self):
        self.fresh()
        response = self.client.post('/accounts/users/create/', {
            'username': 'Synthetic-account', 'password': '123', 'is_admin': '1'})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'value="Synthetic-account"', status_code=400)
        self.assertNotContains(response, 'value="123"', status_code=400)

    def test_excel_date_cells_and_overlong_profile_are_previewed_correctly(self):
        import io
        import openpyxl
        from apps.transfers.row_imports import read_table
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(['id', 'name', 'arrival_date', 'mission'])
        sheet.append(['SYN-XLSX', 'Synthetic Excel', datetime(2026, 10, 12), 'Mission'])
        buf = io.BytesIO()
        book.save(buf)
        rows = read_table(filename='synthetic.xlsx', content=buf.getvalue())
        preview = self.preview(rows)
        confirm_row_import(actor=self.admin, preview=preview, selected=None)
        self.assertEqual(Missionary.objects.get(external_id='SYN-XLSX').arrival_date, date(2026, 10, 12))
        result = build_preview(kind='roster', mode='add_update', rows=[
            ['id', 'name', 'mission'], ['SYN-LONG', 'Synthetic', 'x' * 129]])
        self.assertEqual(len(result['errors']), 1)

    def test_repeated_shortage_shows_original_and_actual_revised_quantities(self):
        person = make_missionary(self.admin)
        available = make_item(self.admin, opening_qty=2)
        empty = make_item(self.admin, opening_qty=0)
        response = self.client.post('/issue/confirm/', {
            'missionary_id': str(person.pk), 'idempotency_key': 'second-shortage',
            'p-item': str(available.pk), 'p-qty': '4',
            'item-0': str(available.pk), 'qty-0': '8',
            'item-1': str(empty.pk), 'qty-1': '3', 'note': 'Original retained'})
        self.assertEqual(response.status_code, 409)
        rows = {row['item_id']: row for row in response.context['shortage_rows'] + response.context['ok_rows']}
        self.assertEqual(rows[str(available.pk)]['requested'], 8)
        self.assertEqual(rows[str(available.pk)]['revised'], 2)
        self.assertEqual(rows[str(empty.pk)]['requested'], 3)
        self.assertEqual(rows[str(empty.pk)]['revised'], 0)
        self.assertFalse(Issue.objects.exists())

    def test_history_pagination_keeps_archived_default_and_explicit_exclusion(self):
        person = make_missionary(self.admin)
        Missionary.objects.filter(pk=person.pk).update(is_archived=True)
        Issue.objects.bulk_create([Issue(missionary=person, missionary_name='Archived synthetic',
            missionary_external_id=person.external_id, issued_by=self.admin,
            issued_by_username=self.admin.username, idempotency_key=f'arch-page-{i}') for i in range(51)])
        self.assertEqual(len(self.client.get('/issue/history/').context['issues']), 50)
        self.assertEqual(len(self.client.get('/issue/history/', {'page': 2}).context['issues']), 1)
        self.assertEqual(len(self.client.get('/issue/history/', {'filtered': '1'}).context['issues']), 0)

    def test_undo_maintenance_refusal_keeps_reason_and_history(self):
        from apps.operations.guards import maintenance_mode
        person = make_missionary(self.admin)
        item = make_item(self.admin)
        issue = issuing.create_issue(actor=self.admin, missionary_id=person.pk,
            lines=[{'item_id': str(item.pk), 'quantity': 2}], idempotency_key='undo-keep')
        with maintenance_mode(owner='synthetic-test', reason='Synthetic maintenance'):
            response = self.client.post(f'/issue/{issue.pk}/undo/', {'reason': 'Keep correction reason'})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'value="Keep correction reason"', status_code=400)
        issue.refresh_from_db()
        self.assertEqual(issue.status, Issue.ACTIVE)

    def test_malformed_original_on_confirmation_is_a_bound_error_not_500(self):
        person = make_missionary(self.admin)
        item = make_item(self.admin, opening_qty=0)
        response = self.client.post('/issue/confirm/', {
            'missionary_id': str(person.pk), 'idempotency_key': 'malformed-original',
            'item_id': 'invalid-uuid', 'quantity': '2',
            'p-item': str(item.pk), 'p-qty': '1', 'note': 'Retain this note'})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Retain this note', status_code=400)
        self.assertFalse(Issue.objects.exists())

    def test_invalid_email_is_a_preview_row_error_not_a_confirm_failure(self):
        result = build_preview(kind='roster', mode='add_update', rows=[
            ['id', 'name', 'email'], ['SYN-EMAIL', 'Synthetic email', 'invalid@']])
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(result['valid'], [])
