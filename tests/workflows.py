"""Milestone 02 route/permission/workflow cases (synthetic data only)."""

import re
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.inventory.models import Item
from apps.issuing.models import Issue
from tests.factories import make_admin, make_item, make_missionary, make_staff

User = get_user_model()


def _non_staff():
    user = User.objects.create_user(username=f"plain-{uuid.uuid4().hex[:8]}", password="PlainPass123!")
    user.is_staff = False
    user.is_superuser = False
    user.must_change_password = False
    user.save()
    return user


class RouteAccessTests(TestCase):
    def setUp(self):
        self.staff, self.pw = make_staff()

    def test_login_required_for_staff_screens(self):
        for url in ["/", "/roster/", "/inventory/", "/issue/", "/reports/", "/guide/",
                    "/issue/history/"]:
            response = self.client.get(url)
            self.assertIn(response.status_code, (302, 301), url)
        # Poll endpoints answer JSON 401 (not a login redirect) so background
        # polling can tell "sign-in expired" apart from "offline".
        for url in ["/api/stock/", "/api/dashboard/"]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 401, url)
            self.assertEqual(response.json()["error"], "sign-in-required")

    def test_staff_can_open_all_screens(self):
        self.client.login(username=self.staff.username, password=self.pw)
        for url in ["/", "/roster/", "/inventory/", "/issue/", "/reports/", "/guide/",
                    "/issue/history/"]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)

    def test_non_staff_gets_403(self):
        user = _non_staff()
        self.client.force_login(user)
        for url in ["/", "/roster/", "/inventory/", "/issue/", "/reports/"]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, url)

    def test_admin_tools_hidden_from_staff(self):
        staff, pw = make_staff()
        self.client.login(username=staff.username, password=pw)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "/accounts/users/")


class PlainLanguageTests(TestCase):
    def setUp(self):
        self.staff, self.pw = make_staff()
        self.client.login(username=self.staff.username, password=self.pw)

    def test_plain_labels_and_no_jargon_in_nav(self):
        for url in ["/", "/issue/", "/inventory/", "/reports/"]:
            response = self.client.get(url)
            content = response.content.decode()
            self.assertIn("Issue goods", content, url)
        inv_item = make_item(self.staff, opening_qty=5)
        response = self.client.get(f"/inventory/{inv_item.pk}/")
        content = response.content.decode()
        self.assertIn("Available", content)
        self.assertIn("Add stock", content)
        self.assertIn("Correct stock count", content)
        self.assertNotIn("ledger", content.lower())
        self.assertNotIn("movement", content.lower())

    def test_issue_detail_uses_undo_language(self):
        m = make_missionary(self.staff)
        item = make_item(self.staff, opening_qty=5)
        self.client.post("/issue/", {
            "missionary_id": str(m.pk), "note": "",
            "idempotency_key": f"plain-{uuid.uuid4().hex}",
            "item-0": str(item.pk), "qty-0": "1",
            "item-1": "", "qty-1": "", "item-2": "", "qty-2": "",
        })
        issue = Issue.objects.get()
        response = self.client.get(f"/issue/{issue.pk}/")
        content = response.content.decode()
        self.assertIn("Undo this issue", content)
        self.assertIn(issue.issued_by_username, content)

    def test_contextual_help_and_guide_link_everywhere(self):
        for url in ["/", "/roster/", "/inventory/", "/issue/", "/reports/"]:
            response = self.client.get(url)
            content = response.content.decode()
            self.assertIn("guide", content.lower(), url)
            self.assertIn("help", content.lower(), url)
        response = self.client.get("/guide/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Issue goods", response.content.decode())

    def test_quick_start_doc_exists_with_synthetic_illustrations(self):
        from pathlib import Path
        doc = Path("docs/QUICK_START.md")
        self.assertTrue(doc.exists())
        text = doc.read_text()
        for needle in ["Sign in", "Issue goods", "Add stock", "Undo", "Available"]:
            self.assertIn(needle, text)
        self.assertIn("Synthetic", text)


