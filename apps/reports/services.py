"""Report queries, CSV builders and bundled-PDF rendering."""

from __future__ import annotations

import csv
import io

from django.db.models import Sum
from django.db.models.functions import Coalesce

from apps.inventory.models import Item
from apps.issuing.models import Issue, IssueLine
from apps.transfers.bundle import safe_encode

#: On-screen tables stay readable with an explicit cap; exports never
#: truncate. Every capped screen states "showing first N of M".
SCREEN_LIMIT = 500


def report_context(*, include_archived: bool = False):
    active_lines = IssueLine.objects.filter(issue__status=Issue.ACTIVE)
    if not include_archived:
        active_lines = active_lines.filter(item__is_archived=False)
    per_item_qs = (
        active_lines.values("item_id", "item_category", "item_name", "item_size")
        .annotate(total=Sum("quantity")).order_by("-total")
    )
    per_item_total = per_item_qs.count()
    per_item = list(per_item_qs[:SCREEN_LIMIT])
    person_lines = IssueLine.objects.filter(issue__status=Issue.ACTIVE)
    if not include_archived:
        person_lines = person_lines.filter(issue__missionary__is_archived=False)
    per_person_qs = (
        person_lines.values("issue__missionary_id", "issue__missionary_name",
                            "issue__missionary_external_id")
        .annotate(total=Sum("quantity")).order_by("-total")
    )
    per_person_total = per_person_qs.count()
    per_person = list(per_person_qs[:SCREEN_LIMIT])
    items = Item.objects.all() if include_archived else Item.objects.filter(is_archived=False)
    low_qs = items.filter(on_hand__lte=Coalesce("low_threshold", 0)).order_by("category", "name")
    low_total = low_qs.count()
    low_items = list(low_qs[:SCREEN_LIMIT])
    stock_rows = list(items.order_by("category", "name", "size")[:SCREEN_LIMIT])
    stock_total = items.count()
    category_qs = (
        active_lines.values("item_category")
        .annotate(total=Sum("quantity")).order_by("-total")
    )
    category_total = category_qs.count()
    per_category = list(category_qs[:SCREEN_LIMIT])
    issues = Issue.objects.select_related("missionary", "issued_by", "reversed_by")
    if not include_archived:
        issues = issues.filter(missionary__is_archived=False)
    issues = issues.prefetch_related("lines").order_by("-issued_at")
    recent_total = issues.count()
    recent = list(issues[:200])
    intake = _intake_summary(include_archived=include_archived)
    return {"per_item": per_item, "per_item_total": per_item_total,
            "per_person": per_person, "per_person_total": per_person_total,
            "low_items": low_items, "low_total": low_total,
            "stock_rows": stock_rows, "stock_total": stock_total,
            "per_category": per_category, "category_total": category_total,
            "recent": recent, "recent_total": recent_total,
            "intake": intake}


def _intake_summary(*, include_archived: bool):
    """Arrival/release overview grouped by arrival status and arrival month."""
    from apps.roster.models import Missionary
    from django.db.models import Count

    qs = Missionary.objects.all()
    if not include_archived:
        qs = qs.filter(is_archived=False)
    by_status = list(qs.values("arrival_status").annotate(total=Count("id")).order_by("-total"))
    by_month = list(
        qs.exclude(arrival_date__isnull=True)
        .values("arrival_date__year", "arrival_date__month")
        .annotate(total=Count("id")).order_by("arrival_date__year", "arrival_date__month"))
    return {"by_status": by_status, "by_month": by_month, "total": qs.count()}


