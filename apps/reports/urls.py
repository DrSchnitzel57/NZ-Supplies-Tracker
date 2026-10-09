from django.urls import path

from apps.reports import views

urlpatterns = [
    path("", views.report_home, name="reports-home"),
    path("roster.csv", views.export_roster_csv, name="reports-roster-csv"),
    path("inventory.csv", views.export_inventory_csv, name="reports-inventory-csv"),
    path("family/<str:family>/", views.export_report, name="reports-family"),
    path("bundle/export/", views.export_bundle, name="reports-bundle"),
]
