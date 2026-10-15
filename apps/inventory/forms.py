from django import forms

from apps.common.forms import COUNT_MAX
from apps.inventory.models import Item
import uuid


class ItemForm(forms.ModelForm):
    expected_version = forms.IntegerField(min_value=0, required=False, widget=forms.HiddenInput)
    low_threshold = forms.IntegerField(min_value=0, max_value=COUNT_MAX, label='Low-stock alert level')

    class Meta:
        model = Item
        fields = ['category', 'name', 'size', 'unit', 'low_threshold']
        labels = {'name': 'Item name'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance._state.adding:
            self.fields['expected_version'].required = True
            self.initial['expected_version'] = self.instance.version


class NewItemForm(ItemForm):
    opening_qty = forms.IntegerField(min_value=0, max_value=COUNT_MAX, initial=0,
                                    label='Opening quantity')
    reason = forms.CharField(max_length=4000, initial='opening balance')
    idempotency_key = forms.CharField(required=False, max_length=128, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Keep the pre-existing creation API: omitted unit means "each".
        self.fields['unit'].required = False


class BatchItemForm(NewItemForm):
    row_key = forms.CharField(max_length=64, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('expected_version')
        self.fields.pop('idempotency_key')
        self.initial.setdefault('row_key', uuid.uuid4().hex)


BatchItemFormSet = forms.formset_factory(BatchItemForm, extra=0, min_num=1,
    max_num=50, absolute_max=50, validate_min=True, validate_max=True)
