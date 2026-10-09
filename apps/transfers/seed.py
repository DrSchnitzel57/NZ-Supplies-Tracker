"""Extract embedded SEED JSON from the local HTML without executing it.

Supports the documented source shapes: an object with roster-like keys
(missionaries/people/roster) and item-like keys (items/inventory), each a
list. Roster entries accept external_id/id/extId + name/fullName +
email; items accept category/cat + name/item + size + quantity keys
(remaining/stock/onHand/qty/quantity) plus optional out.
"""

from __future__ import annotations

import json
import re
import uuid


def _strip_js_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    return text


def extract_seed_json(html: str) -> dict | list:
    """Find a SEED assignment and return the decoded JSON value."""
    match = re.search(r"\bSEED\b", html)
    if not match:
        raise ValueError("no SEED block found in source HTML")
    tail = html[match.end():]
    # Skip to the first = then first { or [.
    eq = tail.find("=")
    if eq == -1:
        raise ValueError("no SEED assignment found")
    tail = tail[eq + 1:]
    start = None
    for i, ch in enumerate(tail):
        if ch in "{[":
            start = i
            break
    if start is None:
        raise ValueError("no SEED JSON value found")
    opener = tail[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    quote = ""
    for i in range(start, len(tail)):
        ch = tail[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str = True
            quote = ch
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                raw = tail[start:i + 1]
                raw = _strip_js_comments(raw)
                # Tolerate single quotes and trailing commas conservatively.
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    fixed = re.sub(r"'([^']*)'", r'"\1"', raw)
                    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
                    return json.loads(fixed)
    raise ValueError("unterminated SEED JSON value")


def _first(d: dict, *keys, default=""):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    for k in keys:
        for existing in d:
            if existing.lower() == k.lower() and d[existing] not in (None, ""):
                return d[existing]
    return default


def normalize_seed(seed) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (roster, items, distributions) normalized dicts."""
    if isinstance(seed, list):
        # Ambiguous bare list: treat as items only.
        return [], [_norm_item(i, n) for n, i in enumerate(seed)], []
    if not isinstance(seed, dict):
        raise ValueError("unsupported SEED shape")
    roster_src = _first(seed, "missionaries", "people", "roster", default=[])
    items_src = _first(seed, "items", "inventory", default=[])
    dist_src = _first(seed, "distributions", "issues", "history", default=[])
    if not isinstance(roster_src, list):
        roster_src = []
    if not isinstance(items_src, list):
        items_src = []
    if not isinstance(dist_src, list):
        dist_src = []
    roster = [_norm_person(p, n) for n, p in enumerate(roster_src)]
    items = [_norm_item(i, n) for n, i in enumerate(items_src)]
    return roster, items, list(dist_src)


def _norm_person(p: dict, n: int) -> dict:
    if not isinstance(p, dict):
        p = {"name": str(p)}
    ext = str(_first(p, "external_id", "externalId", "extId", "id", default=f"SEED-{n + 1}")).strip()
    name = str(_first(p, "name", "fullName", "full_name", default=f"Seed Person {n + 1}")).strip()
    email = str(_first(p, "email", default="")).strip()
    return {"external_id": ext or f"SEED-{n + 1}", "name": name, "email": email}


def _to_int(value, default=0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError, AttributeError):
        return default


def _norm_item(i: dict, n: int) -> dict:
    if not isinstance(i, dict):
        i = {"name": str(i)}
    category = str(_first(i, "category", "cat", "group", default="General")).strip() or "General"
    name = str(_first(i, "name", "item", "itemName", "product", "title", default=f"Seed Item {n + 1}")).strip()
    size = str(_first(i, "size", "variant", default="")).strip()
    remaining = _first(i, "remaining", "stock", "onHand", "on_hand", "qty", "quantity", default=0)
    remaining_qty = _to_int(remaining, 0)
    out = _to_int(_first(i, "out", "givenOut", "issued", default=""), 0) if _first(i, "out", "givenOut", "issued", default="") != "" else 0
    # Stock-minus-Out form: when explicit stock+out given without remaining.
    has_stock = any(k in i and str(i[k]).strip() != "" for k in ("stock", "Stock"))
    has_remaining = any(k in i for k in ("remaining", "Remaining", "onHand", "on_hand", "qty", "quantity"))
    if has_stock and not has_remaining and any(k in i for k in ("out", "Out", "givenOut")):
        remaining_qty = _to_int(i.get("stock", i.get("Stock", 0)), 0) - out
    low = _to_int(_first(i, "low_threshold", "low", "threshold", default=0), 0)
    return {
        "category": category, "name": name or f"Seed Item {n + 1}", "size": size,
        "remaining": remaining_qty, "out": out, "low_threshold": max(0, low),
        "seed_index": n,
    }


def seed_to_bundle_rows(seed) -> dict:
    """Convert normalized seed to bundle row dicts with correction metadata."""
    roster, items, dists = normalize_seed(seed)
    roster_rows = [
        {"id": str(uuid.uuid4()), "external_id": p["external_id"], "name": p["name"],
         "email": p.get("email", ""), "is_archived": "false", "archived_at": "",
         "version": "0"}
        for p in roster
    ]
    item_rows: list[dict] = []
    movement_rows: list[dict] = []
    audit_rows: list[dict] = []
    for it in items:
        item_id = str(uuid.uuid4())
        raw = it["remaining"]
        corrected = raw < 0
        live = 0 if corrected else raw
        item_rows.append({
            "id": item_id, "category": it["category"], "name": it["name"], "size": it.get("size", ""),
            "unit": "each", "on_hand": str(live), "low_threshold": str(it.get("low_threshold", 0)),
            "is_archived": "false", "archived_at": "", "version": "0",
            "source_remaining": str(raw) if corrected else "",
            "source_corrected": "true" if corrected else "false",
        })
        movement_rows.append({
            "id": str(uuid.uuid4()), "item_id": item_id, "kind": "OPENING",
            "delta": str(live), "balance_after": str(live),
            "reason": "seed opening balance (corrected to zero)" if corrected else "seed opening balance",
            "issue_id": "", "created_by_username": "", "created_at": "2026-01-01T00:00:00+00:00",
        })
        if corrected:
            audit_rows.append({
                "id": str(uuid.uuid4()), "actor_username": "", "action": "seed.correction",
                "target_type": "item", "target_id": item_id,
                "detail_json": json.dumps({
                    "source_remaining": raw, "corrected_to": 0,
                    "authorized": True,
                    "note": "source-negative opening balance zeroed on import",
                    "category": it["category"], "name": it["name"], "size": it.get("size", ""),
                }),
                "created_at": "2026-01-01T00:00:00+00:00",
            })
    return {
        "roster": roster_rows, "items": item_rows, "issues": [], "lines": [],
        "movements": movement_rows, "audits": audit_rows,
        "settings": [], "distributions": list(dists),
    }
