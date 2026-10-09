"""Inventory views: list with filters, detail, edit, Add stock, Correct stock count."""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import staff_required
from apps.common.errors import DomainError
from apps.inventory import services as inv
from apps.inventory.models import Item, StockMovement


def _summaries(items):
    """Attach honest display figures: Available, net given out, corrections."""
    ids = [i.pk for i in items]
    net_map = {
        row["item_id"]: row["s"]
        for row in (
            __import__("apps.issuing.models", fromlist=["IssueLine"])
            .IssueLine.objects.filter(item_id__in=ids, issue__status="ACTIVE")
            .values("item_id")
            .annotate(s=Sum("quantity"))
        )
    }
    adj_map = {
        row["item_id"]: row["s"]
        for row in (
            StockMovement.objects.filter(
                item_id__in=ids, kind=StockMovement.COUNT_ADJUST
            )
            .values("item_id")
            .annotate(s=Sum("delta"))
        )
    }
    for item in items:
        item.net_given = net_map.get(item.pk, 0) or 0
        item.corrections_total = adj_map.get(item.pk, 0) or 0
    return items


@staff_required
def item_list(request):
    q = (request.GET.get("q") or "").strip()
    category = (request.GET.get("category") or "").strip()
    size = (request.GET.get("size") or "").strip()
    low_only = request.GET.get("low") == "1"
    show_archived = request.GET.get("archived") == "1"
    qs = Item.objects.all()
    if not show_archived:
        qs = qs.filter(is_archived=False)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(category__icontains=q))
    if category:
        qs = qs.filter(category__iexact=category)
    if size:
        qs = qs.filter(size__iexact=size)
    if low_only:
        qs = qs.filter(on_hand__lte=Coalesce("low_threshold", 0))
    qs = list(qs.order_by("category", "name", "size")[:1000])
    _summaries(qs)
    categories = list(
        Item.objects.filter(is_archived=False)
        .order_by("category")
        .values_list("category", flat=True)
        .distinct()[:200]
    )
    return render(
        request,
        "inventory/list.html",
        {
            "items": qs,
            "q": q,
            "category": category,
            "size": size,
            "low_only": low_only,
            "show_archived": show_archived,
            "categories": categories,
        },
    )


@staff_required
def item_create(request):
    if request.method == "POST":
        import uuid as _uuid

        try:
            opening = int(request.POST.get("opening_qty") or 0)
        except (TypeError, ValueError):
            opening = 0
        try:
            obj = inv.create_item(
                actor=request.user,
                category=request.POST.get("category", ""),
                name=request.POST.get("name", ""),
                size=request.POST.get("size", ""),
                opening_qty=opening,
                reason=(request.POST.get("reason") or "opening balance").strip()
                or "opening balance",
                idempotency_key=f"ui-create-{_uuid.uuid4().hex}",
            )
        except DomainError as exc:
            messages.error(request, f"Could not add item: {exc}")
            return render(
                request,
                "inventory/form.html",
                {
                    "mode": "create",
                    "category": request.POST.get("category", ""),
                    "name": request.POST.get("name", ""),
                    "size": request.POST.get("size", ""),
                },
            )
        # Optional low-stock threshold from the same form.
        try:
            threshold = int(request.POST.get("low_threshold") or 0)
        except (TypeError, ValueError):
            threshold = 0
        if threshold != obj.low_threshold:
            try:
                inv.update_item(
                    actor=request.user,
                    item_id=obj.pk,
                    expected_version=obj.version,
                    low_threshold=max(0, threshold),
                )
            except DomainError:
                pass
        messages.success(request, f"Added {obj}.")
        return redirect("inventory-detail", pk=obj.pk)
    return render(request, "inventory/form.html", {"mode": "create"})


@staff_required
def item_detail(request, pk):
    item = get_object_or_404(Item, pk=pk)
    _summaries([item])
    movements = list(
        StockMovement.objects.filter(item=item).order_by("-created_at")[:100]
    )
    plain = {
        StockMovement.OPENING: "Opening amount",
        StockMovement.RECEIPT: "Added stock",
        StockMovement.COUNT_ADJUST: "Count correction",
        StockMovement.ISSUE: "Issued goods",
        StockMovement.REVERSAL: "Returned (issue undone)",
    }
    for m in movements:
        m.plain_kind = plain.get(m.kind, m.kind)
    ledger_total = inv.ledger_balance(item.pk)
    return render(
        request,
        "inventory/detail.html",
        {
            "item": item,
            "movements": movements,
            "ledger_total": ledger_total,
        },
    )


