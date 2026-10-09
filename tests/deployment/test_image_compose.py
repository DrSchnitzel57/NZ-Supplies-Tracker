"""Image exclusions + persisted-data contracts (no daemon needed)."""

from pathlib import Path


def _read(name):
    return Path(name).read_text()


def test_dockerignore_excludes_personal_data_and_secrets():
    text = _read(".dockerignore")
    for needle in ["MTC Missionary Supplies Tracker.html", ".local/",
                   "*.sqlite3", ".env", "*.zip"]:
        assert needle in text, needle


def test_dockerfile_pinned_no_secrets_nonroot():
    text = _read("Dockerfile")
    assert "python:3.13-slim-bookworm" in text
    assert "USER appuser" in text
    assert "collectstatic" in text
    assert "gunicorn" in text.lower()
    lowered = text.lower()
    assert "secret" not in lowered or "build-only-insecure" in text
    # Prototype HTML must never be copied into the image.
    assert "MTC Missionary" not in text


def test_compose_persists_data_backups_ca_no_admin_port():
    text = _read("compose.yaml")
    for needle in ["app-data:/app/data", "app-backups", "caddy-data"]:
        assert needle in text, needle
    assert "caddy:2.9-alpine" in text
    assert "admin off" not in text  # Caddy admin flag lives in Caddyfile
    assert "APP_HOST" in text and "APP_PORT" in text
    # No public proxy admin port: only the configured HTTPS port is published.
    assert text.count("ports:") == 1


def test_compose_test_uses_synthetic_settings_only():
    text = _read("compose.test.yaml")
    assert "test-only-synthetic-secret" in text
    assert "0.0.0-test" in text
    assert "env_file" in text  # explicitly empty: never loads production secrets
    assert "replace-with-a-long-random-secret" not in text
    assert "DJANGO_DEBUG: \"0\"" in text or 'DJANGO_DEBUG: "0"' in text
