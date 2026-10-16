"""Figures for the Overview screen, shared by the page and its 15-second refresh.

Stock alerts separate what staff can act on:
* running low  — still on the shelf, at or below the item's alert level;
* ran out      — at zero after having had stock (it was given out or received);
* never stocked — at zero and never had any stock (listed only in Inventory,
  so a long tail of unused catalogue lines doesn't drown the real alerts).
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Exists, F, OuterRef, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.inventory.models import Item, StockMovement
from apps.issuing.models import Issue, IssueLine
from apps.roster.models import ARRIVED, EXPECTED, Missionary

LEAVING_WINDOW_DAYS = 14


def _active_items():
    stocked = StockMovement.objects.filter(item=OuterRef("pk"), delta__gt=0)
    return Item.objects.filter(is_archived=False).annotate(ever_stocked=Exists(stocked))


def stock_counts() -> dict:
    items = _active_items()
    at_or_below = items.filter(on_hand__lte=Coalesce("low_threshold", 0))
    return {
        # Kept for the API/test contract: every active item at or below its level.
        "low_stock": at_or_below.count(),
        "running_low": at_or_below.filter(on_hand__gt=0).count(),
        "ran_out": items.filter(on_hand=0, ever_stocked=True).count(),
        "never_stocked": items.filter(on_hand=0, ever_stocked=False).count(),
        "out_of_stock": items.filter(on_hand=0).count(),
    }


def attention(limit: int = 6) -> dict:
    """The short lists on the Overview: what to reorder and who is leaving."""
    items = _active_items()
    running_low = list(
        items.filter(on_hand__gt=0, on_hand__lte=F("low_threshold"))
        .order_by("on_hand", "category", "name")[:200]
    )
    running_low.sort(key=lambda i: (i.on_hand / max(i.low_threshold, 1), i.category, i.name))
    last_issue = (StockMovement.objects.filter(item=OuterRef("pk"), kind=StockMovement.ISSUE)
                  .order_by("-created_at").values("created_at")[:1])
    ran_out = list(
        items.filter(on_hand=0, ever_stocked=True)
        .annotate(last_out=last_issue)
        .order_by(F("last_out").desc(nulls_last=True), "category", "name")[:limit]
    )
    today = timezone.localdate()
    leaving = list(
        Missionary.objects.filter(
            is_archived=False, departure_date__gte=today,
            departure_date__lte=today + timedelta(days=LEAVING_WINDOW_DAYS))
        .annotate(received=Coalesce(Sum("issues__lines__quantity",
                                        filter=Q(issues__status=Issue.ACTIVE)), 0))
        .order_by("departure_date", "name")
    )
    return {"running_low": running_low[:limit], "running_low_total": len(running_low),
            "ran_out": ran_out[:limit], "leaving": leaving[:limit + 2], "leaving_total": len(leaving),
            "leaving_window": LEAVING_WINDOW_DAYS}


def people_counts() -> dict:
    people = Missionary.objects.filter(is_archived=False)
    by_status = dict(people.values_list("arrival_status").annotate(n=Count("id")))
    today = timezone.localdate()
    return {
        "people": people.count(),
        "arrived": by_status.get(ARRIVED, 0),
        "expected": by_status.get(EXPECTED, 0),
        "leaving_soon": people.filter(
            departure_date__gte=today,
            departure_date__lte=today + timedelta(days=LEAVING_WINDOW_DAYS)).count(),
        "next_departure": people.filter(departure_date__gte=today)
        .order_by("departure_date").values_list("departure_date", flat=True).first(),
    }


def activity_counts() -> dict:
    today = timezone.localdate()
    week_start = today - timedelta(days=6)
    current = Issue.objects.filter(status=Issue.ACTIVE)
    week = current.filter(issued_at__date__gte=week_start)
    return {
        "issues_today": current.filter(issued_at__date=today).count(),
        "week_issues": week.count(),
        "week_units": IssueLine.objects.filter(issue__in=week).aggregate(
            s=Coalesce(Sum("quantity"), 0))["s"],
        "week_people": week.values("missionary_id").distinct().count(),
    }


def given_by_category(days: int = 30) -> list[dict]:
    since = timezone.localdate() - timedelta(days=days - 1)
    rows = list(
        IssueLine.objects.filter(issue__status=Issue.ACTIVE, issue__issued_at__date__gte=since)
        .values("item_category").annotate(total=Sum("quantity")).order_by("-total", "item_category")
    )
    peak = max((r["total"] for r in rows), default=0) or 1
    return [{"label": r["item_category"], "value": r["total"],
             "width": max(2, round(r["total"] / peak * 100))} for r in rows[:8]]


def recent_issues(limit: int = 8):
    return list(Issue.objects.select_related("missionary", "issued_by")
                .prefetch_related("lines").order_by("-issued_at")[:limit])


def counts() -> dict:
    return {**stock_counts(), **people_counts(), **activity_counts(),
            "items": Item.objects.filter(is_archived=False).count()}
