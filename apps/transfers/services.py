"""Full-bundle export/import plus row-import confirmation services."""

from __future__ import annotations

import json

from django.db import transaction
from django.utils import timezone

from apps.accounts import services as permissions
from apps.common.db import transact
from apps.common.errors import ValidationError
from apps.operations.guards import assert_writes_allowed, maintenance_mode
from apps.transfers import bundle as bundle_mod


def _iso(value) -> str:
    if not value:
        return ""
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def export_bundle(*, actor) -> bytes:
    permissions.require_admin(actor)
    from apps.audit.models import AuditEvent
    from apps.inventory.models import Item, StockMovement
    from apps.issuing.models import Issue, IssueLine
    from apps.roster.models import Missionary

    try:
        from apps.operations.models import SystemSetting
        settings_rows = [{"key": s.key, "value": s.value} for s in SystemSetting.objects.all()]
    except Exception:
        settings_rows = []
    roster_rows = [{
        "id": str(m.pk), "external_id": m.external_id, "name": m.name, "email": m.email,
        "title_prefix": m.title_prefix, "arrival_status": m.arrival_status,
        "stake": m.stake, "mission": m.mission, "language": m.language,
        "arrival_date": m.arrival_date.isoformat() if m.arrival_date else "",
        "release_date": m.release_date.isoformat() if m.release_date else "",
        "personal_email": m.personal_email,
        "intake_metadata": json.dumps(m.intake_metadata or {}),
        "is_archived": "true" if m.is_archived else "false",
        "archived_at": _iso(m.archived_at), "version": str(m.version),
    } for m in Missionary.objects.all()]
    item_rows = [{
        "id": str(i.pk), "category": i.category, "name": i.name, "size": i.size,
        "unit": i.unit, "on_hand": str(i.on_hand), "low_threshold": str(i.low_threshold),
        "is_archived": "true" if i.is_archived else "false",
        "archived_at": _iso(i.archived_at), "version": str(i.version),
        "source_remaining": "" if i.source_remaining is None else str(i.source_remaining),
        "source_corrected": "true" if i.source_corrected else "false",
    } for i in Item.objects.all()]
    issue_rows = [{
        "id": str(s.pk), "missionary_id": str(s.missionary_id),
        "missionary_name": s.missionary_name, "missionary_external_id": s.missionary_external_id,
        "issued_by_username": s.issued_by_username, "issued_at": _iso(s.issued_at),
        "note": s.note, "status": s.status, "reversed_at": _iso(s.reversed_at),
        "reversal_reason": s.reversal_reason,
        "reversed_by_username": getattr(s.reversed_by, "username", "") or "",
    } for s in Issue.objects.all()]
    line_rows = [{
        "id": str(line.pk), "issue_id": str(line.issue_id), "item_id": str(line.item_id),
        "quantity": str(line.quantity), "item_category": line.item_category,
        "item_name": line.item_name, "item_size": line.item_size,
    } for line in IssueLine.objects.all()]
    movement_rows = [{
        "id": str(mv.pk), "item_id": str(mv.item_id), "kind": mv.kind,
        "delta": str(mv.delta), "balance_after": str(mv.balance_after),
        "reason": mv.reason, "issue_id": str(mv.issue_id) if mv.issue_id else "",
        "created_by_username": mv.created_by_username or getattr(mv.created_by, "username", "") or "",
        "created_at": _iso(mv.created_at),
    } for mv in StockMovement.objects.all()]
    audit_rows = [{
        "id": str(a.pk),
        "actor_username": a.actor_username or getattr(a.actor, "username", "") or "",
        "action": a.action, "target_type": a.target_type, "target_id": a.target_id,
        "detail_json": json.dumps(a.detail or {}), "created_at": _iso(a.created_at),
    } for a in AuditEvent.objects.all()]
    actor_name = getattr(actor, "username", "")
    return bundle_mod.build_bundle(
        source=f"export:{actor_name}", settings_rows=settings_rows,
        roster_rows=roster_rows, item_rows=item_rows, issue_rows=issue_rows,
        line_rows=line_rows, movement_rows=movement_rows, audit_rows=audit_rows,
    )


