from django.contrib.auth import views as auth_views
from django.urls import path

from apps.accounts import views

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="accounts-login"),
    path("logout/", auth_views.LogoutView.as_view(), name="accounts-logout"),
    path(
        "password-change/",
        views.ForcedPasswordChangeView.as_view(),
        name="accounts-password-change",
    ),
    path(
        "password-change/done/",
        views.PasswordChangeDoneView.as_view(),
        name="accounts-password-done",
    ),
    path("users/", views.user_list, name="accounts-users"),
    path("users/create/", views.user_create, name="accounts-user-create"),
    path("users/<int:pk>/reset-password/", views.user_reset_password, name="accounts-user-reset"),
    path("users/<int:pk>/deactivate/", views.user_deactivate, name="accounts-user-deactivate"),
    path("users/<int:pk>/reactivate/", views.user_reactivate, name="accounts-user-reactivate"),
]
