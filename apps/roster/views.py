"""Roster views: list, detail/profile with quick issue, create, edit, archive."""

from __future__ import annotations

import uuid

from django.contrib import messages
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import staff_required
from apps.common.errors import DomainError
from apps.inventory.models import Item
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
        qs = qs.filter(Q(name__icontains=q) | Q(external_id__icontains=q))
    qs = qs.order_by("name")[:500]
    return render(
        request,
        "roster/list.html",
        {"missionaries": qs, "q": q, "show_archived": show_archived},
    )


@staff_required
def missionary_create(request):
    if request.method == "POST":
        try:
            obj = roster_services.create_missionary(
                actor=request.user,
                external_id=request.POST.get("external_id", ""),
                name=request.POST.get("name", ""),
                email=request.POST.get("email", ""),
                title_prefix=request.POST.get("title_prefix", ""),
                arrival_status=request.POST.get("arrival_status", ""),
                stake=request.POST.get("stake", ""),
                mission=request.POST.get("mission", ""),
                language=request.POST.get("language", ""),
                personal_email=request.POST.get("personal_email", ""),
            )
        except DomainError as exc:
            messages.error(request, f"Could not add person: {exc}")
            return render(
                request,
                "roster/form.html",
                {
                    "mode": "create",
                    "external_id": request.POST.get("external_id", ""),
                    "name": request.POST.get("name", ""),
                    "email": request.POST.get("email", ""),
                },
            )
        messages.success(request, f"Added {obj.name}.")
        return redirect("roster-detail", pk=obj.pk)
    return render(request, "roster/form.html", {"mode": "create"})


@staff_required
def missionary_detail(request, pk):
    person = get_object_or_404(Missionary, pk=pk)
    issues = (
        Issue.objects.filter(missionary=person)
        .prefetch_related("lines")
        .order_by("-issued_at")[:50]
    )
    # Net given to this person (active issues only).
    net_total = (
        Issue.objects.filter(missionary=person, status=Issue.ACTIVE)
        .aggregate(s=Sum("lines__quantity"))["s"]
        or 0
    )
    active_items = Item.objects.filter(is_archived=False).order_by(
        "category", "name", "size"
    )[:1000]
    draft_key = str(uuid.uuid4().hex)
    return render(
        request,
        "roster/detail.html",
        {
            "person": person,
            "issues": issues,
            "net_total": net_total,
            "active_items": active_items,
            "draft_key": draft_key,
        },
    )


@staff_required
def missionary_edit(request, pk):
    person = get_object_or_404(Missionary, pk=pk)
    if request.method == "POST":
        try:
            expected = int(request.POST.get("expected_version", person.version))
        except (TypeError, ValueError):
            expected = person.version
        try:
            roster_services.update_missionary(
                actor=request.user,
                missionary_id=person.pk,
                expected_version=expected,
                external_id=request.POST.get("external_id"),
                name=request.POST.get("name"),
                email=request.POST.get("email"),
                title_prefix=request.POST.get("title_prefix"),
                arrival_status=request.POST.get("arrival_status"),
                stake=request.POST.get("stake"),
                mission=request.POST.get("mission"),
                language=request.POST.get("language"),
                personal_email=request.POST.get("personal_email"),
            )
        except DomainError as exc:
            messages.error(request, f"Could not save changes: {exc}")
            person.refresh_from_db()
            return render(
                request,
                "roster/form.html",
                {
                    "mode": "edit",
                    "person": person,
                    "external_id": request.POST.get("external_id", ""),
                    "name": request.POST.get("name", ""),
                    "email": request.POST.get("email", ""),
                },
            )
        messages.success(request, "Saved changes.")
        return redirect("roster-detail", pk=person.pk)
    return render(request, "roster/form.html", {"mode": "edit", "person": person})


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
