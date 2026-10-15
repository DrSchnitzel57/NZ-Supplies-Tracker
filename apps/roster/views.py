"""Roster views: list, detail/profile with quick issue, create, edit, archive."""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import staff_required
from apps.common.errors import DomainError
from apps.common.pagination import paginate
from apps.common.search import people
from apps.common.navigation import list_return
from urllib.parse import urlencode
from apps.roster.forms import MissionaryForm
from apps.issuing.models import Issue
from apps.roster import services as roster_services
from apps.roster.models import Missionary


@staff_required
def missionary_list(request):
    q = (request.GET.get("q") or "").strip()[:160]
    show_archived = request.GET.get("archived") == "1"
    qs = Missionary.objects.all()
    if not show_archived:
        qs = qs.filter(is_archived=False)
    title = request.GET.get("title", "")
    arrival = request.GET.get("arrival", "")
    for field, value in (("title_prefix", title), ("arrival_status", arrival)):
        if value:
            qs = qs.filter(**{field: "" if value == "__blank__" else value})
    sort = request.GET.get("sort", "name")
    ordering = {"name": ("name",), "-name": ("-name",), "id": ("external_id",),
                "arrival": ("arrival_date", "name")}.get(sort, ("name",))
    qs = people(qs, q, ordering)
    total = qs.count()
    qs, pagination = paginate(request, qs)
    return render(
        request,
        "roster/list.html",
        {"missionaries": qs, "q": q, "show_archived": show_archived,
           "total": total, "shown": len(qs), "title": title, "arrival": arrival, "sort": sort,
           "titles": Missionary.objects.order_by("title_prefix").values_list("title_prefix", flat=True).distinct(),
           "arrivals": Missionary.objects.order_by("arrival_status").values_list("arrival_status", flat=True).distinct(),
           **pagination},
    )


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
            messages.success(request, f"Added {obj.name}.")
            return redirect("roster-detail", pk=obj.pk)
    return render(request, "roster/form.html", {"mode": "create", "form": form},
                  status=400 if form.is_bound else 200)


@staff_required
def missionary_detail(request, pk):
    person = get_object_or_404(Missionary, pk=pk)
    issues, pagination = paginate(request, (
        Issue.objects.filter(missionary=person)
        .prefetch_related("lines")
        .order_by("-issued_at", "pk")))
    # Net given to this person (active issues only).
    net_total = (
        Issue.objects.filter(missionary=person, status=Issue.ACTIVE)
        .aggregate(s=Sum("lines__quantity"))["s"]
        or 0
    )
    return render(
        request,
        "roster/detail.html",
        {
            "person": person,
            "issues": issues,
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
            request, f"Archived {person.name}. Past history is kept."
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
        messages.success(request, f"Reactivated {person.name}.")
    return redirect("roster-detail", pk=person.pk)