@staff_required
def item_edit(request, pk):
    item = get_object_or_404(Item, pk=pk)
    if request.method == "POST":
        try:
            expected = int(request.POST.get("expected_version", item.version))
        except (TypeError, ValueError):
            expected = item.version
        try:
            threshold = request.POST.get("low_threshold")
            threshold = int(threshold) if threshold not in (None, "") else None
        except (TypeError, ValueError):
            messages.error(request, "Low-stock alert level must be a whole number.")
            return render(request, "inventory/form.html", {"mode": "edit", "item": item})
        try:
            inv.update_item(
                actor=request.user,
                item_id=item.pk,
                expected_version=expected,
                category=request.POST.get("category"),
                name=request.POST.get("name"),
                size=request.POST.get("size"),
                unit=request.POST.get("unit"),
                low_threshold=threshold,
            )
        except DomainError as exc:
            messages.error(request, f"Could not save changes: {exc}")
            item.refresh_from_db()
            return render(
                request, "inventory/form.html", {"mode": "edit", "item": item}
            )
        messages.success(request, "Saved changes.")
        return redirect("inventory-detail", pk=item.pk)
    return render(request, "inventory/form.html", {"mode": "edit", "item": item})


@staff_required
@require_http_methods(["POST"])
def item_receive(request, pk):
    """Add stock: positive quantity plus a reason."""
    item = get_object_or_404(Item, pk=pk)
    import uuid as _uuid

    key = (request.POST.get("idempotency_key") or "").strip() or f"ui-rx-{_uuid.uuid4().hex}"
    try:
        qty = int(request.POST.get("quantity") or 0)
    except (TypeError, ValueError):
        messages.error(request, "Add-stock quantity must be a whole number.")
        return redirect("inventory-detail", pk=item.pk)
    try:
        inv.receive_stock(
            actor=request.user,
            item_id=item.pk,
            quantity=qty,
            reason=request.POST.get("reason", ""),
            idempotency_key=key,
        )
    except DomainError as exc:
        messages.error(request, f"Could not add stock: {exc}")
        return redirect("inventory-detail", pk=item.pk)
    messages.success(request, f"Added {qty} to {item}.")
    return redirect("inventory-detail", pk=item.pk)


@staff_required
@require_http_methods(["POST"])
def item_correct(request, pk):
    """Correct stock count: set Available to the counted value with a reason."""
    item = get_object_or_404(Item, pk=pk)
    import uuid as _uuid

    key = (request.POST.get("idempotency_key") or "").strip() or f"ui-ct-{_uuid.uuid4().hex}"
    try:
        expected = int(request.POST.get("expected_version", item.version))
    except (TypeError, ValueError):
        expected = item.version
    try:
        counted = int(request.POST.get("counted_qty", ""))
    except (TypeError, ValueError):
        messages.error(request, "Counted quantity must be a whole number.")
        return redirect("inventory-detail", pk=item.pk)
    try:
        inv.adjust_count(
            actor=request.user,
            item_id=item.pk,
            counted_qty=counted,
            reason=request.POST.get("reason", ""),
            expected_version=expected,
            idempotency_key=key,
        )
    except DomainError as exc:
        messages.error(request, f"Could not correct the count: {exc}")
        return redirect("inventory-detail", pk=item.pk)
    messages.success(request, f"Count corrected to {counted} for {item}.")
    return redirect("inventory-detail", pk=item.pk)


@staff_required
@require_http_methods(["POST"])
def item_archive(request, pk):
    item = get_object_or_404(Item, pk=pk)
    try:
        inv.archive_item(actor=request.user, item_id=item.pk)
    except DomainError as exc:
        messages.error(request, f"Could not archive: {exc}")
    else:
        messages.success(request, f"Archived {item}. Past history is kept.")
    return redirect("inventory-detail", pk=item.pk)


@staff_required
@require_http_methods(["POST"])
def item_reactivate(request, pk):
    item = get_object_or_404(Item, pk=pk)
    try:
        inv.reactivate_item(actor=request.user, item_id=item.pk)
    except DomainError as exc:
        messages.error(request, f"Could not reactivate: {exc}")
    else:
        messages.success(request, f"Reactivated {item}.")
    return redirect("inventory-detail", pk=item.pk)
