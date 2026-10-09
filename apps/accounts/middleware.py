"""Session idle-expiry and account-status middleware.

IdleTimeoutMiddleware expires a session after 30 minutes of user inactivity.
Poll endpoints must not extend the timer: views marked with
@no_activity_update (or paths in settings.SESSION_POLL_PATHS) are read without
refreshing last-activity.

AccountStatusMiddleware logs out deactivated accounts immediately and enforces
the temporary-password gate before application access.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils import timezone

PASSWORD_CHANGE_PATH = "/accounts/password-change/"
EXEMPT_PREFIXES = (
    "/accounts/login/",
    "/accounts/logout/",
    PASSWORD_CHANGE_PATH,
    "/static/",
)


class IdleTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            now = timezone.now().timestamp()
            timeout = getattr(settings, "SESSION_IDLE_TIMEOUT", 1800)
            last = request.session.get("last_activity")
            if last is not None and (now - float(last)) > timeout:
                logout(request)
                if request.path.startswith("/api/"):
                    from django.http import JsonResponse

                    return JsonResponse(
                        {"ok": False, "error": "sign-in-required"}, status=401)
                return redirect(f"{settings.LOGIN_URL}?expired=1")
        response = self.get_response(request)
        # Refresh idle timer only for authenticated, non-poll activity.
        user = getattr(request, "user", None)
        poll_paths = getattr(settings, "SESSION_POLL_PATHS", [])
        is_poll_path = any(request.path.startswith(p) for p in poll_paths)
        is_poll_view = getattr(getattr(response, "resolver_match", None), "func", None) is not None and getattr(
            response.resolver_match.func, "no_activity_update", False
        ) if hasattr(response, "resolver_match") and response.resolver_match else False
        # Fallback: function attribute check via request resolver is handled in
        # process_view; here we rely on path list plus the view flag when set
        # on the request by process_view.
        is_poll_view = getattr(request, "_no_activity_update", False) or is_poll_view
        if (
            user is not None
            and getattr(user, "is_authenticated", False)
            and not is_poll_path
            and not is_poll_view
        ):
            request.session["last_activity"] = timezone.now().timestamp()
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        if getattr(view_func, "no_activity_update", False):
            request._no_activity_update = True
        return None


class AccountStatusMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            # Re-read active flag so deactivation takes effect immediately.
            try:
                fresh = user.__class__.objects.get(pk=user.pk)
            except user.__class__.DoesNotExist:
                logout(request)
                return self._auth_failure(request)
            if not fresh.is_active:
                logout(request)
                return self._auth_failure(request)
            if fresh.must_change_password and not self._exempt(request.path):
                if request.path.startswith("/api/"):
                    from django.http import JsonResponse

                    return JsonResponse(
                        {"ok": False, "error": "password-change-required"},
                        status=403)
                return redirect(PASSWORD_CHANGE_PATH)
        return self.get_response(request)

    @staticmethod
    def _auth_failure(request):
        if request.path.startswith("/api/"):
            from django.http import JsonResponse

            return JsonResponse({"ok": False, "error": "sign-in-required"},
                                status=401)
        return redirect(f"{settings.LOGIN_URL}?expired=1")

    @staticmethod
    def _exempt(path: str) -> bool:
        return any(path.startswith(p) for p in EXEMPT_PREFIXES)
