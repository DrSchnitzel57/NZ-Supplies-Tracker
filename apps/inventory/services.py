"""Centralized inventory writes: receipts and physical-count corrections.

Receipts and count adjustments both require reasons. Counts use optimistic
version tokens so a stale form cannot overwrite newer movements. Every write
updates the cached Remaining and appends a ledger movement atomically; the
database check constraints reject any negative balance.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounts import services as permissions
from apps.common.db import transact
from apps.common.errors import (
    NegativeBalanceError,
    StaleVersionError,
    ValidationError,
)
from apps.inventory.models import Item, StockMovement, item_key, normalize_text
from apps.operations.guards import assert_writes_allowed


def _require_reason(reason: str | None, label: str) -> str:
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(f"{label} reason is required")
    return reason


def create_item(
    *,
    actor,
    category: str,
    name: str,
    size: str = "",
    opening_qty: int = 0,
    reason: str = "opening balance",
    idempotency_key: str | None = None,
    low_threshold: int = 0,
    unit: str = "each",
):
    permissions.require_staff(actor)
    category = (category or "").strip()
    name = (name or "").strip()
    size = (size or "").strip()
    if not category or not name:
        raise ValidationError("category and name are required")
    try:
        opening_qty = int(opening_qty)
    except (TypeError, ValueError) as exc:
        raise ValidationError("opening quantity must be a whole number") from exc
    if opening_qty < 0:
        raise NegativeBalanceError("opening quantity cannot be negative")
    try:
        low_threshold = int(low_threshold)
    except (TypeError, ValueError) as exc:
        raise ValidationError("alert level must be a whole number") from exc
    if low_threshold < 0:
        raise ValidationError("alert level cannot be negative")

    def _work():
        assert_writes_allowed()
        if idempotency_key:
            existing = StockMovement.objects.filter(
                idempotency_key=idempotency_key
            ).first()
            if existing is not None:
                return Item.objects.get(pk=existing.item_id)
        key = item_key(category, name, size)
        if Item.objects.filter(key=key).exists():
            raise ValidationError("item already exists (normalized match)")
        item = Item(
            category=category, name=name, size=size, on_hand=opening_qty,
            low_threshold=low_threshold, unit=unit,
        )
        try:
            with transaction.atomic():
                item.save()
        except IntegrityError as exc:
            if idempotency_key:
                winner = StockMovement.objects.filter(
                    idempotency_key=idempotency_key
                ).first()
                if winner is not None:
                    return Item.objects.get(pk=winner.item_id)
            raise ValidationError("item already exists (normalized match)") from exc
        try:
            with transaction.atomic():
                StockMovement.objects.create(
                    item=item,
                    kind=StockMovement.OPENING,
                    delta=opening_qty,
                    balance_after=opening_qty,
                    reason=_require_reason(reason, "opening"),
                    created_by=actor,
                    idempotency_key=idempotency_key,
                )
        except IntegrityError:
            if idempotency_key and StockMovement.objects.filter(
                idempotency_key=idempotency_key
            ).exists():
                # A concurrent same-key submit won: remove the item row this
                # attempt just created so it cannot linger as an orphan
                # without movements, then return the winner's row.
                item.delete()
                return Item.objects.get(
                    pk=StockMovement.objects.get(
                        idempotency_key=idempotency_key
                    ).item_id
                )
            raise
        _audit(actor, "item.created", item, {"opening_qty": opening_qty})
        return item

    return transact(_work)


def receive_stock(*, actor, item_id, quantity: int, reason: str, idempotency_key=None):
    """Add stock. Quantity must be positive; reason is mandatory."""
    permissions.require_staff(actor)
    try:
        quantity = int(quantity)
    except (TypeError, ValueError) as exc:
        raise ValidationError("receipt quantity must be a whole number") from exc
    if quantity <= 0:
        raise ValidationError("receipt quantity must be positive")
    reason = _require_reason(reason, "receipt")

    def _work():
        assert_writes_allowed()
        if idempotency_key:
            existing = StockMovement.objects.filter(
                idempotency_key=idempotency_key
            ).first()
            if existing is not None:
                return Item.objects.get(pk=existing.item_id), existing
        item = Item.objects.select_for_update().get(pk=item_id)
        balance = item.on_hand + quantity
        try:
            with transaction.atomic():
                movement = StockMovement.objects.create(
                    item=item,
                    kind=StockMovement.RECEIPT,
                    delta=quantity,
                    balance_after=balance,
                    reason=reason,
                    created_by=actor,
                    idempotency_key=idempotency_key,
                )
        except IntegrityError:
            if not idempotency_key:
                raise
            # Idempotency race lost: savepoint keeps this transaction usable.
            existing = StockMovement.objects.get(idempotency_key=idempotency_key)
            return Item.objects.get(pk=existing.item_id), existing
        item.on_hand = balance
        item.version += 1
        item.save()
        _audit(
            actor,
            "stock.received",
            item,
            {"quantity": quantity, "reason": reason},
        )
        return item, movement

    return transact(_work)


def create_batch(*, actor, rows, idempotency_key):
    """Create 1–50 reviewed items atomically; audit stores the retry fingerprint.

    Reuse existing ledger creation inside the outer IMMEDIATE transaction. No
    schema/ZIP change: the batch receipt is an ordinary portable audit event.
    """
    import hashlib
    import json
    from apps.audit.models import AuditEvent
    from apps.inventory.forms import NewItemForm

    permissions.require_staff(actor)
    if not isinstance(rows, list) or not 1 <= len(rows) <= 50:
        raise ValidationError("Add between 1 and 50 items; nothing was saved.")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValidationError("A valid batch retry key is required.")
    cleaned, keys, row_keys = [], set(), set()
    for index, row in enumerate(rows, 1):
        form = NewItemForm(row)
        if not form.is_valid():
            raise ValidationError(f"Item {index}: {form.errors.as_text()}")
        values = form.cleaned_data.copy()
        row_key = row.get("row_key", "")
        if not row_key or len(row_key) > 64 or row_key in row_keys:
            raise ValidationError("Each batch row needs its own stable key.")
        row_keys.add(row_key)
        values.pop("expected_version", None)
        values.pop("idempotency_key", None)
        values["unit"] = values.get("unit") or "each"
        key = item_key(values["category"], values["name"], values["size"])
        if key in keys:
            raise ValidationError(f"Item {index} duplicates another batch row.")
        keys.add(key)
        cleaned.append({**values, "row_key": row_key})
    fingerprint = hashlib.sha256(json.dumps(cleaned, sort_keys=True).encode()).hexdigest()
    receipt_key = hashlib.sha256(("batch:" + idempotency_key).encode()).hexdigest()

    def _work():
        assert_writes_allowed()
        receipt = AuditEvent.objects.filter(action="inventory.batch_created", target_id=receipt_key).first()
        if receipt:
            if receipt.detail.get("fingerprint") != fingerprint or receipt.actor_id != actor.pk:
                raise ValidationError("This retry key belongs to a different batch. Nothing was saved.")
            return list(Item.objects.filter(pk__in=receipt.detail["items"]).order_by("category", "name", "size", "pk"))
        if Item.objects.filter(key__in=keys).exists():
            raise ValidationError("An item already exists, including archived inventory. Nothing was saved.")
        created = []
        for row in cleaned:
            values = row.copy()
            row_key = values.pop("row_key")
            movement_key = "batch-" + hashlib.sha256((receipt_key + row_key).encode()).hexdigest()
            created.append(create_item(actor=actor, idempotency_key=movement_key, **values))
        AuditEvent.objects.create(actor=actor, action="inventory.batch_created", target_type="batch",
            target_id=receipt_key, detail={"fingerprint": fingerprint, "items": [str(i.pk) for i in created]})
        return created

    return transact(_work)


def adjust_count(
    *,
    actor,
    item_id,
    counted_qty: int,
    reason: str,
    expected_version: int,
    idempotency_key=None,
):
    """Set Remaining to a counted value via an explicit correction movement."""
    permissions.require_staff(actor)
    try:
        counted_qty = int(counted_qty)
    except (TypeError, ValueError) as exc:
        raise ValidationError("counted quantity must be a whole number") from exc
    if counted_qty < 0:
        raise NegativeBalanceError("counted quantity cannot be negative")
    reason = _require_reason(reason, "count correction")

    def _work():
        assert_writes_allowed()
        if idempotency_key:
            existing = StockMovement.objects.filter(
                idempotency_key=idempotency_key
            ).first()
            if existing is not None:
                return Item.objects.get(pk=existing.item_id), existing
        try:
            item = Item.objects.select_for_update().get(pk=item_id)
        except Item.DoesNotExist as exc:
            raise ValidationError("item not found") from exc
        if item.version != expected_version:
            raise StaleVersionError(
                "stale count; stock changed since this form was opened"
            )
        delta = counted_qty - item.on_hand
        try:
            with transaction.atomic():
                movement = StockMovement.objects.create(
                    item=item,
                    kind=StockMovement.COUNT_ADJUST,
                    delta=delta,
                    balance_after=counted_qty,
                    reason=reason,
                    created_by=actor,
                    idempotency_key=idempotency_key,
                )
        except IntegrityError as exc:
            if idempotency_key and StockMovement.objects.filter(
                idempotency_key=idempotency_key
            ).exists():
                existing = StockMovement.objects.get(
                    idempotency_key=idempotency_key
                )
                return Item.objects.get(pk=existing.item_id), existing
            raise
        item.on_hand = counted_qty
        item.version += 1
        item.save()
        _audit(
            actor,
            "stock.count_corrected",
            item,
            {"counted": counted_qty, "delta": delta, "reason": reason},
        )
        return item, movement

    return transact(_work)


def update_item(*, actor, item_id, expected_version: int, **fields):
    permissions.require_staff(actor)

    def _work():
        assert_writes_allowed()
        try:
            item = Item.objects.select_for_update().get(pk=item_id)
        except Item.DoesNotExist as exc:
            raise ValidationError("item not found") from exc
        if item.version != expected_version:
            raise StaleVersionError("stale item; refresh and retry")
        for attr in ("category", "name", "size", "unit", "low_threshold"):
            if attr in fields and fields[attr] is not None:
                setattr(item, attr, fields[attr])
        if not item.category.strip() or not item.name.strip():
            raise ValidationError("category and name are required")
        new_key = item_key(item.category, item.name, item.size)
        if Item.objects.exclude(pk=item.pk).filter(key=new_key).exists():
            raise ValidationError("item already exists (normalized match)")
        item.version += 1
        try:
            item.full_clean()
        except Exception as exc:
            raise ValidationError(str(exc)) from exc
        try:
            item.save()
        except IntegrityError as exc:
            raise ValidationError("item already exists (normalized match)") from exc
        _audit(actor, "item.updated", item, {"key": new_key})
        return item

    return transact(_work)


def archive_item(*, actor, item_id):
    permissions.require_staff(actor)

    def _work():
        assert_writes_allowed()
        item = Item.objects.select_for_update().get(pk=item_id)
        item.is_archived = True
        item.archived_at = timezone.now()
        item.version += 1
        item.save()
        _audit(actor, "item.archived", item, {})
        return item

    return transact(_work)


def reactivate_item(*, actor, item_id):
    permissions.require_staff(actor)

    def _work():
        assert_writes_allowed()
        item = Item.objects.select_for_update().get(pk=item_id)
        item.is_archived = False
        item.archived_at = None
        item.version += 1
        item.save()
        _audit(actor, "item.reactivated", item, {})
        return item

    return transact(_work)


def remaining(item_or_id) -> int:
    item = _coerce(item_or_id)
    return item.on_hand


def ledger_balance(item_id) -> int:
    """Authoritative sum of ledger deltas; must equal cached Remaining."""
    total = (
        StockMovement.objects.filter(item_id=item_id).aggregate(s=Sum("delta"))["s"]
    )
    return total or 0


def net_out(item_id) -> int:
    """Net recorded issuance: active issue lines minus nothing else.

    Reversed issues are excluded, so reversals correctly reduce net Out.
    Count corrections are reported separately and never fabricate issues.
    """
    from apps.issuing.models import IssueLine

    total = (
        IssueLine.objects.filter(item_id=item_id, issue__status="ACTIVE").aggregate(
            s=Sum("quantity")
        )["s"]
    )
    return total or 0


def _coerce(item_or_id) -> Item:
    if isinstance(item_or_id, Item):
        return item_or_id
    return Item.objects.get(pk=item_or_id)


def _audit(actor, action, item, detail):
    from apps.audit.models import AuditEvent

    AuditEvent.objects.create(
        actor=actor,
        action=action,
        target_type="item",
        target_id=str(item.pk),
        detail={"key": item.key, **detail},
    )


__all__ = [
    "create_item", "receive_stock", "adjust_count", "update_item",
    "archive_item", "reactivate_item", "remaining", "ledger_balance", "net_out",
]
