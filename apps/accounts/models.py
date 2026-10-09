from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Local staff identity. No external providers in this release."""

    must_change_password = models.BooleanField(default=False)
    password_changed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["username"]
