"""Shared atomic bounded throttling backed by the database.

One bucket per normalized identity key, updated under ``select_for_update``
so concurrent failures in different Gunicorn workers count atomically (a
per-process cache counter would let each worker grant its own attempts).
Used for both login attempts and administrator re-authentication, with
separate key namespaces. Never stores passwords — only counters and lock
timestamps.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone


def normalize_key(raw: str) -> str:
    return (raw or "").strip().lower()


def _key(kind: str, identity: str) -> str:
    return f"{kind}:{normalize_key(identity)}"


def login_key(username: str) -> str:
    return _key("login", username)


def reauth_key(user_id) -> str:
    return _key("reauth", str(user_id))


def _limits() -> tuple[int, int, int]:
    return (
        int(getattr(settings, "LOGIN_THROTTLE_MAX_ATTEMPTS", 5)),
        int(getattr(settings, "LOGIN_THROTTLE_WINDOW", 600)),
        int(getattr(settings, "LOGIN_THROTTLE_LOCKOUT", 600)),
    )


def _bucket(key: str, *, locked: bool = False):
    from apps.accounts.models import LoginThrottle

    qs = LoginThrottle.objects.all()
    if locked:
        qs = qs.select_for_update()
    try:
        return qs.get(key=key)
    except LoginThrottle.DoesNotExist:
        return LoginThrottle(key=key)


def _coerce(key_or_username: str) -> str:
    """Accept a namespaced throttle key or a bare login username."""
    text = (key_or_username or "").strip()
    if ":" in text:
        return text
    return login_key(text)


def is_locked(key_or_username: str) -> bool:
    key = _coerce(key_or_username)
    if not normalize_key(key.split(":", 1)[-1]):
        return False
    from apps.accounts.models import LoginThrottle

    try:
        bucket = LoginThrottle.objects.get(key=key)
    except LoginThrottle.DoesNotExist:
        return False
    if bucket.locked_until is None:
        return False
    if bucket.locked_until <= timezone.now():
        return False
    return True


def check_allowed(key_or_username: str) -> None:
    from apps.common.errors import ThrottledError

    if is_locked(_coerce(key_or_username)):
        raise ThrottledError("too many attempts; try again later")


def record_failure(key_or_username: str) -> None:
    key = _coerce(key_or_username)
    if not normalize_key(key.split(":", 1)[-1]):
        return
    max_attempts, window, lockout = _limits()
    now = timezone.now()
    with transaction.atomic():
        bucket = _bucket(key, locked=True)
        if bucket.window_started_at is None or (
                now - bucket.window_started_at).total_seconds() > window:
            bucket.failures = 1
            bucket.window_started_at = now
        else:
            bucket.failures = (bucket.failures or 0) + 1
        if bucket.failures >= max_attempts:
            bucket.locked_until = now + timedelta(seconds=lockout)
        bucket.save()


def record_success(key_or_username: str) -> None:
    key = _coerce(key_or_username)
    if not normalize_key(key.split(":", 1)[-1]):
        return
    from apps.accounts.models import LoginThrottle

    LoginThrottle.objects.filter(key=key).delete()


def clear(key_or_username: str) -> None:
    record_success(_coerce(key_or_username))


# Backwards-compatible per-username helpers used by the login form.
def _key_username(username: str) -> str:
    return login_key(username)


def _lock_key(username: str) -> str:  # pragma: no cover - legacy shape
    return login_key(username)
