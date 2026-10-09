import uuid

from django.db import models


class Missionary(models.Model):
    """Roster entry with a stable internal ID.

    external_id is the operator-visible unique string. History rows snapshot
    name/external_id at write time, so renames never rewrite past issues.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    external_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, default="")
    is_archived = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)
    version = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.external_id})"
