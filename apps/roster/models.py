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
    # Source-preserved roster context (converted from the approved seed and
    # carried through bundles; blank when the source did not provide them).
    title_prefix = models.CharField(max_length=16, blank=True, default="")  # Elder/Sister
    arrival_status = models.CharField(max_length=32, blank=True, default="")
    stake = models.CharField(max_length=128, blank=True, default="")
    mission = models.CharField(max_length=128, blank=True, default="")
    language = models.CharField(max_length=64, blank=True, default="")
    arrival_date = models.DateField(null=True, blank=True)
    release_date = models.DateField(null=True, blank=True)
    personal_email = models.EmailField(blank=True, default="")
    intake_metadata = models.JSONField(default=dict, blank=True)
    is_archived = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)
    version = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.external_id})"
