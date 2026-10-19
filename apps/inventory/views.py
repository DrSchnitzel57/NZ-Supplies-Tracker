"""Inventory views: list with filters, detail, edit, Add stock, Correct stock count."""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Exists, F, OuterRef, Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.views.decorators.clickjacking import xframe_options_sameorigin

from apps.accounts.decorators import staff_required
from apps.common.errors import DomainError
from apps.common.forms import AddStockForm, CorrectCountForm
from apps.common.pagination import paginate
from apps.common.sorting import item_sort_key, natural_key
from apps.common.search import items as search_items
from apps.common.navigation import list_return
from urllib.parse import urlencode
from apps.inventory.forms import ItemForm, NewItemForm
from apps.inventory.forms import BatchItemFormSet, category_options
from apps.inventory import services as inv
from apps.inventory.models import Item, StockMovement


def _summaries(items):
    """Attach honest display figures: Available, net given out, corrections."""
    from apps.issuing.models import IssueLine

    ids = [i.pk for i in items]
    net_map = {
        row["item_id"]: row["s"]
        for row in (
            IssueLine.objects.filter(item_id__in=ids, issue__status="ACTIVE")
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


STOCK_FILTERS = [("", "All items"), ("attention", "Needs reordering"), ("low", "Running low"),
                 ("out", "Out of stock"), ("ok", "In stock")]
SORTS = [("category", "Category"), ("name", "Name A–Z"), ("-name", "Name Z–A"),
         ("stock", "Fewest available first")]


def _stock_filter(qs, value):
    stocked = StockMovement.objects.filter(item=OuterRef("pk"), delta__gt=0)
    if value == "attention":
        return qs.annotate(ever_stocked=Exists(stocked)).filter(
            Q(on_hand__gt=0, on_hand__lte=F("low_threshold")) | Q(on_hand=0, ever_stocked=True))
    if value == "low":
        return qs.filter(on_hand__gt=0, on_hand__lte=F("low_threshold"))
    if value == "out":
        return qs.filter(on_hand=0)
    if value == "ok":
        return qs.filter(on_hand__gt=F("low_threshold"))
    if value == "below":  # legacy ?low=1: everything at or below its alert level
        return qs.filter(on_hand__lte=Coalesce("low_threshold", 0))
    return qs


def _category_sections(items, *, fold_sizes: bool) -> list[dict]:
    """[{name, lines, attention, groups:[{key, name, variants, folded, ...}]}].

    Items arrive sorted by category, name and natural size order. A group is
    one item name; it folds into a single row when it has several sizes.
    """
    sections: list[dict] = []
    for item in items:
        if not sections or sections[-1]["name"] != item.category:
            sections.append({"name": item.category, "lines": 0, "attention": 0, "groups": []})
        section = sections[-1]
        section["lines"] += 1
        if item.on_hand <= item.low_threshold:
            section["attention"] += 1
        groups = section["groups"]
        if fold_sizes and groups and groups[-1]["name"].lower() == item.name.lower():
            groups[-1]["variants"].append(item)
        else:
            groups.append({"key": str(item.pk), "name": item.name, "variants": [item]})
    for section in sections:
        for group in section["groups"]:
            variants = group["variants"]
            group["folded"] = len(variants) > 1
            group["available"] = sum(v.on_hand for v in variants)
            group["given"] = sum(v.net_given for v in variants)
            group["out"] = sum(1 for v in variants if v.on_hand <= 0)
            group["low"] = sum(1 for v in variants if 0 < v.on_hand <= v.low_threshold)
    return sections


@staff_required
def item_list(request):
    q = (request.GET.get("q") or "").strip()[:160]
    category = (request.GET.get("category") or "").strip()
    size = (request.GET.get("size") or "").strip()
    stock = (request.GET.get("stock") or "").strip()
    if not stock and request.GET.get("low") == "1":
        stock = "below"
    show_archived = request.GET.get("archived") == "1"
    qs = Item.objects.all()
    if not show_archived:
        qs = qs.filter(is_archived=False)
    sort = request.GET.get("sort", "category")
    if sort not in dict(SORTS):
        sort = "category"
    qs = search_items(qs, q, ("category", "name", "size"))
    if category:
        qs = qs.filter(category__iexact=category)
    if size == "__blank__":
        qs = qs.filter(size="")
    elif size:
        qs = qs.filter(size__icontains=size)
    scope = qs
    qs = _stock_filter(qs, stock)
    rows = list(qs)
    if sort == "name":
        rows.sort(key=lambda i: (i.search_rank, i.name.lower(), natural_key(i.size)))
    elif sort == "-name":
        # Stable sorts, applied least-significant first: size, name (Z–A), rank.
        rows.sort(key=lambda i: natural_key(i.size))
        rows.sort(key=lambda i: i.name.lower(), reverse=True)
        rows.sort(key=lambda i: i.search_rank)
    elif sort == "stock":
        rows.sort(key=lambda i: (i.search_rank, i.on_hand, item_sort_key(i)))
    else:
        rows.sort(key=lambda i: (i.search_rank, item_sort_key(i)))
    total = len(rows)
    sections = None
    if sort == "category":
        # Category sections (collapsible) with every line on one page; with no
        # search or stock filter, sizes of the same item fold under one row.
        rows.sort(key=item_sort_key)
        items = _summaries(rows)
        sections = _category_sections(items, fold_sizes=not (q or stock or size))
        pagination = {}
    else:
        page, pagination = paginate(request, rows)
        items = _summaries(list(page))
    for item in items:
        item.level = 0 if item.on_hand <= 0 else min(100, round(
            item.on_hand / max(item.low_threshold * 3, item.on_hand, 1) * 100))
    stock_counts = {
        "": scope.count(),
        "attention": _stock_filter(scope, "attention").count(),
        "low": _stock_filter(scope, "low").count(),
        "out": _stock_filter(scope, "out").count(),
        "ok": _stock_filter(scope, "ok").count(),
    }
    choices = Item.objects.all() if show_archived else Item.objects.filter(is_archived=False)
    categories = list(choices.order_by("category").values_list("category", flat=True).distinct())
    query = request.GET.copy()
    query.pop("page", None)
    query.pop("low", None)
    stock_links = []
    for value, label in STOCK_FILTERS:
        link = query.copy()
        if value:
            link["stock"] = value
        else:
            link.pop("stock", None)
        stock_links.append({"value": value, "label": label, "count": stock_counts.get(value),
                            "url": "?" + link.urlencode(), "current": value == stock})
    return render(
        request,
        "inventory/list.html",
        {
            "items": items,
            "q": q,
            "category": category,
            "size": size,
            "sort": sort,
            "sorts": SORTS,
            "stock": stock,
            "stock_links": stock_links,
            "low_only": stock == "below",
            "show_archived": show_archived,
            "categories": categories,
            "sections": sections,
            "total": total,
            "shown": len(items),
            "item_total": Item.objects.filter(is_archived=False).count(),
            "filters_on": any([q, category, size, stock, show_archived]),
            **pagination,
        },
    )


@staff_required
def item_create(request):
    from apps.operations.models import SystemSetting

    threshold = SystemSetting.objects.filter(key="default_low_threshold").first()
    data = request.POST.copy() if request.method == "POST" else None
    if data is not None and not data.get("low_threshold"):
        data["low_threshold"] = threshold.value if threshold else "0"
    form = NewItemForm(data, initial={
        "idempotency_key": f"ui-create-{_new_uuid()}",
        "low_threshold": threshold.value if threshold else 0})
    if request.method == "POST" and form.is_valid():
        try:
            fields = form.cleaned_data.copy()
            fields.pop("expected_version", None)
            fields["unit"] = fields.get("unit") or "each"
            fields["idempotency_key"] = fields.get("idempotency_key") or f"ui-create-{_new_uuid()}"
            obj = inv.create_item(
                actor=request.user, **fields)
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Added {obj}.")
            return redirect("inventory-detail", pk=obj.pk)
    return render(request, "inventory/form.html",
                  {"mode": "create", "form": form, "category_options": category_options()},
                  status=400 if form.is_bound else 200)


def _new_uuid() -> str:
    import uuid as _uuid

    return _uuid.uuid4().hex


@staff_required
def item_batch(request):
    from apps.operations.models import SystemSetting
    threshold = SystemSetting.objects.filter(key="default_low_threshold").first()
    formset = BatchItemFormSet(request.POST if request.method == "POST" else None,
        form_kwargs={"initial": {"low_threshold": threshold.value if threshold else 0, "unit": "each"}})
    key = request.POST.get("idempotency_key", "") if request.method == "POST" else "ui-batch-" + _new_uuid()
    error, reviewed = "", False
    if request.method == "POST" and formset.is_valid():
        rows = [form.cleaned_data for form in formset if form.cleaned_data]
        from apps.inventory.models import item_key
        keys = [item_key(r["category"], r["name"], r["size"]) for r in rows]
        if len(set(keys)) != len(keys):
            error = "Two rows describe the same item. Change or remove the duplicate; nothing was saved."
        elif request.POST.get("confirm") == "1":
            try:
                created = inv.create_batch(actor=request.user, rows=rows, idempotency_key=key)
            except DomainError as exc:
                error = str(exc)
            else:
                messages.success(request, f"Added {len(created)} items. Opening stock recorded for every item.")
                return redirect("inventory-list")
        elif Item.objects.filter(key__in=keys).exists():
            error = "An item already exists, including archived inventory. Review the existing item instead."
        else:
            reviewed = True
    return render(request, "inventory/batch.html", {"formset": formset, "batch_key": key,
        "batch_error": error, "reviewed": reviewed, "category_options": category_options()},
        status=400 if request.method == "POST" and not reviewed else 200)


@staff_required
@xframe_options_sameorigin
def item_stock_form(request, pk, mode):
    item = get_object_or_404(Item, pk=pk)
    form = (AddStockForm(auto_id="receive_%s", initial={"idempotency_key": "ui-rx-" + _new_uuid()}) if mode == "stock"
            else CorrectCountForm(initial={"idempotency_key": "ui-ct-" + _new_uuid(),
                 "expected_version": item.version, "counted_qty": item.on_hand}, auto_id="correct_%s"))
    return _render_stock_form(request, item, form, mode)


def _render_stock_form(request, item, form, mode, status=200):
    return render(request, "inventory/stock_form.html", {"item": item, "form": form,
        "heading": "Add stock" if mode == "stock" else "Correct stock count",
        "return_to": list_return(request, '/inventory/'),
        "action": "add-stock" if mode == "stock" else "correct-count"}, status=status)


def _item_destination(request, item):
    destination = f"/inventory/{item.pk}/"
    if 'return_to' in request.GET:
        destination += '?' + urlencode({'return_to': list_return(request, '/inventory/')})
    return destination


@staff_required
def item_detail(request, pk):
    item = get_object_or_404(Item, pk=pk)
    return _render_detail(request, item)


def _render_detail(request, item):
    _summaries([item])
    page, pagination = paginate(request, StockMovement.objects.filter(item=item)
                                .select_related("issue", "issue__missionary", "created_by")
                                .order_by("-created_at", "pk"))
    movements = list(page)
    plain = {
        StockMovement.OPENING: "Opening amount",
        StockMovement.RECEIPT: "Added stock",
        StockMovement.COUNT_ADJUST: "Count correction",
        StockMovement.ISSUE: "Issued goods",
        StockMovement.REVERSAL: "Returned (issue undone)",
    }
    for m in movements:
        m.plain_kind = plain.get(m.kind, m.kind)
        # Issue movements store a machine reason ("issue <id>"); show who it
        # went to instead, linked to the issue record.
        if m.issue_id and m.kind in (StockMovement.ISSUE, StockMovement.REVERSAL):
            who = m.issue.missionary.display_name if m.issue.missionary_id else m.issue.missionary_name
            m.plain_reason = (f"Given to {who}" if m.kind == StockMovement.ISSUE
                              else f"Undone issue to {who}" + (f" — {m.issue.reversal_reason}" if m.issue.reversal_reason else ""))
        else:
            m.plain_reason = m.reason
    item.alert_status = ("out" if item.on_hand <= 0 else "low" if item.on_hand <= item.low_threshold else "ok")
    ledger_total = inv.ledger_balance(item.pk)
    return render(
        request,
        "inventory/detail.html",
        {
            "item": item,
            "return_to": list_return(request, '/inventory/'),
            "movements": movements,
            "ledger_total": ledger_total,
            **pagination,
        },
    )


@staff_required
@xframe_options_sameorigin
def item_edit(request, pk):
    item = get_object_or_404(Item, pk=pk)
    form = ItemForm(request.POST if request.method == "POST" else None, instance=item)
    if request.method == "POST" and form.is_valid():
        try:
            inv.update_item(
                actor=request.user, item_id=item.pk, **form.cleaned_data)
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Saved changes.")
            return redirect(_item_destination(request, item))
    return render(request, "inventory/form.html", {"mode": "edit", "item": item, "form": form,
                  "category_options": category_options()},
                  status=400 if form.is_bound else 200)


@staff_required
@require_http_methods(["POST"])
def item_receive(request, pk):
    """Add stock: positive quantity plus a reason."""
    item = get_object_or_404(Item, pk=pk)
    data = request.POST.copy()
    data.setdefault("idempotency_key", f"ui-rx-{_new_uuid()}")
    form = AddStockForm(data, auto_id="receive_%s")
    if form.is_valid():
        try:
            inv.receive_stock(actor=request.user, item_id=item.pk, **form.cleaned_data)
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Added {form.cleaned_data['quantity']} to {item.name}{f' ({item.size})' if item.size else ''}.")
            return redirect(_item_destination(request, item))
    return _render_stock_form(request, item, form, "stock", status=400)


@staff_required
@require_http_methods(["POST"])
def item_correct(request, pk):
    """Correct stock count: set Available to the counted value with a reason."""
    item = get_object_or_404(Item, pk=pk)
    data = request.POST.copy()
    data.setdefault("idempotency_key", f"ui-ct-{_new_uuid()}")
    form = CorrectCountForm(data, auto_id="correct_%s")
    if form.is_valid():
        try:
            inv.adjust_count(actor=request.user, item_id=item.pk, **form.cleaned_data)
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Count corrected to {form.cleaned_data['counted_qty']} for {item}.")
            return redirect(_item_destination(request, item))
    return _render_stock_form(request, item, form, "count", status=400)


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
