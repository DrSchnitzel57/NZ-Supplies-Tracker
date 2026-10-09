import uuid

from django.conf import settings
from django.db import models


class Issue(models.Model):
    ACTIVE = "ACTIVE"
    REVERSED = "REVERSED"
    STATUSES = [(ACTIVE, "Active"), (REVERSED, "Reversed")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    missionary = models.ForeignKey(
        "roster.Missionary", on_delete=models.PROTECT, related_name="issues"
    )
    # Snapshots: renames never rewrite history.
    missionary_name = models.CharField(max_length=255)
    missionary_external_id = models.CharField(max_length=64)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="issues",
    )
    issued_by_username = models.CharField(max_length=150, blank=True, default="")
    issued_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUSES, default=ACTIVE)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.TextField(blank=True, default="")
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reversals",
    )
    idempotency_key = models.CharField(max_length=128, unique=True)
    version = models.IntegerField(default=0)

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return f"Issue {self.pk} to {self.missionary_name}"


class IssueLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issue = models.ForeignKey(
        Issue, on_delete=models.CASCADE, related_name="lines"
    )
    item = models.ForeignKey(
        "inventory.Item", on_delete=models.PROTECT, related_name="issue_lines"
    )
    quantity = models.PositiveIntegerField()
    # Item snapshots at issue time.
    item_category = models.CharField(max_length=128)
    item_name = models.CharField(max_length=255)
    item_size = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["item_category", "item_name", "item_size"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="line_quantity_positive"
            ),
        ]
