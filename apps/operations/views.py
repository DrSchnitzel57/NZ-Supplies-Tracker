"""Dashboard, on-screen reports, guide and read-only polling endpoints."""

from __future__ import annotations

from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.accounts.decorators import no_activity_update, staff_required
from apps.inventory.models import Item
from apps.issuing.models import Issue, IssueLine
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
def reports(request):
    active_lines = IssueLine.objects.filter(issue__status=Issue.ACTIVE)
    per_item = list(
        active_lines.values(
            "item_id", "item_category", "item_name", "item_size"
        ).annotate(total=Sum("quantity")).order_by("-total")[:200]
    )
    per_person = list(
        active_lines.values(
            "issue__missionary_id",
            "issue__missionary_name",
            "issue__missionary_external_id",
        ).annotate(total=Sum("quantity")).order_by("-total")[:200]
    )
    low_items = list(
        Item.objects.filter(
            is_archived=False, on_hand__lte=Coalesce("low_threshold", 0)
        ).order_by("category", "name")[:200]
    )
    recent = list(
        Issue.objects.select_related("missionary", "issued_by", "reversed_by")
        .prefetch_related("lines")
        .order_by("-issued_at")[:50]
    )
    return render(
        request,
        "reports.html",
        {
            "per_item": per_item,
            "per_person": per_person,
            "low_items": low_items,
            "recent": recent,
        },
    )


@staff_required
def guide(request):
    return render(request, "guide.html")


@staff_required
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


@staff_required
@no_activity_update
@require_GET
def dashboard_api(request):
    low = Item.objects.filter(
        is_archived=False, on_hand__lte=Coalesce("low_threshold", 0)
    ).count()
    return JsonResponse(
        {
            "people": Missionary.objects.filter(is_archived=False).count(),
            "items": Item.objects.filter(is_archived=False).count(),
            "low_stock": low,
            "issues_today": Issue.objects.filter(
                issued_at__date=timezone.now().date(), status=Issue.ACTIVE
            ).count(),
            "now": timezone.now().isoformat(),
        }
    )
