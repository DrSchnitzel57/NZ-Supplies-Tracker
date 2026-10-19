"""Regressions for the finishing overhaul (milestone 08).

Each test pins a problem found while using the app with a real-sized roster:
filters that silently applied themselves, the lost MTC departure date,
"True"/"False" arrival text, alphabetical size order, sizes missing from
issue summaries, raw IDs in stock history, the unthrottled Django admin login,
US date formats, and the new names/overview/demo features. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import uuid

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import formats, timezone

from apps.inventory.models import Item
from apps.issuing import services as issuing
from apps.roster.models import Missionary, normalize_arrival
from tests.factories import make_admin, make_item, make_missionary, make_staff


def _issue(actor, person, *pairs):
    return issuing.create_issue(
        actor=actor, missionary_id=person.pk, idempotency_key=uuid.uuid4().hex,
        lines=[{"item_id": str(item.pk), "quantity": qty} for item, qty in pairs])


class FilterDefaultsTests(TestCase):
    """Submitting a list's filter bar unchanged must not filter anything."""

    def setUp(self):
        self.staff, pw = make_staff()
        self.client.login(username=self.staff.username, password=pw)

    def test_roster_search_with_untouched_filters_returns_everyone(self):
        make_missionary(self.staff, name="Untitled Person")          # blank title
        titled = make_missionary(self.staff, name="Quill, Ana")
        titled.title_prefix = "Sister"
        titled.save()
        page = self.client.get("/roster/")
        self.assertContains(page, '<option value="" selected>All</option>', html=False)
        submitted = self.client.get("/roster/", {"q": "", "title": "", "arrival": "",
                                                "leaving": "", "sort": "name"})
        self.assertEqual(submitted.context["total"], 2)

    def test_inventory_filter_with_untouched_fields_returns_every_item(self):
        make_item(self.staff, name="No size thing")
        make_item(self.staff, name="Sized thing", size="M")
        submitted = self.client.get("/inventory/", {"q": "", "category": "", "sort": "category"})
        self.assertEqual(submitted.context["total"], 2)
        self.assertNotContains(submitted, "__blank__")


class SeedAndArrivalTests(TestCase):
    def test_real_source_shape_keeps_arrival_and_departure(self):
        from apps.transfers.seed import normalize_seed

        roster, _, _ = normalize_seed({"missionaries": [{
            "id": 9000001, "arrived": True, "type": "Elder", "name": "Sample, Tavita",
            "arrivalDate": "2026-09-18", "missionArrival": "2026-11-12",
            "missionRelease": "2028-09-21"}], "inventory": []})
        person = roster[0]
        self.assertEqual(person["arrival_date"], "2026-09-18")
        self.assertEqual(person["departure_date"], "2026-11-12")
        self.assertEqual(person["release_date"], "2028-09-21")
        self.assertEqual(person["arrival_status"], "True")  # normalised on save

    def test_arrival_values_are_normalised_on_save(self):
        cases = {True: "arrived", "True": "arrived", "yes": "arrived", "In-house": "arrived",
                 False: "expected", "False": "expected", "Awaiting": "expected",
                 "": "", "On a visa delay": "On a visa delay"}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_arrival(raw), expected)
        admin, _ = make_admin()
        person = make_missionary(admin)
        person.arrival_status = "True"
        person.save()
        person.refresh_from_db()
        self.assertEqual(person.arrival_status, "arrived")
        self.assertTrue(person.arrived)
        self.assertEqual(person.arrival_label, "Arrived")

    def test_prototype_export_headers_import_every_column(self):
        from apps.transfers.row_imports import build_preview

        rows = [["Missionary ID", "Name", "Type", "Home Stake", "Mission", "Training Language",
                 "Arrival", "Mission Arrival", "Mission Release", "Missionary Email",
                 "Personal Email", "Status", "Goods Received"],
                ["SYN-1", "Doe, Jane", "Sister", "North Stake", "Synthetic Mission", "English",
                 "2026-10-01", "2026-10-29", "2028-04-01", "jane@example.test",
                 "jd@example.test", "In-house", "3"]]
        preview = build_preview(kind="roster", rows=rows, mode="add_update")
        self.assertEqual(preview["errors"], [])
        entry = preview["valid"][0]
        self.assertEqual(entry["arrival_date"], "2026-10-01")
        self.assertEqual(entry["departure_date"], "2026-10-29")
        self.assertEqual(entry["language"], "English")
        self.assertEqual(entry["email"], "jane@example.test")
        self.assertEqual(entry["arrival_status"], "arrived")
        self.assertEqual(entry["title_prefix"], "Sister")


