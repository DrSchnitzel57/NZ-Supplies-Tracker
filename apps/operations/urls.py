from django.urls import path

from apps.operations import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("reports/", views.reports, name="reports"),
    path("guide/", views.guide, name="guide"),
    path("api/stock/", views.stock_api, name="api-stock"),
    path("api/dashboard/", views.dashboard_api, name="api-dashboard"),
]
