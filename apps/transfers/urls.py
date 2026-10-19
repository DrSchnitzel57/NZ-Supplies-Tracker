from django.urls import path

from apps.transfers import views

urlpatterns = [
    path("bundle/", views.bundle_home, name="transfers-bundle"),
    path("bundle/export/", views.bundle_export, name="transfers-export"),
    path("bundle/import/", views.bundle_import, name="transfers-import"),
    path("bundle/import-original/", views.original_import, name="transfers-import-original"),
    path("roster/", views.roster_import, name="transfers-roster"),
    path("inventory/", views.inventory_import, name="transfers-inventory"),
    path("preview/<str:token>/", views.import_preview, name="transfers-preview"),
    path("preview/<str:token>/confirm/", views.import_confirm, name="transfers-confirm"),
]