def operational_empty() -> bool:
    from apps.inventory.models import Item, StockMovement
    from apps.issuing.models import Issue
    from apps.roster.models import Missionary

    return (
        Missionary.objects.count() == 0
        and Item.objects.count() == 0
        and Issue.objects.count() == 0
        and StockMovement.objects.count() == 0
    )


def import_bundle(*, actor, data: bytes, filename: str = "bundle.zip") -> dict:
    """Atomic full import into empty operational storage. Admin only."""
    permissions.require_admin(actor)
    if len(data) > 10 * 1024 * 1024:
        raise ValidationError("bundle is too large (limit 10 MB)")
    parsed = bundle_mod.parse_bundle(data)  # raises BundleError (ValueError)
    tables = parsed["tables"]
    # The maintenance window blocks routine writers; this operation is the
    # holder, so its own writes must not trip the routine write guard.
    with maintenance_mode(owner="bundle-import", reason=filename):
        def _work():
            if not operational_empty():
                raise ValidationError("full import requires empty operational data (reset first)")
            _load_tables(actor, tables)
            detail = {"file": filename, "counts": parsed["manifest"].get("counts", {})}
            if parsed["manifest"].get("migrated_from_version") is not None:
                detail["migrated_from_version"] = parsed["manifest"]["migrated_from_version"]
            _audit(actor, "transfer.bundle_imported", detail)
            return {"counts": parsed["manifest"].get("counts", {})}
        return transact(_work)


def _parse_dt(raw: str):
    if not raw:
        return None
    from django.utils.dateparse import parse_datetime
    dt = parse_datetime(raw)
    return dt


def _parse_date(raw: str):
    if not raw:
        return None
    from django.utils.dateparse import parse_date
    return parse_date(raw)


