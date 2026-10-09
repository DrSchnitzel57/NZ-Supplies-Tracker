from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.urls import reverse_lazy
from django.views import View

from apps.accounts import lifecycle
from apps.accounts.decorators import admin_required
from apps.accounts.forms import ForcedPasswordChangeForm, ThrottledLoginForm


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = ThrottledLoginForm


class LogoutView(auth_views.LogoutView):
    pass


class ForcedPasswordChangeView(LoginRequiredMixin, auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    form_class = ForcedPasswordChangeForm
    success_url = reverse_lazy("accounts-password-done")

    def form_valid(self, form):
        response = super().form_valid(form)
        lifecycle.mark_password_changed(user=self.request.user)
        return response


class PasswordChangeDoneView(LoginRequiredMixin, auth_views.PasswordChangeDoneView):
    template_name = "accounts/password_change_done.html"


@admin_required
def user_list(request):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    names = ", ".join(User.objects.order_by("username").values_list("username", flat=True))
    return HttpResponse(f"users: {names}")


class StaffOnlyView(View):
    pass