class IssueFlowTests(TestCase):
    def setUp(self):
        self.staff, self.pw = make_staff()
        self.client.login(username=self.staff.username, password=self.pw)

    def _post_issue(self, missionary, lines, key=None, note=""):
        data = {"missionary_id": str(missionary.pk), "note": note,
                "idempotency_key": key or f"k-{uuid.uuid4().hex}"}
        for i, (item, qty) in enumerate(lines):
            data[f"item-{i}"] = str(item.pk)
            data[f"qty-{i}"] = str(qty)
        for i in range(len(lines), 3):
            data[f"item-{i}"] = ""
            data[f"qty-{i}"] = ""
        return self.client.post("/issue/", data)

    def test_main_issue_commits_and_shows_history(self):
        m = make_missionary(self.staff)
        item = make_item(self.staff, opening_qty=10)
        response = self._post_issue(m, [(item, 3)])
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 7)
        response = self.client.get("/issue/history/")
        self.assertContains(response, m.name)

    def test_duplicate_item_lines_consolidate(self):
        m = make_missionary(self.staff)
        item = make_item(self.staff, opening_qty=10)
        response = self._post_issue(m, [(item, 2), (item, 3)])
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 5)
        issue = Issue.objects.get()
        self.assertEqual(issue.lines.get().quantity, 5)

    def test_shortage_shows_revision_and_confirms(self):
        m = make_missionary(self.staff)
        item = make_item(self.staff, opening_qty=3)
        response = self._post_issue(m, [(item, 5)], key="short-ui-1")
        self.assertEqual(response.status_code, 409)
        content = response.content.decode()
        self.assertIn("You asked for", content)
        self.assertIn("Available now", content)
        self.assertIn("Revised quantity", content)
        self.assertIn("Confirm revised quantities", content)
        # Nothing was written.
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 3)
        self.assertEqual(Issue.objects.count(), 0)
        # Confirm the revised list via the hidden revised-quantity fields.
        p_items = re.findall(r'name="p-item" value="([^"]+)"', content)
        p_qtys = re.findall(r'name="p-qty" value="([^"]+)"', content)
        self.assertTrue(p_items)
        post_data = [("missionary_id", str(m.pk)), ("note", ""),
                     ("idempotency_key", "short-ui-1-confirm")]
        for i, q in zip(p_items, p_qtys):
            post_data.append(("p-item", i))
            post_data.append(("p-qty", q))
        # QueryDict preserves repeated hidden fields.
        from django.http import QueryDict
        qd = QueryDict(mutable=True)
        for k, v in post_data:
            qd.appendlist(k, v)
        response = self.client.post("/issue/confirm/", qd)
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 0)

    def test_duplicate_click_writes_once(self):
        m = make_missionary(self.staff)
        item = make_item(self.staff, opening_qty=10)
        key = f"dbl-{uuid.uuid4().hex}"
        r1 = self._post_issue(m, [(item, 2)], key=key)
        r2 = self._post_issue(m, [(item, 2)], key=key)
        self.assertEqual(r1.status_code, 302)
        self.assertEqual(Issue.objects.filter(idempotency_key=key).count(), 1)
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 8)

    def test_draft_kept_after_validation_error(self):
        m = make_missionary(self.staff)
        item = make_item(self.staff, opening_qty=10)
        response = self.client.post("/issue/", {
            "missionary_id": str(m.pk), "note": "keep me",
            "idempotency_key": "draft-keep-1",
            "item-0": str(item.pk), "qty-0": "abc",
            "item-1": "", "qty-1": "", "item-2": "", "qty-2": "",
        })
        self.assertEqual(response.status_code, 400)
        content = response.content.decode()
        self.assertIn("keep me", content)
        self.assertIn(str(item.pk), content)

    def test_archived_absent_from_new_issue_selectors(self):
        from apps.inventory import services as inv
        from apps.roster import services as roster

        m = make_missionary(self.staff, name="Soon Archived")
        item = make_item(self.staff, opening_qty=5)
        roster.archive_missionary(actor=self.staff, missionary_id=m.pk)
        inv.archive_item(actor=self.staff, item_id=item.pk)
        response = self.client.get("/issue/")
        content = response.content.decode()
        self.assertNotIn("Soon Archived", content)
        self.assertNotIn(str(item.pk), content)
        # But archived history still opens.
        m2 = make_missionary(self.staff)
        item2 = make_item(self.staff, opening_qty=5)
        self._post_issue(m2, [(item2, 1)])
        issue = Issue.objects.get()
        roster.archive_missionary(actor=self.staff, missionary_id=m2.pk)
        inv.archive_item(actor=self.staff, item_id=item2.pk)
        response = self.client.get(f"/issue/{issue.pk}/")
        self.assertEqual(response.status_code, 200)

    def test_quick_profile_issue_uses_same_service(self):
        m = make_missionary(self.staff)
        item = make_item(self.staff, opening_qty=6)
        response = self.client.post("/issue/quick/", {
            "missionary_id": str(m.pk), "item_id": str(item.pk),
            "quantity": "2", "idempotency_key": f"q-{uuid.uuid4().hex}",
        })
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 4)