class NamesTests(TestCase):
    def test_missionaries_are_shown_the_way_staff_address_them(self):
        person = Missionary(name="Halvorsen, Margot Louise", title_prefix="Sister")
        self.assertEqual(person.display_name, "Sister Halvorsen")
        self.assertEqual(person.full_name, "Margot Louise Halvorsen")
        shouting = Missionary(name="VOSS, ANNIKA", title_prefix="Sister")
        self.assertEqual(shouting.display_name, "Sister Voss")
        couple = Missionary(name="Ostrander, Joan", title_prefix="Couple")
        self.assertEqual(couple.display_name, "Elder & Sister Ostrander")
        untitled = Missionary(name="Synthetic Person")
        self.assertEqual(untitled.display_name, "Synthetic Person")

    def test_staff_display_name_appears_in_history(self):
        from apps.accounts import lifecycle

        admin, admin_pw = make_admin()
        staff = lifecycle.create_user(actor=admin, username="helper-1", password="Temp12345!x",
                                      display_name="Sister Helper")
        lifecycle.set_display_name(actor=admin, user_id=staff.pk, display_name="Sister Helper")
        staff.must_change_password = False
        staff.save()
        person = make_missionary(admin)
        item = make_item(admin, opening_qty=5)
        _issue(staff, person, (item, 1))
        self.client.login(username=admin.username, password=admin_pw)
        self.assertContains(self.client.get("/issue/history/"), "Sister Helper")


class DisplayDetailTests(TestCase):
    def setUp(self):
        self.staff, pw = make_staff()
        self.client.login(username=self.staff.username, password=pw)

    def test_sizes_sort_naturally_and_appear_in_summaries(self):
        for size in ("10", "6", "8", "12"):
            make_item(self.staff, category="Clothing", name="Dress", size=size, opening_qty=4)
        page = self.client.get("/inventory/", {"sort": "category"})
        sizes = [item.size for item in page.context["items"]]
        self.assertEqual(sizes, ["6", "8", "10", "12"])
        person = make_missionary(self.staff)
        dress = Item.objects.get(size="8")
        _issue(self.staff, person, (dress, 1))
        self.assertContains(self.client.get("/issue/history/"), "Dress (8) × 1")
        self.assertContains(self.client.get("/"), "Dress (8) × 1")

    def test_stock_history_names_the_recipient_not_an_id(self):
        person = make_missionary(self.staff, name="Recipient, Example")
        person.title_prefix = "Elder"
        person.save()
        item = make_item(self.staff, opening_qty=5)
        issue = _issue(self.staff, person, (item, 2))
        page = self.client.get(f"/inventory/{item.pk}/")
        self.assertContains(page, "Given to Elder Recipient")
        self.assertNotContains(page, f"issue {issue.pk}")

    def test_dates_use_new_zealand_formats(self):
        self.assertEqual(formats.date_format(dt.date(2026, 10, 17)), "17 Oct 2026")
        self.assertEqual(formats.date_format(dt.date(2026, 10, 17), "SHORT_DATE_FORMAT"), "17/10/2026")

    def test_django_admin_site_is_gone(self):
        self.assertEqual(self.client.get("/admin/").status_code, 404)
        self.assertEqual(self.client.post("/admin/login/", {"username": "x", "password": "y"}).status_code, 404)


