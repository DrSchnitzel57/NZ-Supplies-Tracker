"""Report permission, net-issuance and export cases (synthetic data only)."""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.issuing import services as issuing
from apps.issuing.models import Issue
from apps.reports import services as rep
from tests.factories import make_admin, make_item, make_missionary, make_staff

User = get_user_model()


def _non_staff():
    user = User.objects.create_user(username=f"plain-{uuid.uuid4().hex[:8]}", password="PlainPass123!")
    user.is_staff = False
    user.is_superuser = False
    user.must_change_password = False
    user.save()
    return user


class ReportPermissionTests(TestCase):
    def setUp(self):
        self.staff, self.pw = make_staff()
        self.admin, self.apw = make_admin()

    def test_staff_can_open_reports_and_exports(self):
        self.client.login(username=self.staff.username, password=self.pw)
        for url in ["/reports/", "/reports/roster.csv", "/reports/inventory.csv",
                    "/reports/family/per-item/", "/reports/family/history/?format=pdf"]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)

    def test_non_staff_forbidden(self):
        self.client.force_login(_non_staff())
        for url in ["/reports/", "/reports/roster.csv", "/reports/family/per-item/"]:
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_bundle_export_is_admin_only(self):
        self.client.login(username=self.staff.username, password=self.pw)
        self.assertEqual(self.client.get("/reports/bundle/export/").status_code, 403)
        self.client.login(username=self.admin.username, password=self.apw)
        self.assertEqual(self.client.get('/reports/bundle/export/').status_code, 302)
        self.client.post('/admin-area/reauth/', {'password': self.apw})
        response = self.client.get("/reports/bundle/export/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")


class NetReportingTests(TestCase):
    def setUp(self):
        self.staff, _ = make_staff()

    def _issue(self, m, item, qty, key):
        return issuing.create_issue(actor=self.staff, missionary_id=m.pk,
                                    lines=[{"item_id": str(item.pk), "quantity": qty}],
                                    idempotency_key=key)

    def test_net_excludes_reversed_issues_but_history_keeps_both(self):
        m = make_missionary(self.staff)
        item = make_item(self.staff, opening_qty=10)
        issue = self._issue(m, item, 4, f"net-{uuid.uuid4().hex}")
        issuing.reverse_issue(actor=self.staff, issue_id=issue.pk, reason="mistake")
        ctx = rep.report_context()
        self.assertEqual(ctx["per_item"], [])
        self.assertEqual(ctx["per_person"], [])
        statuses = {i.status for i in ctx["recent"]}
        self.assertIn("REVERSED", statuses)

    def test_active_and_archived_filters(self):
        from apps.inventory import services as inv
        from apps.roster import services as roster

        m = make_missionary(self.staff)
        item = make_item(self.staff, opening_qty=10)
        self._issue(m, item, 2, f"f-{uuid.uuid4().hex}")
        roster.archive_missionary(actor=self.staff, missionary_id=m.pk)
        inv.archive_item(actor=self.staff, item_id=item.pk)
        without = rep.report_context(include_archived=False)
        with_archived = rep.report_context(include_archived=True)
        self.assertEqual(without["per_item"], [])
        self.assertTrue(len(with_archived["per_item"]) >= 1)

    def test_csv_and_pdf_exports(self):
        m = make_missionary(self.staff)
        item = make_item(self.staff, opening_qty=6)
        self._issue(m, item, 2, f"e-{uuid.uuid4().hex}")
        roster = rep.roster_csv()
        self.assertIn("external_id", roster.content.decode())
        inventory = rep.inventory_csv()
        body = inventory.content.decode()
        self.assertIn("given_out_net", body)
        self.assertIn("2", body)
        pdf = rep.report_pdf("per-item")
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        for family in ["per-item", "per-person", "low-stock", "history"]:
            response = rep.report_csv(family)
            self.assertEqual(response.status_code, 200, family)

    def test_report_screen_uses_plain_language(self):
        self.client.force_login(self.staff)
        response = self.client.get("/reports/")
        content = response.content.decode()
        self.assertIn("Given out (net)", content)
        self.assertIn("Quick-start", content)
        self.assertNotIn("ledger", content.lower())