def _load_tables(actor, tables: dict) -> None:
    from django.contrib.auth import get_user_model

    from apps.audit.models import AuditEvent
    from apps.inventory.models import Item, StockMovement
    from apps.issuing.models import Issue, IssueLine
    from apps.roster.models import Missionary

    User = get_user_model()
    users = {u.username: u for u in User.objects.all()}
    with transaction.atomic():
        for row in tables["settings.csv"]:
            _upsert_setting(row)
        roster_by_id = {}
        for row in tables["roster.csv"]:
            try:
                try:
                    intake_metadata = json.loads(row.get("intake_metadata") or "{}")
                except ValueError:
                    intake_metadata = {"raw": row.get("intake_metadata", "")}
                if not isinstance(intake_metadata, dict):
                    intake_metadata = {"value": intake_metadata}
                obj = Missionary(
                    id=row["id"], external_id=row["external_id"], name=row["name"],
                    email=row.get("email", ""),
                    title_prefix=row.get("title_prefix", ""),
                    arrival_status=row.get("arrival_status", ""),
                    stake=row.get("stake", ""), mission=row.get("mission", ""),
                    language=row.get("language", ""),
                    arrival_date=_parse_date(row.get("arrival_date", "")),
                    release_date=_parse_date(row.get("release_date", "")),
                    personal_email=row.get("personal_email", ""),
                    intake_metadata=intake_metadata,
                    is_archived=row.get("is_archived") == "true",
                    archived_at=_parse_dt(row.get("archived_at", "")),
                    version=int(row.get("version") or 0),
                )
                obj.full_clean()
            except KeyError as exc:
                raise ValidationError(f"bundle roster row is missing {exc}") from exc
            except ValidationError:
                raise
            except Exception as exc:
                raise ValidationError(f"invalid bundle roster row: {exc}") from exc
            obj.save()
            roster_by_id[str(obj.pk)] = obj
        item_by_id = {}
        for row in tables["items.csv"]:
            try:
                source_remaining = (row.get("source_remaining") or "").strip()
                obj = Item(
                    id=row["id"], category=row["category"], name=row["name"],
                    size=row.get("size", ""), unit=row.get("unit", "each") or "each",
                    on_hand=int(row.get("on_hand") or 0),
                    low_threshold=int(row.get("low_threshold") or 0),
                    source_remaining=(int(source_remaining)
                                      if source_remaining != "" else None),
                    source_corrected=row.get("source_corrected") == "true",
                    is_archived=row.get("is_archived") == "true",
                    archived_at=_parse_dt(row.get("archived_at", "")),
                    version=int(row.get("version") or 0),
                )
                obj.full_clean()
            except KeyError as exc:
                raise ValidationError(f"bundle item row is missing {exc}") from exc
            except ValidationError:
                raise
            except Exception as exc:
                raise ValidationError(f"invalid bundle item row: {exc}") from exc
            obj.save()
            item_by_id[str(obj.pk)] = obj
        for row in tables["issues.csv"]:
            try:
                missionary = roster_by_id[row["missionary_id"]]
            except KeyError as exc:
                raise ValidationError(
                    f"issue {row.get('id')} references unknown missionary") from exc
            issued_by = users.get(row.get("issued_by_username", ""))
            reversed_by = users.get(row.get("reversed_by_username", ""))
            status = row.get("status", "ACTIVE") or "ACTIVE"
            try:
                issue = Issue(
                    id=row["id"], missionary=missionary,
                    missionary_name=row.get("missionary_name", missionary.name),
                    missionary_external_id=row.get("missionary_external_id", missionary.external_id),
                    issued_by=issued_by,
                    issued_by_username=row.get("issued_by_username", ""),
                    note=row.get("note", ""), status=status,
                    reversed_at=_parse_dt(row.get("reversed_at", "")),
                    reversal_reason=row.get("reversal_reason", ""),
                    reversed_by=reversed_by,
                    idempotency_key=f"bundle-{row['id']}",
                )
                issue.full_clean()
            except ValidationError:
                raise
            except Exception as exc:
                raise ValidationError(f"invalid bundle issue row: {exc}") from exc
            issue.save()
            issued_at = _parse_dt(row.get("issued_at", ""))
            if issued_at is not None:
                Issue.objects.filter(pk=issue.pk).update(issued_at=issued_at)
        for row in tables["issue_lines.csv"]:
            try:
                IssueLine.objects.create(
                    id=row["id"], issue_id=row["issue_id"], item_id=row["item_id"],
                    quantity=int(row["quantity"]),
                    item_category=row.get("item_category", ""),
                    item_name=row.get("item_name", ""),
                    item_size=row.get("item_size", ""),
                )
            except KeyError as exc:
                raise ValidationError(f"bundle line row is missing {exc}") from exc
            except Exception as exc:
                raise ValidationError(f"invalid bundle line row: {exc}") from exc
        for row in tables["movements.csv"]:
            try:
                movement = StockMovement(
                    id=row["id"], item_id=row["item_id"], kind=row["kind"],
                    delta=int(row.get("delta") or 0),
                    balance_after=int(row.get("balance_after") or 0),
                    reason=row.get("reason", ""),
                    issue_id=row.get("issue_id") or None,
                    created_by=users.get(row.get("created_by_username", "")),
                    created_by_username=row.get("created_by_username", ""),
                )
                movement.full_clean()
            except ValidationError:
                raise
            except Exception as exc:
                raise ValidationError(f"invalid bundle movement row: {exc}") from exc
            movement.save()
            created_at = _parse_dt(row.get("created_at", ""))
            if created_at is not None:
                StockMovement.objects.filter(pk=movement.pk).update(created_at=created_at)
        for row in tables["audits.csv"]:
            try:
                detail = json.loads(row.get("detail_json") or "{}")
            except ValueError:
                detail = {"raw": row.get("detail_json", "")}
            try:
                event = AuditEvent(
                    id=row["id"],
                    actor=users.get(row.get("actor_username", "")),
                    actor_username=row.get("actor_username", ""),
                    action=row.get("action", ""),
                    target_type=row.get("target_type", ""),
                    target_id=row.get("target_id", ""),
                    detail=detail,
                )
                event.full_clean()
            except ValidationError:
                raise
            except Exception as exc:
                raise ValidationError(f"invalid bundle audit row: {exc}") from exc
            event.save()
            created_at = _parse_dt(row.get("created_at", ""))
            if created_at is not None:
                AuditEvent.objects.filter(pk=event.pk).update(created_at=created_at)


