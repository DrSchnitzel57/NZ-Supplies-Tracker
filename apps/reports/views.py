"""Staff report screens plus CSV/PDF exports. Bundle export stays admin-only."""

from __future__ import annotations

from django.shortcuts import render
from django.http import HttpResponseBadRequest
from urllib.parse import urlencode

from apps.accounts.decorators import admin_required, staff_required
from apps.accounts.reauth import reauth_required
from apps.reports import services as rep
from apps.transfers import services as transfer_services
from apps.reports.filters import REPORT_CHOICES, ReportFilters
from apps.common.pagination import paginate


@staff_required
def report_home(request):
    family = request.GET.get('family', 'stock')
    if family not in rep.REPORT_FAMILIES:
        return HttpResponseBadRequest('Choose a supported report.')
    form = ReportFilters(request.GET, family=family)
    valid = form.is_valid()
    title, fields, rows = rep.report_table(family, filters=form.cleaned_data) if valid else (dict(REPORT_CHOICES)[family], [], [])
    page, pagination = paginate(request, rows)
    query = urlencode({key: '1' if value is True else getattr(value, 'pk', value)
                       for key, value in form.cleaned_data.items() if value}) if valid else ''
    return render(request, 'reports/home.html', {
        'family': family, 'families': REPORT_CHOICES, 'form': form, 'report_title': title,
        'headings': ['Given out (net)' if field == 'given_out_net' else field.replace('_', ' ').capitalize() for field in fields],
        'report_rows': [[row.get(field, '') for field in fields] for row in page],
        'filters_query': query, 'filters_valid': valid, **pagination}, status=200 if valid else 400)


@staff_required
def export_roster_csv(request):
    form = ReportFilters(request.GET, family='roster')
    if not form.is_valid():
        return HttpResponseBadRequest(form.errors.as_text())
    return rep.roster_csv(filters=form.cleaned_data)


@staff_required
def export_inventory_csv(request):
    form = ReportFilters(request.GET, family='inventory')
    if not form.is_valid():
        return HttpResponseBadRequest(form.errors.as_text())
    return rep.inventory_csv(filters=form.cleaned_data)


@staff_required
def export_report(request, family: str):
    fmt = (request.GET.get("format") or "csv").strip().lower()
    if family not in rep.REPORT_FAMILIES:
        from django.http import HttpResponseNotFound
        return HttpResponseNotFound("unknown report")
    form = ReportFilters(request.GET, family=family)
    if not form.is_valid():
        return HttpResponseBadRequest(form.errors.as_text())
    if fmt == "pdf":
        return rep.report_pdf(family, filters=form.cleaned_data)
    return rep.report_csv(family, filters=form.cleaned_data)


@admin_required
@reauth_required
def export_bundle(request):
    from django.http import HttpResponse
    data = transfer_services.export_bundle(actor=request.user)
    response = HttpResponse(data, content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="supplies-bundle.zip"'
    return response
