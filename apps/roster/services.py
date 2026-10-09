"""Roster services with optimistic version checks."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.accounts import services as permissions
from apps.common.errors import StaleVersionError, ValidationError
from apps.operations.guards import assert_writes_allowed


def _clean(value: str | None) -> str:
    return (value or "").strip()


def create_missionary(*, actor, external_id: str, name: str, email: str = ""):
    permissions.require_staff(actor)
    assert_writes_allowed()
    external_id = _clean(external_id)
    name = _clean(name)
    if not external_id:
        raise ValidationError("external ID is required")
    if not name:
        raise ValidationError("name is required")
    from apps.roster.models import Missionary

    if Missionary.objects.filter(external_id=external_id).exists():
        raise ValidationError("external ID already exists")
    with transaction.atomic():
        obj = Missionary.objects.create(
            external_id=external_id, name=name, email=_clean(email)
        )
        _audit(actor, "roster.created", obj)
        return obj


def update_missionary(*, actor, missionary_id, expected_version: int, **fields):
    permissions.require_staff(actor)
    assert_writes_allowed()
    from apps.roster.models import Missionary

    with transaction.atomic():
        try:
            obj = Missionary.objects.select_for_update().get(pk=missionary_id)
        except Missionary.DoesNotExist as exc:
            raise ValidationError("missionary not found") from exc
        if obj.version != expected_version:
            raise StaleVersionError("stale roster record; refresh and retry")
        if "external_id" in fields and fields["external_id"] is not None:
            new_ext = _clean(fields["external_id"])
            if not new_ext:
                raise ValidationError("external ID is required")
            if new_ext != obj.external_id and Missionary.objects.filter(
                external_id=new_ext
            ).exists():
                raise ValidationError("external ID already exists")
            obj.external_id = new_ext
        if "name" in fields and fields["name"] is not None:
            new_name = _clean(fields["name"])
            if not new_name:
                raise ValidationError("name is required")
            obj.name = new_name
        if "email" in fields and fields["email"] is not None:
            obj.email = _clean(fields["email"])
        obj.version += 1
        obj.full_clean()
        obj.save()
        _audit(actor, "roster.updated", obj)
        return obj


def archive_missionary(*, actor, missionary_id, expected_version: int | None = None):
    permissions.require_staff(actor)
    assert_writes_allowed()
    from apps.roster.models import Missionary

    with transaction.atomic():
        obj = Missionary.objects.select_for_update().get(pk=missionary_id)
        if expected_version is not None and obj.version != expected_version:
            raise StaleVersionError("stale roster record; refresh and retry")
        obj.is_archived = True
        obj.archived_at = timezone.now()
        obj.version += 1
        obj.save()
        _audit(actor, "roster.archived", obj)
        return obj


def reactivate_missionary(*, actor, missionary_id):
    permissions.require_staff(actor)
    assert_writes_allowed()
    from apps.roster.models import Missionary

    with transaction.atomic():
        obj = Missionary.objects.select_for_update().get(pk=missionary_id)
        obj.is_archived = False
        obj.archived_at = None
        obj.version += 1
        obj.save()
        _audit(actor, "roster.reactivated", obj)
        return obj


def _audit(actor, action, obj):
    from apps.audit.models import AuditEvent

    AuditEvent.objects.create(
        actor=actor,
        action=action,
        target_type="missionary",
        target_id=str(obj.pk),
        detail={"external_id": obj.external_id, "name": obj.name},
    )
