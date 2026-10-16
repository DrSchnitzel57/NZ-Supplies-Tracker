from django import forms

from apps.roster.models import ARRIVAL_CHOICES, Missionary, normalize_arrival

TITLE_CHOICES = [("", "Not recorded"), ("Elder", "Elder"), ("Sister", "Sister"),
                 ("Couple", "Couple")]


class MissionaryForm(forms.ModelForm):
    expected_version = forms.IntegerField(min_value=0, required=False, widget=forms.HiddenInput)
    arrival_status = forms.CharField(
        label="Arrived at the MTC?", required=False,
        widget=forms.Select(choices=ARRIVAL_CHOICES))

    class Meta:
        model = Missionary
        fields = ['title_prefix', 'name', 'external_id', 'mission', 'language',
                  'arrival_status', 'arrival_date', 'departure_date', 'release_date',
                  'stake', 'email', 'personal_email']
        labels = {
            'external_id': 'Missionary ID',
            'name': 'Name (Family, Given names)',
            'title_prefix': 'Title',
            'stake': 'Home stake',
            'mission': 'Assigned mission',
            'language': 'Training language',
            'arrival_date': 'Arrived at the MTC on',
            'departure_date': 'Leaves the MTC on',
            'release_date': 'Mission release date',
            'email': 'Missionary email',
            'personal_email': 'Personal email',
        }
        help_texts = {
            'name': 'For example “Halvorsen, Margot Louise”. Shown to staff as “Sister Halvorsen”.',
            'departure_date': 'The day they fly to their mission.',
        }
        widgets = {
            'title_prefix': forms.Select(choices=TITLE_CHOICES),
            **{field: forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')
               for field in ('arrival_date', 'departure_date', 'release_date')},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and not self.instance._state.adding:
            self.fields['expected_version'].required = True
            self.initial['expected_version'] = self.instance.version
        # Keep any title already on a record selectable, even an unusual one.
        current = (self.initial.get('title_prefix') or self.data.get('title_prefix') or '').strip()
        choices = list(TITLE_CHOICES)
        if current and current not in dict(choices):
            choices.append((current, current))
        self.fields['title_prefix'].widget.choices = choices
        self.initial['arrival_status'] = normalize_arrival(self.initial.get('arrival_status', ''))
        arrival = normalize_arrival(self.data.get('arrival_status', '')) if self.is_bound else \
            self.initial['arrival_status']
        arrival_choices = list(ARRIVAL_CHOICES)
        if arrival and arrival not in dict(arrival_choices):
            arrival_choices.append((arrival, arrival))
        self.fields['arrival_status'].widget.choices = arrival_choices

    def clean_arrival_status(self):
        return normalize_arrival(self.cleaned_data.get('arrival_status', ''))
