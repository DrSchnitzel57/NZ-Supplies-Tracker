from django import forms

from apps.roster.models import Missionary


class MissionaryForm(forms.ModelForm):
    expected_version = forms.IntegerField(min_value=0, required=False, widget=forms.HiddenInput)

    class Meta:
        model = Missionary
        fields = ['external_id', 'name', 'title_prefix', 'arrival_status', 'arrival_date',
                  'release_date', 'mission', 'language', 'stake', 'email', 'personal_email']
        labels = {'external_id': 'External ID', 'name': 'Full name', 'stake': 'Home stake',
                  'title_prefix': 'Title (Elder / Sister)', 'email': 'Email'}
        widgets = {field: forms.DateInput(attrs={'type': 'date'})
                   for field in ('arrival_date', 'release_date')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and not self.instance._state.adding:
            self.fields['expected_version'].required = True
            self.initial['expected_version'] = self.instance.version