def _csv_response(rows: list[dict], fields: list[str], filename: str):
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: safe_encode(row.get(k, "")) for k in fields})
    from django.http import HttpResponse
    response = HttpResponse(buf.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def roster_csv(*, include_archived: bool = True):
    from apps.roster.models import Missionary
    qs = Missionary.objects.order_by("name")
    if not include_archived:
        qs = qs.filter(is_archived=False)
    rows = [{"external_id": m.external_id, "name": m.name, "email": m.email,
             "title_prefix": m.title_prefix, "arrival_status": m.arrival_status,
             "stake": m.stake, "mission": m.mission, "language": m.language,
             "arrival_date": m.arrival_date.isoformat() if m.arrival_date else "",
             "release_date": m.release_date.isoformat() if m.release_date else "",
             "archived": "yes" if m.is_archived else "no"}
            for m in qs.iterator()]
    return _csv_response(rows, ["external_id", "name", "email", "title_prefix",
                               "arrival_status", "stake", "mission", "language",
                               "arrival_date", "release_date", "archived"], "roster.csv")


def inventory_csv(*, include_archived: bool = True):
    qs = Item.objects.order_by("category", "name", "size")
    if not include_archived:
        qs = qs.filter(is_archived=False)
    items = list(qs.iterator())
    net_map = _net_out_map([i.pk for i in items])
    rows = []
    for i in items:
        rows.append({"category": i.category, "name": i.name, "size": i.size,
                     "available": i.on_hand, "given_out_net": net_map.get(i.pk, 0),
                     "alert_level": i.low_threshold,
                     "archived": "yes" if i.is_archived else "no"})
    return _csv_response(rows,
        ["category", "name", "size", "available", "given_out_net", "alert_level", "archived"],
        "inventory.csv")


def _net_out_map(item_ids=None) -> dict:
    """Net given-out per item in one query (active issue lines only)."""
    qs = IssueLine.objects.filter(issue__status=Issue.ACTIVE)
    if item_ids is not None:
        qs = qs.filter(item_id__in=list(item_ids))
    return {row["item_id"]: row["s"] or 0 for row in
            qs.values("item_id").annotate(s=Sum("quantity"))}


def _export_per_item(ctx):
    return [{"item": f"{r['item_category']} / {r['item_name']}" + (f" ({r['item_size']})" if r["item_size"] else ""),
             "given_out_net": r["total"]} for r in _full_per_item(ctx)]


def _full_per_item(ctx):
    # Exports iterate the full queryset, not the capped screen list.
    active_lines = IssueLine.objects.filter(issue__status=Issue.ACTIVE)
    include_archived = ctx.get("include_archived", False)
    if not include_archived:
        active_lines = active_lines.filter(item__is_archived=False)
    return list(active_lines.values("item_category", "item_name", "item_size")
                .annotate(total=Sum("quantity")).order_by("-total").iterator())


def _full_per_person(ctx):
    person_lines = IssueLine.objects.filter(issue__status=Issue.ACTIVE)
    if not ctx.get("include_archived", False):
        person_lines = person_lines.filter(issue__missionary__is_archived=False)
    return list(person_lines.values("issue__missionary_name", "issue__missionary_external_id")
                .annotate(total=Sum("quantity")).order_by("-total").iterator())


def _full_low(ctx):
    items = Item.objects.all() if ctx.get("include_archived", False) \
        else Item.objects.filter(is_archived=False)
    return list(items.filter(on_hand__lte=Coalesce("low_threshold", 0))
                .order_by("category", "name").iterator())


def _full_stock(ctx):
    items = Item.objects.all() if ctx.get("include_archived", False) \
        else Item.objects.filter(is_archived=False)
    return list(items.order_by("category", "name", "size").iterator())


def _full_history(ctx):
    issues = Issue.objects.select_related("missionary", "issued_by", "reversed_by")
    if not ctx.get("include_archived", False):
        issues = issues.filter(missionary__is_archived=False)
    return list(issues.prefetch_related("lines").order_by("-issued_at").iterator(chunk_size=1000))


def report_csv(family: str, include_archived: bool = False):
    flag = {"include_archived": include_archived}
    if family == "per-item":
        rows = [{"item": f"{r['item_category']} / {r['item_name']}" + (f" ({r['item_size']})" if r["item_size"] else ""),
                 "given_out_net": r["total"]} for r in _full_per_item(flag)]
        return _csv_response(rows, ["item", "given_out_net"], "report-per-item.csv")
    if family == "per-person":
        rows = [{"person": f"{r['issue__missionary_name']} ({r['issue__missionary_external_id']})",
                 "given_out_net": r["total"]} for r in _full_per_person(flag)]
        return _csv_response(rows, ["person", "given_out_net"], "report-per-person.csv")
    if family == "low-stock":
        rows = [{"item": str(i), "available": i.on_hand, "alert_level": i.low_threshold}
                for i in _full_low(flag)]
        return _csv_response(rows, ["item", "available", "alert_level"], "report-low-stock.csv")
    if family == "stock":
        stock = _full_stock(flag)
        net_map = _net_out_map([i.pk for i in stock])
        rows = [{"item": str(i), "available": i.on_hand,
                 "given_out_net": net_map.get(i.pk, 0),
                 "alert_level": i.low_threshold,
                 "archived": "yes" if i.is_archived else "no"} for i in stock]
        return _csv_response(rows, ["item", "available", "given_out_net",
                                   "alert_level", "archived"], "report-stock.csv")
    if family == "category":
        active_lines = IssueLine.objects.filter(issue__status=Issue.ACTIVE)
        if not include_archived:
            active_lines = active_lines.filter(item__is_archived=False)
        grouped = list(active_lines.values("item_category")
                       .annotate(total=Sum("quantity")).order_by("-total").iterator())
        rows = [{"category": r["item_category"] or "(uncategorized)",
                 "given_out_net": r["total"]} for r in grouped]
        return _csv_response(rows, ["category", "given_out_net"], "report-category.csv")
    if family == "intake":
        ctx = report_context(include_archived=include_archived)
        rows = [{"arrival_status": r["arrival_status"] or "(unspecified)",
                 "people": r["total"]} for r in ctx["intake"]["by_status"]]
        rows += [{"arrival_status": "arrival month "
                  f"{r['arrival_date__year']}-{r['arrival_date__month']:02d}",
                  "people": r["total"]} for r in ctx["intake"]["by_month"]]
        return _csv_response(rows, ["arrival_status", "people"], "report-intake.csv")
    if family == "history":
        rows = [{"when": i.issued_at.isoformat() if i.issued_at else "",
                 "person": f"{i.missionary_name} ({i.missionary_external_id})",
                 "status": "Undone" if i.status == Issue.REVERSED else "Current",
                 "given_by": i.issued_by_username,
                 "undone_by": (i.reversed_by_username
                               or getattr(i.reversed_by, "username", "") or ""),
                 "reason": i.reversal_reason or "",
                 "note": i.note or ""} for i in _full_history(flag)]
        return _csv_response(rows, ["when", "person", "status", "given_by",
                                   "undone_by", "reason", "note"],
                             "report-history.csv")
    raise ValueError("unknown report family")


REPORT_FAMILIES = ("per-item", "per-person", "low-stock", "stock",
                   "category", "intake", "history")


def report_pdf(family: str, include_archived: bool = False):
    """Bundled PDF rendering via reportlab (no CDN)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet

    flag = {"include_archived": include_archived}
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            title=f"Supplies report: {family}")
    styles = getSampleStyleSheet()
    story = [Paragraph(f"Supplies report — {family}", styles["Heading1"]),
             Paragraph("Given-out counts are net: undone issues are excluded but listed in history.", styles["Normal"]),
             Spacer(1, 12)]
    if family == "per-item":
        data = [["Item", "Given out (net)"]] + [
            [f"{r['item_category']} / {r['item_name']}" + (f" ({r['item_size']})" if r["item_size"] else ""), r["total"]]
            for r in _full_per_item(flag)]
    elif family == "per-person":
        data = [["Person", "Given out (net)"]] + [
            [f"{r['issue__missionary_name']} ({r['issue__missionary_external_id']})", r["total"]]
            for r in _full_per_person(flag)]
    elif family == "low-stock":
        data = [["Item", "Available", "Alert level"]] + [
            [str(i), i.on_hand, i.low_threshold] for i in _full_low(flag)]
    elif family == "stock":
        stock = _full_stock(flag)
        net_map = _net_out_map([i.pk for i in stock])
        data = [["Item", "Available", "Given out (net)", "Alert level"]] + [
            [str(i), i.on_hand, net_map.get(i.pk, 0), i.low_threshold]
            for i in stock]
    elif family == "category":
        active_lines = IssueLine.objects.filter(issue__status=Issue.ACTIVE)
        if not include_archived:
            active_lines = active_lines.filter(item__is_archived=False)
        grouped = list(active_lines.values("item_category")
                       .annotate(total=Sum("quantity")).order_by("-total").iterator())
        data = [["Category", "Given out (net)"]] + [
            [r["item_category"] or "(uncategorized)", r["total"]] for r in grouped]
    elif family == "intake":
        ctx = report_context(include_archived=include_archived)
        data = [["Arrival status", "People"]] + [
            [r["arrival_status"] or "(unspecified)", r["total"]]
            for r in ctx["intake"]["by_status"]]
    else:
        data = [["When", "Person", "Status", "Given by / undone by"]] + [
            [i.issued_at.strftime("%Y-%m-%d %H:%M") if i.issued_at else "", i.missionary_name,
             "Undone" if i.status == Issue.REVERSED else "Current",
             i.issued_by_username + (f" / {(i.reversed_by_username or (i.reversed_by.username if i.reversed_by else ''))} ({i.reversal_reason})" if i.status == Issue.REVERSED else "")]
            for i in _full_history(flag)]
    if len(data) == 1:
        data.append(["No rows yet.", "", "", ""][:len(data[0])])
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#123a5c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story.append(table)
    doc.build(story)
    from django.http import HttpResponse
    response = HttpResponse(buf.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="report-{family}.pdf"'
    return response
