"""Centralized issuing writes: atomic issues, proposals, whole-issue reversals.

A full issue commits all accepted lines or none. When stock is insufficient
the service raises InsufficientStockError with an explicit revised proposal
and writes nothing; the caller may confirm the revised list, which rechecks
availability on the server. Idempotency keys prevent replay: repeating a key
returns the original issue without a second write. Reversal restores stock for
a whole issue exactly once, with a reason, even for archived records.
"""

from __future__ import annotations
import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts import services as permissions
from apps.common.db import transact
from apps.common.errors import (
    AlreadyReversedError,
    InsufficientStockError,
    ValidationError,
)
from apps.operations.guards import assert_writes_allowed


def _aggregate(lines) -> dict:
    aggregated: dict = {}
    for entry in lines or []:
        item_id = entry.get("item_id") or entry.get("item")
        if item_id is None or str(item_id).strip() == "":
            raise ValidationError("issue line item is required")
        try:
            qty = int(entry.get("quantity", 0))
        except (TypeError, ValueError) as exc:
            raise ValidationError("issue quantities must be whole numbers") from exc
        if qty <= 0:
            raise ValidationError("issue quantities must be positive")
        key = str(item_id)
        try:
            uuid.UUID(key)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError("choose a valid item") from exc
        aggregated[key] = aggregated.get(key, 0) + qty
        if len(aggregated) > 50:
            raise ValidationError("an issue can contain at most 50 distinct items")
    if not aggregated:
        raise ValidationError("at least one line is required")
    return aggregated


def preview_issue(*, actor, lines) -> dict:
    """Non-writing availability check. Returns {available, shortages}."""
    permissions.require_staff(actor)
    from apps.inventory.models import Item

    aggregated = _aggregate(lines)
    items = {str(i.pk): i for i in Item.objects.filter(pk__in=list(aggregated))}
    available = {}
    shortages = []
    for item_id, requested in aggregated.items():
        item = items.get(item_id)
        if item is None:
            raise ValidationError("item not found")
        available[item_id] = item.on_hand
        if item.on_hand < requested:
            shortages.append(
                {
                    "item_id": item_id,
                    "requested": requested,
                    "available": item.on_hand,
                }
            )
    proposal = [
        {"item_id": item_id, "quantity": min(qty, available[item_id])}
        for item_id, qty in aggregated.items()
        if available[item_id] > 0
    ]
    return {"available": available, "shortages": shortages, "proposal": proposal}


def create_issue(*, actor, missionary_id, lines, idempotency_key: str, note: str = ""):
    permissions.require_staff(actor)
    if not idempotency_key or not str(idempotency_key).strip():
        raise ValidationError("idempotency key is required")
    idempotency_key = str(idempotency_key).strip()
    aggregated = _aggregate(lines)

    from apps.inventory.models import Item, StockMovement
    from apps.issuing.models import Issue, IssueLine
    from apps.roster.models import Missionary
    try:
        uuid.UUID(str(missionary_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("choose a valid missionary") from exc

    def _work():
        assert_writes_allowed()
        existing = Issue.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            if existing.issued_by_id != actor.pk:
                raise ValidationError("this request belongs to another staff member")
            return existing
        try:
            missionary = Missionary.objects.select_for_update().get(pk=missionary_id)
        except Missionary.DoesNotExist as exc:
            raise ValidationError("missionary not found") from exc
        if missionary.is_archived:
            raise ValidationError("cannot issue to an archived roster entry")

        items = {
            str(i.pk): i
            for i in Item.objects.select_for_update().filter(
                pk__in=list(aggregated)
            )
        }
        shortages = []
        for item_id, requested in aggregated.items():
            item = items.get(item_id)
            if item is None:
                raise ValidationError("item not found")
            if item.is_archived:
                raise ValidationError(f"item is archived: {item}")
            if item.on_hand < requested:
                shortages.append(
                    {
                        "item_id": item_id,
                        "requested": requested,
                        "available": item.on_hand,
                    }
                )
        if shortages:
            proposal = [
                {"item_id": item_id, "quantity": min(requested, items[item_id].on_hand)}
                for item_id, requested in aggregated.items()
                if items[item_id].on_hand > 0
            ]
            raise InsufficientStockError(shortages=shortages, proposal=proposal)

        try:
            with transaction.atomic():
                issue = Issue.objects.create(
                    missionary=missionary,
                    missionary_name=missionary.name,
                    missionary_external_id=missionary.external_id,
                    issued_by=actor,
                    issued_by_username=getattr(actor, "username", ""),
                    note=(note or "").strip(),
                    idempotency_key=idempotency_key,
                )
        except IntegrityError:
            # Lost an idempotency race: the other writer won; the savepoint
            # above keeps this transaction usable so we can return its row.
            return Issue.objects.get(idempotency_key=idempotency_key)
        for item_id, qty in aggregated.items():
            item = items[item_id]
            IssueLine.objects.create(
                issue=issue,
                item=item,
                quantity=qty,
                item_category=item.category,
                item_name=item.name,
                item_size=item.size,
            )
            StockMovement.objects.create(
                item=item,
                kind=StockMovement.ISSUE,
                delta=-qty,
                balance_after=item.on_hand - qty,
                reason=f"issue {issue.pk}",
                issue=issue,
                created_by=actor,
            )
            item.on_hand -= qty
            item.version += 1
            item.save()
        _audit(actor, "issue.created", issue, {"lines": len(aggregated)})
        return issue

    return transact(_work)


def reverse_issue(*, actor, issue_id, reason: str):
    """Reverse a whole issue once. Archived items/missionaries stay reversible."""
    permissions.require_staff(actor)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("reversal reason is required")

    from apps.inventory.models import Item, StockMovement
    from apps.issuing.models import Issue

    def _work():
        assert_writes_allowed()
        try:
            issue = Issue.objects.select_for_update().get(pk=issue_id)
        except Issue.DoesNotExist as exc:
            raise ValidationError("issue not found") from exc
        if issue.status == Issue.REVERSED:
            raise AlreadyReversedError("issue was already reversed")
        lines = list(issue.lines.select_related("item").all())
        item_ids = [line.item_id for line in lines]
        items = {
            str(i.pk): i
            for i in Item.objects.select_for_update().filter(pk__in=item_ids)
        }
        for line in lines:
            item = items[str(line.item_id)]
            StockMovement.objects.create(
                item=item,
                kind=StockMovement.REVERSAL,
                delta=line.quantity,
                balance_after=item.on_hand + line.quantity,
                reason=reason,
                issue=issue,
                created_by=actor,
            )
            item.on_hand += line.quantity
            item.version += 1
            item.save()
        issue.status = Issue.REVERSED
        issue.reversed_at = timezone.now()
        issue.reversal_reason = reason
        issue.reversed_by = actor
        issue.reversed_by_username = getattr(actor, "username", "") or ""
        issue.version += 1
        issue.save()
        _audit(actor, "issue.reversed", issue, {"reason": reason})
        return issue

    return transact(_work)


def _audit(actor, action, issue, detail):
    from apps.audit.models import AuditEvent

    AuditEvent.objects.create(
        actor=actor,
        action=action,
        target_type="issue",
        target_id=str(issue.pk),
        detail=detail,
    )
