"""Account permission services. Every server entrypoint checks these."""

from __future__ import annotations

from django.contrib.auth import get_user_model

from apps.common.errors import PermissionDeniedError, ValidationError

User = get_user_model()


def is_admin(user) -> bool:
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and user.is_active
        and user.is_superuser
    )


def is_staff_member(user) -> bool:
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and user.is_active
        and (user.is_staff or user.is_superuser)
    )


def require_staff(user) -> None:
    if not is_staff_member(user):
        raise PermissionDeniedError("staff permission required")


def require_admin(user) -> None:
    if not is_admin(user):
        raise PermissionDeniedError("admin permission required")


def can_manage_roster(user) -> bool:
    return is_staff_member(user)


def can_manage_inventory(user) -> bool:
    return is_staff_member(user)


def can_issue(user) -> bool:
    return is_staff_member(user)


def can_reverse(user) -> bool:
    return is_staff_member(user)


def can_manage_users(user) -> bool:
    return is_admin(user)


def validate_new_user(*, username: str, password: str) -> None:
    from django.contrib.auth.password_validation import validate_password

    if not username or not username.strip():
        raise ValidationError("username is required")
    if not password:
        raise ValidationError("temporary password is required")
    try:
        validate_password(password)
    except Exception as exc:
        raise ValidationError(f"temporary password is too weak: {exc}") from exc
