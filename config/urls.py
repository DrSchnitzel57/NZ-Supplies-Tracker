from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.decorators.http import require_GET

from apps.accounts.decorators import no_activity_update


@no_activity_update
@require_GET
def poll(request):
    """Read-only poll endpoint. Must not extend the idle session timer."""
    return JsonResponse({"ok": True})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("poll/", poll, name="poll"),
    path("api/poll/", poll, name="api-poll"),
    path("", include("apps.operations.urls")),
    path("roster/", include("apps.roster.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("issue/", include("apps.issuing.urls")),
]
