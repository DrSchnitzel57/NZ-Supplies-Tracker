"""Shared report rows: stable identities, complete exports and local A4 PDF."""
from __future__ import annotations

import csv
import io
import json
from html import escape

from django.db.models import Count, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.utils import timezone

from apps.inventory.models import Item
from apps.issuing.models import Issue, IssueLine
from apps.reports.filters import REPORT_CHOICES, filter_issues, filter_items, filter_people
from apps.roster.models import Missionary
from apps.transfers.bundle import safe_encode

REPORT_FAMILIES = tuple(name for name, _ in REPORT_CHOICES)


def _net_out_map(item_ids=None):
    qs = IssueLine.objects.filter(issue__status=Issue.ACTIVE)
    if item_ids is not None:
        qs = qs.filter(item_id__in=item_ids)
    return {row['item_id']: row['total'] for row in qs.values('item_id').annotate(total=Sum('quantity'))}


def _summary_lines(filters):
    qs = IssueLine.objects.filter(issue__in=filter_issues(Issue.objects.all(), filters, net=True))
    if not filters.get('archived'):
        qs = qs.filter(item__is_archived=False)
    if filters.get('item'):
        qs = qs.filter(item=filters['item'])
    return qs


def _per_item(filters):
    return _summary_lines(filters).values('item_id', 'item__category', 'item__name', 'item__size').annotate(total=Sum('quantity')).order_by('-total', 'item_id')


def _per_person(filters):
    return _summary_lines(filters).values('issue__missionary_id', 'issue__missionary__name', 'issue__missionary__external_id').annotate(total=Sum('quantity')).order_by('-total', 'issue__missionary_id')


def report_context(*, include_archived=False):
    """Compatibility query API; the product screen selects one report instead."""
    filters = {'archived': include_archived}
    issues = filter_issues(Issue.objects.all(), filters).order_by('-issued_at').prefetch_related('lines')
    items = filter_items(Item.objects.all(), filters)
    people = filter_people(Missionary.objects.all(), filters)
    return {'per_item': list(_per_item(filters)), 'per_person': list(_per_person(filters)),
            'recent': list(issues), 'stock_rows': list(items),
            'low_items': list(items.filter(on_hand__lte=Coalesce('low_threshold', 0))),
            'per_category': list(_summary_lines(filters).values('item__category').annotate(total=Sum('quantity'))),
            'intake': {'total': people.count(), 'by_status': list(people.values('arrival_status').annotate(total=Count('id')))}}


def report_table(family, *, include_archived=False, filters=None):
    """Return the same ordered, untruncated rows for screen, CSV and PDF."""
    filters = {**(filters or {})}
    filters.setdefault('archived', include_archived)
    title = dict(REPORT_CHOICES)[family]
    if family == 'per-item':
        fields = ['item_id', 'item', 'given_out_net']
        rows = [{'item_id': str(r['item_id']),
                 'item': f"{r['item__category']} / {r['item__name']}" + (f" ({r['item__size']})" if r['item__size'] else ''),
                 'given_out_net': r['total']} for r in _per_item(filters)]
    elif family == 'per-person':
        fields = ['missionary_id', 'person', 'given_out_net']
        rows = [{'missionary_id': str(r['issue__missionary_id']),
                 'person': f"{r['issue__missionary__name']} ({r['issue__missionary__external_id']})",
                 'given_out_net': r['total']} for r in _per_person(filters)]
    elif family == 'category':
        fields = ['category', 'given_out_net']
        rows = [{'category': r['item__category'], 'given_out_net': r['total']} for r in
                _summary_lines(filters).values('item__category').annotate(total=Sum('quantity')).order_by('-total', 'item__category')]
    elif family in ('stock', 'low-stock', 'inventory'):
        items = filter_items(Item.objects.all(), filters)
        if family == 'low-stock':
            items = items.filter(on_hand__lte=Coalesce('low_threshold', 0))
        items = list(items.order_by('category', 'name', 'size', 'pk'))
        net = _net_out_map([item.pk for item in items])
        if family == 'inventory':
            fields = ['id', 'category', 'name', 'size', 'unit', 'available', 'given_out_net', 'alert_level', 'archived', 'source_remaining', 'source_corrected']
            rows = [{'id': str(item.pk), 'category': item.category, 'name': item.name, 'size': item.size,
                     'unit': item.unit, 'available': item.on_hand, 'given_out_net': net.get(item.pk, 0),
                     'alert_level': item.low_threshold, 'archived': 'yes' if item.is_archived else 'no',
                     'source_remaining': item.source_remaining, 'source_corrected': item.source_corrected} for item in items]
        else:
            fields = ['item', 'available', 'given_out_net', 'alert_level', 'archived']
            rows = [{'item': str(item), 'available': item.on_hand, 'given_out_net': net.get(item.pk, 0),
                     'alert_level': item.low_threshold, 'archived': 'yes' if item.is_archived else 'no'} for item in items]
    elif family in ('intake', 'roster'):
        people = filter_people(Missionary.objects.all(), filters)
        if family == 'intake':
            fields = ['arrival_status', 'people']
            rows = [{'arrival_status': r['arrival_status'] or '(unspecified)', 'people': r['total']} for r in
                    people.values('arrival_status').annotate(total=Count('id')).order_by('arrival_status')]
            rows += [{'arrival_status': f"arrival month {r['arrival_date__year']}-{r['arrival_date__month']:02d}", 'people': r['total']} for r in
                     people.exclude(arrival_date=None).values('arrival_date__year', 'arrival_date__month').annotate(total=Count('id')).order_by('arrival_date__year', 'arrival_date__month')]
        else:
            fields = ['id', 'external_id', 'name', 'email', 'title_prefix', 'arrival_status', 'stake', 'mission', 'language', 'arrival_date', 'release_date', 'personal_email', 'intake_metadata', 'archived']
            rows = []
            for person in people.order_by('name', 'pk'):
                row = {field: getattr(person, field) for field in fields if field != 'archived'}
                row['id'] = str(person.pk)
                row['intake_metadata'] = json.dumps(person.intake_metadata, ensure_ascii=False)
                row['archived'] = 'yes' if person.is_archived else 'no'
                rows.append(row)
    elif family == 'history':
        fields = ['when', 'person', 'items', 'status', 'given_by', 'undone_by', 'reason', 'note']
        issues = filter_issues(Issue.objects.all(), filters).prefetch_related('lines').order_by('-issued_at', 'pk')
        rows = [{'when': timezone.localtime(i.issued_at).isoformat(),
                 'person': f'{i.missionary_name} ({i.missionary_external_id})',
                 'items': '; '.join(f'{line.item_category} / {line.item_name}' + (f' ({line.item_size})' if line.item_size else '') + f' × {line.quantity}' for line in i.lines.all()),
                 'status': 'Undone' if i.status == Issue.REVERSED else 'Current',
                 'given_by': i.issued_by_username, 'undone_by': i.reversed_by_username,
                 'reason': i.reversal_reason, 'note': i.note} for i in issues]
    else:
        raise ValueError('unknown report family')
    return title, fields, rows


