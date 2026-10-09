"""Health/readiness endpoints: no auth, no PII, no secrets."""

from __future__ import annotations

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from apps.accounts.decorators import no_activity_update


@no_activity_update
@require_GET
def healthz(request):
    # Liveness only: no DB touch, no user data, safe for load-balancer pings.
    return JsonResponse({"ok": True})


@no_activity_update
@require_GET
def readyz(request):
    # Readiness: database reachable and all migrations applied. Reports the
    # maintenance window and running version so update/restore scripts can
    # gate on real application state (not just config files).
    from django.conf import settings

    from apps.operations.guards import maintenance_status

    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if plan:
            return JsonResponse({"ok": False, "reason": "migrations-pending",
                                 "version": getattr(settings, "APP_VERSION", "")},
                                status=503)
    except Exception:
        return JsonResponse({"ok": False, "reason": "db-unavailable",
                             "version": getattr(settings, "APP_VERSION", "")},
                            status=503)
    status = maintenance_status()
    return JsonResponse({"ok": True,
                         "version": getattr(settings, "APP_VERSION", ""),
                         "maintenance": status["active"],
                         "maintenance_owner": status["owner"]})
