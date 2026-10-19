"""Staff report screens plus CSV/PDF exports. Bundle export stays admin-only."""

from __future__ import annotations

from django.shortcuts import render
from django.http import HttpResponseBadRequest
from urllib.parse import urlencode
from urllib.parse import quote

from apps.accounts.decorators import admin_required, staff_required
from apps.accounts.reauth import reauth_required
from apps.reports import services as rep
from apps.transfers import services as transfer_services
from apps.reports.filters import REPORT_CHOICES, REPORT_DESCRIPTIONS, ReportFilters
from apps.common.pagination import paginate

SCREEN_HIDDEN = {'id', 'intake_metadata', 'source_remaining', 'source_corrected'}
SCREEN_HEADINGS = {'given_out_net': 'Given out (net)', 'external_id': 'Missionary ID', 'title_prefix': 'Title',
                   'arrival_status': 'Arrived', 'alert_level': 'Alert level', 'personal_email': 'Personal email',
                   'departure_date': 'Leaves the MTC', 'arrival_date': 'Arrived on', 'release_date': 'Release date'}


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
    summary = []
    if valid and family in ('category', 'intake'):
        label, number = ('category', 'given_out_net') if family == 'category' else ('arrival_status', 'people')
        maximum = max((row[number] for row in rows), default=1) or 1
        summary = [{'label': row[label], 'value': row[number], 'width': round(row[number] / maximum * 100)} for row in rows]
    # Internal record IDs and raw import extras are for the CSV, not the screen.
    shown = [field for field in fields if field not in SCREEN_HIDDEN]
    low_text = ''
    if valid and family == 'low-stock':
        sections = []
        for status in ('Running low', 'Ran out', 'Never stocked'):
            group = [row for row in rows if row['status'] == status]
            if group:
                # Short lines for an email: how many are left only where some are.
                sections.append(f"{status} ({len(group)}):\n" + '\n'.join(
                    f"• {row['item']}: {row['available']} left" if status == 'Running low' else f"• {row['item']}"
                    for row in group))
        low_text = 'Supplies to reorder — MTC\n\n' + '\n\n'.join(sections)
    return render(request, 'reports/home.html', {
        'family': family, 'families': REPORT_CHOICES, 'form': form, 'report_title': title,
        'report_description': REPORT_DESCRIPTIONS.get(family, ''),
        'headings': [SCREEN_HEADINGS.get(field, field.replace('_', ' ').capitalize()) for field in shown],
        'report_rows': [[row.get(field, '') for field in shown] for row in page],
        'hidden_columns': len(fields) - len(shown),
        'filters_query': query, 'filters_valid': valid, 'summary': summary, 'low_text': low_text,
        'mail_url': 'mailto:?subject=' + quote('MTC supplies to reorder') + '&body=' + quote(low_text),
        **pagination}, status=200 if valid else 400)


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
