from django import forms
from apps.common.forms import COUNT_MAX


class SettingsForm(forms.Form):
    site_notice = forms.CharField(required=False, max_length=500, label='Site notice (shown to staff)')
    default_low_threshold = forms.IntegerField(min_value=0, max_value=COUNT_MAX,
        label='Default low-stock alert level for new items')
