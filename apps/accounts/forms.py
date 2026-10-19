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


class AdminUserForm(forms.Form):
    display_name = forms.CharField(max_length=80, required=False, label='Name shown to others',
                                   help_text='How colleagues will see them in History, e.g. “Sister Walker”.',
                                   widget=forms.TextInput(attrs={'autocomplete': 'off', 'placeholder': 'Sister Walker'}))
    username = forms.CharField(max_length=150, validators=User._meta.get_field('username').validators,
                               help_text='Used to sign in. Letters, numbers and . @ + - _ only.',
                               widget=forms.TextInput(attrs={'autocomplete': 'off', 'placeholder': 'sister.walker'}))
    password = forms.CharField(label='Temporary password', strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    is_admin = forms.BooleanField(required=False, label='Administrator (can manage accounts, settings and full imports)')
