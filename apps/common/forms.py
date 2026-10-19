"""Shared safe, bound-form controls; mutations stay in domain services."""

from django import forms

COUNT_MAX = 999_999_999


class VersionedForm(forms.Form):
    expected_version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class ReasonedForm(forms.Form):
    reason = forms.CharField(max_length=4000, label="Reason")
    idempotency_key = forms.CharField(max_length=128, widget=forms.HiddenInput)


class AddStockForm(ReasonedForm):
    field_order = ["quantity", "reason", "idempotency_key"]
    quantity = forms.IntegerField(min_value=1, max_value=COUNT_MAX, label="How many arrived?")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].label = "Where did they come from?"
        self.fields["reason"].widget.attrs.setdefault("placeholder", "e.g. Delivery from the distribution centre")


class CorrectCountForm(ReasonedForm, VersionedForm):
    field_order = ["counted_qty", "reason", "idempotency_key", "expected_version"]
    counted_qty = forms.IntegerField(min_value=0, max_value=COUNT_MAX,
                                    label="How many are on the shelf right now?")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].label = "Reason for the change"
        self.fields["reason"].widget.attrs.setdefault("placeholder", "e.g. Stocktake — two damaged and thrown out")
