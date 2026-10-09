from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Local staff identity. No external providers in this release."""

    must_change_password = models.BooleanField(default=False)
    password_changed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["username"]


class LoginThrottle(models.Model):
    """DB-backed throttle bucket shared by every worker process.

    One row per normalized identity key (login username or re-auth account).
    Updated under ``select_for_update`` so concurrent failures in different
    Gunicorn workers count atomically, unlike a per-process cache counter.
    Never stores passwords — only counters and lock timestamps.
    """

    key = models.CharField(max_length=255, unique=True)
    failures = models.IntegerField(default=0)
    window_started_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"throttle:{self.key}"
