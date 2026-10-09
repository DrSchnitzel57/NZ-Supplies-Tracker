"""Real separate-connection concurrency: threads with their own DB connections."""

from __future__ import annotations

import threading
import uuid

from django.db import connection, connections
from django.test import TransactionTestCase

from apps.common.errors import AlreadyReversedError, InsufficientStockError
from apps.inventory import services as inv
from apps.issuing import services as issuing
from apps.issuing.models import Issue
from tests.factories import make_admin, make_item, make_missionary


def _close_thread_connections():
    for conn in connections.all():
        conn.close_if_unusable_or_obsolete()


class CompetingIssuesTests(TransactionTestCase):
    def test_competing_issues_cannot_create_negative_stock(self):
        admin, _ = make_admin()
        item = make_item(admin, opening_qty=10)
        missionaries = [make_missionary(admin) for _ in range(5)]
        barrier = threading.Barrier(5)
        outcomes = [None] * 5

        def attempt(index):
            _close_thread_connections()
            key = f"race-{uuid.uuid4().hex}"
            barrier.wait(timeout=10)
            try:
                issuing.create_issue(
                    actor=admin, missionary_id=missionaries[index].pk,
                    lines=[{"item_id": str(item.pk), "quantity": 5}],
                    idempotency_key=key,
                )
                outcomes[index] = "ok"
            except InsufficientStockError:
                outcomes[index] = "short"
            except Exception as exc:  # pragma: no cover - surfaced below
                outcomes[index] = f"error:{type(exc).__name__}:{exc}"
            finally:
                _close_thread_connections()

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        item.refresh_from_db()
        self.assertGreaterEqual(item.on_hand, 0)
        self.assertEqual(outcomes.count("ok"), 2)
        self.assertEqual(item.on_hand, 0)
        self.assertEqual(inv.ledger_balance(item.pk), 0)
        self.assertEqual(inv.net_out(item.pk), 10)

    def test_duplicate_reversals_fail_safely(self):
        admin, _ = make_admin()
        m = make_missionary(admin)
        item = make_item(admin, opening_qty=10)
        issue = issuing.create_issue(
            actor=admin, missionary_id=m.pk,
            lines=[{"item_id": str(item.pk), "quantity": 3}],
            idempotency_key=f"dup-rev-{uuid.uuid4().hex}",
        )
        barrier = threading.Barrier(2)
        outcomes = [None, None]

        def attempt(index):
            _close_thread_connections()
            barrier.wait(timeout=10)
            try:
                issuing.reverse_issue(
                    actor=admin, issue_id=issue.pk, reason=f"undo {index}"
                )
                outcomes[index] = "ok"
            except AlreadyReversedError:
                outcomes[index] = "already"
            except Exception as exc:  # pragma: no cover
                outcomes[index] = f"error:{type(exc).__name__}"
            finally:
                _close_thread_connections()

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(sorted(outcomes), ["already", "ok"])
        item.refresh_from_db()
        issue.refresh_from_db()
        self.assertEqual(item.on_hand, 10)
        self.assertEqual(issue.status, Issue.REVERSED)

    def test_stale_counts_cannot_overwrite_newer_movements(self):
        from apps.common.errors import StaleVersionError

        admin, _ = make_admin()
        item = make_item(admin, opening_qty=10)
        stale_version = item.version
        barrier = threading.Barrier(2)
        outcomes = [None, None]

        def counter(index, counted):
            _close_thread_connections()
            barrier.wait(timeout=10)
            try:
                inv.adjust_count(
                    actor=admin, item_id=item.pk, counted_qty=counted,
                    reason=f"recount {index}", expected_version=stale_version,
                    idempotency_key=f"conc-count-{uuid.uuid4().hex}",
                )
                outcomes[index] = "ok"
            except StaleVersionError:
                outcomes[index] = "stale"
            except Exception as exc:  # pragma: no cover
                outcomes[index] = f"error:{type(exc).__name__}"
            finally:
                _close_thread_connections()

        threads = [
            threading.Thread(target=counter, args=(0, 7)),
            threading.Thread(target=counter, args=(1, 4)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        # Exactly one stale-versioned count wins; the loser sees the fresh
        # version and fails instead of silently overwriting newer work.
        self.assertEqual(sorted(outcomes), ["ok", "stale"])
        item.refresh_from_db()
        self.assertIn(item.on_hand, (7, 4))
        self.assertGreaterEqual(item.on_hand, 0)
        self.assertEqual(inv.ledger_balance(item.pk), item.on_hand)
        # Silence unused import warning for reviewers.
        self.assertTrue(connection.vendor == "sqlite")
