"""Real-browser fixtures: Chromium, LAN-only, no skips.

Every test drives the app in a real Chromium page against a live Django
server with all internet hosts blocked — any CDN/font/phone-home request
fails the test. Users are synthetic (uuid names); the database is flushed
after each test.
"""

from __future__ import annotations

import os
import uuid

# Playwright's sync driver keeps an event loop handle in the test thread;
# Django's async-safety guard would otherwise refuse all ORM access in
# browser tests. Tests here are strictly synchronous (sync Playwright API),
# so allow it for this suite only.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

import pytest
from playwright.sync_api import sync_playwright

from tests.factories import make_admin, make_staff

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(scope="session")
def pw():
    handle = sync_playwright().start()
    browser = handle.chromium.launch()
    yield handle
    browser.close()
    handle.stop()


@pytest.fixture()
def externals():
    return []


@pytest.fixture()
def page(pw, live_server, externals):
    browser = pw.chromium.launch()
    context = browser.new_context(viewport={"width": 1280, "height": 900})

    def _handler(route):
        url = route.request.url
        host = route.request.headers.get("host", "")
        if "127.0.0.1" in url or "localhost" in url or "127.0.0.1" in host:
            route.continue_()
        else:
            externals.append(url)
            route.abort()

    context.route("**/*", _handler)
    pg = context.new_page()
    yield pg
    context.close()
    browser.close()


@pytest.fixture()
def mobile_page(pw, live_server, externals):
    browser = pw.chromium.launch()
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                    "Mobile/15E148 Safari/604.1"),
        is_mobile=True, has_touch=True)

    def _handler(route):
        url = route.request.url
        if "127.0.0.1" in url or "localhost" in url:
            route.continue_()
        else:
            externals.append(url)
            route.abort()

    context.route("**/*", _handler)
    pg = context.new_page()
    yield pg
    context.close()
    browser.close()


@pytest.fixture(autouse=True)
def _flush_db():
    yield
    from django.core.management import call_command

    call_command("flush", "--noinput", verbosity=0)


def tag() -> str:
    return uuid.uuid4().hex[:8]


def make_login(page, live_server, username: str, password: str):
    page.goto(f"{live_server.url}/accounts/login/")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("main button[type=submit]")
    page.wait_for_url(f"{live_server.url}/")


def logout(page, live_server):
    page.goto(f"{live_server.url}/")
    page.click(".userbar form[action='/accounts/logout/'] button[type=submit]")


def staff_login(page, live_server, suffix="s"):
    staff, pw = make_staff(username=f"browser-staff-{suffix}-{tag()}")
    make_login(page, live_server, staff.username, pw)
    return staff, pw


def admin_login(page, live_server, suffix="a"):
    admin, pw = make_admin(username=f"browser-admin-{suffix}-{tag()}")
    make_login(page, live_server, admin.username, pw)
    return admin, pw


def assert_no_internet(externals):
    assert externals == [], f"unexpected internet requests: {externals[:5]}"
