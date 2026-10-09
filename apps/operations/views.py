"""Dashboard, on-screen reports, guide, settings, reset and polling endpoints."""

from __future__ import annotations

import time

from django.contrib import messages
from django.contrib.auth import authenticate
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.accounts.decorators import (
    admin_required,
    no_activity_update,
    staff_required,
    staff_required_api,
)
from apps.common.errors import DomainError
from apps.inventory.models import Item
from apps.issuing.models import Issue, IssueLine
from apps.operations import reset as reset_service
from apps.operations.models import SystemSetting
from apps.roster.models import Missionary


@staff_required
def dashboard(request):
    low_items = list(
        Item.objects.filter(
            is_archived=False, on_hand__lte=Coalesce("low_threshold", 0)
        ).order_by("category", "name")[:20]
    )
    recent = list(
        Issue.objects.select_related("missionary")
        .prefetch_related("lines")
        .order_by("-issued_at")[:10]
    )
    counts = {
        "people": Missionary.objects.filter(is_archived=False).count(),
        "items": Item.objects.filter(is_archived=False).count(),
        "issues_today": Issue.objects.filter(
            issued_at__date=timezone.now().date(), status=Issue.ACTIVE
        ).count(),
    }
    return render(
        request,
        "dashboard.html",
        {"low_items": low_items, "recent": recent, "counts": counts},
    )


@staff_required
def guide(request):
    return render(request, "guide.html")


@staff_required_api
@no_activity_update
@require_GET
def stock_api(request):
    items = Item.objects.filter(is_archived=False).order_by("category", "name")[
        :2000
    ]
    payload = [
        {
            "id": str(i.pk),
            "label": str(i),
            "available": i.on_hand,
            "low_threshold": i.low_threshold,
            "low": i.on_hand <= (i.low_threshold or 0),
        }
        for i in items
    ]
    return JsonResponse({"items": payload, "now": timezone.now().isoformat()})


@staff_required_api
@no_activity_update
@require_GET
def dashboard_api(request):
    from django.conf import settings

    low = Item.objects.filter(
        is_archived=False, on_hand__lte=Coalesce("low_threshold", 0)
    ).count()
    # Server-driven expiry countdown so the warning banner is honest: it
    # reflects the actual idle timer, not page-load time.
    last = request.session.get("last_activity")
    timeout = getattr(settings, "SESSION_IDLE_TIMEOUT", 1800)
    expires_in = timeout  # just signed in; the timer starts from now
    if last is not None:
        try:
            expires_in = max(0, int(timeout - (timezone.now().timestamp() - float(last))))
        except (TypeError, ValueError):
            expires_in = timeout
    return JsonResponse(
        {
            "people": Missionary.objects.filter(is_archived=False).count(),
            "items": Item.objects.filter(is_archived=False).count(),
            "low_stock": low,
            "issues_today": Issue.objects.filter(
                issued_at__date=timezone.now().date(), status=Issue.ACTIVE
            ).count(),
            "now": timezone.now().isoformat(),
            "expires_in": expires_in,
        }
    )


@admin_required
def settings_view(request):
    from apps.accounts import reauth as reauth_mod
    from apps.operations.guards import assert_writes_allowed

    if request.method == "POST":
        if not reauth_mod.reauth_fresh(request):
            messages.error(request, "Confirm your password first (recent sign-in required).")
            return redirect("/admin-area/reauth/?next=/admin-area/settings/")
        notice = (request.POST.get("site_notice") or "").strip()[:500]
        threshold_raw = (request.POST.get("default_low_threshold") or "").strip()
        try:
            threshold = max(0, int(threshold_raw)) if threshold_raw else 0
        except ValueError:
            messages.error(request, "Default alert level must be a whole number.")
            return redirect("ops-settings")
        try:
            assert_writes_allowed()
        except DomainError as exc:
            messages.error(request, f"Settings refused: {exc}")
            return redirect("ops-settings")
        SystemSetting.objects.update_or_create(key="site_notice", defaults={"value": notice})
        SystemSetting.objects.update_or_create(
            key="default_low_threshold", defaults={"value": str(threshold)})
        from apps.audit.models import AuditEvent
        AuditEvent.objects.create(
            actor=request.user, action="settings.updated",
            target_type="system", target_id="settings",
            detail={"default_low_threshold": threshold,
                    "notice_len": len(notice)})
        messages.success(request, "Saved system settings.")
        return redirect("ops-settings")
    current = {s.key: s.value for s in SystemSetting.objects.all()}
    return render(request, "operations/settings.html", {"settings": current})


@admin_required
@no_activity_update
def reauth_view(request):
    from apps.accounts import reauth as reauth_mod

    if request.method == "POST":
        try:
            reauth_mod.check_reauth_allowed(request.user)
        except DomainError as exc:
            messages.error(request, str(exc))
            return render(request, "operations/reauth.html",
                          {"next": request.GET.get("next", "")}, status=429)
        user = authenticate(request, username=request.user.username,
                            password=request.POST.get("password", ""))
        if user is None:
            reauth_mod.record_reauth_failure(request.user)
            messages.error(request, "Password did not match. Try again.")
            return render(request, "operations/reauth.html",
                          {"next": request.GET.get("next", "")}, status=401)
        reauth_mod.record_reauth_success(request.user)
        reauth_mod.mark_reauthenticated(request)
        messages.success(request, "Identity confirmed for the next 10 minutes.")
        return redirect(reauth_mod.safe_next(request, "/admin-area/reset/"))
    return render(request, "operations/reauth.html", {"next": request.GET.get("next", "")})


@admin_required
def reset_view(request):
    from apps.accounts import reauth as reauth_mod

    if request.method == "POST":
        if not reauth_mod.reauth_fresh(request):
            messages.error(request, "Confirm your password first (recent sign-in required).")
            return redirect("/admin-area/reauth/?next=/admin-area/reset/")
        try:
            info = reset_service.reset_to_empty(
                actor=request.user,
                confirmation=request.POST.get("confirmation", ""),
                expected_counts={
                    "people": request.POST.get("expected_people", ""),
                    "items": request.POST.get("expected_items", ""),
                    "issues": request.POST.get("expected_issues", ""),
                })
        except DomainError as exc:
            messages.error(request, f"Reset refused: {exc}")
            return redirect("ops-reset")
        except Exception:
            messages.error(request, "Reset refused: backup or database error; nothing was deleted.")
            return redirect("ops-reset")
        messages.success(
            request,
            f"Operational data cleared. Verified backup saved ({info.get('bytes', 0)} bytes, "
            f"sha256 {info.get('sha256', '')[:16]}…).")
        return redirect("dashboard")
    fresh = reauth_mod.reauth_fresh(request)
    counts = reset_service.current_counts()
    return render(request, "operations/reset.html", {"fresh": fresh, "counts": counts})
