"""Staff report screens plus CSV/PDF exports. Bundle export stays admin-only."""

from __future__ import annotations

from django.shortcuts import render

from apps.accounts.decorators import admin_required, staff_required
from apps.accounts.reauth import reauth_required
from apps.reports import services as rep
from apps.transfers import services as transfer_services


@staff_required
def report_home(request):
    include_archived = request.GET.get("archived") == "1"
    ctx = rep.report_context(include_archived=include_archived)
    return render(request, "reports/home.html",
                  {**ctx, "include_archived": include_archived})


@staff_required
def export_roster_csv(request):
    include_archived = request.GET.get("archived", "1") != "0"
    return rep.roster_csv(include_archived=include_archived)


@staff_required
def export_inventory_csv(request):
    include_archived = request.GET.get("archived", "1") != "0"
    return rep.inventory_csv(include_archived=include_archived)


@staff_required
def export_report(request, family: str):
    include_archived = request.GET.get("archived") == "1"
    fmt = (request.GET.get("format") or "csv").strip().lower()
    if family not in rep.REPORT_FAMILIES:
        from django.http import HttpResponseNotFound
        return HttpResponseNotFound("unknown report")
    if fmt == "pdf":
        return rep.report_pdf(family, include_archived=include_archived)
    return rep.report_csv(family, include_archived=include_archived)


@admin_required
@reauth_required
def export_bundle(request):
    from django.http import HttpResponse
    data = transfer_services.export_bundle(actor=request.user)
    response = HttpResponse(data, content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="supplies-bundle.zip"'
    return response
