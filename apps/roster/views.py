"""Roster views: list, detail/profile with quick issue, create, edit, archive."""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.db.models import F, Max, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import formats, timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.accounts.decorators import staff_required, staff_required_api
from apps.common.sorting import natural_key
from apps.common.errors import DomainError
from apps.common.pagination import paginate
from apps.common.search import people
from apps.common.navigation import list_return
from urllib.parse import urlencode
from apps.roster.forms import MissionaryForm
from apps.issuing.models import Issue, IssueLine
from apps.roster import services as roster_services
from apps.roster.models import ARRIVED, EXPECTED, Missionary


TITLE_FILTERS = [("", "All"), ("Elder", "Elders"), ("Sister", "Sisters"), ("Couple", "Couples")]
ARRIVAL_FILTERS = [("", "Any"), (ARRIVED, "Arrived"), (EXPECTED, "Not yet arrived"),
                   ("__blank__", "Not recorded")]
LEAVING_FILTERS = [("", "Any time"), ("7", "Within 7 days"), ("14", "Within 14 days"),
                   ("30", "Within 30 days"), ("left", "Already left")]
SORTS = [("name", "Name A–Z"), ("-name", "Name Z–A"), ("leaving", "Leaving soonest"),
         ("arrival", "Arrived most recently"), ("id", "Missionary ID")]


def _received_map(people):
    """Units each person has received in current (not undone) issues."""
    rows = (Issue.objects.filter(missionary__in=people, status=Issue.ACTIVE)
            .values("missionary_id").annotate(units=Sum("lines__quantity")))
    return {row["missionary_id"]: row["units"] or 0 for row in rows}


@staff_required
def missionary_list(request):
    q = (request.GET.get("q") or "").strip()[:160]
    show_archived = request.GET.get("archived") == "1"
    qs = Missionary.objects.all()
    if not show_archived:
        qs = qs.filter(is_archived=False)
    title = request.GET.get("title", "")
    if title == "__blank__":
        qs = qs.filter(title_prefix="")
    elif title:
        qs = qs.filter(title_prefix__iexact=title)
    arrival = request.GET.get("arrival", "")
    if arrival == "__blank__":
        qs = qs.filter(arrival_status="")
    elif arrival:
        qs = qs.filter(arrival_status=arrival)
    leaving = request.GET.get("leaving", "")
    today = timezone.localdate()
    if leaving == "left":
        qs = qs.filter(departure_date__lt=today)
    elif leaving.isdigit():
        qs = qs.filter(departure_date__gte=today,
                       departure_date__lte=today + timedelta(days=int(leaving)))
    sort = request.GET.get("sort", "name")
    ordering = {"name": ("name",), "-name": ("-name",), "id": ("external_id",),
                "arrival": (F("arrival_date").desc(nulls_last=True), "name"),
                "leaving": (F("departure_date").asc(nulls_last=True), "name")}.get(sort, ("name",))
    qs = people(qs, q, ordering)
    total = qs.count()
    page, pagination = paginate(request, qs)
    page = list(page)
    received = _received_map(page)
    for person in page:
        person.received = received.get(person.pk, 0)
    filters_on = any([q, title, arrival, leaving, show_archived])
    return render(
        request,
        "roster/list.html",
        {"missionaries": page, "q": q, "show_archived": show_archived,
         "total": total, "shown": len(page), "title": title, "arrival": arrival,
         "leaving": leaving, "sort": sort, "filters_on": filters_on,
         "title_filters": TITLE_FILTERS, "arrival_filters": ARRIVAL_FILTERS,
         "leaving_filters": LEAVING_FILTERS, "sorts": SORTS,
         "roster_total": Missionary.objects.filter(is_archived=False).count(),
         **pagination},
    )


