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
    """Remove /* */ and line comments, leaving string contents intact."""
    out: list[str] = []
    i, n = 0, len(text)
    quote = ""
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            end = text.find("\n", i + 2)
            i = n if end == -1 else end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _loads_lenient(raw: str):
    """Parse SEED JSON, tolerating single quotes and trailing commas.

    Double-quoted content (including apostrophes such as O'Brien) is never
    rewritten: only single-quoted strings outside other strings are
    converted, with escapes honored.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    out: list[str] = []
    i, n = 0, len(raw)
    in_str = False
    quote = ""
    while i < n:
        ch = raw[i]
        if in_str:
            if ch == "\\" and i + 1 < n:
                nxt = raw[i + 1]
                if quote == "'":
                    if nxt == "'":
                        out.append("'")
                    elif nxt == '"':
                        out.append('\\"')
                    else:
                        out.append(ch + nxt)
                else:
                    out.append(ch + nxt)
                i += 2
                continue
            if ch == quote:
                out.append('"' if quote == "'" else ch)
                in_str = False
                i += 1
                continue
            if quote == "'" and ch == '"':
                out.append('\\"')
            else:
                out.append(ch)
            i += 1
            continue
        if ch in ("'", '"'):
            in_str = True
            quote = ch
            out.append('"')
            i += 1
            continue
        out.append(ch)
        i += 1
    if in_str:
        raise ValueError("unterminated string in SEED block")
    fixed = re.sub(r",\s*([}\]])", r"\1", "".join(out))
    return json.loads(fixed)


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
                return _loads_lenient(raw)
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
    email = str(_first(p, "email", "emailAddress", default="")).strip()
    # Preserve source roster context; anything unrecognized is kept verbatim
    # in intake_metadata so a later migration can interpret it. The approved
    # source uses the exact original keys arrived, homeStake, missionArrival,
    # missionRelease, tLanguage and type: all are mapped to agreed fields
    # below and listed here so they are never silently dropped.
    known = {"external_id", "externalid", "extid", "id", "name", "fullname",
             "full_name", "email", "emailaddress", "titleprefix", "title_prefix",
             "title", "prefix", "type", "arrivalstatus", "arrival_status", "status",
             "arrived", "stake", "homestake", "home_stake", "mission", "language",
             "tlanguage", "t_language", "arrivaldate", "arrival_date",
             "missionarrival", "mission_arrival", "releasedate", "release_date",
             "missionrelease", "mission_release", "personalemail", "personal_email"}
    title = str(_first(p, "title_prefix", "titlePrefix", "title", "prefix", "type",
                       default="")).strip()
    # Split an "Elder Jane Doe" style name into prefix + remainder.
    if not title:
        for candidate in ("Elder", "Sister"):
            if name.startswith(candidate + " "):
                title = candidate
                name = name[len(candidate) + 1:].strip()
                break
    arrival_status = str(_first(p, "arrival_status", "arrivalStatus", "status",
                                "arrived", default="")).strip()
    stake = str(_first(p, "stake", "homeStake", "home_stake", default="")).strip()
    mission = str(_first(p, "mission", default="")).strip()
    language = str(_first(p, "language", "tLanguage", "t_language", default="")).strip()
    arrival_raw = _first(p, "arrival_date", "arrivalDate", "missionArrival",
                         "mission_arrival", default="")
    release_raw = _first(p, "release_date", "releaseDate", "missionRelease",
                         "mission_release", default="")
    arrival_date = _clean_date(arrival_raw)
    release_date = _clean_date(release_raw)
    personal_email = str(_first(p, "personal_email", "personalEmail", default="")).strip()
    metadata = {k: v for k, v in p.items()
                if k.lower() not in known and v not in (None, "")}
    # Never silently lose an unparseable date: keep the raw source text in
    # metadata so the value survives conversion even when it is not a valid
    # ISO date for the typed field.
    if str(arrival_raw or "").strip() and not arrival_date:
        metadata.setdefault("arrival_date_raw", str(arrival_raw).strip())
    if str(release_raw or "").strip() and not release_date:
        metadata.setdefault("release_date_raw", str(release_raw).strip())
    return {"external_id": ext or f"SEED-{n + 1}", "name": name, "email": email,
            "title_prefix": title, "arrival_status": arrival_status,
            "stake": stake, "mission": mission, "language": language,
            "arrival_date": arrival_date, "release_date": release_date,
            "personal_email": personal_email, "intake_metadata": metadata}


def _clean_date(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    from django.utils.dateparse import parse_date, parse_datetime
    try:
        parsed = parse_date(text)
    except ValueError:
        parsed = None
    if parsed is not None:
        return parsed.isoformat()
    # Tolerate datetime strings and common slash forms without silently
    # dropping the value: fall back to datetime parsing, then explicit
    # MM/DD/YYYY and YYYY/MM/DD shapes.
    try:
        dt = parse_datetime(text)
    except ValueError:
        dt = None
    if dt is not None:
        return dt.date().isoformat()
    import datetime as _dt
    for fmt in ("%m/%d/%Y", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return _dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _to_int(value, default=0) -> int:
    try:
        text = str(value).strip()
        if text == "":
            return default
        number = float(text)
        if number != int(number):
            # Fractional seed counts are preserved as metadata, not silently
            # truncated: the caller records the original.
            raise ValueError("fractional")
        return int(number)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return default


def _raw_text(i: dict, *keys) -> str:
    for k in keys:
        if k in i and i[k] not in (None, ""):
            return str(i[k]).strip()
    for k in keys:
        for existing in i:
            if existing.lower() == k.lower() and i[existing] not in (None, ""):
                return str(i[existing]).strip()
    return ""


def _norm_item(i: dict, n: int) -> dict:
    if not isinstance(i, dict):
        i = {"name": str(i)}
    category = str(_first(i, "category", "cat", "group", default="General")).strip() or "General"
    name = str(_first(i, "name", "item", "itemName", "product", "title", default=f"Seed Item {n + 1}")).strip()
    size = str(_first(i, "size", "variant", default="")).strip()
    # Opening-stock selection: `remain` is the approved source's quantity
    # key and must win like `remaining`. Case-insensitive fallback in
    # _first covers Remaining/Stock/OnHand capitalizations.
    remaining = _first(i, "remaining", "remain", "stock", "onHand", "on_hand",
                       "qty", "quantity", default=0)
    remaining_qty = _to_int(remaining, 0)
    out = _to_int(_first(i, "out", "givenOut", "issued", default=""), 0) if _first(i, "out", "givenOut", "issued", default="") != "" else 0
    # Stock-minus-Out form: when explicit stock+out given without any
    # remaining-style key (remaining/remain/onHand/qty/quantity, any case).
    lowered = {str(k).lower(): k for k in i}
    has_stock = any(k in lowered for k in ("stock",)) and str(i[lowered["stock"]]).strip() != ""
    has_remaining = any(k in lowered for k in (
        "remaining", "remain", "onhand", "on_hand", "qty", "quantity"))
    if has_stock and not has_remaining and any(k in lowered for k in ("out", "givenout", "issued")):
        stock_key = lowered.get("stock", "stock")
        stock_val = i.get(stock_key, i.get("stock", i.get("Stock", 0)))
        remaining_qty = _to_int(stock_val, 0) - out
    low = _to_int(_first(i, "low_threshold", "lowThreshold", "low", "threshold",
                            "reorder", "reorderLevel", "reorder_level",
                            "reorderlevel", "minimum", "alert_level", default=0), 0)
    remain_raw = _raw_text(i, "remain", "remaining", "stock", "onHand", "on_hand",
                           "qty", "quantity")
    low_raw = _raw_text(i, "lowThreshold", "low_threshold", "low", "threshold",
                        "reorder", "reorderLevel")
    return {
        "category": category, "name": name or f"Seed Item {n + 1}", "size": size,
        "remaining": remaining_qty, "out": out, "low_threshold": max(0, low),
        "seed_index": n, "remain_raw": remain_raw, "low_raw": low_raw,
    }


def seed_to_bundle_rows(seed) -> dict:
    """Convert normalized seed to bundle row dicts with correction metadata."""
    roster, items, dists = normalize_seed(seed)
    roster_rows = [
        {"id": str(uuid.uuid4()), "external_id": p["external_id"], "name": p["name"],
         "email": p.get("email", ""),
         "title_prefix": p.get("title_prefix", ""),
         "arrival_status": p.get("arrival_status", ""),
         "stake": p.get("stake", ""), "mission": p.get("mission", ""),
         "language": p.get("language", ""),
         "arrival_date": p.get("arrival_date", ""),
         "release_date": p.get("release_date", ""),
         "personal_email": p.get("personal_email", ""),
         "intake_metadata": json.dumps(p.get("intake_metadata") or {}, sort_keys=True),
         "is_archived": "false", "archived_at": "",
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
                    "remain_raw": it.get("remain_raw", ""),
                    "low_raw": it.get("low_raw", ""),
                }),
                "created_at": "2026-01-01T00:00:00+00:00",
            })
    return {
        "roster": roster_rows, "items": item_rows, "issues": [], "lines": [],
        "movements": movement_rows, "audits": audit_rows,
        "settings": [], "distributions": list(dists),
    }
