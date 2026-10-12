"""Individual roster/inventory CSV/XLSX parsing, preview and confirmation.

Never evaluates formulas: any decoded cell still starting with '=' is a row
error. Macro/legacy workbooks are rejected; only .csv and plain .xlsx.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
import zipfile

from apps.inventory.models import item_key
from apps.transfers.bundle import safe_decode

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_ROWS = 2000
MAX_COLUMNS = 64
MAX_CELL_CHARS = 4000
MAX_CELLS = 200_000
#: Row-import previews expire after 24 hours and are single-use.
PREVIEW_TTL_SECONDS = 24 * 3600
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}
REJECTED_EXTENSIONS = {".xlsm", ".xls", ".xlsb", ".ods", ".zip"}

ROSTER_ALIASES = {
    "external_id": {"external_id", "external id", "externalid", "ext id", "extid",
                    "ext_id", "id", "missionary id", "missionaryid"},
    "name": {"name", "full name", "fullname", "full_name", "missionary",
             "missionary name"},
    "email": {"email", "e-mail", "email address"},
    "title_prefix": {"title_prefix", "title prefix", "titleprefix", "prefix", "elder/sister", "title", "type"},
    "arrival_status": {"arrival_status", "arrival status", "arrivalstatus", "status", "arrived"},
    "stake": {"stake", "home stake", "homestake", "home_stake"},
    "mission": {"mission"},
    "language": {"language", "tlanguage", "t language", "t_language"},
    "arrival_date": {"arrival_date", "arrival date", "arrivaldate", "mission arrival",
                     "missionarrival", "mission_arrival"},
    "release_date": {"release_date", "release date", "releasedate", "mission release",
                     "missionrelease", "mission_release"},
    "personal_email": {"personal_email", "personal email", "personalemail"},
    "reason": {"reason", "note", "comment"},
}
INVENTORY_ALIASES = {
    "category": {"category", "cat", "group"},
    "name": {"name", "item", "item name", "itemname", "item_name", "product", "title"},
    "size": {"size", "variant"},
    "remaining": {"remaining", "remain", "on hand", "onhand", "on_hand", "available",
                  "qty", "quantity", "count"},
    "stock": {"stock"},
    "out": {"out", "given out", "givenout", "issued", "total out"},
    "low_threshold": {"low_threshold", "lowthreshold", "low", "low stock",
                      "threshold", "reorder", "reorder level", "minimum", "alert level"},
    "reason": {"reason", "note", "comment"},
}


def _norm_header(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _map_headers(headers: list[str], aliases: dict[str, set[str]]) -> dict[str, int]:
    normed = [_norm_header(h) for h in headers]
    mapping: dict[str, int] = {}
    for field, names in aliases.items():
        for i, h in enumerate(normed):
            if h in names and field not in mapping:
                mapping[field] = i
    return mapping


def detect_kind(filename: str) -> str:
    name = (filename or "").lower()
    for ext in REJECTED_EXTENSIONS:
        if name.endswith(ext):
            raise ValueError(f"unsupported spreadsheet format ({ext}); use .csv or plain .xlsx")
    if name.endswith(".xlsx"):
        return "xlsx"
    if name.endswith(".csv"):
        return "csv"
    raise ValueError("unsupported file type; upload .csv or plain .xlsx")


def read_table(*, filename: str, content: bytes) -> list[list[str]]:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("file is too large (limit 2 MB)")
    kind = detect_kind(filename)
    if kind == "csv":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("CSV must be UTF-8 encoded") from exc
        reader = csv.reader(io.StringIO(text))
        rows = [[safe_decode(c) for c in row] for row in reader]
    else:
        if not content.startswith(b"PK"):
            raise ValueError("not a valid .xlsx file")
        try:
            import openpyxl
        except ImportError as exc:
            raise ValueError("spreadsheet support is unavailable") from exc
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as exc:
            raise ValueError("not a valid .xlsx file") from exc
        with archive:
            names = archive.namelist()
            if any("vbaProject.bin" in n or n.endswith(".bin")
                   for n in names if n.startswith("xl/")):
                raise ValueError("macro workbooks are not allowed; use plain .xlsx")
            total = sum(i.file_size for i in archive.infolist())
            if total > 16 * 1024 * 1024:
                raise ValueError("spreadsheet expands too large")
        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), data_only=False, read_only=True)
        except Exception as exc:
            raise ValueError(f"not a valid .xlsx file: {exc}") from exc
        ws = wb.active
        if ws.max_column is not None and ws.max_column > MAX_COLUMNS:
            raise ValueError(f"too many columns (limit {MAX_COLUMNS})")
        if ws.max_row is not None and (ws.max_row - 1) > MAX_ROWS:
            raise ValueError("too many rows (limit 2000)")
        if ((ws.max_row or 0) * (ws.max_column or 0)) > MAX_CELLS:
            raise ValueError("spreadsheet has too many cells")
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = []
            for cell in row:
                if cell is None:
                    cells.append("")
                elif isinstance(cell, bool):
                    cells.append("TRUE" if cell else "FALSE")
                elif isinstance(cell, (int, float)):
                    import math
                    if isinstance(cell, float) and (
                            math.isnan(cell) or math.isinf(cell)):
                        raise ValueError("counts must be finite whole numbers")
                    cells.append(safe_decode(str(cell)))
                else:
                    text = safe_decode(str(cell))
                    if len(text) > MAX_CELL_CHARS:
                        raise ValueError("a cell exceeds the text limit")
                    cells.append(text)
            rows.append(cells[:MAX_COLUMNS])
        if wb.sheetnames and len(wb.sheetnames) > 1:
            pass  # only the first worksheet is read; documented.
    # Drop trailing fully-blank rows.
    while rows and not any((c or "").strip() for c in rows[-1]):
        rows.pop()
    for row in rows:
        if len(row) > MAX_COLUMNS:
            raise ValueError(f"too many columns (limit {MAX_COLUMNS})")
        for cell in row:
            if len(cell or "") > MAX_CELL_CHARS:
                raise ValueError("a cell exceeds the text limit")
    if (len(rows) - 1 > MAX_ROWS) or (sum(len(r) for r in rows) > MAX_CELLS):
        raise ValueError("too many rows (limit 2000)")
    return rows


_FORMULA_TRIGGERS = ("=", "+", "-", "@")

COUNT_LIMIT = 999_999_999


def _is_formula(text: str) -> bool:
    stripped = (text or "").lstrip()
    if stripped.startswith(_FORMULA_TRIGGERS):
        return True
    # Bundle-safe quoted formulas ('=1+1) are still formulas for row imports.
    if stripped.startswith("'") and stripped[1:2] in _FORMULA_TRIGGERS:
        return True
    return False


def _find_header_row(rows: list[list[str]], aliases: dict[str, set[str]],
                       required: tuple[str, ...]) -> int:
    """Locate the header row, tolerating up to two leading title rows.

    Returns the index of the first row (within the first three) whose
    normalized cells map every required field; raises a missing-columns
    error otherwise.
    """
    for index in range(min(3, len(rows))):
        mapping = _map_headers([(c or "") for c in rows[index]], aliases)
        if all(f in mapping for f in required):
            return index
    # Report against the first row so the message names the actual upload.
    mapping = _map_headers([(c or "") for c in (rows[0] if rows else [])], aliases)
    missing = [f for f in required if f not in mapping]
    raise ValueError(f"missing required columns: {', '.join(missing)}")


def build_preview(*, kind: str, rows: list[list[str]], mode: str) -> dict:
    """Validate rows without writing. Returns {headers, mapping, valid, errors, summary}."""
    if not rows or not any((c or "").strip() for c in rows[0]):
        raise ValueError("spreadsheet has no header row")
    if kind == "roster":
        start = _find_header_row(rows, ROSTER_ALIASES, ("external_id", "name"))
    elif kind == "inventory":
        start = _find_header_row(rows, INVENTORY_ALIASES, ("category", "name"))
    else:
        raise ValueError("unknown import kind")
    headers = [(c or "").strip() for c in rows[start]]
    data = rows[start + 1:]
    if kind == "roster":
        return _preview_roster(headers, data, mode, first_row=start + 2)
    return _preview_inventory(headers, data, mode, first_row=start + 2)


def _preview_roster(headers, data, mode, first_row: int = 2) -> dict:
    from apps.roster.models import Missionary

    mapping = _map_headers(headers, ROSTER_ALIASES)
    valid: list[dict] = []
    errors: list[dict] = []
    seen: set[str] = set()
    existing = {m.external_id: m for m in Missionary.objects.all()}
    for offset, row in enumerate(data):
        n = first_row + offset

        def cell(field):
            i = mapping.get(field)
            return (row[i] if i is not None and i < len(row) else "").strip()
        ext, name, email = cell("external_id"), cell("name"), cell("email")
        reason = cell("reason")
        if not ext and not name and not email:
            continue  # skip blank lines silently
        problems = []
        for field_name, value in (("external_id", ext), ("name", name), ("email", email),
                                  ("title_prefix", cell("title_prefix")),
                                  ("arrival_status", cell("arrival_status")),
                                  ("stake", cell("stake")), ("mission", cell("mission")),
                                  ("language", cell("language")),
                                  ("arrival_date", cell("arrival_date")),
                                  ("release_date", cell("release_date")),
                                  ("personal_email", cell("personal_email"))):
            if _is_formula(value):
                problems.append(f"{field_name}: formulas are not evaluated; replace with plain text")
        if not ext:
            problems.append("external ID is required")
        if not name:
            problems.append("name is required")
        personal = cell("personal_email")
        if len(ext) > 64:
            problems.append("external ID is too long (limit 64)")
        if len(name) > 255:
            problems.append("name is too long (limit 255)")
        if len(email) > 254 or len(personal) > 254:
            problems.append("email is too long")
        if ext in seen:
            problems.append("duplicate external ID in file")
        seen.add(ext)
        action = "create"
        if ext in existing:
            action = "update"
        elif mode == "replace":
            action = "create"
        if email and "@" not in email:
            problems.append("email looks invalid")
        if personal and "@" not in personal:
            problems.append("personal email looks invalid")
        for label in ("arrival_date", "release_date"):
            raw = cell(label)
            if raw:
                from django.utils.dateparse import parse_date
                try:
                    parsed = parse_date(raw)
                except ValueError:
                    parsed = None
                if parsed is None:
                    problems.append(f"{label} must be a date (YYYY-MM-DD)")
        entry = {"row": n, "external_id": ext, "name": name, "email": email,
                 "title_prefix": cell("title_prefix"),
                 "arrival_status": cell("arrival_status"),
                 "stake": cell("stake"), "mission": cell("mission"),
                 "language": cell("language"),
                 "arrival_date": cell("arrival_date"),
                 "release_date": cell("release_date"),
                 "personal_email": personal,
                  "present_fields": sorted(mapping),
                  "reason": reason, "action": action,
                 "archived": existing[ext].is_archived if ext in existing else False}
        if problems:
            errors.append({"row": n, "problems": problems, "entry": entry})
        else:
            valid.append(entry)
    archived_effect = 0
    if mode == "replace":
        keep = {v["external_id"] for v in valid}
        archived_effect = Missionary.objects.filter(is_archived=False).exclude(external_id__in=keep).count() if keep or not valid else 0
    return {"headers": headers, "mapping": mapping, "valid": valid, "errors": errors,
            "summary": {"valid": len(valid), "errors": len(errors),
                        "to_archive": archived_effect if mode == "replace" else 0, "mode": mode}}


def _to_int_or_none(text: str):
    import math

    text = (text or "").strip()
    if text == "":
        return None
    try:
        number = float(text)
    except ValueError:
        return "invalid"
    if math.isnan(number) or math.isinf(number):
        return "invalid"
    if number != int(number):
        return "invalid"  # fractional counts are rejected, never truncated
    value = int(number)
    if abs(value) > COUNT_LIMIT:
        return "invalid"
    return value


def _preview_inventory(headers, data, mode, first_row: int = 2) -> dict:
    from apps.inventory.models import Item

    mapping = _map_headers(headers, INVENTORY_ALIASES)
    missing = [f for f in ("category", "name") if f not in mapping]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")
    has_remaining = "remaining" in mapping
    has_stock = "stock" in mapping
    has_out = "out" in mapping
    if not has_remaining and not has_stock:
        raise ValueError("inventory import needs a Remaining/Stock column")
    valid: list[dict] = []
    errors: list[dict] = []
    seen: set[str] = set()
    existing = {i.key: i for i in Item.objects.all()}
    for offset, row in enumerate(data):
        n = first_row + offset
        def cell(field):
            i = mapping.get(field)
            return (row[i] if i is not None and i < len(row) else "").strip()
        cat, name, size = cell("category"), cell("name"), cell("size")
        if not cat and not name and not size and not any(cell(f) for f in ("remaining", "stock", "out")):
            continue
        problems = []
        for f in ("category", "name", "size", "remaining", "stock", "out", "low_threshold"):
            if _is_formula(cell(f)):
                problems.append(f"{f}: formulas are not evaluated; replace with plain text")
        if not cat:
            problems.append("category is required")
        if not name:
            problems.append("name is required")
        if len(cat) > 128 or len(name) > 255 or len(size) > 64:
            problems.append("category/name/size is too long")
        key = item_key(cat or "?", name or "?", size)
        if key in seen:
            problems.append("duplicate item in file (category/name/size)")
        seen.add(key)
        remaining_raw = cell("remaining") if has_remaining else ""
        stock_raw = cell("stock") if has_stock else ""
        out_raw = cell("out") if has_out else ""
        counted = None
        movement_note = ""
        if has_remaining and remaining_raw != "":
            parsed = _to_int_or_none(remaining_raw)
            if parsed == "invalid":
                problems.append("Remaining must be a whole number")
            else:
                counted = parsed
                movement_note = "remaining"
        elif has_stock and has_out and stock_raw != "" and out_raw != "":
            s, o = _to_int_or_none(stock_raw), _to_int_or_none(out_raw)
            if s == "invalid" or o == "invalid":
                problems.append("Stock/Out must be whole numbers")
            else:
                counted = s - o
                movement_note = "stock-minus-out"
        elif has_stock and stock_raw != "":
            s = _to_int_or_none(stock_raw)
            if s == "invalid":
                problems.append("Stock must be a whole number")
            else:
                counted = s
                movement_note = "stock"
        else:
            # New items require an explicit count; existing items without a
            # count mean "metadata only" (no movement).
            if key not in existing:
                problems.append("new items need a Remaining/Stock count")
        if counted is not None and counted < 0:
            problems.append("counted quantity cannot be negative")
        low_raw = cell("low_threshold")
        low = None
        if low_raw != "":
            parsed = _to_int_or_none(low_raw)
            if parsed == "invalid" or (isinstance(parsed, int) and parsed < 0):
                problems.append("alert level must be a nonnegative whole number")
            else:
                low = parsed
        action = "update" if key in existing else "create"
        current = existing[key].on_hand if key in existing else None
        delta = (counted - current) if (counted is not None and current is not None) else counted
        entry = {"row": n, "category": cat, "name": name, "size": size,
                 "counted": counted, "movement": movement_note, "delta": delta,
                 "current": current, "low_threshold": low,
                  "present_fields": sorted(mapping),
                  "reason": cell("reason"), "action": action,
                 "archived": existing[key].is_archived if key in existing else False}
        if problems:
            errors.append({"row": n, "problems": problems, "entry": entry})
        else:
            valid.append(entry)
    archived_effect = 0
    if mode == "replace":
        keep_keys = set()
        for v in valid:
            keep_keys.add(item_key(v["category"], v["name"], v["size"]))
        archived_effect = Item.objects.filter(is_archived=False).exclude(key__in=keep_keys).count() if keep_keys or not valid else 0
    return {"headers": headers, "mapping": mapping, "valid": valid, "errors": errors,
            "summary": {"valid": len(valid), "errors": len(errors),
                        "to_archive": archived_effect if mode == "replace" else 0, "mode": mode}}


def current_revision(kind: str) -> str:
    """Exact revision token: count plus a hash of every record's id/version.

    Any edit — even to a record that is not the maximum version — changes
    the token, so a preview built on older data can never confirm against
    newer source data.
    """
    import hashlib

    if kind == "roster":
        from apps.roster.models import Missionary
        pairs = sorted(Missionary.objects.values_list("id", "version"))
        count = len(pairs)
    else:
        from apps.inventory.models import Item
        pairs = sorted(Item.objects.values_list("id", "version"))
        count = len(pairs)
    digest = hashlib.sha256(
        ";".join(f"{pk}:{version}" for pk, version in pairs).encode("utf-8")
    ).hexdigest()[:32]
    prefix = "r" if kind == "roster" else "i"
    return f"{prefix}:{count}:{digest}"


def new_preview_token() -> str:
    return f"imp-{uuid.uuid4().hex}"
