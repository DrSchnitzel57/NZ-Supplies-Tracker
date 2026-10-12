"""Roster services with optimistic version checks."""

from __future__ import annotations

from django.db import IntegrityError
from django.utils import timezone

from apps.accounts import services as permissions
from apps.common.db import transact
from apps.common.errors import StaleVersionError, ValidationError
from apps.operations.guards import assert_writes_allowed


def _clean(value: str | None) -> str:
    return (value or "").strip()


def create_missionary(*, actor, external_id: str, name: str, email: str = "",
                      **extra):
    permissions.require_staff(actor)
    external_id = _clean(external_id)
    name = _clean(name)
    if not external_id:
        raise ValidationError("external ID is required")
    if not name:
        raise ValidationError("name is required")
    fields = {k: _clean(extra.get(k, "")) for k in (
        "title_prefix", "arrival_status", "stake", "mission", "language",
        "personal_email")}
    fields.update({k: extra.get(k) for k in ("arrival_date", "release_date")})
    note = _clean(extra.get("note", ""))
    from apps.roster.models import Missionary

    def _work():
        assert_writes_allowed()
        if Missionary.objects.filter(external_id=external_id).exists():
            raise ValidationError("external ID already exists")
        try:
            obj = Missionary(
                external_id=external_id, name=name, email=_clean(email),
                **fields,
            )
            try:
                obj.full_clean()
            except Exception as exc:
                raise ValidationError(str(exc)) from exc
            obj.save()
        except IntegrityError as exc:
            raise ValidationError("external ID already exists") from exc
        _audit(actor, "roster.created", obj, note=note)
        return obj

    return transact(_work)


def update_missionary(*, actor, missionary_id, expected_version: int, **fields):
    permissions.require_staff(actor)
    from apps.roster.models import Missionary

    def _work():
        assert_writes_allowed()
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
        for extra_field in ("title_prefix", "arrival_status", "stake",
                            "mission", "language", "personal_email"):
            if extra_field in fields and fields[extra_field] is not None:
                setattr(obj, extra_field, _clean(fields[extra_field]))
        if "arrival_date" in fields:
            obj.arrival_date = fields["arrival_date"]
        if "release_date" in fields:
            obj.release_date = fields["release_date"]
        obj.version += 1
        try:
            obj.full_clean()
        except Exception as exc:
            raise ValidationError(str(exc)) from exc
        try:
            obj.save()
        except IntegrityError as exc:
            raise ValidationError("external ID already exists") from exc
        _audit(actor, "roster.updated", obj, note=_clean(fields.get("note", "")))
        return obj

    return transact(_work)


def archive_missionary(*, actor, missionary_id, expected_version: int | None = None):
    permissions.require_staff(actor)
    from apps.roster.models import Missionary

    def _work():
        assert_writes_allowed()
        obj = Missionary.objects.select_for_update().get(pk=missionary_id)
        if expected_version is not None and obj.version != expected_version:
            raise StaleVersionError("stale roster record; refresh and retry")
        obj.is_archived = True
        obj.archived_at = timezone.now()
        obj.version += 1
        obj.save()
        _audit(actor, "roster.archived", obj)
        return obj

    return transact(_work)


def reactivate_missionary(*, actor, missionary_id):
    permissions.require_staff(actor)
    from apps.roster.models import Missionary

    def _work():
        assert_writes_allowed()
        obj = Missionary.objects.select_for_update().get(pk=missionary_id)
        obj.is_archived = False
        obj.archived_at = None
        obj.version += 1
        obj.save()
        _audit(actor, "roster.reactivated", obj)
        return obj

    return transact(_work)


def _audit(actor, action, obj, note: str = ""):
    from apps.audit.models import AuditEvent

    detail = {"external_id": obj.external_id, "name": obj.name}
    if note:
        detail["note"] = note
    AuditEvent.objects.create(
        actor=actor,
        action=action,
        target_type="missionary",
        target_id=str(obj.pk),
        detail=detail,
    )
