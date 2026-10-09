"""Protected full reset: empty operational data, keep accounts + marker.

Recovery failures prevent deletion (fail closed). The reset runs inside an
owned exclusive maintenance window with a verified SQLite backup taken
first. Operational tables cleared: roster, inventory, issuing, movements,
audits, row-import previews. Accounts, sessions of the acting admin, system
settings and maintenance state are kept; a minimal protected reset marker
(action system.reset, no personal data) is the only new audit row.

Newer-work confirmation: the caller passes the exact operational counts
shown on the confirmation screen; a mismatch aborts before the backup, so a
stale confirmation tab cannot silently discard work that arrived after the
screen was rendered.
"""

from __future__ import annotations

from django.utils import timezone

from apps.accounts import services as permissions
from apps.common.db import transact
from apps.common.errors import ValidationError
from apps.operations import backup as backup_mod
from apps.operations.guards import maintenance_mode

RESET_CONFIRMATION_WORD = "RESET"
REAUTH_WINDOW_SECONDS = 10 * 60


def current_counts() -> dict:
    from apps.inventory.models import Item
    from apps.issuing.models import Issue
    from apps.roster.models import Missionary

    return {
        "people": Missionary.objects.count(),
        "items": Item.objects.count(),
        "issues": Issue.objects.count(),
    }


def reset_to_empty(*, actor, confirmation: str, backup_path: str | None = None,
                   expected_counts: dict | None = None) -> dict:
    permissions.require_admin(actor)
    if (confirmation or "").strip() != RESET_CONFIRMATION_WORD:
        raise ValidationError("typed confirmation must be RESET")
    if expected_counts is not None:
        live = current_counts()
        for key in ("people", "items", "issues"):
            try:
                want = int(expected_counts.get(key))
            except (TypeError, ValueError, AttributeError):
                raise ValidationError(
                    "confirm the current counts again before resetting") from None
            if want != live[key]:
                raise ValidationError(
                    "data changed since the confirmation screen; "
                    "review the new counts and confirm again")
    with maintenance_mode(owner="reset", reason="full reset to empty"):
        # Backup first, OUTSIDE the data transaction: file IO is not
        # transactional, and holding the write lock during a slow copy would
        # amplify contention. Any backup failure aborts before deletion.
        dest = backup_path or _default_backup_path()
        try:
            info = backup_mod.backup_sqlite(
                source_path=backup_mod.live_db_path(), dest_path=dest)
        except Exception as exc:
            raise ValidationError(
                f"backup failed; nothing was deleted: {exc}") from exc

        def _work():
            _clear_operational()
            _write_marker(actor, info)
            return info
        return transact(_work)


def _default_backup_path() -> str:
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    return str(backup_mod.live_db_path().parent / "backups" / f"pre-reset-{stamp}.sqlite3")


def _clear_operational() -> None:
    from apps.audit.models import AuditEvent
    from apps.inventory.models import Item, StockMovement
    from apps.issuing.models import Issue, IssueLine
    from apps.roster.models import Missionary

    try:
        from apps.transfers.models import RowImportPreview
        RowImportPreview.objects.all().delete()
    except Exception:
        pass
    IssueLine.objects.all().delete()
    StockMovement.objects.all().delete()
    Issue.objects.all().delete()
    Item.objects.all().delete()
    Missionary.objects.all().delete()
    AuditEvent.objects.all().delete()


def _write_marker(actor, backup_info: dict):
    from apps.audit.models import AuditEvent
    from django.conf import settings

    AuditEvent.objects.create(
        actor=actor, action="system.reset", target_type="system",
        target_id="reset",
        detail={"at": timezone.now().isoformat(),
                "backup_path": backup_info.get("path", ""),
                "backup_sha256": backup_info.get("sha256", ""),
                "backup_bytes": backup_info.get("bytes", 0),
                "app_version": getattr(settings, "APP_VERSION", "")},
    )
