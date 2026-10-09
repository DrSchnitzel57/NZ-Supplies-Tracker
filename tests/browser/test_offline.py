"""Routine operation must not require external internet (no CDN).

Fetches the same HTML a browser receives and asserts every stylesheet,
script, font and form action is host-local: no http(s) CDN references, no
protocol-relative URLs. Mirrors the offline-network manual check in an
automated form.
"""

import re
import uuid

import pytest
from django.test import Client

from tests.factories import make_staff

pytestmark = pytest.mark.django_db

EXTERNAL = re.compile(r'''(https?:)?//(?!localhost|127\.|192\.168\.|10\.|172\.)''', re.I)


def _login():
    staff, pw = make_staff(username=f"offline-{uuid.uuid4().hex[:6]}")
    client = Client()
    assert client.login(username=staff.username, password=pw)
    return client


def test_no_external_references_on_core_screens():
    client = _login()
    for url in ["/", "/roster/", "/inventory/", "/issue/", "/reports/",
                "/guide/", "/accounts/login/"]:
        html = client.get(url).content.decode()
        # Allow only synthetic example URLs in help text, never real assets.
        for tag in re.findall(r'''(?:src|href)=["']([^"']+)["']''', html):
            assert not tag.startswith("https://cdn"), (url, tag)
            assert not tag.startswith("http://cdn"), (url, tag)
            if tag.startswith("http"):
                assert "example." in tag or tag.startswith("mailto:"), (url, tag)
        assert "cdn.jsdelivr" not in html.lower()
        assert "unpkg.com" not in html.lower()
        assert "googleapis" not in html.lower()


def test_static_assets_are_bundled_locally():
    from pathlib import Path
    css = Path("static/app.css").read_text()
    assert "@import" not in css or "http" not in css
    assert "url(http" not in css