def _upsert_setting(row: dict) -> None:
    try:
        from apps.operations.models import SystemSetting
    except Exception:
        return
    key = (row.get("key") or "").strip()
    if not key:
        return
    SystemSetting.objects.update_or_create(key=key, defaults={"value": row.get("value", "")})


def _audit(actor, action, detail):
    from apps.audit.models import AuditEvent

    AuditEvent.objects.create(
        actor=actor, action=action, target_type="transfer",
        target_id="bundle", detail=detail,
    )


def confirm_row_import(*, actor, preview, selected: list[int] | None) -> dict:
    """Apply preview rows atomically.

    ``selected=None`` means every valid row; an explicit (possibly empty) set
    applies exactly those rows, so confirming with nothing checked writes
    nothing. The preview is single-use, owned by its creator (administrators
    may apply anyone's), expires after 24 hours, and the source revision is
    re-checked transactionally against the exact per-record token while
    holding a row lock — concurrent confirms and lower-version edits both
    fail instead of double-applying or silently overwriting.
    """
    from django.db import transaction

    from apps.inventory import services as inv
    from apps.roster import services as roster_services
    from apps.transfers.models import RowImportPreview
    from apps.transfers.row_imports import PREVIEW_TTL_SECONDS, current_revision

    permissions.require_staff(actor)

    def _work():
        assert_writes_allowed()
        try:
            locked = RowImportPreview.objects.select_for_update().get(pk=preview.pk)
        except RowImportPreview.DoesNotExist as exc:
            from apps.common.errors import StaleVersionError
            raise StaleVersionError("preview was already applied or expired") from exc
        if (locked.created_by_id is not None
                and locked.created_by_id != getattr(actor, "pk", None)
                and not permissions.is_admin(actor)):
            raise ValidationError("this preview belongs to another staff member")
        from django.utils import timezone as _tz
        age = (_tz.now() - locked.created_at).total_seconds()
        if age > PREVIEW_TTL_SECONDS:
            locked.delete()
            from apps.common.errors import StaleVersionError
            raise StaleVersionError("preview expired; upload again")
        mode = locked.mode
        if mode == "replace":
            permissions.require_admin(actor)
        if locked.base_revision != current_revision(locked.kind):
            from apps.common.errors import StaleVersionError
            raise StaleVersionError("source data changed since preview; re-upload and review again")
        rows = locked.rows
        valid = [r for r in rows if not r.get("error")]
        if mode == "replace" and any(r.get("error") for r in rows):
            raise ValidationError("replacement requires every row valid; fix errors or use add/update")
        if selected is not None and mode != "add_update":
            raise ValidationError("partial approval is only allowed for add/update imports")
        wanted = set(selected) if selected is not None else {r["row"] for r in valid}
        applied = {"created": 0, "updated": 0, "adjusted": 0, "archived": 0}
        if locked.kind == "roster":
            _apply_roster(actor, roster_services, valid, wanted, mode, applied,
                          key_prefix=f"imp-{locked.token}")
        else:
            _apply_inventory(actor, inv, valid, wanted, mode, applied,
                             key_prefix=f"imp-{locked.token}")
        _audit(actor, "transfer.rows_imported",
               {"kind": locked.kind, "mode": mode, **applied})
        locked.delete()
        return applied

    return transact(_work)


