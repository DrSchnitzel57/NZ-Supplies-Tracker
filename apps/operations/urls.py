from django.urls import path

from apps.operations import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("guide/", views.guide, name="guide"),
    path("api/stock/", views.stock_api, name="api-stock"),
    path("api/dashboard/", views.dashboard_api, name="api-dashboard"),
    path("admin-area/settings/", views.settings_view, name="ops-settings"),
    path("admin-area/reauth/", views.reauth_view, name="ops-reauth"),
    path("admin-area/reset/", views.reset_view, name="ops-reset"),
]
