"""Foundation/schema cases: settings, invariants, ledger definitions, guards."""

from django.conf import settings
from django.db import IntegrityError, connection
from django.test import TestCase

from apps.accounts import services as permissions
from apps.common.errors import MaintenanceActiveError
from apps.inventory.models import Item, StockMovement
from tests.factories import make_admin, make_item, make_missionary


class SettingsFoundationTests(TestCase):
    def test_idle_expiry_is_thirty_minutes(self):
        self.assertEqual(settings.SESSION_IDLE_TIMEOUT, 1800)
        self.assertEqual(settings.SESSION_COOKIE_AGE, 1800)
        self.assertFalse(settings.SESSION_SAVE_EVERY_REQUEST)

    def test_sqlite_uses_immediate_transactions_with_bounded_timeout(self):
        options = settings.DATABASES["default"]["OPTIONS"]
        self.assertEqual(options["transaction_mode"], "IMMEDIATE")
        self.assertGreaterEqual(float(options["timeout"]), 1.0)
        self.assertLessEqual(float(options["timeout"]), 30.0)
        self.assertIn("journal_mode=WAL", options["init_command"])
        self.assertIn("busy_timeout", options["init_command"])

    def test_security_middleware_present(self):
        middleware = list(settings.MIDDLEWARE)
        self.assertIn("django.middleware.csrf.CsrfViewMiddleware", middleware)
        self.assertIn("apps.accounts.middleware.IdleTimeoutMiddleware", middleware)
        self.assertIn("apps.accounts.middleware.AccountStatusMiddleware", middleware)


class SchemaInvariantTests(TestCase):
    def setUp(self):
        self.admin, _ = make_admin()

    def test_external_id_unique(self):
        m1 = make_missionary(self.admin, external_id="EXT-DUP-1")
        self.assertTrue(m1.pk)
        with self.assertRaises(Exception):
            make_missionary(self.admin, external_id="EXT-DUP-1")

    def test_item_normalized_key_unique(self):
        make_item(self.admin, category="Soap", name="Bar", size="Large")
        with self.assertRaises(Exception):
            make_item(
                self.admin, category="  soap ", name="BAR", size=" large ",
            )

    def test_item_on_hand_cannot_go_negative(self):
        item = make_item(self.admin, opening_qty=0)
        item.on_hand = -1
        with self.assertRaises(IntegrityError):
            item.save()

    def test_movement_balance_cannot_go_negative(self):
        item = make_item(self.admin, opening_qty=1)
        with self.assertRaises(IntegrityError):
            StockMovement.objects.create(
                item=item,
                kind=StockMovement.RECEIPT,
                delta=-5,
                balance_after=-4,
                reason="bad",
                created_by=self.admin,
            )

    def test_version_tokens_default_zero_and_increment(self):
        m = make_missionary(self.admin)
        self.assertEqual(m.version, 0)
        item = make_item(self.admin)
        self.assertEqual(item.version, 0)

    def test_archival_fields_exist(self):
        m = make_missionary(self.admin)
        item = make_item(self.admin)
        self.assertFalse(m.is_archived)
        self.assertFalse(item.is_archived)
        self.assertIsNone(m.archived_at)


class RemainingAndOutTests(TestCase):
    def test_remaining_authoritative_and_net_out(self):
        from apps.inventory import services as inv
        from apps.issuing import services as issuing

        admin, _ = make_admin()
        item = make_item(admin, opening_qty=10)
        m = make_missionary(admin)
        self.assertEqual(inv.remaining(item), 10)
        self.assertEqual(inv.ledger_balance(item.pk), 10)
        self.assertEqual(inv.net_out(item.pk), 0)

        inv.receive_stock(
            actor=admin, item_id=item.pk, quantity=5,
            reason="restock", idempotency_key="rx-1",
        )
        item.refresh_from_db()
        self.assertEqual(inv.remaining(item.pk), 15)
        self.assertEqual(inv.ledger_balance(item.pk), 15)

        issuing.create_issue(
            actor=admin, missionary_id=m.pk,
            lines=[{"item_id": str(item.pk), "quantity": 4}],
            idempotency_key="issue-1",
        )
        item.refresh_from_db()
        self.assertEqual(inv.remaining(item.pk), 11)
        self.assertEqual(inv.ledger_balance(item.pk), 11)
        self.assertEqual(inv.net_out(item.pk), 4)

    def test_count_corrections_are_separate_from_issues(self):
        from apps.inventory import services as inv

        admin, _ = make_admin()
        item = make_item(admin, opening_qty=10)
        item.refresh_from_db()
        inv.adjust_count(
            actor=admin, item_id=item.pk, counted_qty=7,
            reason="found damaged box", expected_version=item.version,
            idempotency_key="count-1",
        )
        kinds = list(
            StockMovement.objects.filter(item=item).values_list("kind", flat=True)
        )
        self.assertIn(StockMovement.COUNT_ADJUST, kinds)
        self.assertEqual(inv.net_out(item.pk), 0)
        self.assertEqual(inv.remaining(item.pk), 7)


class PermissionServiceTests(TestCase):
    def test_staff_and_admin_predicates(self):
        admin, _ = make_admin()
        from tests.factories import make_staff

        staff, _ = make_staff()
        self.assertTrue(permissions.is_admin(admin))
        self.assertTrue(permissions.is_staff_member(admin))
        self.assertFalse(permissions.is_admin(staff))
        self.assertTrue(permissions.is_staff_member(staff))

    def test_require_helpers_raise(self):
        from django.contrib.auth.models import AnonymousUser

        from apps.common.errors import PermissionDeniedError
        from tests.factories import make_staff

        admin, _ = make_admin()
        staff, _ = make_staff()
        permissions.require_staff(staff)
        permissions.require_admin(admin)
        with self.assertRaises(PermissionDeniedError):
            permissions.require_staff(AnonymousUser())
        with self.assertRaises(PermissionDeniedError):
            permissions.require_admin(staff)


class MaintenanceGuardTests(TestCase):
    def test_guard_blocks_routine_writes(self):
        from apps.inventory import services as inv
        from apps.operations import guards

        admin, _ = make_admin()
        item = make_item(admin, opening_qty=5)
        with guards.maintenance_mode():
            self.assertTrue(guards.is_maintenance_active())
            with self.assertRaises(MaintenanceActiveError):
                inv.receive_stock(
                    actor=admin, item_id=item.pk, quantity=1,
                    reason="x", idempotency_key="blocked-1",
                )
        self.assertFalse(guards.is_maintenance_active())
        item.refresh_from_db()
        self.assertEqual(item.on_hand, 5)