def _csv_response(rows, fields, filename):
    buf = io.StringIO(newline='')
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    for row in rows:
        writer.writerow({field: safe_encode(row.get(field) if row.get(field) is not None else '') for field in fields})
    response = HttpResponse(buf.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def report_csv(family, include_archived=False, *, filters=None):
    _, fields, rows = report_table(family, include_archived=include_archived, filters=filters)
    return _csv_response(rows, fields, f'report-{family}.csv')


def roster_csv(*, include_archived=True, filters=None):
    return report_csv('roster', include_archived, filters=filters)


def inventory_csv(*, include_archived=True, filters=None):
    return report_csv('inventory', include_archived, filters=filters)


def report_pdf(family, include_archived=False, *, filters=None):
    from django.conf import settings
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font = 'SuppliesNotoSans'
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font, str(settings.BASE_DIR / 'static/fonts/NotoSans-Regular.ttf')))
    title, fields, rows = report_table(family, include_archived=include_archived, filters=filters)
    buf = io.BytesIO()
    # Wide record/history exports use landscape A4; stock/summaries use portrait.
    pagesize = (A4[1], A4[0]) if len(fields) > 6 else A4
    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=30, rightMargin=30,
                            topMargin=30, bottomMargin=30, title=title)
    cell = ParagraphStyle('cell', fontName=font, fontSize=8, leading=11, splitLongWords=True)
    heading = ParagraphStyle('heading', parent=cell, fontSize=15, leading=20, spaceAfter=10)
    header = ParagraphStyle('header', parent=cell, textColor=colors.white)
    description = 'Stock is available now, not as-of a past date. Given-out dates select issuance; current net excludes every undone issue, even when undone later. History keeps original snapshots.'
    story = [Paragraph(escape(title), heading), Paragraph(description, cell), Spacer(1, 10)]
    if filters:
        applied = ', '.join(f'{key}: {value}' for key, value in filters.items() if value)
        if applied:
            story += [Paragraph('Filters — ' + escape(applied), cell), Spacer(1, 10)]
    # Stable IDs and raw provenance remain in complete exports, but broad record
    # tables are transposed into labeled blocks to avoid illegible tiny columns.
    if family in ('roster', 'inventory', 'history'):
        for row in rows:
            data = [[Paragraph('Field', header), Paragraph('Value', header)]]
            data += [[Paragraph(escape(field.replace('_', ' ').title()), cell),
                     Paragraph(escape(str(row.get(field) if row.get(field) is not None else '')).replace('\n', '<br/>'), cell)] for field in fields]
            table = Table(data, colWidths=[doc.width * .2, doc.width * .8], repeatRows=1, hAlign='LEFT', splitByRow=1, splitInRow=1)
            table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0b5850')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, -1), .3, colors.lightgrey)]))
            story += [table, Spacer(1, 12)]
    else:
        visible = [field for field in fields if field not in ('item_id', 'missionary_id')]
        data = [[Paragraph(escape(field.replace('_', ' ').title()), header) for field in visible]]
        data += [[Paragraph(escape(str(row.get(field, ''))), cell) for field in visible] for row in rows]
        if not rows:
            data.append([Paragraph('No matching records.', cell)] + [''] * (len(visible) - 1))
        widths = [doc.width * .6] + [doc.width * .4 / (len(visible) - 1)] * (len(visible) - 1)
        table = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT', splitByRow=1, splitInRow=1)
        table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0b5850')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('GRID', (0, 0), (-1, -1), .3, colors.lightgrey),
            ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
        story.append(table)
    if not rows and family in ('roster', 'inventory', 'history'):
        story.append(Paragraph('No matching records.', cell))
    doc.build(story)
    response = HttpResponse(buf.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="report-{family}.pdf"'
    return response
