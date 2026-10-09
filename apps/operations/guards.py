"""Maintenance/write guards reusable by import/reset/update operations."""

from __future__ import annotations

from contextlib import contextmanager

from django.db import transaction

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
    """Exclusive maintenance window: blocks routine writes inside the block."""
    from apps.operations.models import MaintenanceState

    with transaction.atomic():
        state, _ = MaintenanceState.objects.select_for_update().get_or_create(pk=1)
        state.maintenance_mode = True
        state.save()
        try:
            yield state
        finally:
            state.maintenance_mode = False
            state.save()


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