class OverviewTests(TestCase):
    def setUp(self):
        self.staff, pw = make_staff()
        self.client.login(username=self.staff.username, password=pw)

    def test_alerts_separate_running_low_ran_out_and_never_stocked(self):
        low = make_item(self.staff, name="Low thing", opening_qty=2)
        Item.objects.filter(pk=low.pk).update(low_threshold=3)
        gone = make_item(self.staff, name="Gone thing", opening_qty=1)
        Item.objects.filter(pk=gone.pk).update(low_threshold=3)
        never = make_item(self.staff, name="Never thing", opening_qty=0)
        Item.objects.filter(pk=never.pk).update(low_threshold=3)
        _issue(self.staff, make_missionary(self.staff), (gone, 1))
        response = self.client.get("/")
        counts = response.context["counts"]
        self.assertEqual((counts["running_low"], counts["ran_out"], counts["never_stocked"]), (1, 1, 1))
        self.assertEqual(counts["low_stock"], 3)  # API/back-compat figure unchanged
        names = [i.name for i in response.context["running_low"]] + [i.name for i in response.context["ran_out"]]
        self.assertEqual(names, ["Low thing", "Gone thing"])
        api = self.client.get("/api/dashboard/").json()
        self.assertEqual((api["running_low"], api["ran_out"]), (1, 1))
        report = self.client.get("/reports/", {"family": "low-stock"})
        statuses = [row[1] for row in report.context["report_rows"]]
        self.assertEqual(statuses, ["Running low", "Ran out", "Never stocked"])

    def test_leaving_soon_and_arrivals(self):
        today = timezone.localdate()
        soon = make_missionary(self.staff, name="Soon, Leaving")
        soon.departure_date = today + dt.timedelta(days=5)
        soon.arrival_status = "arrived"
        soon.save()
        later = make_missionary(self.staff, name="Later, Leaving")
        later.departure_date = today + dt.timedelta(days=40)
        later.save()
        response = self.client.get("/")
        self.assertEqual(response.context["counts"]["leaving_soon"], 1)
        self.assertEqual(response.context["counts"]["arrived"], 1)
        self.assertEqual([p.pk for p in response.context["leaving"]], [soon.pk])
        listed = self.client.get("/roster/", {"leaving": "14"})
        self.assertEqual(listed.context["total"], 1)

    def test_recipient_summary_lists_what_was_received(self):
        person = make_missionary(self.staff, name="Card, Example")
        shirt = make_item(self.staff, name="Shirt", size="40 L", opening_qty=5)
        issue = _issue(self.staff, person, (shirt, 2))
        payload = self.client.get(f"/roster/{person.pk}/summary/").json()
        self.assertEqual(payload["received"], [{"label": "Shirt (40 L)", "units": 2}])
        issuing.reverse_issue(actor=self.staff, issue_id=issue.pk, reason="synthetic undo")
        payload = self.client.get(f"/roster/{person.pk}/summary/").json()
        self.assertEqual(payload["received"], [])
        self.client.logout()
        self.assertEqual(self.client.get(f"/roster/{person.pk}/summary/").status_code, 401)


class DemoDataTests(TestCase):
    def test_demo_data_fills_an_empty_install_and_refuses_real_data(self):
        admin, _ = make_admin()
        call_command("load_demo_data", username=admin.username, people=12, days=7, verbosity=0)
        self.assertEqual(Missionary.objects.count(), 12)
        self.assertTrue(Missionary.objects.filter(external_id__startswith="DEMO-").exists())
        self.assertTrue(Item.objects.filter(category="Clothing").exists())
        from apps.inventory import services as inv
        for item in Item.objects.all():
            self.assertEqual(inv.ledger_balance(item.pk), item.on_hand)
        with self.assertRaises(CommandError):
            call_command("load_demo_data", username=admin.username, verbosity=0)


