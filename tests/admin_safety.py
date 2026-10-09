"""Admin safeguards: accounts UI, settings and protected reset."""

import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.audit.models import AuditEvent
from apps.inventory.models import Item
from apps.operations import reset as reset_service
from apps.roster.models import Missionary
from tests.factories import make_admin, make_item, make_missionary, make_staff

User = get_user_model()


class AccountAdminTests(TestCase):
    def setUp(self):
        self.admin, self.apw = make_admin()
        self.staff, self.spw = make_staff()

    def _reauth(self):
        import time

        session = self.client.session
        session["admin_reauth"] = {
            "at": time.time(),
            "user_id": self.admin.pk,
            "pwd": self.admin.password,
        }
        session.save()

    def test_staff_cannot_use_account_tools(self):
        self.client.login(username=self.staff.username, password=self.spw)
        self.assertEqual(self.client.get("/accounts/users/").status_code, 403)
        self.assertEqual(self.client.post("/accounts/users/create/",
                                          {"username": "x", "password": "y"}).status_code, 403)

    def test_admin_creates_resets_and_deactivates_without_terminal(self):
        self.client.login(username=self.admin.username, password=self.apw)
        self._reauth()
        response = self.client.post("/accounts/users/create/",
                                    {"username": "new-staff-1", "password": "TempPass123!"})
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username="new-staff-1")
        self.assertTrue(user.must_change_password)
        # Temporary password reset invalidates sessions and forces change.
        response = self.client.post(f"/accounts/users/{user.pk}/reset-password/",
                                    {"password": "TempPass456!"})
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.must_change_password)
        # Deactivation blocks access; reactivation restores it.
        self.assertEqual(self.client.post(f"/accounts/users/{user.pk}/deactivate/").status_code, 302)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertEqual(self.client.post(f"/accounts/users/{user.pk}/reactivate/").status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_self_deactivation_blocked(self):
        self.client.login(username=self.admin.username, password=self.apw)
        self._reauth()
        response = self.client.post(f"/accounts/users/{self.admin.pk}/deactivate/")
        self.assertEqual(response.status_code, 302)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_admin_account_changes_need_fresh_reauth(self):
        self.client.login(username=self.admin.username, password=self.apw)
        response = self.client.post("/accounts/users/create/",
                                    {"username": "nope-1", "password": "TempPass123!"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(username="nope-1").exists())

    def test_settings_saved_by_admin_only(self):
        self.client.login(username=self.staff.username, password=self.spw)
        self.assertEqual(self.client.get("/admin-area/settings/").status_code, 403)
        self.client.login(username=self.admin.username, password=self.apw)
        self._reauth()
        response = self.client.post("/admin-area/settings/",
                                    {"site_notice": "Hello", "default_low_threshold": "3"})
        self.assertEqual(response.status_code, 302)
        from apps.operations.models import SystemSetting
        self.assertEqual(SystemSetting.objects.get(key="site_notice").value, "Hello")


class ProtectedResetTests(TestCase):
    def setUp(self):
        self.admin, self.apw = make_admin()
        self.staff, _ = make_staff()
        self.person = make_missionary(self.admin)
        self.item = make_item(self.admin, opening_qty=5)

    def _reauth(self):
        import time
        session = self.client.session
        session["admin_reauth"] = {
            "at": time.time(),
            "user_id": self.admin.pk,
            "pwd": self.admin.password,
        }
        session.save()

    def _counts(self):
        return {"expected_people": "1", "expected_items": "1", "expected_issues": "0"}

    def test_reset_requires_reauth_and_typed_confirmation(self):
        self.client.login(username=self.admin.username, password=self.apw)
        # No reauth -> redirected to confirm identity, nothing deleted.
        response = self.client.post("/admin-area/reset/", {"confirmation": "RESET"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Missionary.objects.count(), 1)
        # Reauth but wrong word -> refused.
        self._reauth()
        with mock.patch("apps.operations.backup.backup_sqlite",
                        return_value={"path": "x", "sha256": "y", "bytes": 1}):
            response = self.client.post("/admin-area/reset/",
                                        {"confirmation": "DELETE", **self._counts()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Missionary.objects.count(), 1)

    def test_reset_refuses_stale_confirmation_counts(self):
        self.client.login(username=self.admin.username, password=self.apw)
        self._reauth()
        make_missionary(self.admin)  # newer work after the screen rendered
        with mock.patch("apps.operations.backup.backup_sqlite",
                        return_value={"path": "x", "sha256": "y", "bytes": 1}):
            response = self.client.post("/admin-area/reset/",
                                        {"confirmation": "RESET", **self._counts()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Missionary.objects.count(), 2)

    def test_successful_reset_keeps_accounts_and_marker_only(self):
        self.client.login(username=self.admin.username, password=self.apw)
        self._reauth()
        with mock.patch("apps.operations.backup.backup_sqlite",
                        return_value={"path": "x", "sha256": "abc", "bytes": 10}):
            response = self.client.post("/admin-area/reset/",
                                        {"confirmation": "RESET", **self._counts()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Missionary.objects.count(), 0)
        self.assertEqual(Item.objects.count(), 0)
        self.assertTrue(User.objects.filter(username=self.admin.username).exists())
        markers = AuditEvent.objects.filter(action="system.reset")
        self.assertEqual(markers.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), 1)  # no hidden live copy
        # Marker carries no personal data.
        self.assertNotIn(self.person.name, str(markers.get().detail))

    def test_backup_failure_prevents_deletion(self):
        self.client.login(username=self.admin.username, password=self.apw)
        self._reauth()
        with mock.patch("apps.operations.backup.backup_sqlite",
                        side_effect=ValueError("disk full")):
            response = self.client.post("/admin-area/reset/",
                                        {"confirmation": "RESET", **self._counts()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Missionary.objects.count(), 1)
        self.assertEqual(Item.objects.count(), 1)

    def test_reset_service_fails_closed_on_backup_error(self):
        with mock.patch("apps.operations.backup.backup_sqlite",
                        side_effect=ValueError("no space")):
            with self.assertRaises(Exception):
                reset_service.reset_to_empty(actor=self.admin, confirmation="RESET")
        self.assertEqual(Missionary.objects.count(), 1)

    def test_staff_cannot_reset(self):
        staff, pw = make_staff()
        self.client.login(username=staff.username, password=pw)
        self.assertEqual(self.client.get("/admin-area/reset/").status_code, 403)
