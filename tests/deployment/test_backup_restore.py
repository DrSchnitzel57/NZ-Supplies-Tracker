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
    proc = compose_exec(stack, "python", "manage.py", "verify_backup", dest)
    assert proc.returncode == 0, proc.stdout
    assert "verified" in proc.stdout
    proc = _shell(stack,
                  "import json;"
                  "info = json.loads(open('/app/data/backups/drill-pre-update.sqlite3.manifest.json').read());"
                  "assert info['completed'] is True;"
                  "print('manifest', info['sha256'][:8])")
    assert proc.returncode == 0, proc.stdout
    assert "manifest" in proc.stdout
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


def test_protected_restore_drill_restores_data(stack):
    """Named drill actually restores: backup, newer work, offline restore
    with preserved copy, migrate + readiness, window held until reopened."""
    proc = _shell(stack,
                  "from apps.roster.models import Missionary;"
                  "Missionary.objects.create(external_id='restore-keep', name='Keep Me');"
                  "print('seeded')")
    assert proc.returncode == 0, proc.stdout
    proc = compose_exec(stack, "python", "manage.py", "backup_db",
                        "--dest", "/app/data/backups/drill-restore.sqlite3",
                        "--quiesce")
    assert proc.returncode == 0, proc.stdout
    proc = _shell(stack,
                  "from apps.roster.models import Missionary;"
                  "Missionary.objects.create(external_id='restore-new', name='New Work');"
                  "print(Missionary.objects.count())")
    assert proc.returncode == 0, proc.stdout
    # Without confirmation, newer work is protected (fail closed).
    proc = compose_exec(stack, "python", "manage.py", "restore_db",
                        "/app/data/backups/drill-restore.sqlite3")
    assert proc.returncode != 0
    assert "newer work" in proc.stdout
    # Writers are stopped for the offline restore (scripts do this); the
    # command holds maintenance across restore+migrate and prints its token.
    run(*stack["prefix"], "stop", "app", timeout=300)
    try:
        proc = compose_exec(stack, "python", "manage.py", "restore_db",
                            "/app/data/backups/drill-restore.sqlite3",
                            "--allow-newer-data-loss")
        assert proc.returncode == 0, proc.stdout
        assert "maintenance-token:" in proc.stdout
        token = [line for line in proc.stdout.splitlines()
                 if "maintenance-token:" in line][0].split(
                     "maintenance-token:")[1].strip().split()[0]
    finally:
        run(*stack["prefix"], "start", "app", timeout=300)
    wait_until(lambda: https_get("/readyz/")[0] == 200,
               label="readiness after restore")
    # Newer work is gone, kept rows are back, and the window is still held
    # until the caller reopens after its own readiness gate.
    proc = _shell(stack,
                  "from apps.roster.models import Missionary;"
                  "print('keep=%d' % Missionary.objects.filter(external_id='restore-keep').count());"
                  "print('new=%d' % Missionary.objects.filter(external_id='restore-new').count())")
    assert proc.returncode == 0, proc.stdout
    assert "keep=1" in proc.stdout
    assert "new=0" in proc.stdout
    status, body = https_get("/readyz/")
    payload = json.loads(body.decode())
    assert payload["ok"] is True
    assert payload["maintenance"] is True
    proc = compose_exec(stack, "python", "manage.py", "maintenance",
                        "--exit", "--token", token)
    assert proc.returncode == 0, proc.stdout
    status, body = https_get("/readyz/")
    assert json.loads(body.decode())["maintenance"] is False


def test_failed_update_rollback_in_stack(stack):
    """A held update window blocks plain restore; explicit audited takeover
    recovers without reopening writes early. End-to-end in real containers."""
    proc = _shell(stack,
                  "from apps.roster.models import Missionary;"
                  "Missionary.objects.create(external_id='rb-keep', name='Keep');"
                  "print('seeded')")
    assert proc.returncode == 0, proc.stdout
    proc = compose_exec(stack, "python", "manage.py", "backup_db",
                        "--dest", "/app/data/backups/rb-pre.sqlite3",
                        "--quiesce")
    assert proc.returncode == 0, proc.stdout
    proc = _shell(stack,
                  "from apps.operations import guards;"
                  "print('token=' + guards.enter_maintenance(owner='update-1.2.3', reason='synthetic failed update'))")
    assert proc.returncode == 0, proc.stdout
    # Plain restore refuses to share the failed update's window.
    proc = compose_exec(stack, "python", "manage.py", "restore_db",
                        "/app/data/backups/rb-pre.sqlite3",
                        "--allow-newer-data-loss",
                        "--owner", "restore-db")
    assert proc.returncode != 0
    assert "already held" in proc.stdout
    # Explicit takeover recovers (audited, writers stopped, offline WAL
    # restore, preserved copy, readiness before reopening).
    run(*stack["prefix"], "stop", "app", timeout=300)
    try:
        proc = compose_exec(stack, "python", "manage.py", "restore_db",
                            "/app/data/backups/rb-pre.sqlite3",
                            "--allow-newer-data-loss",
                            "--owner", "restore-db", "--force-takeover")
        assert proc.returncode == 0, proc.stdout
        assert "maintenance-token:" in proc.stdout
        token = [line for line in proc.stdout.splitlines()
                 if "maintenance-token:" in line][0].split(
                     "maintenance-token:")[1].strip().split()[0]
    finally:
        run(*stack["prefix"], "start", "app", timeout=300)
    wait_until(lambda: https_get("/readyz/")[0] == 200,
               label="readiness after takeover restore")
    proc = _shell(stack,
                  "from apps.audit.models import AuditEvent;"
                  "print('takeover=%d' % AuditEvent.objects.filter(action='maintenance.takeover').count())")
    assert proc.returncode == 0, proc.stdout
    assert "takeover=" in proc.stdout
    # The takeover audit survives the file restore (count >= 1).
    count = int([line for line in proc.stdout.splitlines()
                 if "takeover=" in line][0].split("takeover=")[1].strip())
    assert count >= 1, proc.stdout
    proc = compose_exec(stack, "python", "manage.py", "maintenance",
                        "--exit", "--token", token)
    assert proc.returncode == 0, proc.stdout


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
