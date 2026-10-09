"""Individual roster/inventory CSV/XLSX parsing, preview and confirmation.

Never evaluates formulas: any decoded cell still starting with '=' is a row
error. Macro/legacy workbooks are rejected; only .csv and plain .xlsx.
"""

from __future__ import annotations

import csv
import io
import re
import uuid

from apps.inventory.models import item_key
from apps.transfers.bundle import safe_decode

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_ROWS = 2000
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}
REJECTED_EXTENSIONS = {".xlsm", ".xls", ".xlsb", ".ods", ".zip"}

ROSTER_ALIASES = {
    "external_id": {"external_id", "external id", "id", "missionary id", "ext id", "ext_id"},
    "name": {"name", "full name", "missionary", "missionary name"},
    "email": {"email", "e-mail", "email address"},
    "reason": {"reason", "note", "comment"},
}
INVENTORY_ALIASES = {
    "category": {"category", "cat", "group"},
    "name": {"name", "item", "item name", "product", "title"},
    "size": {"size", "variant"},
    "remaining": {"remaining", "on hand", "on_hand", "available", "qty", "quantity", "count"},
    "stock": {"stock"},
    "out": {"out", "given out", "issued", "total out"},
    "low_threshold": {"low_threshold", "low", "low stock", "threshold", "reorder", "minimum", "alert level"},
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
        try:
            import openpyxl
        except ImportError as exc:
            raise ValueError("spreadsheet support is unavailable") from exc
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=False, read_only=True)
        ws = wb.active
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = []
            for cell in row:
                if cell is None:
                    cells.append("")
                else:
                    cells.append(safe_decode(str(cell)))
            rows.append(cells)
        if wb.sheetnames and len(wb.sheetnames) > 1:
            pass  # only the first worksheet is read; documented.
    # Drop trailing fully-blank rows.
    while rows and not any((c or "").strip() for c in rows[-1]):
        rows.pop()
    if len(rows) - 1 > MAX_ROWS:
        raise ValueError("too many rows (limit 2000)")
    return rows


def _is_formula(text: str) -> bool:
    return text.lstrip().startswith("=")


def build_preview(*, kind: str, rows: list[list[str]], mode: str) -> dict:
    """Validate rows without writing. Returns {headers, mapping, valid, errors, summary}."""
    if not rows or not any((c or "").strip() for c in rows[0]):
        raise ValueError("spreadsheet has no header row")
    headers = [(c or "").strip() for c in rows[0]]
    data = rows[1:]
    if kind == "roster":
        return _preview_roster(headers, data, mode)
    if kind == "inventory":
        return _preview_inventory(headers, data, mode)
    raise ValueError("unknown import kind")


def _preview_roster(headers, data, mode) -> dict:
    from apps.roster.models import Missionary

    mapping = _map_headers(headers, ROSTER_ALIASES)
    missing = [f for f in ("external_id", "name") if f not in mapping]
    valid: list[dict] = []
    errors: list[dict] = []
    seen: set[str] = set()
    existing = {m.external_id: m for m in Missionary.objects.all()}
    for n, row in enumerate(data, start=2):
        def cell(field):
            i = mapping.get(field)
            return (row[i] if i is not None and i < len(row) else "").strip()
        ext, name, email, reason = cell("external_id"), cell("name"), cell("email"), cell("reason")
        if not ext and not name and not email:
            continue  # skip blank lines silently
        problems = []
        if _is_formula(ext) or _is_formula(name) or _is_formula(email):
            problems.append("formulas are not evaluated; replace with plain text")
        if not ext:
            problems.append("external ID is required")
        if not name:
            problems.append("name is required")
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
        entry = {"row": n, "external_id": ext, "name": name, "email": email,
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
    text = (text or "").strip()
    if text == "":
        return None
    try:
        return int(float(text))
    except ValueError:
        return "invalid"


def _preview_inventory(headers, data, mode) -> dict:
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
    for n, row in enumerate(data, start=2):
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
    """Revision token: counts plus max versions, invalidating stale previews."""
    if kind == "roster":
        from apps.roster.models import Missionary
        qs = Missionary.objects.all()
        return f"r:{qs.count()}:{Missionary.objects.order_by('-version').values_list('version', flat=True).first() or 0}"
    from apps.inventory.models import Item
    qs = Item.objects.all()
    return f"i:{qs.count()}:{Item.objects.order_by('-version').values_list('version', flat=True).first() or 0}"


def new_preview_token() -> str:
    return f"imp-{uuid.uuid4().hex}"
