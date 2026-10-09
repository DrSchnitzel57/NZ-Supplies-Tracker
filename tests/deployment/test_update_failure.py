"""Image/compose contracts plus update/restore script refusal paths."""

from __future__ import annotations

import os
import subprocess
import tempfile

import pytest

from tests.deployment.conftest import COMPOSE, run

pytestmark = pytest.mark.django_db(transaction=False)


def test_compose_test_config_parses():
    run(*COMPOSE, "config", "--quiet")


def test_production_compose_config_parses_with_synthetic_env(tmp_path):
    import shutil

    shutil.copy("compose.yaml", tmp_path / "compose.yaml")
    (tmp_path / ".env").write_text(
        "DJANGO_SECRET_KEY=synthetic-only-not-a-real-secret-123\n"
        "DJANGO_ALLOWED_HOSTS=127.0.0.1\n"
        "DJANGO_CSRF_TRUSTED_ORIGINS=https://127.0.0.1:8443\n"
        "APP_HOST=127.0.0.1\nAPP_PORT=8443\nVERSION=0.4.0\n"
        "IMAGE=supplies-tracker:test\n")
    proc = subprocess.run(["docker", "compose", "-f", "compose.yaml", "config",
                           "--quiet"], capture_output=True, text=True,
                          cwd=tmp_path, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_update_script_refuses_bad_input(tmp_path):
    script = os.path.abspath("scripts/update.sh")
    # Missing version.
    proc = subprocess.run(["bash", script], capture_output=True, text=True,
                          cwd=tmp_path, timeout=60)
    assert proc.returncode == 2
    # Unpinned version.
    proc = subprocess.run(["bash", script, "latest"], capture_output=True,
                          text=True, cwd=tmp_path, timeout=60)
    assert proc.returncode == 2
    assert "pinned" in proc.stderr
    # Missing .env.
    proc = subprocess.run(["bash", script, "1.2.3"], capture_output=True,
                          text=True, cwd=tmp_path, timeout=60)
    assert proc.returncode == 2
    assert ".env" in proc.stderr
    # Placeholder secret.
    (tmp_path / ".env").write_text(
        "DJANGO_SECRET_KEY=replace-with-a-long-random-secret\n")
    proc = subprocess.run(["bash", script, "1.2.3"], capture_output=True,
                          text=True, cwd=tmp_path, timeout=60)
    assert proc.returncode == 2
    assert "placeholder" in proc.stderr
    # Requested pin survives .env contents (never clobbered by sourcing).
    (tmp_path / ".env").write_text(
        "DJANGO_SECRET_KEY=a-very-long-synthetic-secret-value-123\n"
        "VERSION=9.9.9\nIMAGE=evil-registry/example\n")
    proc = subprocess.run(["bash", "-n", script], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0
    text = open(script).read()
    assert "REQUESTED_VERSION" in text
    assert "set -a; . ./.env" not in text


def test_restore_script_refuses_bad_input(tmp_path):
    script = os.path.abspath("scripts/restore.sh")
    proc = subprocess.run(["bash", script], capture_output=True, text=True,
                          cwd=tmp_path, timeout=60)
    assert proc.returncode == 2
    backup = tmp_path / "b.sqlite3"
    backup.write_bytes(b"fake")
    (tmp_path / ".env").write_text(
        "DJANGO_SECRET_KEY=a-very-long-synthetic-secret-value-123\n")
    proc = subprocess.run(["bash", script, str(backup), "not-a-version"],
                          capture_output=True, text=True, cwd=tmp_path,
                          timeout=60)
    assert proc.returncode == 2
    assert "pinned" in proc.stderr
    proc = subprocess.run(
        ["bash", script, str(backup), "1.2.3", "--bogus-flag"],
        capture_output=True, text=True, cwd=tmp_path, timeout=60)
    assert proc.returncode == 2
    # Restore order is verify, stop writers, restore, migrate, readiness,
    # reopen — and restore_db reports the token that reopens writes.
    text = open(script).read()
    assert text.index("verify") < text.index("restore_db")
    assert text.index("stop") < text.index("restore_db")
    assert text.index("restore_db") < text.index("readyz") or \
        text.index("restore_db") < text.index("readiness")
    assert "maintenance-token" in text
    assert "realpath" in text


def test_shell_syntax():
    for script in ("scripts/update.sh", "scripts/restore.sh",
                   "scripts/daily_backup.sh"):
        proc = subprocess.run(["bash", "-n", os.path.abspath(script)],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, script
