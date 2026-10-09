"""Update/restore failure contracts: scripts fail closed (static checks)."""

from pathlib import Path


def _script(name):
    return Path(f"scripts/{name}").read_text()


def test_update_script_fails_closed():
    text = _script("update.sh")
    assert "VERSION must be pinned" in text
    assert "maintenance" in text.lower()
    assert "backup_db" in text
    assert "migrate" in text
    assert "readyz" in text
    assert "UPDATE FAILED" in text
    assert "mutation access remains closed" in text


def test_restore_script_guards_newer_work():
    text = _script("restore.sh")
    assert "verify" in text.lower()
    assert "--allow-newer-data-loss" in text
    assert "maintenance" in text.lower()
    assert "live data untouched" in text or "failed checks" in text
    assert "VERSION must be pinned" in text


def test_restore_command_refuses_newer_work_without_flag():
    import pathlib
    text = pathlib.Path(
        "apps/operations/management/commands/restore_db.py").read_text()
    assert "--allow-newer-data-loss" in text
    assert "newer work" in text
