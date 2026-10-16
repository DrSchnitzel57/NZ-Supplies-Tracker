"""One validated filter contract for screens, complete CSVs and PDFs."""
from django import forms
from django.db.models import Q

from apps.inventory.models import Item
from apps.issuing.models import Issue
from apps.roster.models import Missionary

REPORT_CHOICES = [('stock', 'Stock on hand now'), ('low-stock', 'Reorder list (low or out of stock)'),
                  ('per-item', 'Given out — by item'),
                  ('per-person', 'Given out — by missionary'),
                  ('category', 'Given out — by category'),
                  ('intake', 'Arrivals and departures'), ('history', 'Issue history'),
                  ('roster', 'Missionary records (full export)'), ('inventory', 'Inventory records (full export)')]
REPORT_DESCRIPTIONS = {
    'stock': 'Everything in the catalogue with what is on the shelf right now and how much has been given out.',
    'low-stock': 'Items at or below their alert level, ready to copy into an email or order. Includes items at zero.',
    'per-item': 'How many of each item have been given out. Undone issues are not counted.',
    'per-person': 'How many items each missionary has received. Undone issues are not counted.',
    'category': 'Items given out per category. Use the dates to look at one intake or month.',
    'intake': 'How many missionaries have arrived, and how many arrive and leave each month.',
    'history': 'Every issue with its items, who gave it and whether it was undone.',
    'roster': 'Every field for every missionary — the same columns the import accepts.',
    'inventory': 'Every item with its counts and alert levels — the same columns the import accepts.',
}


class MissionaryChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        if obj.display_name != obj.full_name:
            return f"{obj.display_name} — {obj.full_name} ({obj.external_id})"
        return f"{obj.full_name} ({obj.external_id})"


class ItemChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.category} / {obj.name}" + (f" ({obj.size})" if obj.size else "")


class ReportFilters(forms.Form):
    q = forms.CharField(required=False, max_length=255, label='Search',
                        widget=forms.TextInput(attrs={'type': 'search', 'placeholder': 'Missionary, item or note'}))
    missionary = MissionaryChoiceField(required=False, queryset=Missionary.objects.all(), label='Missionary', empty_label='All missionaries')
    item = ItemChoiceField(required=False, queryset=Item.objects.all(), empty_label='All items')
    start = forms.DateField(required=False, label='Issued from', widget=forms.DateInput(attrs={'type': 'date'}))
    end = forms.DateField(required=False, label='Issued through', widget=forms.DateInput(attrs={'type': 'date'}))
    staff = forms.ChoiceField(required=False, label='Issued by')
    status = forms.ChoiceField(required=False, choices=[('', 'Current and undone'), (Issue.ACTIVE, 'Current only'), (Issue.REVERSED, 'Undone only')])
    archived = forms.BooleanField(required=False, label='Include archived records')

    def __init__(self, *args, family='history', **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model
        shown = dict(get_user_model().objects.exclude(display_name='')
                     .values_list('username', 'display_name'))
        self.fields['staff'].choices = [('', 'All staff')] + [(name, shown.get(name, name)) for name in
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
        if family in ('history', 'per-item', 'per-person', 'category'):
            self.fields['start'].label = 'Given out from'
            self.fields['end'].label = 'Given out to'
        if family in ('roster', 'intake'):
            self.fields['start'].label = 'Arrived from'
            self.fields['end'].label = 'Arrived to'
        if 'archived' in self.fields:
            self.fields['archived'].label = 'Include archived'
        if 'staff' in self.fields:
            self.fields['staff'].label = 'Given by'

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
            | Q(missionary__name__icontains=q) | Q(missionary__external_id__icontains=q)
            | Q(lines__item_name__icontains=q)
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
