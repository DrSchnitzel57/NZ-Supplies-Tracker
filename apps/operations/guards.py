"""Maintenance/write guards reusable by import/reset/update operations."""

from __future__ import annotations

from contextlib import contextmanager

from apps.common.errors import MaintenanceActiveError


def _state():
    from apps.operations.models import MaintenanceState

    state, _ = MaintenanceState.objects.get_or_create(pk=1)
    return state


def is_maintenance_active() -> bool:
    return _state().maintenance_mode


def assert_writes_allowed() -> None:
    if is_maintenance_active():
        raise MaintenanceActiveError("writes are blocked during maintenance")


@contextmanager
def maintenance_mode():
    """Exclusive maintenance window: blocks routine writes inside the block.

    The flag is committed in its own transactions on entry and exit so that
    other connections observe it while the block is active.
    """
    enter_maintenance()
    try:
        yield _state()
    finally:
        exit_maintenance()


def enter_maintenance():
    from apps.operations.models import MaintenanceState

    state, _ = MaintenanceState.objects.get_or_create(pk=1)
    state.maintenance_mode = True
    state.save()
    return state


def exit_maintenance():
    from apps.operations.models import MaintenanceState

    state, _ = MaintenanceState.objects.get_or_create(pk=1)
    state.maintenance_mode = False
    state.save()
    return state
