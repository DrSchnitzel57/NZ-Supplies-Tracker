"""Row-import preview records. Payloads are operational data (may include
names); they are deleted on full reset and expire after 24 hours."""

from __future__ import annotations

import uuid

from django.db import models


class RowImportPreview(models.Model):
    ADD_UPDATE = "add_update"
    REPLACE = "replace"
    MODES = [(ADD_UPDATE, "Add/update"), (REPLACE, "Replace")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=16)  # roster | inventory
    mode = models.CharField(max_length=16, choices=MODES, default=ADD_UPDATE)
    rows = models.JSONField(default=list)
    base_revision = models.CharField(max_length=128, default="")
    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
