"""Fresh identity confirmation for sensitive administrator actions.

A re-authentication is bound to the exact identity that confirmed it: the
session stores the user id plus the current password hash, so a password
change (or a different account on the same session) invalidates the
confirmation immediately. Confirmations expire after
``REAUTH_WINDOW_SECONDS``. Failures are throttled with the shared atomic
throttle used for logins, so the password-confirm form cannot be guessed
without bound.
"""

from __future__ import annotations

import time
from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts import throttling as throttle_mod
from apps.common.errors import ThrottledError
from apps.operations.reset import REAUTH_WINDOW_SECONDS


def mark_reauthenticated(request) -> None:
    request.session["admin_reauth"] = {
        "at": time.time(),
        "user_id": request.user.pk,
        "pwd": request.user.password,
    }
    try:
        request.session.cycle_key()
    except Exception:
        pass


def reauth_fresh(request) -> bool:
    try:
        record = request.session.get("admin_reauth") or {}
        if not isinstance(record, dict):
            return False
        if record.get("user_id") != request.user.pk:
            return False
        if record.get("pwd") != request.user.password:
            return False
        return (time.time() - float(record.get("at", 0))) < REAUTH_WINDOW_SECONDS
    except (TypeError, ValueError, AttributeError):
        return False


def reauth_key_for(user) -> str:
    return throttle_mod.reauth_key(user.pk)


def check_reauth_allowed(user) -> None:
    throttle_mod.check_allowed(reauth_key_for(user))


def record_reauth_failure(user) -> None:
    throttle_mod.record_failure(reauth_key_for(user))


def record_reauth_success(user) -> None:
    throttle_mod.record_success(reauth_key_for(user))


def safe_next(request, default: str) -> str:
    nxt = request.GET.get("next") or request.POST.get("next") or default
    if url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return default


def reauth_required(view_func):
    """Require a fresh password-bound confirmation; else send to re-auth."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not reauth_fresh(request):
            messages.error(
                request, "Confirm your password first (recent sign-in required).")
            return redirect(f"/admin-area/reauth/?next={request.path}")
        try:
            return view_func(request, *args, **kwargs)
        except ThrottledError as exc:
            messages.error(request, str(exc))
            return redirect(f"/admin-area/reauth/?next={request.path}")

    return _wrapped
