from __future__ import annotations

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from apps.accounts import services as permissions


def no_activity_update(view_func):
    """Mark a view as a background poll that must not extend idle expiry."""
    view_func.no_activity_update = True
    return view_func


def staff_required(view_func):
    @login_required
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not permissions.is_staff_member(request.user):
            raise PermissionDenied("staff permission required")
        return view_func(request, *args, **kwargs)

    return _wrapped


def admin_required(view_func):
    @login_required
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not permissions.is_admin(request.user):
            raise PermissionDenied("admin permission required")
        return view_func(request, *args, **kwargs)

    return _wrapped
