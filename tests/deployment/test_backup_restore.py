"""Real backup/restore/maintenance drills inside the isolated stack."""

from __future__ import annotations

import json

import pytest

from tests.deployment.conftest import (
    compose_exec,
    https_get,
    run,
    wait_until,
)

pytestmark = pytest.mark.django_db(transaction=False)

SHELL = ("python", "manage.py", "shell", "-c")


def _shell(stack, code: str):
    return compose_exec(stack, *SHELL, code)


def test_persistence_across_container_restart(stack):
    proc = _shell(stack,
                 "from apps.roster.models import Missionary;"
                 "Missionary.objects.create(external_id='persist-1', name='Persist Me');"
                 "print('created')")
    assert proc.returncode == 0, proc.stdout
    run(*stack["prefix"], "restart", "app", timeout=300)
    wait_until(lambda: https_get("/readyz/")[0] == 200,
               label="readiness after restart")
    proc = _shell(stack,
                 "from apps.roster.models import Missionary;"
                 "print(Missionary.objects.filter(external_id='persist-1').count())")
    assert proc.returncode == 0, proc.stdout
    assert proc.stdout.strip().endswith("1")


def test_backup_verify_and_protected_restore_drill(stack):
    dest = "/app/data/backups/drill-pre-update.sqlite3"
    proc = compose_exec(stack, "python", "manage.py", "backup_db",
                        "--dest", dest, "--quiesce")
    assert proc.returncode == 0, proc.stdout
    proc = _shell(stack,
                  "from apps.operations import backup;"
                  "backup.verify_sqlite_backup('/app/data/backups/drill-pre-update.sqlite3');"
                  "info = __import__('json').loads(open('/app/data/backups/drill-pre-update.sqlite3.manifest.json').read());"
                  "assert info['completed'] is True;"
                  "print('verified', info['sha256'][:8])")
    assert proc.returncode == 0, proc.stdout
    assert "verified" in proc.stdout
    # A corrupt copy is refused and can never become the protected backup.
    proc = _shell(stack,
                  "import shutil\n"
                  "shutil.copy('/app/data/backups/drill-pre-update.sqlite3', '/tmp/evil.sqlite3')\n"
                  "open('/tmp/evil.sqlite3','r+b').write(b'\\x00' * 64)\n"
                  "from apps.operations import backup\n"
                  "try:\n"
                  "    backup.verify_sqlite_backup('/tmp/evil.sqlite3')\n"
                  "    print('NOT-REFUSED')\n"
                  "except ValueError:\n"
                  "    print('refused')")
    assert "refused" in proc.stdout
    assert "NOT-REFUSED" not in proc.stdout


def test_owned_maintenance_token_flow(stack):
    proc = _shell(stack,
                  "from apps.operations import guards;"
                  "print('token=' + guards.enter_maintenance(owner='drill', reason='test'))")
    assert proc.returncode == 0, proc.stdout
    token = [line for line in proc.stdout.splitlines() if "token=" in line][0].split("token=")[1].strip()
    # A second operator cannot share the window.
    proc = compose_exec(stack, "python", "manage.py", "maintenance",
                        "--enter", "--owner", "intruder")
    assert proc.returncode != 0
    assert "already held" in proc.stdout
    # Exiting without the token is refused; with it the window closes.
    proc = compose_exec(stack, "python", "manage.py", "maintenance", "--exit")
    assert proc.returncode != 0
    proc = compose_exec(stack, "python", "manage.py", "maintenance",
                        "--exit", "--token", token)
    assert proc.returncode == 0, proc.stdout
    status, body = https_get("/readyz/")
    payload = json.loads(body.decode())
    assert payload["ok"] is True and payload["maintenance"] is False


def test_dead_server_recovers(stack):
    run(*stack["prefix"], "stop", "app", timeout=300)
    try:
        import urllib.error
        try:
            https_get("/readyz/", timeout=10)
            reachable = True
        except Exception:  # noqa: BLE001 - any failure means not serving
            reachable = False
        assert not reachable, "stopped app still answers HTTPS"
    finally:
        run(*stack["prefix"], "start", "app", timeout=300)
    wait_until(lambda: https_get("/readyz/")[0] == 200,
               label="readiness after restart")


def test_restore_refuses_version_mismatch(stack):
    proc = compose_exec(stack, "python", "manage.py", "backup_db",
                        "--dest", "/app/data/backups/v.sqlite3")
    assert proc.returncode == 0, proc.stdout
    proc = compose_exec(stack, "python", "manage.py", "restore_db",
                        "/app/data/backups/v.sqlite3",
                        "--expected-version", "9.9.9")
    assert proc.returncode != 0
    assert "mismatch" in proc.stdout
