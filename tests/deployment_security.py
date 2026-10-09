"""Deployment/security safeguards (synthetic data only)."""

from __future__ import annotations

import threading
import uuid

from django.conf import settings
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings

from apps.common.errors import InsufficientStockError
from apps.inventory import services as inv
from apps.issuing import services as issuing
from tests.factories import make_admin, make_item, make_missionary, make_staff


def _close_thread_connections():
    for conn in connections.all():
        conn.close_if_unusable_or_obsolete()


class ProductionConfigTests(TestCase):
    def test_production_requires_nonempty_allowed_hosts(self):
        self.assertTrue(settings.ALLOWED_HOSTS)

    def test_secure_cookie_flags_outside_debug(self):
        import os
        import subprocess
        import sys

        # Load the real settings module in production mode and assert the
        # secure flags are actually set (not just present as strings).
        code = (
            "import django, os;"
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings');"
            "django.setup();"
            "from django.conf import settings;"
            "assert settings.SESSION_COOKIE_SECURE is True;"
            "assert settings.CSRF_COOKIE_SECURE is True;"
            "assert settings.SECURE_SSL_REDIRECT is True;"
            "assert settings.SECURE_HSTS_SECONDS >= 31536000;"
            "assert settings.SECURE_REDIRECT_EXEMPT;"
            "print('production flags ok')")
        env = dict(os.environ, DJANGO_DEBUG="0",
                   DJANGO_SECRET_KEY="synthetic-test-secret-not-real-1234567890",
                   DJANGO_ALLOWED_HOSTS="127.0.0.1",
                   DJANGO_CSRF_TRUSTED_ORIGINS="https://127.0.0.1:8443")
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, timeout=120, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("production flags ok", proc.stdout)
        # In the shipped settings file the flags are conditional on not DEBUG.
        import pathlib
        text = pathlib.Path("config/settings.py").read_text()
        for needle in ["SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE",
                       "SECURE_SSL_REDIRECT", "SECURE_HSTS_SECONDS",
                       "CSRF_TRUSTED_ORIGINS", "DATA_UPLOAD_MAX_MEMORY_SIZE",
                       "FILE_UPLOAD_MAX_MEMORY_SIZE",
                       "DATA_UPLOAD_MAX_NUMBER_FIELDS",
                       "SECURE_REDIRECT_EXEMPT"]:
            self.assertIn(needle, text)

    def test_upload_and_throttle_bounds_are_sane(self):
        self.assertLessEqual(settings.DATA_UPLOAD_MAX_MEMORY_SIZE, 10 * 1024 * 1024)
        self.assertLessEqual(settings.FILE_UPLOAD_MAX_MEMORY_SIZE, 10 * 1024 * 1024)
        self.assertLessEqual(settings.DATA_UPLOAD_MAX_NUMBER_FIELDS, 10000)
        self.assertGreaterEqual(settings.LOGIN_THROTTLE_MAX_ATTEMPTS, 1)
        self.assertLessEqual(settings.LOGIN_THROTTLE_MAX_ATTEMPTS, 20)

    def test_secret_placeholder_rejected_outside_debug(self):
        import pathlib
        text = pathlib.Path("config/settings.py").read_text()
        self.assertIn("DJANGO_SECRET_KEY must be set", text)


class ProductionPermissionTests(TestCase):
    def setUp(self):
        self.admin, self.apw = make_admin()
        self.staff, self.spw = make_staff()

    def test_staff_blocked_from_admin_bundle_and_settings(self):
        self.client.login(username=self.staff.username, password=self.spw)
        self.assertEqual(self.client.get("/admin-area/settings/").status_code, 403)
        self.assertEqual(self.client.get("/transfers/bundle/").status_code, 403)
        self.assertEqual(self.client.get("/admin-area/reset/").status_code, 403)

    def test_health_and_readiness_reveal_no_pii_or_secrets(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode().lower()
        self.assertIn("ok", body)
        self.assertNotIn("secret", body)
        ready = self.client.get("/readyz/")
        self.assertIn(ready.status_code, (200, 503))
        rbody = ready.content.decode().lower()
        self.assertNotIn("secret", rbody)
        # Synthetic person names must never appear in health output.
        person = make_missionary(self.admin)
        again = self.client.get("/healthz/").content.decode()
        self.assertNotIn(person.name, again)

    def test_readiness_ok_when_migrated(self):
        response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])


class TenClientStockSafetyTests(TransactionTestCase):
    def test_ten_clients_cannot_drive_stock_negative(self):
        admin, _ = make_admin()
        item = make_item(admin, opening_qty=20)
        missionaries = [make_missionary(admin) for _ in range(10)]
        barrier = threading.Barrier(10)
        outcomes = [None] * 10

        def attempt(index):
            _close_thread_connections()
            barrier.wait(timeout=15)
            try:
                issuing.create_issue(
                    actor=admin, missionary_id=missionaries[index].pk,
                    lines=[{"item_id": str(item.pk), "quantity": 5}],
                    idempotency_key=f"ten-{uuid.uuid4().hex}",
                )
                outcomes[index] = "ok"
            except InsufficientStockError:
                outcomes[index] = "short"
            except Exception as exc:
                outcomes[index] = f"error:{type(exc).__name__}:{exc}"
            finally:
                _close_thread_connections()

        # Ten separate connections contend for 20 units at 5 each:
        # exactly 4 win, stock ends at 0, never negative.
        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        item.refresh_from_db()
        self.assertGreaterEqual(item.on_hand, 0)
        self.assertEqual(outcomes.count("ok"), 4)
        self.assertEqual(item.on_hand, 0)
        self.assertEqual(inv.ledger_balance(item.pk), 0)