def _apply_roster(actor, roster_services, valid, wanted, mode, applied, key_prefix):
    from apps.roster.models import Missionary

    for entry in valid:
        if entry["row"] not in wanted:
            continue
        existing = Missionary.objects.filter(external_id=entry["external_id"]).first()
        note = (entry.get("reason") or "").strip()
        profile = {
            "title_prefix": entry.get("title_prefix", ""),
            "arrival_status": entry.get("arrival_status", ""),
            "stake": entry.get("stake", ""),
            "mission": entry.get("mission", ""),
            "language": entry.get("language", ""),
            "personal_email": entry.get("personal_email", ""),
        }
        for date_field in ("arrival_date", "release_date"):
            raw = (entry.get(date_field) or "").strip()
            if raw:
                from django.utils.dateparse import parse_date
                try:
                    profile[date_field] = parse_date(raw)
                except ValueError:
                    profile[date_field] = None
        if existing is None:
            roster_services.create_missionary(
                actor=actor, external_id=entry["external_id"],
                name=entry["name"], email=entry.get("email", ""),
                note=note, **profile)
            applied["created"] += 1
        else:
            if existing.is_archived:
                roster_services.reactivate_missionary(actor=actor, missionary_id=existing.pk)
                existing = Missionary.objects.get(pk=existing.pk)
            changed = (
                existing.name != entry["name"]
                or existing.email != entry.get("email", "")
                or any((getattr(existing, f) or "") != (profile[f] or "")
                       for f in profile if f not in ("arrival_date", "release_date"))
            )
            if not changed:
                for date_field in ("arrival_date", "release_date"):
                    want = profile.get(date_field)
                    if want is not None and getattr(existing, date_field) != want:
                        changed = True
            if changed:
                roster_services.update_missionary(
                    actor=actor, missionary_id=existing.pk,
                    expected_version=existing.version,
                    name=entry["name"], email=entry.get("email", ""),
                    note=note, **profile)
                applied["updated"] += 1
    if mode == "replace":
        keep = {v["external_id"] for v in valid}
        for obj in Missionary.objects.filter(is_archived=False).exclude(external_id__in=keep):
            roster_services.archive_missionary(actor=actor, missionary_id=obj.pk)
            applied["archived"] += 1


def _apply_inventory(actor, inv, valid, wanted, mode, applied, key_prefix):
    from apps.inventory.models import Item, item_key

    for entry in valid:
        if entry["row"] not in wanted:
            continue
        key = item_key(entry["category"], entry["name"], entry.get("size", ""))
        existing = Item.objects.filter(key=key).first()
        reason = (entry.get("reason") or "").strip() or "spreadsheet import"
        stable_key = f"{key_prefix}-{entry['row']}"
        if existing is None:
            item = inv.create_item(
                actor=actor, category=entry["category"], name=entry["name"],
                size=entry.get("size", ""), opening_qty=entry["counted"] or 0,
                reason=reason, idempotency_key=stable_key,
            )
            applied["created"] += 1
            if entry.get("low_threshold") is not None and entry["low_threshold"] != item.low_threshold:
                inv.update_item(actor=actor, item_id=item.pk,
                                expected_version=item.version,
                                low_threshold=entry["low_threshold"])
        else:
            if existing.is_archived:
                inv.reactivate_item(actor=actor, item_id=existing.pk)
                existing = Item.objects.get(pk=existing.pk)
            changed = False
            if entry.get("low_threshold") is not None and entry["low_threshold"] != existing.low_threshold:
                existing = inv.update_item(actor=actor, item_id=existing.pk,
                                           expected_version=existing.version,
                                           low_threshold=entry["low_threshold"])
                changed = True
            if entry.get("counted") is not None and entry["counted"] != existing.on_hand:
                inv.adjust_count(actor=actor, item_id=existing.pk,
                                 counted_qty=entry["counted"], reason=reason,
                                 expected_version=existing.version,
                                 idempotency_key=f"{stable_key}-adj")
                applied["adjusted"] += 1
            elif changed:
                applied["updated"] += 1
    if mode == "replace":
        keep = {item_key(v["category"], v["name"], v.get("size", "")) for v in valid}
        for obj in Item.objects.filter(is_archived=False).exclude(key__in=keep):
            inv.archive_item(actor=actor, item_id=obj.pk)
            applied["archived"] += 1
