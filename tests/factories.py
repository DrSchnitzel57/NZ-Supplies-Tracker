"""Synthetic fixtures only. No real roster/personal data."""

from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model

User = get_user_model()


def uid() -> str:
    return uuid.uuid4().hex[:8]


def make_admin(username: str | None = None, password: str = "AdminPass123!"):
    username = username or f"admin-{uid()}"
    user = User.objects.create_user(username=username, password=password)
    user.is_staff = True
    user.is_superuser = True
    user.must_change_password = False
    user.save()
    return user, password


def make_staff(username: str | None = None, password: str = "StaffPass123!"):
    username = username or f"staff-{uid()}"
    user = User.objects.create_user(username=username, password=password)
    user.is_staff = True
    user.is_superuser = False
    user.must_change_password = False
    user.save()
    return user, password


def make_missionary(actor, external_id: str | None = None, name: str | None = None):
    from apps.roster import services as roster_services

    tag = uid()
    return roster_services.create_missionary(
        actor=actor,
        external_id=external_id or f"EXT-{tag}",
        name=name or f"Synthetic Person {tag}",
        email=f"person-{tag}@example.test",
    )


def make_item(actor, opening_qty: int = 10, **overrides):
    from apps.inventory import services as inventory_services

    tag = uid()
    return inventory_services.create_item(
        actor=actor,
        category=overrides.get("category", f"Cat {tag}"),
        name=overrides.get("name", f"Item {tag}"),
        size=overrides.get("size", ""),
        opening_qty=opening_qty,
        reason=overrides.get("reason", "opening balance"),
        idempotency_key=overrides.get("idempotency_key") or f"open-{tag}",
    )