class OriginalHtmlImportTests(TestCase):
    """Administrators load the old tracker from the browser, not a terminal."""

    def _html(self, roster=2, items=2):
        import json

        seed = {"missionaries": [{"id": f"SYN-{i}", "name": f"Synthetic, Person {i}", "type": "Elder",
                                  "arrived": False, "missionArrival": "2026-11-12"} for i in range(roster)],
                "inventory": [{"cat": "Hygiene", "item": f"Thing {i}", "remain": 3} for i in range(items)]}
        return f"<html><script>const SEED = {json.dumps(seed)};</script></html>".encode()

    def _login(self, user, pw, reauth=True):
        import time

        self.client.login(username=user.username, password=pw)
        if reauth:
            session = self.client.session
            session["admin_reauth"] = {"at": time.time(), "user_id": user.pk, "pwd": user.password}
            session.save()

    def _post(self, data, name="MTC Missionary Supplies Tracker.html"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return self.client.post("/transfers/bundle/import-original/",
                                {"original": SimpleUploadedFile(name, data, content_type="text/html")},
                                follow=True)

    def test_admin_uploads_the_original_file_into_an_empty_app(self):
        admin, pw = make_admin()
        self._login(admin, pw)
        self.assertContains(self.client.get("/transfers/bundle/"), 'name="original"')
        page = self._post(self._html())
        self.assertContains(page, "Imported the original tracker: 2 missionaries and 2 stock items.")
        self.assertNotContains(page, "Synthetic, Person")
        person = Missionary.objects.get(external_id="SYN-0")
        self.assertEqual(person.departure_date, dt.date(2026, 11, 12))
        self.assertEqual(person.arrival_status, "expected")
        self.assertEqual(Item.objects.count(), 2)

    def test_refused_when_not_empty_not_a_tracker_or_not_an_admin(self):
        admin, pw = make_admin()
        self._login(admin, pw)
        page = self._post(b"<html><body>hello</body></html>", name="other.html")
        self.assertContains(page, "Nothing was imported: no tracker data was found")
        page = self._post(b"\xff\xfe\x00garbage", name="tracker.html")
        self.assertContains(page, "Nothing was imported")
        self.assertEqual(Missionary.objects.count(), 0)
        make_missionary(admin)
        page = self._post(self._html())
        self.assertContains(page, "Nothing was imported")
        self.assertEqual(Missionary.objects.count(), 1)
        self.client.logout()
        staff, spw = make_staff()
        self.client.login(username=staff.username, password=spw)
        self.assertEqual(self._post(self._html()).status_code, 403)

    def test_without_fresh_password_confirmation_nothing_is_imported(self):
        admin, pw = make_admin()
        self._login(admin, pw, reauth=False)
        page = self._post(self._html())
        self.assertContains(page, "Confirm your password")
        self.assertEqual(Missionary.objects.count(), 0)


class CheckInTests(TestCase):
    """Staff mark arrivals with one click instead of editing the record."""

    def setUp(self):
        self.staff, pw = make_staff()
        self.client.login(username=self.staff.username, password=pw)

    def test_check_in_marks_arrived_today_once(self):
        from apps.audit.models import AuditEvent

        person = make_missionary(self.staff, name="Doorway, Example")
        person.title_prefix = "Sister"
        person.arrival_status = "expected"
        person.save()
        listing = self.client.get("/roster/", {"arrival": "expected"})
        self.assertContains(listing, f'action="/roster/{person.pk}/check-in/')
        self.assertContains(self.client.get(f"/roster/{person.pk}/"), "Check in")
        page = self.client.post(f"/roster/{person.pk}/check-in/?return_to=/roster/%3Farrival%3Dexpected",
                                follow=True)
        self.assertContains(page, "Checked in Sister Doorway: arrived")
        self.assertEqual(page.redirect_chain[-1][0], "/roster/?arrival=expected")
        person.refresh_from_db()
        self.assertTrue(person.arrived)
        self.assertEqual(person.arrival_date, timezone.localdate())
        again = self.client.post(f"/roster/{person.pk}/check-in/?from=profile", follow=True)
        self.assertContains(again, "was already checked in")
        self.assertEqual(AuditEvent.objects.filter(action="roster.checked_in").count(), 1)
        self.assertNotContains(self.client.get(f"/roster/{person.pk}/"), f'/roster/{person.pk}/check-in/')

    def test_check_in_keeps_a_recorded_arrival_date_and_rejects_bad_returns(self):
        person = make_missionary(self.staff, name="Early, Example")
        person.arrival_date = dt.date(2026, 10, 1)
        person.save()
        page = self.client.post(f"/roster/{person.pk}/check-in/?return_to=https://evil.example/", follow=True)
        self.assertEqual(page.redirect_chain[-1][0], "/roster/")
        person.refresh_from_db()
        self.assertEqual(person.arrival_date, dt.date(2026, 10, 1))
        self.assertTrue(person.arrived)
        self.assertEqual(self.client.get(f"/roster/{person.pk}/check-in/").status_code, 405)
        early = make_missionary(self.staff, name="Ahead, Example")
        early.arrival_date = timezone.localdate() + dt.timedelta(days=3)  # planned, came early
        early.save()
        self.client.post(f"/roster/{early.pk}/check-in/")
        early.refresh_from_db()
        self.assertEqual(early.arrival_date, timezone.localdate())