class ReversalFlowTests(TestCase):
    def setUp(self):
        self.staff, self.pw = make_staff()
        self.client.login(username=self.staff.username, password=self.pw)

    def test_undo_requires_reason_and_shows_actors(self):
        from tests.factories import make_staff as _ms
        other, _ = _ms()
        m = make_missionary(self.staff)
        item = make_item(self.staff, opening_qty=10)
        self.client.post("/issue/", {
            "missionary_id": str(m.pk), "note": "",
            "idempotency_key": f"rev-ui-{uuid.uuid4().hex}",
            "item-0": str(item.pk), "qty-0": "4",
            "item-1": "", "qty-1": "", "item-2": "", "qty-2": "",
        })
        issue = Issue.objects.get()
        # Empty reason keeps the issue active.
        response = self.client.post(f"/issue/{issue.pk}/undo/", {"reason": " "})
        self.assertEqual(response.status_code, 302)
        issue.refresh_from_db()
        self.assertEqual(issue.status, "ACTIVE")
        response = self.client.post(
            f"/issue/{issue.pk}/undo/", {"reason": "wrong person selected"}
        )
        self.assertEqual(response.status_code, 302)
        issue.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(issue.status, "REVERSED")
        self.assertEqual(item.on_hand, 10)
        response = self.client.get(f"/issue/{issue.pk}/")
        content = response.content.decode()
        self.assertIn(issue.issued_by_username, content)
        self.assertIn(self.staff.username, content)
        self.assertIn("wrong person selected", content)
        # Reports use net issuance.
        response = self.client.get("/reports/")
        self.assertContains(response, "Given out (net)")

    def test_double_undo_fails_safely(self):
        m = make_missionary(self.staff)
        item = make_item(self.staff, opening_qty=10)
        self.client.post("/issue/", {
            "missionary_id": str(m.pk), "note": "",
            "idempotency_key": f"rev2-{uuid.uuid4().hex}",
            "item-0": str(item.pk), "qty-0": "2",
            "item-1": "", "qty-1": "", "item-2": "", "qty-2": "",
        })
        issue = Issue.objects.get()
        self.client.post(f"/issue/{issue.pk}/undo/", {"reason": "first"})
        response = self.client.post(f"/issue/{issue.pk}/undo/", {"reason": "second"})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 10)


class StockCorrectionFlowTests(TestCase):
    def setUp(self):
        self.staff, self.pw = make_staff()
        self.client.login(username=self.staff.username, password=self.pw)

    def test_add_stock_and_correct_count_via_ui(self):
        item = make_item(self.staff, opening_qty=5)
        response = self.client.post(f"/inventory/{item.pk}/add-stock/", {
            "quantity": "3", "reason": "delivery", "idempotency_key": "ui-rx-9",
        })
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 8)
        response = self.client.post(f"/inventory/{item.pk}/correct-count/", {
            "counted_qty": "6", "reason": "recount",
            "expected_version": str(item.version),
            "idempotency_key": "ui-ct-9",
        })
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 6)
        response = self.client.get(f"/inventory/{item.pk}/")
        content = response.content.decode()
        self.assertIn("Given out (net)", content)
        self.assertIn("Count corrections", content)

    def test_stale_count_shows_error_and_keeps_value(self):
        from apps.inventory import services as inv
        item = make_item(self.staff, opening_qty=10)
        stale = item.version
        inv.receive_stock(actor=self.staff, item_id=item.pk, quantity=1,
                          reason="r", idempotency_key="stale-ui-rx")
        response = self.client.post(f"/inventory/{item.pk}/correct-count/", {
            "counted_qty": "5", "reason": "recount",
            "expected_version": str(stale), "idempotency_key": "stale-ui-ct",
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'value="5"', status_code=400)
        self.assertContains(response, 'value="recount"', status_code=400)
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 11)


class PollSessionTests(TestCase):
    def setUp(self):
        self.staff, self.pw = make_staff()
        self.client.login(username=self.staff.username, password=self.pw)
        self.client.get("/")

    def test_stock_and_dashboard_polls_do_not_extend_session(self):
        from django.utils import timezone
        session = self.client.session
        old = timezone.now().timestamp() - 100
        session["last_activity"] = old
        session.save()
        self.client.get("/api/stock/")
        self.assertAlmostEqual(float(self.client.session["last_activity"]), old, places=2)
        self.client.get("/api/dashboard/")
        self.assertAlmostEqual(float(self.client.session["last_activity"]), old, places=2)
        self.client.get("/")
        self.assertGreater(float(self.client.session["last_activity"]), old)

    def test_poll_payload_has_no_personal_data(self):
        make_missionary(self.staff, name="Private Person Should Not Leak")
        response = self.client.get("/api/stock/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Private Person Should Not Leak", response.content.decode())
