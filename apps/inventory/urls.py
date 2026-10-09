from django.urls import path

from apps.inventory import views

urlpatterns = [
    path("", views.item_list, name="inventory-list"),
    path("new/", views.item_create, name="inventory-create"),
    path("<uuid:pk>/", views.item_detail, name="inventory-detail"),
    path("<uuid:pk>/edit/", views.item_edit, name="inventory-edit"),
    path("<uuid:pk>/add-stock/", views.item_receive, name="inventory-receive"),
    path("<uuid:pk>/correct-count/", views.item_correct, name="inventory-correct"),
    path("<uuid:pk>/archive/", views.item_archive, name="inventory-archive"),
    path(
        "<uuid:pk>/reactivate/",
        views.item_reactivate,
        name="inventory-reactivate",
    ),
]
