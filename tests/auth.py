"""Identity/permission/session cases."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts import lifecycle, throttling
from tests.factories import make_admin, make_staff

User = get_user_model()


class TempPasswordTests(TestCase):
    def test_new_account_must_change_password_before_app_access(self):
        admin, _ = make_admin()
        user = lifecycle.create_user(
            actor=admin, username="newstaff1", password="Temp12345!"
        )
        self.assertTrue(user.must_change_password)
        self.client.force_login(user)
        response = self.client.get("/accounts/users/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/password-change/", response["Location"])

    def test_password_change_clears_gate(self):
        admin, _ = make_admin()
        user = lifecycle.create_user(
            actor=admin, username="newstaff2", password="Temp12345!"
        )
        self.client.force_login(user)
        response = self.client.post(
            "/accounts/password-change/",
            {
                "old_password": "Temp12345!",
                "new_password1": "BrandNewPass123!",
                "new_password2": "BrandNewPass123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertFalse(user.must_change_password)

    def test_temp_password_gate_also_covers_admin_site(self):
        admin, _ = make_admin()
        user = lifecycle.create_user(
            actor=admin, username="newstaff-admin", password="Temp12345!"
        )
        self.client.force_login(user)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/password-change/", response["Location"])

    def test_ui_password_change_records_audit_event(self):
        from apps.audit.models import AuditEvent

        admin, _ = make_admin()
        user = lifecycle.create_user(
            actor=admin, username="newstaff-audit", password="Temp12345!"
        )
        self.client.force_login(user)
        self.client.post(
            "/accounts/password-change/",
            {
                "old_password": "Temp12345!",
                "new_password1": "BrandNewPass123!",
                "new_password2": "BrandNewPass123!",
            },
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action="user.password_changed", target_id=str(user.pk)
            ).exists()
        )


class PermissionEndpointTests(TestCase):
    def test_admin_view_requires_admin(self):
        staff, pw = make_staff()
        self.client.login(username=staff.username, password=pw)
        response = self.client.get("/accounts/users/")
        self.assertEqual(response.status_code, 403)
        self.client.logout()
        admin, admin_pw = make_admin()
        self.client.login(username=admin.username, password=admin_pw)
        response = self.client.get("/accounts/users/")
        self.assertEqual(response.status_code, 200)

    def test_staff_cannot_provision_users(self):
        from apps.common.errors import PermissionDeniedError

        staff, _ = make_staff()
        with self.assertRaises(PermissionDeniedError):
            lifecycle.create_user(actor=staff, username="x", password="y123456!")

    def test_deactivation_invalidates_session(self):
        admin, admin_pw = make_admin()
        staff, staff_pw = make_staff()
        # Staff logs in with a separate client.
        staff_client = self.client_class()
        self.assertTrue(
            staff_client.login(username=staff.username, password=staff_pw)
        )
        response = staff_client.get("/accounts/users/")
        self.assertIn(response.status_code, (302, 403))
        lifecycle.deactivate_user(actor=admin, user_id=staff.pk)
        staff.refresh_from_db()
        self.assertFalse(staff.is_active)
        response = staff_client.get("/accounts/users/")
        # Deactivated account is logged out and sent to login.
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])


class IdleTimeoutTests(TestCase):
    def test_poll_does_not_extend_idle_timer_but_app_request_does(self):
        staff, pw = make_staff()
        self.client.login(username=staff.username, password=pw)
        # Prime the timer via a normal response.
        self.client.get("/accounts/users/")
        session = self.client.session
        old = timezone.now().timestamp() - 100
        session["last_activity"] = old
        session.save()
        self.client.get("/poll/")
        self.assertAlmostEqual(
            float(self.client.session["last_activity"]), old, places=2
        )
        self.client.get("/accounts/users/")
        refreshed = float(self.client.session["last_activity"])
        self.assertGreater(refreshed, old)

    def test_idle_expiry_logs_out(self):
        staff, pw = make_staff()
        self.client.login(username=staff.username, password=pw)
        session = self.client.session
        session["last_activity"] = timezone.now().timestamp() - 2000
        session.save()
        response = self.client.get("/accounts/users/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
        # Session is expired: subsequent app access needs login.
        response = self.client.get("/accounts/users/")
        self.assertEqual(response.status_code, 302)


class ThrottlingTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_repeated_failures_lock_out_even_correct_password(self):
        staff, pw = make_staff(username="throttle-user")
        for _ in range(5):
            response = self.client.post(
                "/accounts/login/",
                {"username": "throttle-user", "password": "wrong!"},
            )
            self.assertEqual(response.status_code, 200)
        self.assertTrue(throttling.is_locked("throttle-user"))
        response = self.client.post(
            "/accounts/login/", {"username": "throttle-user", "password": pw}
        )
        self.assertContains(response, "Too many login attempts", status_code=200)
        throttling.clear("throttle-user")
        self.assertTrue(
            self.client.login(username="throttle-user", password=pw)
        )


class RecoveryCommandTests(TestCase):
    def test_recover_admin_promotes_without_password_argument(self):
        from django.core.management.base import CommandError

        staff, _ = make_staff(username="recover-me")
        self.assertFalse(User.objects.get(username="recover-me").is_superuser)
        with patch("getpass.getpass", side_effect=["TempPass123!", "TempPass123!"]):
            call_command("recover_admin", "--username", "recover-me")
        user = User.objects.get(username="recover-me")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.check_password("TempPass123!"))

    def test_recover_admin_takes_no_password_option(self):
        from apps.accounts.management.commands.recover_admin import Command

        cmd = Command()
        import argparse

        parser = argparse.ArgumentParser()
        cmd.add_arguments(parser)
        actions = [a.dest for a in parser._actions]
        self.assertNotIn("password", " ".join(actions).lower())
