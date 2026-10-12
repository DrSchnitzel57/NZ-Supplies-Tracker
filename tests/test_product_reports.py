"""Shared-filter, stable-identity and actual A4/Unicode PDF probes."""
import csv
import io
from pathlib import Path
from datetime import datetime, timezone

import json
import subprocess
import sys
from django.test import TestCase

from apps.inventory.models import Item
from apps.issuing import services as issuing
from apps.issuing.models import Issue
from apps.reports import services as reports
from apps.roster.models import Missionary
from tests.factories import make_staff, make_missionary, make_item


class FinishedReportTests(TestCase):
    def setUp(self):
        self.staff, _ = make_staff()
        self.client.force_login(self.staff)
        self.person = make_missionary(self.staff, name='Synthetic before')
        self.item = make_item(self.staff, opening_qty=999)

    def issue(self, quantity, key):
        return issuing.create_issue(actor=self.staff, missionary_id=self.person.pk,
            lines=[{'item_id': str(self.item.pk), 'quantity': quantity}], idempotency_key=key)

    def test_renames_keep_one_stable_summary_with_current_label_and_history_snapshot(self):
        first = self.issue(2, 'first')
        Missionary.objects.filter(pk=self.person.pk).update(name='Synthetic current')
        Item.objects.filter(pk=self.item.pk).update(name='Current item')
        self.issue(3, 'second')
        people = list(csv.DictReader(io.StringIO(reports.report_csv('per-person').content.decode())))
        items = list(csv.DictReader(io.StringIO(reports.report_csv('per-item').content.decode())))
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0]['given_out_net'], '5')
        self.assertIn('Synthetic current', people[0]['person'])
        self.assertEqual(len(items), 1)
        self.assertIn('Current item', items[0]['item'])
        first.refresh_from_db()
        self.assertEqual(first.missionary_name, 'Synthetic before')

    def test_selected_archived_report_screen_and_download_share_filter(self):
        Item.objects.filter(pk=self.item.pk).update(is_archived=True)
        response = self.client.get('/reports/', {'family': 'stock', 'archived': '1'})
        self.assertContains(response, 'archived=1')
        self.assertContains(response, str(self.item))
        no_archives = self.client.get('/reports/inventory.csv').content.decode()
        self.assertNotIn(self.item.name, no_archives)
        included = self.client.get('/reports/inventory.csv', {'archived': '1'}).content.decode()
        self.assertIn(self.item.name, included)

    def test_local_date_filters_current_net_not_reversal_date_or_stock_asof(self):
        old = self.issue(2, 'old')
        current = self.issue(3, 'current')
        undone = self.issue(4, 'undone')
        Issue.objects.filter(pk=old.pk).update(issued_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
        for issue in (current, undone):
            Issue.objects.filter(pk=issue.pk).update(issued_at=datetime(2026, 10, 11, 12, 30, tzinfo=timezone.utc))
        issuing.reverse_issue(actor=self.staff, issue_id=undone.pk, reason='Synthetic mistake')
        params = {'start': '2026-10-12', 'end': '2026-10-12', 'missionary': str(self.person.pk)}
        response = self.client.get('/reports/family/per-person/', params)
        rows = list(csv.DictReader(io.StringIO(response.content.decode())))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['given_out_net'], '3')
        history = self.client.get('/issue/history/', {**params, 'status': 'REVERSED'})
        self.assertEqual([i.pk for i in history.context['issues']], [undone.pk])

    def test_more_than_200_history_records_are_reachable_and_exports_complete(self):
        Issue.objects.bulk_create([Issue(missionary=self.person, missionary_name=f'Synthetic row {i}',
            missionary_external_id=self.person.external_id, issued_by=self.staff,
            issued_by_username=self.staff.username, idempotency_key=f'bulk-{i}') for i in range(205)])
        last = self.client.get('/issue/history/', {'page': '5'})
        self.assertEqual(len(last.context['issues']), 5)
        rows = list(csv.DictReader(io.StringIO(reports.report_csv('history').content.decode())))
        self.assertEqual(len(rows), 205)

    def test_pdf_embeds_macron_font_wraps_long_supported_fields_inside_a4(self):
        Item.objects.filter(pk=self.item.pk).update(name='Māori āēīōū ĀĒĪŌŪ café ' + 'verylongdescription' * 10)
        self.item.refresh_from_db()
        self.issue(3, 'unicode')
        data = reports.report_pdf('stock').content
        evidence = Path('.local/review')
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / 'stock-unicode.pdf').write_bytes(data)
        document = json.loads(subprocess.check_output([sys.executable, 'tests/pdf_probe.py',
            str(evidence / 'stock-unicode.pdf'), str(evidence / 'stock-unicode.png')]))
        text = ''.join(page['text'] for page in document)
        self.assertIn('āēīōū', text)
        self.assertIn('ĀĒĪŌŪ', text)
        self.assertIn('café', text)
        for page in document:
            self.assertAlmostEqual(page['width'], 595.28, places=1)
            self.assertAlmostEqual(page['height'], 841.89, places=1)
            for word in page['words']:
                self.assertGreaterEqual(word[0], 20)
                self.assertLessEqual(word[2], page['width'] - 20)
                self.assertLessEqual(word[3], page['height'] - 20)
            self.assertTrue(any('NotoSans' in font[3] for font in page['fonts']))
            self.assertTrue(page['noto_embedded'])
            # Actually rasterize, rather than treating a %PDF header as evidence.
            self.assertGreater(page['pixels'], 400_000)

    def test_long_history_and_complete_roster_pdfs_wrap_and_repeat_headers(self):
        issue = self.issue(2, 'long-pdf')
        self.issue(2, 'long-pdf-second')
        Issue.objects.filter(pk=issue.pk).update(note='Māori long note ' * 260)
        Missionary.objects.filter(pk=self.person.pk).update(personal_email='synthetic-personal@example.test',
            intake_metadata={'source_note': 'Synthetic provenance ' * 200})
        make_missionary(self.staff, name='Synthetic second record')
        for family, fragment in [('history', 'Māori long note'), ('roster', 'synthetic-personal@example.test')]:
            evidence = Path('.local/review')
            evidence.mkdir(parents=True, exist_ok=True)
            path = evidence / f'long-{family}.pdf'
            path.write_bytes(reports.report_pdf(family).content)
            document = json.loads(subprocess.check_output([sys.executable, 'tests/pdf_probe.py', str(path)]))
            text = ''.join(page['text'] for page in document)
            self.assertIn(fragment, text)
            self.assertGreaterEqual(len(document), 2)
            for page in document:
                self.assertIn('Field', page['text'])
                for word in page['words']:
                    self.assertGreaterEqual(word[0], 20)
                    self.assertLessEqual(word[2], page['width'] - 20)
                    self.assertLessEqual(word[3], page['height'] - 20)
