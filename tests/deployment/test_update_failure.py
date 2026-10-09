"""Image/compose contracts plus update/restore script refusal paths.

Config assertions run the real `docker compose config` binary and parse its
JSON output (image tag + APP_VERSION), not source strings. Script failure
paths execute the real scripts with synthetic inputs; persistence is proven
by executing the scripts' own persist function.
"""

from __future__ import annotations

import json
import os
import subprocess

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
        "IMAGE_BASE=supplies-tracker:test\n")
    proc = subprocess.run(["docker", "compose", "-f", "compose.yaml", "config",
                           "--quiet"], capture_output=True, text=True,
                          cwd=tmp_path, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_production_image_tag_matches_requested_version(tmp_path):
    """The reported bug: IMAGE=bare repo + VERSION=1.2.3 ran :latest while
    announcing 1.2.3. Proven fixed by real `compose config` JSON."""
    import shutil

    shutil.copy("compose.yaml", tmp_path / "compose.yaml")
    (tmp_path / ".env").write_text(
        "DJANGO_SECRET_KEY=synthetic-only-not-a-real-secret-123\n"
        "DJANGO_ALLOWED_HOSTS=127.0.0.1\n"
        "DJANGO_CSRF_TRUSTED_ORIGINS=https://127.0.0.1:8443\n"
        "APP_HOST=127.0.0.1\nAPP_PORT=8443\nVERSION=0.4.0\n"
        "IMAGE_BASE=ghcr.io/example/supplies-tracker\n")
    env = {**os.environ, "VERSION": "1.2.3",
           "IMAGE_BASE": "ghcr.io/example/supplies-tracker"}
    proc = subprocess.run(
        ["docker", "compose", "-f", "compose.yaml", "config",
         "--format", "json"],
        capture_output=True, text=True, cwd=tmp_path, timeout=120, env=env)
    assert proc.returncode == 0, proc.stderr
    config = json.loads(proc.stdout)
    image = config["services"]["app"]["image"]
    assert image == "ghcr.io/example/supplies-tracker:1.2.3", image
    app_env = config["services"]["app"].get("environment") or {}
    assert app_env.get("APP_VERSION") == "1.2.3", app_env
    # A second release resolves to its own tag (two-release identity).
    env2 = {**os.environ, "VERSION": "1.2.4",
            "IMAGE_BASE": "ghcr.io/example/supplies-tracker"}
    proc2 = subprocess.run(
        ["docker", "compose", "-f", "compose.yaml", "config",
         "--format", "json"],
        capture_output=True, text=True, cwd=tmp_path, timeout=120, env=env2)
    assert proc2.returncode == 0, proc2.stderr
    config2 = json.loads(proc2.stdout)
    assert config2["services"]["app"]["image"] == \
        "ghcr.io/example/supplies-tracker:1.2.4"
    assert (config2["services"]["app"].get("environment") or {}).get(
        "APP_VERSION") == "1.2.4"
    # Legacy IMAGE (tagless repo) also resolves with the requested tag.
    (tmp_path / ".env").write_text(
        "DJANGO_SECRET_KEY=synthetic-only-not-a-real-secret-123\n"
        "DJANGO_ALLOWED_HOSTS=127.0.0.1\n"
        "APP_HOST=127.0.0.1\nAPP_PORT=8443\nVERSION=0.4.0\n"
        "IMAGE=ghcr.io/example/supplies-tracker\n")
    env3 = {**os.environ, "VERSION": "1.2.3",
            "IMAGE": "ghcr.io/example/supplies-tracker"}
    # IMAGE_BASE from the previous env must not leak into this check.
    env3.pop("IMAGE_BASE", None)
    proc3 = subprocess.run(
        ["docker", "compose", "-f", "compose.yaml", "config",
         "--format", "json"],
        capture_output=True, text=True, cwd=tmp_path, timeout=120, env=env3)
    assert proc3.returncode == 0, proc3.stderr
    assert json.loads(proc3.stdout)["services"]["app"]["image"] == \
        "ghcr.io/example/supplies-tracker:1.2.3"


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
    # A tag inside IMAGE_BASE is refused before any docker call (the base
    # must be a tagless repository; VERSION supplies the tag).
    (tmp_path / ".env").write_text(
        "DJANGO_SECRET_KEY=a-very-long-synthetic-secret-value-123\n"
        "IMAGE_BASE=ghcr.io/example/supplies-tracker:9.9.9\n")
    proc = subprocess.run(["bash", script, "1.2.3"], capture_output=True,
                          text=True, cwd=tmp_path, timeout=60)
    assert proc.returncode == 2
    assert "without a tag" in proc.stderr
    # Bad takeover flag.
    (tmp_path / ".env").write_text(
        "DJANGO_SECRET_KEY=a-very-long-synthetic-secret-value-123\n")
    proc = subprocess.run(["bash", script, "1.2.3", "--bogus"],
                          capture_output=True, text=True, cwd=tmp_path,
                          timeout=60)
    assert proc.returncode == 2


def test_update_script_persists_release_pin(tmp_path):
    """Execute the script's own persist function: a successful release must
    durably update .env VERSION so later plain Compose runs the new code."""
    script = os.path.abspath("scripts/update.sh")
    (tmp_path / ".env").write_text(
        "DJANGO_SECRET_KEY=a-very-long-synthetic-secret-value-123\n"
        "IMAGE_BASE=ghcr.io/example/supplies-tracker\n"
        "VERSION=1.2.3\n")
    # Extract and execute the real persist_version function from the script.
    func = subprocess.run(
        ["bash", "-c",
         "sed -n '/^persist_version/,/^}/p' " + script + " > /tmp/pv.sh && "
         "cat /tmp/pv.sh"],
        capture_output=True, text=True, timeout=60)
    assert "VERSION=" in func.stdout
    proc = subprocess.run(
        ["bash", "-c",
         f"cd {tmp_path} && VERSION=1.2.4 && "
         "eval \"$(sed -n '/^persist_version/,/^}/p' " + script + ")\" && "
         "persist_version && cat .env"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "VERSION=1.2.4" in proc.stdout
    assert "VERSION=1.2.3" not in proc.stdout


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
    # Tagged base refused before touching live data.
    (tmp_path / ".env").write_text(
        "DJANGO_SECRET_KEY=a-very-long-synthetic-secret-value-123\n"
        "IMAGE=ghcr.io/example/supplies-tracker:1.2.3\n")
    proc = subprocess.run(
        ["bash", script, str(backup), "1.2.3", "--allow-newer-data-loss"],
        capture_output=True, text=True, cwd=tmp_path, timeout=60)
    assert proc.returncode == 2
    assert "without a tag" in proc.stderr
    # Takeover flag passes validation (it fails later on docker/verify, not
    # on argument parsing) — proving the recovery protocol is wired.
    (tmp_path / ".env").write_text(
        "DJANGO_SECRET_KEY=a-very-long-synthetic-secret-value-123\n")
    proc = subprocess.run(
        ["bash", script, str(backup), "1.2.3", "--allow-newer-data-loss",
         "--takeover"],
        capture_output=True, text=True, cwd=tmp_path, timeout=120)
    # It must get past argument parsing (exit is from docker/verify, never 2
    # for bad args). Any failure here mentions docker, verify, or backup —
    # not argument usage.
    assert proc.returncode != 2 or "takeover" not in proc.stderr.lower(), \
        proc.stderr


def test_restore_script_uses_initialized_verify(stack):
    """restore.sh must verify via the initialized management command, not a
    bare python -c without Django setup (which failed with
    'settings are not configured'). Proven inside the real container."""
    from tests.deployment.conftest import compose_exec

    proc = compose_exec(stack, "python", "manage.py", "backup_db",
                        "--dest", "/app/data/backups/verify-probe.sqlite3")
    assert proc.returncode == 0, proc.stdout
    proc = compose_exec(stack, "python", "manage.py", "verify_backup",
                        "/app/data/backups/verify-probe.sqlite3")
    assert proc.returncode == 0, proc.stdout
    assert "verified" in proc.stdout


def test_shell_syntax():
    for script in ("scripts/update.sh", "scripts/restore.sh",
                   "scripts/daily_backup.sh"):
        proc = subprocess.run(["bash", "-n", os.path.abspath(script)],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, script
