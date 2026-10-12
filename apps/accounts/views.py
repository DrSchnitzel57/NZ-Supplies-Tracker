from django.contrib import messages
from django.contrib.auth import get_user_model, views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_http_methods

from apps.accounts import lifecycle
from apps.accounts.decorators import admin_required, staff_required_api, no_activity_update
from apps.accounts.forms import ForcedPasswordChangeForm, ThrottledLoginForm, AdminUserForm
from apps.accounts.reauth import reauth_required, reauth_fresh
from apps.common.errors import DomainError


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = ThrottledLoginForm


class LogoutView(auth_views.LogoutView):
    pass


@staff_required_api
@no_activity_update
def session_refresh(request):
    """Repair open-tab security tokens without extending idle expiry or writing."""
    from django.http import JsonResponse
    from django.middleware.csrf import get_token

    response = JsonResponse({'user_id': str(request.user.pk), 'csrf_token': get_token(request)})
    response['Cache-Control'] = 'no-store'
    return response


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
    return _user_screen(request)


def _user_screen(request, form=None, status=200):
    User = get_user_model()
    users = list(User.objects.order_by("username"))
    return render(request, "accounts/users.html", {"users": users,
        "form": form or AdminUserForm(), "fresh": reauth_fresh(request)}, status=status)


@admin_required
@reauth_required
@require_http_methods(["POST"])
def user_create(request):
    form = AdminUserForm(request.POST)
    if not form.is_valid():
        return _user_screen(request, form=form, status=400)
    try:
        user = lifecycle.create_user(actor=request.user, **form.cleaned_data)
    except DomainError as exc:
        form.add_error(None, str(exc))
        return _user_screen(request, form=form, status=400)
    messages.success(
        request,
        f"Created {'administrator' if user.is_superuser else 'staff'} account {user.username}. "
        "They must change the temporary password on first sign-in.")
    return redirect("accounts-users")


@admin_required
@reauth_required
@require_http_methods(["POST"])
def user_reset_password(request, pk):
    User = get_user_model()
    try:
        target = User.objects.get(pk=pk)
    except User.DoesNotExist:
        messages.error(request, "Account not found.")
        return redirect("accounts-users")
    try:
        lifecycle.reset_password(actor=request.user, user_id=target.pk,
                                 temporary_password=request.POST.get("password") or "")
    except DomainError as exc:
        messages.error(request, f"Could not reset password: {exc}")
        return redirect("accounts-users")
    messages.success(request, f"Set a temporary password for {target.username}.")
    return redirect("accounts-users")


@admin_required
@reauth_required
@require_http_methods(["POST"])
def user_deactivate(request, pk):
    try:
        user = lifecycle.deactivate_user(actor=request.user, user_id=pk)
    except DomainError as exc:
        messages.error(request, f"Could not deactivate: {exc}")
        return redirect("accounts-users")
    messages.success(request, f"Deactivated {user.username}; their sessions were signed out.")
    return redirect("accounts-users")


@admin_required
@reauth_required
@require_http_methods(["POST"])
def user_reactivate(request, pk):
    try:
        user = lifecycle.reactivate_user(actor=request.user, user_id=pk)
    except DomainError as exc:
        messages.error(request, f"Could not reactivate: {exc}")
        return redirect("accounts-users")
    messages.success(request, f"Reactivated {user.username}.")
    return redirect("accounts-users")
