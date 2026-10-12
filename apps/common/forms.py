"""Shared safe, bound-form controls; mutations stay in domain services."""

from django import forms

COUNT_MAX = 999_999_999


class VersionedForm(forms.Form):
    expected_version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class ReasonedForm(forms.Form):
    reason = forms.CharField(max_length=4000, label="Reason")
    idempotency_key = forms.CharField(max_length=128, widget=forms.HiddenInput)


class AddStockForm(ReasonedForm):
    quantity = forms.IntegerField(min_value=1, max_value=COUNT_MAX, label="Quantity to add")


class CorrectCountForm(ReasonedForm, VersionedForm):
    counted_qty = forms.IntegerField(min_value=0, max_value=COUNT_MAX,
                                    label="Counted quantity now on the shelf")
