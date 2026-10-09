from django.urls import path

from apps.issuing import views

urlpatterns = [
    path("", views.issue_main, name="issue-main"),
    path("quick/", views.issue_quick, name="issue-quick"),
    path("confirm/", views.issue_confirm, name="issue-confirm"),
    path("history/", views.issue_list, name="issue-history"),
    path("<uuid:pk>/", views.issue_detail, name="issue-detail"),
    path("<uuid:pk>/undo/", views.issue_reverse, name="issue-reverse"),
]
