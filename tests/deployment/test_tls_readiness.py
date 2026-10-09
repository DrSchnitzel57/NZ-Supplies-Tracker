"""TLS/Caddy + readiness contracts (synthetic)."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.django_db


def test_caddyfile_lan_https_only():
    text = Path("deploy/Caddyfile").read_text()
    assert "admin off" in text
    assert "tls internal" in text
    assert "reverse_proxy app:8000" in text
    assert "APP_HOST" in text and "APP_PORT" in text
    # Single HTTPS entrypoint; no plain-http listener.
    assert "http://" not in text


def test_healthz_has_no_pii_or_secrets(django_user_model):
    from django.test import Client

    from tests.factories import make_admin, make_missionary
    admin, _ = make_admin()
    make_missionary(admin)
    client = Client()
    for url in ("/healthz/", "/readyz/"):
        response = client.get(url)
        assert response.status_code in (200, 503)
        body = response.content.decode()
        assert "secret" not in body.lower()
        assert "@" not in body  # no emails leak into health output
    assert client.get("/healthz/").json() == {"ok": True}


def test_readyz_ok_on_migrated_db():
    from django.test import Client
    assert Client().get("/readyz/").json() == {"ok": True}