def received_summary(person):
    """What a missionary has received so far, one row per item (undone excluded)."""
    rows = (IssueLine.objects.filter(issue__missionary=person, issue__status=Issue.ACTIVE)
            .values("item_id", "item_category", "item_name", "item_size")
            .annotate(units=Sum("quantity"), last=Max("issue__issued_at"))
            .order_by("item_category", "item_name"))
    rows = sorted(rows, key=lambda r: (r["item_category"].lower(), r["item_name"].lower(),
                                       natural_key(r["item_size"])))
    for row in rows:
        row["label"] = f"{row['item_name']} ({row['item_size']})" if row["item_size"] else row["item_name"]
    return rows


@staff_required_api
@require_GET
def missionary_summary(request, pk):
    """Recipient card on Issue supplies: who they are and what they already have."""
    person = get_object_or_404(Missionary, pk=pk)
    rows = received_summary(person)
    return JsonResponse({
        "id": str(person.pk), "display_name": person.display_name,
        "full_name": person.full_name, "external_id": person.external_id,
        "mission": person.mission, "language": person.language,
        "arrival": person.arrival_label,
        "departure": (f"Leaves {formats.date_format(person.departure_date, 'D j M')}"
                      if person.departure_date else ""),
        "received_units": sum(r["units"] for r in rows),
        "received": [{"label": r["label"], "units": r["units"]} for r in rows],
    })


@staff_required
def missionary_create(request):
    form = MissionaryForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            fields = form.cleaned_data.copy()
            fields.pop("expected_version", None)
            obj = roster_services.create_missionary(
                actor=request.user, **fields)
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Added {obj.display_name}.")
            return redirect("roster-detail", pk=obj.pk)
    return render(request, "roster/form.html", {"mode": "create", "form": form},
                  status=400 if form.is_bound else 200)


@staff_required
def missionary_detail(request, pk):
    person = get_object_or_404(Missionary, pk=pk)
    issues, pagination = paginate(request, (
        Issue.objects.filter(missionary=person)
        .select_related("issued_by")
        .prefetch_related("lines")
        .order_by("-issued_at", "pk")))
    received = received_summary(person)
    net_total = sum(row["units"] for row in received)
    return render(
        request,
        "roster/detail.html",
        {
            "person": person,
            "issues": issues,
            "received": received,
            "net_total": net_total,
            "return_to": list_return(request, '/roster/'),
            **pagination,
        },
    )


@staff_required
def missionary_edit(request, pk):
    person = get_object_or_404(Missionary, pk=pk)
    form = MissionaryForm(request.POST if request.method == "POST" else None, instance=person)
    if request.method == "POST" and form.is_valid():
        try:
            roster_services.update_missionary(
                actor=request.user, missionary_id=person.pk, **form.cleaned_data)
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Saved changes.")
            destination = f"/roster/{person.pk}/"
            if 'return_to' in request.GET:
                destination += '?' + urlencode({'return_to': list_return(request, '/roster/')})
            return redirect(destination)
    return render(request, "roster/form.html", {"mode": "edit", "person": person, "form": form, "return_to": list_return(request, '/roster/')},
                  status=400 if form.is_bound else 200)


@staff_required
@require_http_methods(["POST"])
def missionary_archive(request, pk):
    person = get_object_or_404(Missionary, pk=pk)
    try:
        roster_services.archive_missionary(actor=request.user, missionary_id=person.pk)
    except DomainError as exc:
        messages.error(request, f"Could not archive: {exc}")
    else:
        messages.success(
            request, f"Archived {person.display_name}. Their history is kept."
        )
    return redirect("roster-detail", pk=person.pk)


@staff_required
@require_http_methods(["POST"])
def missionary_reactivate(request, pk):
    person = get_object_or_404(Missionary, pk=pk)
    try:
        roster_services.reactivate_missionary(
            actor=request.user, missionary_id=person.pk
        )
    except DomainError as exc:
        messages.error(request, f"Could not reactivate: {exc}")
    else:
        messages.success(request, f"Reactivated {person.display_name}.")
    return redirect("roster-detail", pk=person.pk)
