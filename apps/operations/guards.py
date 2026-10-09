"""Maintenance/write guards reusable by import/reset/update operations.

The maintenance window is owned and exclusive:

- ``enter_maintenance(owner, reason)`` fails with ``MaintenanceActiveError``
  when a different owner already holds the window, so a second operator (or
  an overlapping script) aborts instead of silently sharing the window.
- The same owner may re-enter re-entrantly (depth counting); the window only
  closes when the outermost holder exits with its token, so a nested block
  can never reopen an outer operation early.
- Exiting requires the token returned on entry; a failed operation only ever
  closes its own window and can never clear another holder's window.
- Crash recovery is explicit, never automatic: a stale window keeps writes
  closed (fail closed) until an operator takes over with ``force=True``,
  which records the takeover in the audit trail.
"""

from __future__ import annotations

import secrets
from contextlib import contextmanager

from django.db import transaction
from django.utils import timezone

from apps.common.errors import MaintenanceActiveError

#: After this long without activity a held window is *reported* as stale so
#: operators (and scripts) can see a likely-crashed holder. It is never
#: auto-cleared: takeover requires an explicit forced entry.
STALE_AFTER_SECONDS = 30 * 60


def _get_state(*, locked: bool = False):
    from apps.operations.models import MaintenanceState

    qs = MaintenanceState.objects.all()
    if locked:
        qs = qs.select_for_update()
    try:
        return qs.get(pk=1)
    except MaintenanceState.DoesNotExist:
        return MaintenanceState(pk=1)


def is_maintenance_active() -> bool:
    from apps.operations.models import MaintenanceState

    try:
        return bool(MaintenanceState.objects.get(pk=1).maintenance_mode)
    except MaintenanceState.DoesNotExist:
        return False


def maintenance_status() -> dict:
    """Describe the current window for operators and scripts."""
    from apps.operations.models import MaintenanceState

    try:
        state = MaintenanceState.objects.get(pk=1)
    except MaintenanceState.DoesNotExist:
        return {"active": False, "owner": "", "reason": "",
                "depth": 0, "stale": False, "age_seconds": None}
    age = None
    if state.acquired_at is not None:
        age = (timezone.now() - state.acquired_at).total_seconds()
    return {
        "active": bool(state.maintenance_mode),
        "owner": state.owner,
        "reason": state.reason,
        "depth": state.depth,
        "stale": bool(state.maintenance_mode and age is not None
                      and age > STALE_AFTER_SECONDS),
        "age_seconds": age,
    }


def assert_writes_allowed() -> None:
    if is_maintenance_active():
        raise MaintenanceActiveError("writes are blocked during maintenance")


def assert_maintenance_active() -> None:
    """For restore/offline paths that must only run inside a held window."""
    if not is_maintenance_active():
        raise MaintenanceActiveError(
            "this operation requires an active maintenance window")


@contextmanager
def maintenance_mode(owner: str = "operation", reason: str = "",
                     token: str | None = None):
    """Hold an owned exclusive maintenance window for the block.

    Acquisition failure raises ``MaintenanceActiveError`` (the caller aborts);
    the window is released with the entry token, so exceptions inside the
    block close only this holder's own window. ``token`` presents an outer
    holder's token to nest: host scripts own the outer window and pass the
    token down to inner commands. A release failure never masks the block's
    own exception.
    """
    import sys

    entry = enter_maintenance(owner=owner, reason=reason, token=token)
    try:
        yield entry
    finally:
        try:
            exit_maintenance(entry)
        except MaintenanceActiveError:
            if sys.exception() is None:
                raise


def enter_maintenance(owner: str = "operation", reason: str = "",
                      force: bool = False, token: str | None = None) -> str:
    """Acquire the window. Returns the ownership token for ``exit_maintenance``.

    Raises ``MaintenanceActiveError`` when another owner holds it (unless
    ``force=True``, which takes over explicitly and audits the takeover).
    Nesting under the same owner requires presenting the held ``token``:
    host scripts capture it from ``--enter`` and pass it down, so an inner
    command joins its own operation's window but can never join (or clear)
    someone else's.
    """
    from apps.operations.models import MaintenanceState

    fresh = secrets.token_hex(16)
    with transaction.atomic():
        try:
            state = MaintenanceState.objects.select_for_update().get(pk=1)
        except MaintenanceState.DoesNotExist:
            state = MaintenanceState(pk=1)
        if state.maintenance_mode:
            if state.token and owner == state.owner and not force:
                # Re-entrant hold by the same owner presenting its token.
                if token != state.token:
                    raise MaintenanceActiveError(
                        "maintenance already held by "
                        f"{state.owner or 'another operation'}; present its "
                        "token to nest, or abort instead of sharing the window")
                state.depth += 1
                state.save()
                return state.token
            if not force:
                raise MaintenanceActiveError(
                    f"maintenance already held by {state.owner or 'another operation'}"
                    + (f" ({state.reason})" if state.reason else "")
                    + "; aborting instead of sharing the window")
            _audit_takeover(previous_owner=state.owner, new_owner=owner,
                            reason=reason)
            state.owner = owner
            state.token = fresh
            state.reason = reason or ""
            state.depth = 1
            state.acquired_at = timezone.now()
            state.maintenance_mode = True
            state.save()
            return fresh
        state.maintenance_mode = True
        state.owner = owner
        state.token = fresh
        state.reason = reason or ""
        state.depth = 1
        state.acquired_at = timezone.now()
        state.save()
        return fresh


def exit_maintenance(token: str | None = None) -> bool:
    """Release one hold on the window. Only the owning token can release.

    Returns True when this call closed the window, False when an outer hold
    is still active. A token that does not own the window raises
    ``MaintenanceActiveError`` instead of clearing someone else's window.
    ``token=None`` preserves the legacy call shape and is only honored when
    no owned window is active (otherwise it raises).
    """
    from apps.operations.models import MaintenanceState

    with transaction.atomic():
        try:
            state = MaintenanceState.objects.select_for_update().get(pk=1)
        except MaintenanceState.DoesNotExist:
            return False
        if not state.maintenance_mode:
            return False
        if token is None:
            raise MaintenanceActiveError(
                "refusing to clear a maintenance window held by "
                f"{state.owner or 'another operation'} without its token")
        if token != state.token:
            raise MaintenanceActiveError(
                "refusing to clear another holder's maintenance window")
        state.depth = max(0, (state.depth or 1) - 1)
        if state.depth > 0:
            state.save()
            return False
        state.maintenance_mode = False
        state.owner = ""
        state.token = ""
        state.reason = ""
        state.depth = 0
        state.save()
        return True


def _audit_takeover(*, previous_owner: str, new_owner: str, reason: str) -> None:
    try:
        from apps.audit.models import AuditEvent

        AuditEvent.objects.create(
            actor=None, action="maintenance.takeover",
            target_type="system", target_id="maintenance",
            detail={"previous_owner": previous_owner or "",
                    "new_owner": new_owner, "reason": reason or ""},
        )
    except Exception:
        pass
