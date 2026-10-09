"""Mutation/history/idempotency cases."""

from django.test import TestCase

from apps.common.errors import (
    AlreadyReversedError,
    InsufficientStockError,
    StaleVersionError,
    ValidationError,
)
from apps.common.errors import NegativeBalanceError
from apps.inventory import services as inv
from apps.issuing import services as issuing
from apps.issuing.models import Issue
from tests.factories import make_admin, make_item, make_missionary


class ReceiptTests(TestCase):
    def setUp(self):
        self.admin, _ = make_admin()

    def test_receipt_requires_reason(self):
        item = make_item(self.admin)
        with self.assertRaises(ValidationError):
            inv.receive_stock(
                actor=self.admin, item_id=item.pk, quantity=2, reason="  ",
            )

    def test_receipt_quantity_must_be_positive(self):
        item = make_item(self.admin)
        for bad in (0, -3):
            with self.assertRaises(ValidationError):
                inv.receive_stock(
                    actor=self.admin, item_id=item.pk, quantity=bad,
                    reason="restock",
                )

    def test_receipt_idempotent(self):
        item = make_item(self.admin, opening_qty=5)
        inv.receive_stock(
            actor=self.admin, item_id=item.pk, quantity=3, reason="r",
            idempotency_key="idem-rx-1",
        )
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 8)
        inv.receive_stock(
            actor=self.admin, item_id=item.pk, quantity=3, reason="r",
            idempotency_key="idem-rx-1",
        )
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 8)


class CountTests(TestCase):
    def setUp(self):
        self.admin, _ = make_admin()

    def test_negative_count_rejected(self):
        item = make_item(self.admin)
        with self.assertRaises(NegativeBalanceError):
            inv.adjust_count(
                actor=self.admin, item_id=item.pk, counted_qty=-1,
                reason="recount", expected_version=item.version,
            )

    def test_count_requires_reason(self):
        item = make_item(self.admin)
        with self.assertRaises(ValidationError):
            inv.adjust_count(
                actor=self.admin, item_id=item.pk, counted_qty=3,
                reason=" ", expected_version=item.version,
            )

    def test_stale_count_rejected(self):
        item = make_item(self.admin, opening_qty=10)
        stale = item.version
        inv.receive_stock(
            actor=self.admin, item_id=item.pk, quantity=1, reason="r",
            idempotency_key="stale-rx",
        )
        with self.assertRaises(StaleVersionError):
            inv.adjust_count(
                actor=self.admin, item_id=item.pk, counted_qty=5,
                reason="recount", expected_version=stale,
            )
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 11)


class RenameHistoryTests(TestCase):
    def test_renames_do_not_rewrite_issue_snapshots(self):
        from apps.roster import services as roster

        admin = self.admin if hasattr(self, "admin") else make_admin()[0]
        admin, _ = make_admin()
        m = make_missionary(admin, name="Original Name")
        item = make_item(admin, opening_qty=10)
        issue = issuing.create_issue(
            actor=admin, missionary_id=m.pk,
            lines=[{"item_id": str(item.pk), "quantity": 2}],
            idempotency_key="rename-issue-1",
        )
        roster.update_missionary(
            actor=admin, missionary_id=m.pk, expected_version=m.version,
            name="Renamed Person",
        )
        item.refresh_from_db()
        inv.update_item(
            actor=admin, item_id=item.pk, expected_version=item.version,
            name="Renamed Item",
        )
        issue.refresh_from_db()
        line = issue.lines.get()
        self.assertEqual(issue.missionary_name, "Original Name")
        self.assertNotEqual(issue.missionary_name, "Renamed Person")
        self.assertNotEqual(line.item_name, "Renamed Item")


