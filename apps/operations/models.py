from django.db import models


class MaintenanceState(models.Model):
    """Singleton row (pk=1) coordinating maintenance/write guards.

    Import, reset and update operations enter maintenance to quiesce writes;
    routine services refuse to write while the flag is set. Ownership makes
    the window exclusive: a second operator (or a nested block under a
    different owner) fails to acquire instead of silently sharing or clearing
    someone else's window. ``depth`` counts re-entrant holds by the same
    owner token so an inner block cannot reopen an outer window early.
    ``acquired_at``/``reason`` support crash recovery: a stale window is only
    ever taken over explicitly (force + audit), never auto-expired, so a
    crashed operation keeps writes closed (fail closed) until an operator
    takes over deliberately.
    """

    id = models.IntegerField(primary_key=True, default=1)
    maintenance_mode = models.BooleanField(default=False)
    owner = models.CharField(max_length=128, blank=True, default="")
    token = models.CharField(max_length=64, blank=True, default="")
    reason = models.CharField(max_length=255, blank=True, default="")
    depth = models.IntegerField(default=0)
    acquired_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="singleton_row"),
        ]


class SystemSetting(models.Model):
    """Administrator-editable site settings. No secrets stored here."""

    key = models.CharField(max_length=64, unique=True)
    value = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return f"{self.key}={self.value}"
