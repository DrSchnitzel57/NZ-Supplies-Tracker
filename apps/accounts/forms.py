from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm

from apps.accounts import throttling
from apps.common.errors import ThrottledError

User = get_user_model()


class ThrottledLoginForm(AuthenticationForm):
    def clean(self):
        username = self.data.get("username", "")
        key = throttling.login_key(username)
        try:
            throttling.check_allowed(key)
        except ThrottledError:
            raise forms.ValidationError(
                "Too many login attempts. Try again later."
            )
        try:
            cleaned = super().clean()
        except Exception:
            throttling.record_failure(key)
            raise
        throttling.record_success(key)
        return cleaned


class ForcedPasswordChangeForm(PasswordChangeForm):
    pass
