"""One validated filter contract for screens, complete CSVs and PDFs."""
from django import forms
from django.db.models import Q

from apps.inventory.models import Item
from apps.issuing.models import Issue
from apps.roster.models import Missionary

REPORT_CHOICES = [('stock', 'Available stock now'), ('low-stock', 'Low / out of stock now'),
                  ('per-item', 'Given out per item (current net)'),
                  ('per-person', 'Given out per missionary (current net)'),
                  ('category', 'Given out by category (current net)'),
                  ('intake', 'Arrivals overview'), ('history', 'Issue history'),
                  ('roster', 'Missionary records'), ('inventory', 'Inventory records')]


class ReportFilters(forms.Form):
    q = forms.CharField(required=False, max_length=255, label='Search')
    missionary = forms.ModelChoiceField(required=False, queryset=Missionary.objects.all(), label='Missionary', empty_label='All missionaries')
    item = forms.ModelChoiceField(required=False, queryset=Item.objects.all(), empty_label='All items')
    start = forms.DateField(required=False, label='Issued from', widget=forms.DateInput(attrs={'type': 'date'}))
    end = forms.DateField(required=False, label='Issued through', widget=forms.DateInput(attrs={'type': 'date'}))
    staff = forms.ChoiceField(required=False, label='Issued by')
    status = forms.ChoiceField(required=False, choices=[('', 'All statuses'), (Issue.ACTIVE, 'Current'), (Issue.REVERSED, 'Undone')])
    archived = forms.BooleanField(required=False, label='Include archived records')

    def __init__(self, *args, family='history', **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['staff'].choices = [('', 'All staff')] + [(name, name) for name in
            Issue.objects.order_by('issued_by_username').values_list('issued_by_username', flat=True).distinct()]
        if family in ('stock', 'low-stock', 'inventory'):
            for field in ('missionary', 'start', 'end', 'staff', 'status'):
                self.fields.pop(field)
        elif family in ('roster', 'intake'):
            for field in ('item', 'staff', 'status'):
                self.fields.pop(field)
            self.fields['start'].label = 'Arrival from'
            self.fields['end'].label = 'Arrival through'
        elif family != 'history':
            self.fields.pop('status')

    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['start'] > data['end']:
            self.add_error('end', 'End date must be on or after the start date.')
        return data


def filter_issues(qs, filters, *, net=False):
    if net:
        qs = qs.filter(status=Issue.ACTIVE)
    elif filters.get('status'):
        qs = qs.filter(status=filters['status'])
    if not filters.get('archived'):
        qs = qs.filter(missionary__is_archived=False)
    if filters.get('missionary'):
        qs = qs.filter(missionary=filters['missionary'])
    if filters.get('item'):
        qs = qs.filter(lines__item=filters['item'])
    if filters.get('staff'):
        qs = qs.filter(issued_by_username=filters['staff'])
    if filters.get('start'):
        qs = qs.filter(issued_at__date__gte=filters['start'])
    if filters.get('end'):
        qs = qs.filter(issued_at__date__lte=filters['end'])
    if filters.get('q'):
        q = filters['q']
        qs = qs.filter(Q(missionary_name__icontains=q) | Q(missionary_external_id__icontains=q)
            | Q(missionary__name__icontains=q) | Q(lines__item_name__icontains=q)
            | Q(lines__item__name__icontains=q) | Q(note__icontains=q))
    return qs.distinct()


def filter_items(qs, filters):
    if not filters.get('archived'):
        qs = qs.filter(is_archived=False)
    if filters.get('item'):
        qs = qs.filter(pk=filters['item'].pk)
    if filters.get('q'):
        q = filters['q']
        qs = qs.filter(Q(name__icontains=q) | Q(category__icontains=q) | Q(size__icontains=q))
    return qs


def filter_people(qs, filters):
    if not filters.get('archived'):
        qs = qs.filter(is_archived=False)
    if filters.get('missionary'):
        qs = qs.filter(pk=filters['missionary'].pk)
    if filters.get('q'):
        q = filters['q']
        qs = qs.filter(Q(name__icontains=q) | Q(external_id__icontains=q) | Q(mission__icontains=q) | Q(language__icontains=q))
    if filters.get('start'):
        qs = qs.filter(arrival_date__gte=filters['start'])
    if filters.get('end'):
        qs = qs.filter(arrival_date__lte=filters['end'])
    return qs
