"""Dashboard, on-screen reports, guide, settings, reset and polling endpoints."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import authenticate
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.template.loader import render_to_string

from apps.accounts.decorators import (
    admin_required,
    no_activity_update,
    staff_required,
    staff_required_api,
)
from apps.common.errors import DomainError
from apps.inventory.models import Item
from apps.issuing.models import Issue
from apps.operations import reset as reset_service
from apps.operations.models import SystemSetting
from apps.roster.models import Missionary


@staff_required
def dashboard(request):
    from apps.operations import overview

    return render(request, "dashboard.html", {
        "counts": overview.counts(),
        "categories": overview.given_by_category(),
        "recent": overview.recent_issues(),
        **overview.attention(),
    })


@staff_required
def guide(request):
    return render(request, "guide.html")


@staff_required_api
@no_activity_update
@require_GET
def stock_api(request):
    from apps.inventory.views import _summaries
    items = _summaries(list(Item.objects.filter(is_archived=False).order_by("category", "name")))
    payload = [
        {
            "id": str(i.pk),
            "label": str(i),
            "available": i.on_hand,
            "low_threshold": i.low_threshold,
            "low": i.on_hand <= (i.low_threshold or 0),
            "net_given": i.net_given,
            "corrections_total": i.corrections_total,
        }
        for i in items
    ]
    return JsonResponse({"items": payload, "now": timezone.now().isoformat()})


@staff_required_api
@no_activity_update
@require_GET
def dashboard_api(request):
    from django.conf import settings

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
    from apps.operations import overview

    counts = overview.counts()
    return JsonResponse(
        {
            **{key: counts[key] for key in (
                "people", "items", "low_stock", "issues_today", "running_low", "ran_out",
                "out_of_stock", "arrived", "leaving_soon", "week_units", "week_issues")},
            "now": timezone.now().isoformat(),
            "expires_in": expires_in,
            "attention_html": render_to_string("components/home_attention.html", {
                **overview.attention(), "recent": overview.recent_issues(),
                "counts": counts}),
        }
    )


@admin_required
def settings_view(request):
    from apps.accounts import reauth as reauth_mod
    from apps.operations.settings_service import update_settings
    from apps.operations.forms import SettingsForm

    current = {s.key: s.value for s in SystemSetting.objects.all()}
    form = SettingsForm(request.POST if request.method == "POST" else None,
                        initial={"default_low_threshold": 0, **current})
    if request.method == "POST":
        if not reauth_mod.reauth_fresh(request):
            messages.error(request, "Confirm your password first (recent sign-in required).")
            return redirect("/admin-area/reauth/?next=/admin-area/settings/")
        if form.is_valid():
            try:
                update_settings(actor=request.user, notice=form.cleaned_data['site_notice'],
                                threshold=form.cleaned_data['default_low_threshold'])
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Saved system settings.")
                return redirect("ops-settings")
    return render(request, "operations/settings.html", {"form": form}, status=400 if form.is_bound else 200)


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
