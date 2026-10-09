from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm

from apps.accounts import throttling
from apps.common.errors import ThrottledError

User = get_user_model()


class ThrottledLoginForm(AuthenticationForm):
    def clean(self):
        username = self.data.get("username", "")
        try:
            throttling.check_allowed(username)
        except ThrottledError:
            raise forms.ValidationError(
                "Too many login attempts. Try again later."
            )
        try:
            cleaned = super().clean()
        except forms.ValidationError:
            throttling.record_failure(username)
            raise
        throttling.record_success(username)
        return cleaned


class ForcedPasswordChangeForm(PasswordChangeForm):
    pass
