"""Bounded login throttling backed by the Django cache.

Counts failures per normalized username within a window. After the limit is
reached, further attempts are locked out for a bounded period. Success clears
the counter. Never stores passwords.
"""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache


def _key(username: str) -> str:
    return f"login-throttle:{username.strip().lower()}"


def _lock_key(username: str) -> str:
    return f"login-lock:{username.strip().lower()}"


def is_locked(username: str) -> bool:
    if not username:
        return False
    return bool(cache.get(_lock_key(username)))


def check_allowed(username: str) -> None:
    from apps.common.errors import ThrottledError

    if is_locked(username):
        raise ThrottledError("too many login attempts; try again later")


def record_failure(username: str) -> None:
    if not username:
        return
    max_attempts = getattr(settings, "LOGIN_THROTTLE_MAX_ATTEMPTS", 5)
    window = getattr(settings, "LOGIN_THROTTLE_WINDOW", 600)
    lockout = getattr(settings, "LOGIN_THROTTLE_LOCKOUT", 600)
    key = _key(username)
    try:
        count = cache.get(key, 0) + 1
    except Exception:
        count = 1
    cache.set(key, count, window)
    if count >= max_attempts:
        cache.set(_lock_key(username), True, lockout)


def record_success(username: str) -> None:
    if not username:
        return
    cache.delete(_key(username))
    cache.delete(_lock_key(username))


def clear(username: str) -> None:
    record_success(username)