class IssueAtomicityTests(TestCase):
    def setUp(self):
        self.admin, _ = make_admin()

    def test_shortage_returns_proposal_without_writing(self):
        m = make_missionary(self.admin)
        a = make_item(self.admin, opening_qty=3)
        b = make_item(self.admin, opening_qty=10)
        before_a = a.on_hand
        count_before = Issue.objects.count()
        with self.assertRaises(InsufficientStockError) as ctx:
            issuing.create_issue(
                actor=self.admin, missionary_id=m.pk,
                lines=[
                    {"item_id": str(a.pk), "quantity": 5},
                    {"item_id": str(b.pk), "quantity": 2},
                ],
                idempotency_key="short-1",
            )
        err = ctx.exception
        self.assertTrue(err.shortages)
        self.assertTrue(err.proposal)
        self.assertEqual(Issue.objects.count(), count_before)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.on_hand, before_a)
        self.assertEqual(b.on_hand, 10)

    def test_confirmation_rechecks_and_succeeds_with_revised_list(self):
        m = make_missionary(self.admin)
        a = make_item(self.admin, opening_qty=3)
        try:
            issuing.create_issue(
                actor=self.admin, missionary_id=m.pk,
                lines=[{"item_id": str(a.pk), "quantity": 5}],
                idempotency_key="short-2",
            )
            self.fail("expected shortage")
        except InsufficientStockError as err:
            revised = err.proposal
        issue = issuing.create_issue(
            actor=self.admin, missionary_id=m.pk, lines=revised,
            idempotency_key="short-2-confirm",
        )
        self.assertEqual(issue.lines.get().quantity, 3)

    def test_whole_issue_rolls_back_on_partial_shortage(self):
        m = make_missionary(self.admin)
        a = make_item(self.admin, opening_qty=10)
        b = make_item(self.admin, opening_qty=1)
        with self.assertRaises(InsufficientStockError):
            issuing.create_issue(
                actor=self.admin, missionary_id=m.pk,
                lines=[
                    {"item_id": str(a.pk), "quantity": 4},
                    {"item_id": str(b.pk), "quantity": 9},
                ],
                idempotency_key="rollback-1",
            )
        a.refresh_from_db()
        self.assertEqual(a.on_hand, 10)

    def test_issue_idempotency_prevents_replay(self):
        m = make_missionary(self.admin)
        item = make_item(self.admin, opening_qty=10)
        first = issuing.create_issue(
            actor=self.admin, missionary_id=m.pk,
            lines=[{"item_id": str(item.pk), "quantity": 2}],
            idempotency_key="replay-1",
        )
        second = issuing.create_issue(
            actor=self.admin, missionary_id=m.pk,
            lines=[{"item_id": str(item.pk), "quantity": 2}],
            idempotency_key="replay-1",
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Issue.objects.filter(idempotency_key="replay-1").count(), 1)
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 8)

    def test_archived_records_reject_new_issues(self):
        m = make_missionary(self.admin)
        item = make_item(self.admin, opening_qty=10)
        inv.archive_item(actor=self.admin, item_id=item.pk)
        with self.assertRaises(ValidationError):
            issuing.create_issue(
                actor=self.admin, missionary_id=m.pk,
                lines=[{"item_id": str(item.pk), "quantity": 1}],
                idempotency_key="arch-item-1",
            )


class ReversalTests(TestCase):
    def setUp(self):
        self.admin, _ = make_admin()

    def _issued(self, qty=4, opening=10):
        m = make_missionary(self.admin)
        item = make_item(self.admin, opening_qty=opening)
        issue = issuing.create_issue(
            actor=self.admin, missionary_id=m.pk,
            lines=[{"item_id": str(item.pk), "quantity": qty}],
            idempotency_key=f"rev-{item.pk}-{qty}-{m.pk}",
        )
        item.refresh_from_db()
        return m, item, issue

    def test_reverse_restores_stock_once_with_reason(self):
        m, item, issue = self._issued()
        self.assertEqual(item.on_hand, 6)
        issuing.reverse_issue(
            actor=self.admin, issue_id=issue.pk, reason="returned unused"
        )
        item.refresh_from_db()
        issue.refresh_from_db()
        self.assertEqual(item.on_hand, 10)
        self.assertEqual(issue.status, Issue.REVERSED)
        self.assertEqual(inv.net_out(item.pk), 0)

    def test_reverse_requires_reason(self):
        m, item, issue = self._issued()
        with self.assertRaises(ValidationError):
            issuing.reverse_issue(actor=self.admin, issue_id=issue.pk, reason=" ")

    def test_double_reversal_fails_safely(self):
        m, item, issue = self._issued()
        issuing.reverse_issue(actor=self.admin, issue_id=issue.pk, reason="first")
        item.refresh_from_db()
        before = item.on_hand
        with self.assertRaises(AlreadyReversedError):
            issuing.reverse_issue(actor=self.admin, issue_id=issue.pk, reason="second")
        item.refresh_from_db()
        self.assertEqual(item.on_hand, before)

    def test_reverse_allows_archived_records(self):
        from apps.roster import services as roster

        m, item, issue = self._issued()
        m.refresh_from_db()
        roster.archive_missionary(actor=self.admin, missionary_id=m.pk)
        inv.archive_item(actor=self.admin, item_id=item.pk)
        issuing.reverse_issue(actor=self.admin, issue_id=issue.pk, reason="undo")
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 10)
