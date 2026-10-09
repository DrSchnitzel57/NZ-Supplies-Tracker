"""Report queries, CSV builders and bundled-PDF rendering."""

from __future__ import annotations

import csv
import io

from django.db.models import Sum
from django.db.models.functions import Coalesce

from apps.inventory.models import Item
from apps.issuing.models import Issue, IssueLine


def report_context(*, include_archived: bool = False):
    active_lines = IssueLine.objects.filter(issue__status=Issue.ACTIVE)
    if not include_archived:
        active_lines = active_lines.filter(item__is_archived=False)
    per_item = list(
        active_lines.values("item_id", "item_category", "item_name", "item_size")
        .annotate(total=Sum("quantity")).order_by("-total")[:500]
    )
    person_lines = IssueLine.objects.filter(issue__status=Issue.ACTIVE)
    if not include_archived:
        person_lines = person_lines.filter(issue__missionary__is_archived=False)
    per_person = list(
        person_lines.values("issue__missionary_id", "issue__missionary_name",
                            "issue__missionary_external_id")
        .annotate(total=Sum("quantity")).order_by("-total")[:500]
    )
    items = Item.objects.all() if include_archived else Item.objects.filter(is_archived=False)
    low_items = list(items.filter(on_hand__lte=Coalesce("low_threshold", 0))
                     .order_by("category", "name")[:500])
    issues = Issue.objects.select_related("missionary", "issued_by", "reversed_by")
    if not include_archived:
        issues = issues.filter(missionary__is_archived=False)
    recent = list(issues.prefetch_related("lines").order_by("-issued_at")[:200])
    return {"per_item": per_item, "per_person": per_person,
            "low_items": low_items, "recent": recent}


def _csv_response(rows: list[dict], fields: list[str], filename: str):
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in fields})
    from django.http import HttpResponse
    response = HttpResponse(buf.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def roster_csv():
    from apps.roster.models import Missionary
    rows = [{"external_id": m.external_id, "name": m.name, "email": m.email,
             "archived": "yes" if m.is_archived else "no"}
            for m in Missionary.objects.order_by("name")]
    return _csv_response(rows, ["external_id", "name", "email", "archived"], "roster.csv")


def inventory_csv():
    from apps.inventory import services as inv
    rows = []
    for i in Item.objects.order_by("category", "name", "size"):
        rows.append({"category": i.category, "name": i.name, "size": i.size,
                     "available": i.on_hand, "given_out_net": inv.net_out(i.pk),
                     "alert_level": i.low_threshold,
                     "archived": "yes" if i.is_archived else "no"})
    return _csv_response(rows,
        ["category", "name", "size", "available", "given_out_net", "alert_level", "archived"],
        "inventory.csv")


def report_csv(family: str, include_archived: bool = False):
    ctx = report_context(include_archived=include_archived)
    if family == "per-item":
        rows = [{"item": f"{r['item_category']} / {r['item_name']}" + (f" ({r['item_size']})" if r["item_size"] else ""),
                 "given_out_net": r["total"]} for r in ctx["per_item"]]
        return _csv_response(rows, ["item", "given_out_net"], "report-per-item.csv")
    if family == "per-person":
        rows = [{"person": f"{r['issue__missionary_name']} ({r['issue__missionary_external_id']})",
                 "given_out_net": r["total"]} for r in ctx["per_person"]]
        return _csv_response(rows, ["person", "given_out_net"], "report-per-person.csv")
    if family == "low-stock":
        rows = [{"item": str(i), "available": i.on_hand, "alert_level": i.low_threshold}
                for i in ctx["low_items"]]
        return _csv_response(rows, ["item", "available", "alert_level"], "report-low-stock.csv")
    if family == "history":
        rows = [{"when": i.issued_at.isoformat() if i.issued_at else "",
                 "person": i.missionary_name,
                 "status": "Undone" if i.status == Issue.REVERSED else "Current",
                 "given_by": i.issued_by_username,
                 "undone_by": getattr(i.reversed_by, "username", "") or "",
                 "reason": i.reversal_reason or ""} for i in ctx["recent"]]
        return _csv_response(rows, ["when", "person", "status", "given_by", "undone_by", "reason"],
                             "report-history.csv")
    raise ValueError("unknown report family")


def report_pdf(family: str, include_archived: bool = False):
    """Bundled PDF rendering via reportlab (no CDN)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet

    ctx = report_context(include_archived=include_archived)
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
            for r in ctx["per_item"]]
    elif family == "per-person":
        data = [["Person", "Given out (net)"]] + [
            [f"{r['issue__missionary_name']} ({r['issue__missionary_external_id']})", r["total"]]
            for r in ctx["per_person"]]
    elif family == "low-stock":
        data = [["Item", "Available", "Alert level"]] + [
            [str(i), i.on_hand, i.low_threshold] for i in ctx["low_items"]]
    else:
        data = [["When", "Person", "Status", "Given by / undone by"]] + [
            [i.issued_at.strftime("%Y-%m-%d %H:%M") if i.issued_at else "", i.missionary_name,
             "Undone" if i.status == Issue.REVERSED else "Current",
             i.issued_by_username + (f" / {(i.reversed_by.username if i.reversed_by else '')} ({i.reversal_reason})" if i.status == Issue.REVERSED else "")]
            for i in ctx["recent"]]
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
