"""Roster views: list, detail/profile with quick issue, create, edit, archive."""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import staff_required
from apps.common.errors import DomainError
from apps.common.pagination import paginate
from apps.roster.forms import MissionaryForm
from apps.issuing.models import Issue
from apps.roster import services as roster_services
from apps.roster.models import Missionary


@staff_required
def missionary_list(request):
    q = (request.GET.get("q") or "").strip()
    show_archived = request.GET.get("archived") == "1"
    qs = Missionary.objects.all()
    if not show_archived:
        qs = qs.filter(is_archived=False)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(external_id__icontains=q)
                       | Q(email__icontains=q))
    total = qs.count()
    qs, pagination = paginate(request, qs.order_by("name", "pk"))
    return render(
        request,
        "roster/list.html",
        {"missionaries": qs, "q": q, "show_archived": show_archived,
          "total": total, "shown": len(qs), **pagination},
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
            return redirect("roster-detail", pk=person.pk)
    return render(request, "roster/form.html", {"mode": "edit", "person": person, "form": form},
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
