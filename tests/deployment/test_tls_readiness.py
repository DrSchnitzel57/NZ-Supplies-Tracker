"""Real TLS/static/readiness behavior of the isolated Compose stack."""

from __future__ import annotations

import json
import ssl

import pytest

from tests.deployment.conftest import compose_exec, https_get, wait_until

pytestmark = pytest.mark.django_db(transaction=False)


def test_tls_serves_readyz_with_version(stack):
    status, body = https_get("/readyz/")
    assert status == 200
    payload = json.loads(body.decode())
    assert payload["ok"] is True
    assert payload["version"] == "0.0.0-test"
    assert payload["maintenance"] is False


def test_static_assets_served_over_tls(stack):
    for path, needle in (("/static/app.css", ".topbar"),
                         ("/static/app.js", "pollOnce")):
        status, body = https_get(path)
        assert status == 200, path
        assert needle.encode() in body, path


def test_health_reveals_no_pii_or_secrets(stack):
    for path in ("/healthz/", "/readyz/"):
        status, body = https_get(path)
        assert status == 200
        text = body.decode().lower()
        assert "@" not in text
        assert "secret" not in text
        assert "password" not in text


def test_http_only_paths_never_served_without_tls(stack):
    # Only the mapped HTTPS port answers; the app container exposes 8000
    # internally and publishes nothing itself.
    import subprocess

    proc = subprocess.run(
        ["docker", "compose", "-f", "compose.test.yaml", "-p",
         stack["project"], "port", "app", "8000"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    # `compose port` prints nothing (or ":0") when the port is not published.
    assert proc.stdout.strip() in ("", ":0"), f"app port published: {proc.stdout!r}"


def test_image_excludes_private_data(stack):
    proc = compose_exec(
        stack, "python", "-c",
        ("import pathlib;"
         "app = pathlib.Path('/app');"
         "html = [str(p) for p in app.rglob('*.html') if 'templates' not in str(p)];"
         "assert not html, html;"
         "assert not (app / '.local').exists();"
         "assert not (app / 'tests').exists();"
         "assert not list(app.glob('*.sqlite3'));"
         "assert not list((app / 'backups').glob('*')) if (app / 'backups').exists() else True;"
         "print('exclusions ok')"))
    assert proc.returncode == 0, proc.stdout
    assert "exclusions ok" in proc.stdout
