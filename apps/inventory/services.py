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
):
    permissions.require_staff(actor)
    assert_writes_allowed()
    category = (category or "").strip()
    name = (name or "").strip()
    size = (size or "").strip()
    if not category or not name:
        raise ValidationError("category and name are required")
    if opening_qty < 0:
        raise NegativeBalanceError("opening quantity cannot be negative")
    if idempotency_key:
        existing = StockMovement.objects.filter(
            idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return Item.objects.get(pk=existing.item_id)
    with transaction.atomic():
        key = item_key(category, name, size)
        if Item.objects.filter(key=key).exists():
            raise ValidationError("item already exists (normalized match)")
        item = Item(
            category=category, name=name, size=size, on_hand=opening_qty
        )
        item.save()
        StockMovement.objects.create(
            item=item,
            kind=StockMovement.OPENING,
            delta=opening_qty,
            balance_after=opening_qty,
            reason=_require_reason(reason, "opening"),
            created_by=actor,
            idempotency_key=idempotency_key,
        )
        _audit(actor, "item.created", item, {"opening_qty": opening_qty})
        return item


def receive_stock(*, actor, item_id, quantity: int, reason: str, idempotency_key=None):
    """Add stock. Quantity must be positive; reason is mandatory."""
    permissions.require_staff(actor)
    if quantity is None or int(quantity) <= 0:
        raise ValidationError("receipt quantity must be positive")
    quantity = int(quantity)
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
    if counted_qty is None or int(counted_qty) < 0:
        raise NegativeBalanceError("counted quantity cannot be negative")
    counted_qty = int(counted_qty)
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
            raise NegativeBalanceError("count would create negative stock") from exc
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
    assert_writes_allowed()
    with transaction.atomic():
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
        item.save()
        _audit(actor, "item.updated", item, {"key": new_key})
        return item


def archive_item(*, actor, item_id):
    permissions.require_staff(actor)
    assert_writes_allowed()
    with transaction.atomic():
        item = Item.objects.select_for_update().get(pk=item_id)
        item.is_archived = True
        item.archived_at = timezone.now()
        item.version += 1
        item.save()
        _audit(actor, "item.archived", item, {})
        return item


def reactivate_item(*, actor, item_id):
    permissions.require_staff(actor)
    assert_writes_allowed()
    with transaction.atomic():
        item = Item.objects.select_for_update().get(pk=item_id)
        item.is_archived = False
        item.archived_at = None
        item.version += 1
        item.save()
        _audit(actor, "item.reactivated", item, {})
        return item


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


__all__ = ["normalize_text"]
