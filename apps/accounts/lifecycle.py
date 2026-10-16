"""Account lifecycle operations: provisioning, temp passwords, deactivation."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.utils import timezone

from apps.accounts import services as permissions
from apps.common.errors import ValidationError

User = get_user_model()


def create_user(*, actor, username: str, password: str, is_admin: bool = False,
                display_name: str = ""):
    """Admin-only provisioning. New accounts must change the temp password."""
    permissions.require_admin(actor)
    permissions.validate_new_user(username=username, password=password)
    username = username.strip()
    if User.objects.filter(username=username).exists():
        raise ValidationError("username already exists")
    user = User(username=username, display_name=(display_name or "").strip()[:80])
    user.set_password(password)
    user.must_change_password = True
    user.password_changed_at = None
    if is_admin:
        user.is_staff = True
        user.is_superuser = True
    else:
        user.is_staff = True
        user.is_superuser = False
    user.full_clean(exclude=["password"])
    user.save()
    from apps.audit.models import AuditEvent

    AuditEvent.objects.create(
        actor=actor,
        action="user.created",
        target_type="user",
        target_id=str(user.pk),
        detail={"username": username, "is_admin": is_admin},
    )
    return user


def reset_password(*, actor, user_id, temporary_password: str):
    permissions.require_admin(actor)
    if not temporary_password:
        raise ValidationError("temporary password is required")
    from django.contrib.auth.password_validation import validate_password
    try:
        validate_password(temporary_password)
    except Exception as exc:
        raise ValidationError(f"temporary password is too weak: {exc}") from exc
    user = User.objects.get(pk=user_id)
    user.set_password(temporary_password)
    user.must_change_password = True
    user.password_changed_at = None
    user.save(update_fields=["password", "must_change_password", "password_changed_at"])
    invalidate_sessions(user)
    from apps.audit.models import AuditEvent

    AuditEvent.objects.create(
        actor=actor,
        action="user.password_reset",
        target_type="user",
        target_id=str(user.pk),
        detail={"username": user.username},
    )
    return user


def complete_password_change(*, user, new_password: str):
    if not new_password:
        raise ValidationError("password is required")
    user.set_password(new_password)
    user.must_change_password = False
    user.password_changed_at = timezone.now()
    user.save(update_fields=["password", "must_change_password", "password_changed_at"])
    _audit_password_changed(user)
    return user


def mark_password_changed(*, user):
    """Clear the temporary-password gate after a UI password change.

    The password itself was already saved by the password-change form, so
    this only clears the flag, stamps the change and records the audit event
    without touching the password hash (which would invalidate the session).
    """
    user.must_change_password = False
    user.password_changed_at = timezone.now()
    user.save(update_fields=["must_change_password", "password_changed_at"])
    _audit_password_changed(user)
    return user


def _audit_password_changed(user):
    from apps.audit.models import AuditEvent

    AuditEvent.objects.create(
        actor=user,
        action="user.password_changed",
        target_type="user",
        target_id=str(user.pk),
        detail={},
    )
    return user


def deactivate_user(*, actor, user_id):
    permissions.require_admin(actor)
    user = User.objects.get(pk=user_id)
    if user.pk == actor.pk:
        raise ValidationError("cannot deactivate your own account")
    user.is_active = False
    user.save(update_fields=["is_active"])
    invalidate_sessions(user)
    from apps.audit.models import AuditEvent

    AuditEvent.objects.create(
        actor=actor,
        action="user.deactivated",
        target_type="user",
        target_id=str(user.pk),
        detail={"username": user.username},
    )
    return user


def reactivate_user(*, actor, user_id):
    permissions.require_admin(actor)
    user = User.objects.get(pk=user_id)
    user.is_active = True
    user.save(update_fields=["is_active"])
    from apps.audit.models import AuditEvent

    AuditEvent.objects.create(
        actor=actor,
        action="user.reactivated",
        target_type="user",
        target_id=str(user.pk),
        detail={"username": user.username},
    )
    return user


def invalidate_sessions(user) -> int:
    """Delete every active session belonging to user. Returns count."""
    count = 0
    for session in Session.objects.all().iterator():
        data = session.get_decoded()
        if str(data.get("_auth_user_id")) == str(user.pk):
            session.delete()
            count += 1
    return count


def set_display_name(*, actor, user_id, display_name: str):
    """Admin-only: the name shown to colleagues ("Sister Langkilde")."""
    permissions.require_admin(actor)
    user = User.objects.get(pk=user_id)
    user.display_name = (display_name or "").strip()[:80]
    user.save(update_fields=["display_name"])
    from apps.audit.models import AuditEvent

    AuditEvent.objects.create(
        actor=actor, action="user.display_name", target_type="user",
        target_id=str(user.pk), detail={"username": user.username,
                                        "display_name": user.display_name})
    return user
