from django.urls import path

from apps.roster import views

urlpatterns = [
    path("", views.missionary_list, name="roster-list"),
    path("new/", views.missionary_create, name="roster-create"),
    path("<uuid:pk>/", views.missionary_detail, name="roster-detail"),
    path("<uuid:pk>/summary/", views.missionary_summary, name="roster-summary"),
    path("<uuid:pk>/edit/", views.missionary_edit, name="roster-edit"),
    path("<uuid:pk>/archive/", views.missionary_archive, name="roster-archive"),
    path("<uuid:pk>/check-in/", views.missionary_check_in, name="roster-check-in"),
    path(
        "<uuid:pk>/reactivate/",
        views.missionary_reactivate,
        name="roster-reactivate",
    ),
]
