import uuid

from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    action = models.CharField(max_length=64)
    target_type = models.CharField(max_length=64)
    target_id = models.CharField(max_length=64)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    # Actor snapshot: retained when the linked account is later removed, and
    # preserved across bundle export/import without requiring accounts.
    actor_username = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        ordering = ["created_at"]

    def save(self, *args, **kwargs):
        if self.actor_id and not self.actor_username:
            try:
                username = self.actor.username
            except Exception:
                username = ""
            self.actor_username = username or ""
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["created_at"]
