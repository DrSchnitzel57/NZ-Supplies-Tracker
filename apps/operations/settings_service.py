"""Guarded settings mutations: values and audit either all commit or none."""

from apps.accounts.services import require_admin
from apps.common.db import transact
from apps.common.errors import ValidationError


def update_settings(*, actor, notice, threshold):
    require_admin(actor)
    if threshold < 0:
        raise ValidationError("Default alert level cannot be negative.")

    def work():
        from apps.audit.models import AuditEvent
        from apps.operations.guards import assert_writes_allowed
        from apps.operations.models import SystemSetting

        assert_writes_allowed()
        SystemSetting.objects.update_or_create(key="site_notice", defaults={"value": notice})
        SystemSetting.objects.update_or_create(
            key="default_low_threshold", defaults={"value": str(threshold)})
        AuditEvent.objects.create(
            actor=actor, action="settings.updated", target_type="system",
            target_id="settings", detail={"default_low_threshold": threshold,
                                          "notice_len": len(notice)})

    return transact(work)
