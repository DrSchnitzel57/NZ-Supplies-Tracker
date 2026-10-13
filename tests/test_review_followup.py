"""Synthetic regressions for the independent milestone-06 review."""
import csv
import io

from django.contrib.auth import authenticate, get_user_model
from django.test import TestCase

from apps.issuing import services as issuing
from apps.roster import services as roster
from apps.roster.models import Missionary
from apps.transfers.models import RowImportPreview
from apps.transfers.row_imports import build_preview, current_revision, new_preview_token, read_table
from apps.transfers.services import confirm_row_import
from tests.factories import make_admin, make_item, make_missionary


class ReviewFollowupTests(TestCase):
    def setUp(self):
        self.admin, self.password = make_admin()
        self.client.force_login(self.admin)

    def test_roster_csv_roundtrip_preserves_identity_in_both_modes(self):
        person = make_missionary(self.admin, external_id='SYN-ROUNDTRIP')
        item = make_item(self.admin)
        issue = issuing.create_issue(actor=self.admin, missionary_id=person.pk,
            lines=[{'item_id': str(item.pk), 'quantity': 2}], idempotency_key='roundtrip-identity')
        original_ids = set(Missionary.objects.values_list('pk', flat=True))
        response = self.client.get('/reports/roster.csv')
        rows = read_table(filename='export.csv', content=response.content)
        for mode in ('add_update', 'replace'):
            with self.subTest(mode=mode):
                result = build_preview(kind='roster', rows=rows, mode=mode)
                self.assertFalse(result['errors'])
                self.assertEqual(result['valid'][0]['external_id'], person.external_id)
                preview = RowImportPreview.objects.create(
                    token=new_preview_token(), kind='roster', mode=mode,
                    rows=[{**r, 'error': False} for r in result['valid']],
                    base_revision=current_revision('roster'), created_by=self.admin)
                applied = confirm_row_import(actor=self.admin, preview=preview, selected=None)
                self.assertEqual(applied['created'], 0)
                self.assertEqual(applied['archived'], 0)
                self.assertEqual(set(Missionary.objects.values_list('pk', flat=True)), original_ids)
                person.refresh_from_db()
                issue.refresh_from_db()
                self.assertFalse(person.is_archived)
                self.assertEqual(issue.missionary_id, person.pk)

    def test_canonical_headers_win_regardless_of_order_and_legacy_alias_still_works(self):
        for headers, values in (
            (['id', 'external_id', 'name', 'full name'], ['internal', 'SYN-CANON', 'Canonical name', 'Alias name']),
            (['external_id', 'id', 'full name', 'name'], ['SYN-CANON', 'internal', 'Alias name', 'Canonical name']),
            (['id', 'full name'], ['SYN-CANON', 'Canonical name']),
        ):
            with self.subTest(headers=headers):
                result = build_preview(kind='roster', mode='add_update', rows=[headers, values])
                self.assertEqual(result['valid'][0]['external_id'], 'SYN-CANON')
                self.assertEqual(result['valid'][0]['name'], 'Canonical name')

    def test_account_creation_preserves_exact_password_including_spaces(self):
        self.client.post('/admin-area/reauth/', {'password': self.password})
        exact = '  SyntheticTemp984!  '
        response = self.client.post('/accounts/users/create/', {
            'username': 'exact-space-account', 'password': exact})
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username='exact-space-account')
        self.assertTrue(user.check_password(exact))
        self.assertFalse(user.check_password(exact.strip()))
        self.assertEqual(authenticate(username=user.username, password=exact), user)
        self.assertIsNone(authenticate(username=user.username, password=exact.strip()))

    def test_history_and_summary_search_match_edited_external_id_and_snapshot(self):
        person = make_missionary(self.admin, external_id='SYN-OLD-IDENTITY')
        item = make_item(self.admin)
        issue = issuing.create_issue(actor=self.admin, missionary_id=person.pk,
            lines=[{'item_id': str(item.pk), 'quantity': 2}], idempotency_key='edited-id-search')
        roster.update_missionary(actor=self.admin, missionary_id=person.pk,
            expected_version=person.version, external_id='SYN-CURRENT-IDENTITY')
        for query in ('SYN-OLD-IDENTITY', 'SYN-CURRENT-IDENTITY'):
            with self.subTest(query=query):
                screen = self.client.get('/reports/', {'family': 'per-person', 'q': query})
                self.assertContains(screen, 'SYN-CURRENT-IDENTITY')
                response = self.client.get('/reports/family/per-person/', {'q': query})
                rows = list(csv.DictReader(io.StringIO(response.content.decode())))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['given_out_net'], '2')
                history = self.client.get('/issue/history/', {'q': query})
                self.assertEqual([row.pk for row in history.context['issues']], [issue.pk])
        issue.refresh_from_db()
        self.assertEqual(issue.missionary_external_id, 'SYN-OLD-IDENTITY')
