"""Protected full reset: empty operational data, keep accounts + marker.

Recovery failures prevent deletion (fail closed). The reset runs inside an
exclusive maintenance window with a verified SQLite backup taken first.
Operational tables cleared: roster, inventory, issuing, movements, audits,
row-import previews. Accounts, sessions of the acting admin, system
settings and maintenance state are kept; a minimal protected reset marker
(action system.reset, no personal data) is the only new audit row.
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


def reset_to_empty(*, actor, confirmation: str, backup_path: str | None = None) -> dict:
    permissions.require_admin(actor)
    if (confirmation or "").strip() != RESET_CONFIRMATION_WORD:
        raise ValidationError("typed confirmation must be RESET")
    with maintenance_mode():
        def _work():
            dest = backup_path or _default_backup_path()
            try:
                info = backup_mod.backup_sqlite(
                    source_path=backup_mod.live_db_path(), dest_path=dest)
            except ValueError as exc:
                raise ValidationError(f"backup failed; nothing was deleted: {exc}") from exc
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

    AuditEvent.objects.create(
        actor=actor, action="system.reset", target_type="system",
        target_id="reset",
        detail={"at": timezone.now().isoformat(),
                "backup_sha256": backup_info.get("sha256", ""),
                "backup_bytes": backup_info.get("bytes", 0)},
    )
