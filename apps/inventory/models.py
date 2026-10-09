import re
import uuid

from django.db import models

_WS = re.compile(r"\s+")


def normalize_text(value: str | None) -> str:
    return _WS.sub(" ", (value or "").strip().lower())


def item_key(category: str, name: str, size: str) -> str:
    return "\x1f".join(
        [normalize_text(category), normalize_text(name), normalize_text(size or "")]
    )


class Item(models.Model):
    """Stock-keeping record. on_hand is the cached authoritative Remaining.

    The ledger (StockMovement) is the source of truth; on_hand is maintained
    inside the same atomic transaction and guarded by a non-negative check.
    key enforces normalized category/item/size uniqueness.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.CharField(max_length=128)
    name = models.CharField(max_length=255)
    size = models.CharField(max_length=64, blank=True, default="")
    key = models.CharField(max_length=512, unique=True, editable=False)
    unit = models.CharField(max_length=32, default="each")
    on_hand = models.IntegerField(default=0)
    low_threshold = models.IntegerField(default=0)
    # Seed-conversion provenance: source-negative opening balances are zeroed
    # live (never negative) and the original value is preserved here so a
    # bundle export round-trips the authorized correction metadata.
    source_remaining = models.IntegerField(null=True, blank=True)
    source_corrected = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)
    version = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "name", "size"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(on_hand__gte=0), name="item_on_hand_nonnegative"
            ),
        ]

    def save(self, *args, **kwargs):
        self.key = item_key(self.category, self.name, self.size)
        super().save(*args, **kwargs)

    def __str__(self):
        size = f" ({self.size})" if self.size else ""
        return f"{self.category} / {self.name}{size}"


class StockMovement(models.Model):
    OPENING = "OPENING"
    RECEIPT = "RECEIPT"
    COUNT_ADJUST = "COUNT_ADJUST"
    ISSUE = "ISSUE"
    REVERSAL = "REVERSAL"
    KINDS = [
        (OPENING, "Opening balance"),
        (RECEIPT, "Receipt"),
        (COUNT_ADJUST, "Physical count adjustment"),
        (ISSUE, "Issue"),
        (REVERSAL, "Reversal"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="movements"
    )
    kind = models.CharField(max_length=16, choices=KINDS)
    delta = models.IntegerField()
    balance_after = models.IntegerField()
    reason = models.TextField(blank=True, default="")
    issue = models.ForeignKey(
        "issuing.Issue",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="movements",
    )
    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    # Actor snapshot: retained when the linked account is later removed, and
    # preserved across bundle export/import without requiring accounts.
    created_by_username = models.CharField(max_length=150, blank=True, default="")
    idempotency_key = models.CharField(
        max_length=128, null=True, blank=True, unique=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(balance_after__gte=0),
                name="movement_balance_nonnegative",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.created_by_id and not self.created_by_username:
            try:
                username = self.created_by.username
            except Exception:
                username = ""
            self.created_by_username = username or ""
        super().save(*args, **kwargs)
