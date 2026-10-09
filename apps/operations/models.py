from django.db import models


class MaintenanceState(models.Model):
    """Singleton row (pk=1) coordinating maintenance/write guards.

    Import, reset and update operations enter maintenance to quiesce writes;
    routine services refuse to write while the flag is set.
    """

    id = models.IntegerField(primary_key=True, default=1)
    maintenance_mode = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="singleton_row"),
        ]
