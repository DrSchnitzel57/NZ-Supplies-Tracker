"""Versioned ZIP/CSV bundle read/write/validate. See docs/DATA_FORMAT.md."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

BUNDLE_VERSION = 1
BUNDLE_FORMAT = "mtc-bundle"
TABLE_FILES = [
    "settings.csv",
    "roster.csv",
    "items.csv",
    "issues.csv",
    "issue_lines.csv",
    "movements.csv",
    "audits.csv",
]

ROSTER_FIELDS = ["id", "external_id", "name", "email", "title_prefix",
                 "arrival_status", "stake", "mission", "language",
                 "arrival_date", "release_date", "personal_email",
                 "intake_metadata",
                 "is_archived", "archived_at", "version"]
ITEM_FIELDS = ["id", "category", "name", "size", "unit", "on_hand", "low_threshold",
               "is_archived", "archived_at", "version", "source_remaining", "source_corrected"]
ISSUE_FIELDS = ["id", "missionary_id", "missionary_name", "missionary_external_id",
                "issued_by_username", "issued_at", "note", "status",
                "reversed_at", "reversal_reason", "reversed_by_username"]
LINE_FIELDS = ["id", "issue_id", "item_id", "quantity", "item_category", "item_name", "item_size"]
MOVEMENT_FIELDS = ["id", "item_id", "kind", "delta", "balance_after", "reason",
                   "issue_id", "created_by_username", "created_at"]
AUDIT_FIELDS = ["id", "actor_username", "action", "target_type", "target_id", "detail_json", "created_at"]
SETTINGS_FIELDS = ["key", "value"]

TABLE_HEADERS = {
    "settings.csv": SETTINGS_FIELDS,
    "roster.csv": ROSTER_FIELDS,
    "items.csv": ITEM_FIELDS,
    "issues.csv": ISSUE_FIELDS,
    "issue_lines.csv": LINE_FIELDS,
    "movements.csv": MOVEMENT_FIELDS,
    "audits.csv": AUDIT_FIELDS,
}

#: Hard bounds enforced BEFORE any table is allocated in memory.
MAX_BUNDLE_BYTES = 12 * 1024 * 1024
MAX_ZIP_ENTRIES = 16
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_TABLE_BYTES = 8 * 1024 * 1024
MAX_CSV_ROWS = 20000

_SAFE_TRIGGERS = ("=", "+", "-", "@", "|", "%")


def safe_encode(value) -> str:
    """Spreadsheet-safe encoding that round-trips losslessly.

    A leading apostrophe is prepended when the remainder — after removing
    that quote on decode — would otherwise be ambiguous: values whose
    left-stripped text starts with a formula trigger, or values that already
    start with an apostrophe. Whitespace and control characters are preserved
    byte-for-byte by CSV quoting; nothing is stripped here.
    """
    text = "" if value is None else str(value)
    if text.startswith("'") or text.lstrip().startswith(_SAFE_TRIGGERS):
        return "'" + text
    return text


def safe_decode(value) -> str:
    """Inverse of safe_encode: strip exactly one protective quote."""
    text = "" if value is None else str(value)
    if text.startswith("'"):
        rest = text[1:]
        if rest.startswith("'") or rest.lstrip().startswith(_SAFE_TRIGGERS):
            return rest
    return text


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _csv_bytes(fields, rows) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: safe_encode(row.get(k, "")) for k in fields})
    return buf.getvalue().encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_bundle(*, source: str, settings_rows, roster_rows, item_rows,
                 issue_rows, line_rows, movement_rows, audit_rows) -> bytes:
    tables = {
        "settings.csv": _csv_bytes(SETTINGS_FIELDS, settings_rows),
        "roster.csv": _csv_bytes(ROSTER_FIELDS, roster_rows),
        "items.csv": _csv_bytes(ITEM_FIELDS, item_rows),
        "issues.csv": _csv_bytes(ISSUE_FIELDS, issue_rows),
        "issue_lines.csv": _csv_bytes(LINE_FIELDS, line_rows),
        "movements.csv": _csv_bytes(MOVEMENT_FIELDS, movement_rows),
        "audits.csv": _csv_bytes(AUDIT_FIELDS, audit_rows),
    }
    manifest = {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "created_at": _iso_now(),
        "source": source,
        "counts": {
            "settings": len(settings_rows),
            "roster": len(roster_rows),
            "items": len(item_rows),
            "issues": len(issue_rows),
            "issue_lines": len(line_rows),
            "movements": len(movement_rows),
            "audits": len(audit_rows),
        },
        "checksums": {name: _sha(data) for name, data in tables.items()},
        "notes": "Migration interchange, not an exact database backup.",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
        for name in TABLE_FILES:
            zf.writestr(name, tables[name])
    return buf.getvalue()


class BundleError(ValueError):
    pass


def parse_bundle(data: bytes) -> dict:
    """Validate structure, bounds, checksums, versions, references.

    Returns {"manifest", "tables"}. Raises BundleError before any write.
    """
    if len(data) > MAX_BUNDLE_BYTES:
        raise BundleError("bundle is too large")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BundleError("not a valid ZIP archive") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise BundleError("bundle has too many entries")
        total = 0
        names = set()
        for info in infos:
            _check_entry(info)
            names.add(info.filename)
            total += info.file_size
            if info.file_size > MAX_TABLE_BYTES:
                raise BundleError(f"bundle entry too large: {info.filename}")
        if total > MAX_UNCOMPRESSED_BYTES:
            raise BundleError("bundle expands too large")
        manifest = _read_manifest(zf)
        if manifest.get("format") != BUNDLE_FORMAT:
            raise BundleError("unknown bundle format")
        version = manifest.get("version")
        if version != BUNDLE_VERSION:
            migrated = _migrate_legacy(zf, names, manifest, version)
            if migrated is not None:
                return migrated
            raise BundleError(f"unsupported bundle version {version!r}")
        checksums = manifest.get("checksums") or {}
        missing_checksums = [n for n in TABLE_FILES if n not in checksums]
        if missing_checksums:
            raise BundleError(
                f"manifest is missing checksums for: {', '.join(missing_checksums)}")
        tables: dict[str, list[dict]] = {}
        for name in TABLE_FILES:
            try:
                raw = zf.read(name)
            except KeyError as exc:
                raise BundleError(f"missing table {name}") from exc
            if _sha(raw) != checksums[name]:
                raise BundleError(f"checksum mismatch in {name}")
            tables[name] = _read_csv(name, raw)
        _check_counts(manifest, tables)
    _check_references(tables)
    return {"manifest": manifest, "tables": tables}


def _check_entry(info: zipfile.ZipInfo) -> None:
    name = info.filename
    if info.is_dir():
        raise BundleError(f"bundle must not contain directories: {name}")
    if name.startswith("/") or ".." in name.split("/") or "\\" in name:
        raise BundleError(f"unsafe bundle entry name: {name}")
    if name not in ("manifest.json", *TABLE_FILES):
        raise BundleError(f"unexpected bundle entry: {name}")
    if info.flag_bits & 0x1:
        raise BundleError(f"encrypted bundle entry is not allowed: {name}")


def _read_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        raw = zf.read("manifest.json")
    except KeyError as exc:
        raise BundleError("manifest.json is missing or invalid") from exc
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BundleError("manifest.json is missing or invalid") from exc
    if not isinstance(manifest, dict):
        raise BundleError("manifest.json is missing or invalid")
    return manifest


def _read_csv(name: str, raw: bytes) -> list[dict]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError(f"{name} must be UTF-8 encoded") from exc
    reader = csv.DictReader(io.StringIO(text))
    expected = TABLE_HEADERS[name]
    if reader.fieldnames is None:
        raise BundleError(f"{name} has no header row")
    actual = [(h or "") for h in reader.fieldnames]
    if actual != expected:
        raise BundleError(
            f"{name} has an unexpected header; "
            f"expected exactly: {', '.join(expected)}")
    rows = []
    for n, row in enumerate(reader, start=2):
        if row is None:
            raise BundleError(f"{name} row {n} is malformed")
        if None in row:
            raise BundleError(f"{name} row {n} has too many columns")
        rows.append({k: safe_decode(v) for k, v in row.items()})
        if len(rows) > MAX_CSV_ROWS:
            raise BundleError(f"{name} exceeds the row limit")
    return rows


def _check_counts(manifest: dict, tables: dict) -> None:
    counts = manifest.get("counts") or {}
    mapping = {"settings": "settings.csv", "roster": "roster.csv",
               "items": "items.csv", "issues": "issues.csv",
               "issue_lines": "issue_lines.csv", "movements": "movements.csv",
               "audits": "audits.csv"}
    for key, name in mapping.items():
        if key not in counts:
            raise BundleError(f"manifest is missing the {key} count")
        try:
            declared = int(counts[key])
        except (TypeError, ValueError) as exc:
            raise BundleError(f"manifest has an invalid {key} count") from exc
        if declared != len(tables[name]):
            raise BundleError(
                f"manifest count mismatch for {name}: "
                f"declared {declared}, found {len(tables[name])}")


def _migrate_legacy(zf: zipfile.ZipFile, names: set[str], manifest: dict,
                    version) -> dict | None:
    """Explicit migration path for older bundle layouts.

    Only a legacy manifest (version 0, same format, all seven tables with
    current headers but no checksums/counts) is migrated: checksums and
    counts are computed, every other validation still applies, and the
    migration is recorded on the returned manifest. Anything else is
    rejected — there is no silent reinterpretation of unknown formats.
    """
    if not isinstance(manifest, dict):
        return None
    if manifest.get("format") != BUNDLE_FORMAT or version != 0:
        return None
    if any(n not in names for n in TABLE_FILES):
        return None
    tables: dict[str, list[dict]] = {}
    checksums: dict[str, str] = {}
    counts: dict[str, int] = {}
    mapping = {"settings": "settings.csv", "roster": "roster.csv",
               "items": "items.csv", "issues": "issues.csv",
               "issue_lines": "issue_lines.csv", "movements": "movements.csv",
               "audits": "audits.csv"}
    try:
        for name in TABLE_FILES:
            raw = zf.read(name)
            if len(raw) > MAX_TABLE_BYTES:
                raise BundleError(f"bundle entry too large: {name}")
            checksums[name] = _sha(raw)
            tables[name] = _read_csv(name, raw)
        for key, name in mapping.items():
            counts[key] = len(tables[name])
    except (KeyError, BundleError):
        return None
    migrated_manifest = {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "created_at": manifest.get("created_at") or _iso_now(),
        "source": manifest.get("source") or "legacy-migration",
        "counts": counts,
        "checksums": checksums,
        "notes": "Migration interchange, not an exact database backup.",
        "migrated_from_version": 0,
    }
    _check_references(tables)
    return {"manifest": migrated_manifest, "tables": tables}


def _parse_int(raw, *, label: str, minimum: int | None = None) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError, AttributeError) as exc:
        raise BundleError(f"invalid integer for {label}: {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise BundleError(f"invalid value for {label}: {raw!r}")
    return value


def _parse_bool(raw, *, label: str) -> bool:
    text = str(raw or "").strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no", ""):
        return False
    raise BundleError(f"invalid true/false for {label}: {raw!r}")


def _parse_dt(raw, *, label: str, allow_blank: bool = True):
    from django.utils.dateparse import parse_datetime

    text = (raw or "").strip()
    if not text:
        if allow_blank:
            return None
        raise BundleError(f"missing timestamp for {label}")
    try:
        dt = parse_datetime(text)
    except ValueError as exc:
        raise BundleError(f"invalid timestamp for {label}: {raw!r}") from exc
    if dt is None:
        raise BundleError(f"invalid timestamp for {label}: {raw!r}")
    return dt


def _parse_uuid(raw, *, label: str) -> str:
    import uuid as _uuid

    try:
        return str(_uuid.UUID(str(raw).strip()))
    except (ValueError, AttributeError, TypeError) as exc:
        raise BundleError(f"invalid ID for {label}: {raw!r}") from exc


def _parse_date(raw, *, label: str):
    from django.utils.dateparse import parse_date

    text = (raw or "").strip()
    if not text:
        return None
    try:
        dt = parse_date(text)
    except ValueError as exc:
        raise BundleError(f"invalid date for {label}: {raw!r}") from exc
    if dt is None:
        raise BundleError(f"invalid date for {label}: {raw!r}")
    return dt


def _check_references(tables: dict) -> None:
    roster_ids = {r.get("id") for r in tables["roster.csv"]}
    item_ids = {r.get("id") for r in tables["items.csv"]}
    issue_ids = {r.get("id") for r in tables["issues.csv"]}
    line_ids = [r.get("id") for r in tables["issue_lines.csv"]]
    movement_ids = [r.get("id") for r in tables["movements.csv"]]
    audit_ids = [r.get("id") for r in tables["audits.csv"]]
    if "" in roster_ids or "" in item_ids or "" in issue_ids:
        raise BundleError("blank record IDs are not allowed")
    if "" in line_ids or "" in movement_ids or "" in audit_ids:
        raise BundleError("blank record IDs are not allowed")
    if len(roster_ids) != len(tables["roster.csv"]):
        raise BundleError("duplicate roster IDs")
    if len(item_ids) != len(tables["items.csv"]):
        raise BundleError("duplicate item IDs")
    if len(issue_ids) != len(tables["issues.csv"]):
        raise BundleError("duplicate issue IDs")
    if len(set(line_ids)) != len(line_ids):
        raise BundleError("duplicate issue line IDs")
    if len(set(movement_ids)) != len(movement_ids):
        raise BundleError("duplicate movement IDs")
    if len(set(audit_ids)) != len(audit_ids):
        raise BundleError("duplicate audit IDs")
    ext_ids = [r.get("external_id") for r in tables["roster.csv"]]
    if any(not (e or "").strip() for e in ext_ids):
        raise BundleError("blank roster external IDs are not allowed")
    if len(set(ext_ids)) != len(ext_ids):
        raise BundleError("duplicate roster external IDs")
    from apps.inventory.models import item_key as _item_key

    keys = [_item_key(r.get("category") or "", r.get("name") or "",
                      r.get("size") or "") for r in tables["items.csv"]]
    if len(set(keys)) != len(keys):
        raise BundleError("duplicate inventory items (category/name/size)")
    for row in tables["roster.csv"]:
        _parse_uuid(row.get("id"), label=f"roster {row.get('id')}")
        _parse_int(row.get("version") or 0, label="roster version", minimum=0)
        email = (row.get("email") or "").strip()
        if email and "@" not in email:
            raise BundleError(f"invalid email for roster {row.get('id')}")
        personal = (row.get("personal_email") or "").strip()
        if personal and "@" not in personal:
            raise BundleError(f"invalid personal email for roster {row.get('id')}")
        _parse_date(row.get("arrival_date"), label="arrival date")
        _parse_date(row.get("release_date"), label="release date")
        _parse_dt(row.get("archived_at"), label="roster archived_at")
    for row in tables["items.csv"]:
        _parse_uuid(row.get("id"), label=f"item {row.get('id')}")
        on_hand = _parse_int(row.get("on_hand") or 0,
                             label=f"on_hand for item {row.get('id')}", minimum=0)
        _parse_int(row.get("low_threshold") or 0,
                   label=f"low_threshold for item {row.get('id')}", minimum=0)
        _parse_int(row.get("version") or 0, label="item version", minimum=0)
        _parse_bool(row.get("is_archived"), label="item is_archived")
        _parse_bool(row.get("source_corrected"), label="item source_corrected")
        source_remaining = (row.get("source_remaining") or "").strip()
        if source_remaining != "":
            _parse_int(source_remaining, label="item source_remaining")
        _parse_dt(row.get("archived_at"), label="item archived_at")
        if not (row.get("category") or "").strip() or not (row.get("name") or "").strip():
            raise BundleError(f"item {row.get('id')} needs a category and name")
    for row in tables["issues.csv"]:
        _parse_uuid(row.get("id"), label=f"issue {row.get('id')}")
        if row.get("missionary_id") not in roster_ids:
            raise BundleError(f"issue {row.get('id')} references unknown missionary")
        status = (row.get("status") or "").strip()
        if status not in ("ACTIVE", "REVERSED"):
            raise BundleError(f"issue {row.get('id')} has unknown status {status!r}")
        _parse_dt(row.get("issued_at"), label="issued_at", allow_blank=False)
        reversed_at = _parse_dt(row.get("reversed_at"), label="reversed_at")
        reason = (row.get("reversal_reason") or "").strip()
        by = (row.get("reversed_by_username") or "").strip()
        if status == "REVERSED":
            if reversed_at is None or not reason:
                raise BundleError(
                    f"reversed issue {row.get('id')} needs reversed_at and a reason")
        else:
            if reversed_at is not None or reason or by:
                raise BundleError(
                    f"active issue {row.get('id')} must not carry reversal data")
    for row in tables["issue_lines.csv"]:
        if row.get("issue_id") not in issue_ids:
            raise BundleError(f"issue line {row.get('id')} references unknown issue")
        if row.get("item_id") not in item_ids:
            raise BundleError(f"issue line {row.get('id')} references unknown item")
        _parse_int(row.get("quantity") or 0,
                   label=f"quantity on line {row.get('id')}", minimum=1)
    kinds = {"OPENING", "RECEIPT", "COUNT_ADJUST", "ISSUE", "REVERSAL"}
    for row in tables["movements.csv"]:
        if row.get("item_id") not in item_ids:
            raise BundleError(f"movement {row.get('id')} references unknown item")
        if row.get("issue_id") and row.get("issue_id") not in issue_ids:
            raise BundleError(f"movement {row.get('id')} references unknown issue")
        kind = (row.get("kind") or "").strip()
        if kind not in kinds:
            raise BundleError(f"unknown movement kind {row.get('kind')!r}")
        linked = bool((row.get("issue_id") or "").strip())
        if kind in ("ISSUE", "REVERSAL") and not linked:
            raise BundleError(
                f"movement {row.get('id')} of kind {kind} must link an issue")
        if kind in ("OPENING", "RECEIPT", "COUNT_ADJUST") and linked:
            raise BundleError(
                f"movement {row.get('id')} of kind {kind} must not link an issue")
        _parse_int(row.get("delta") if str(row.get("delta")).strip() != "" else "x",
                   label=f"delta in movement {row.get('id')}")
        _parse_int(row.get("balance_after") or 0,
                   label=f"balance in movement {row.get('id')}", minimum=0)
        _parse_dt(row.get("created_at"), label="movement created_at", allow_blank=False)
    for row in tables["audits.csv"]:
        _parse_uuid(row.get("id"), label=f"audit {row.get('id')}")
        if not (row.get("action") or "").strip():
            raise BundleError(f"audit {row.get('id')} needs an action")
        try:
            json.loads(row.get("detail_json") or "{}")
        except ValueError as exc:
            raise BundleError(f"audit {row.get('id')} has invalid detail JSON") from exc
        _parse_dt(row.get("created_at"), label="audit created_at", allow_blank=False)
    _check_ledger_consistency(tables)


def _check_ledger_consistency(tables: dict) -> None:
    """Cross-check cached balances against the movement ledger and issues.

    A bundle whose Available column disagrees with its own ledger (for
    example Available=999 with only 10 units of recorded movement) is
    corrupt and is rejected before any write.
    """
    issues = {r.get("id"): r for r in tables["issues.csv"]}
    lines_by_issue: dict[str, list[dict]] = {}
    for row in tables["issue_lines.csv"]:
        lines_by_issue.setdefault(row.get("issue_id"), []).append(row)
    movements_by_issue: dict[str, list[dict]] = {}
    deltas: dict[str, int] = {}
    for row in tables["movements.csv"]:
        deltas[row.get("item_id")] = deltas.get(row.get("item_id"), 0) + int(
            str(row.get("delta")).strip())
        if (row.get("issue_id") or "").strip():
            movements_by_issue.setdefault(row.get("issue_id"), []).append(row)
    for row in tables["items.csv"]:
        on_hand = int(str(row.get("on_hand") or 0).strip())
        if deltas.get(row.get("id"), 0) != on_hand:
            raise BundleError(
                f"item {row.get('id')} Available={on_hand} disagrees with its "
                f"movement ledger ({deltas.get(row.get('id'), 0)})")
    for issue_id, issue in issues.items():
        lines = lines_by_issue.get(issue_id, [])
        if not lines:
            raise BundleError(f"issue {issue_id} has no lines")
        movements = movements_by_issue.get(issue_id, [])
        by_kind: dict[str, list[dict]] = {}
        for mv in movements:
            by_kind.setdefault((mv.get("kind") or "").strip(), []).append(mv)
        expected_issue = sorted(
            (mv["item_id"], int(str(mv["delta"]).strip())) for mv in by_kind.get("ISSUE", []))
        expected_lines = sorted(
            (ln["item_id"], -int(str(ln["quantity"]).strip())) for ln in lines)
        if expected_issue != expected_lines:
            raise BundleError(
                f"issue {issue_id} lines disagree with ISSUE movements")
        expected_reversal = sorted(
            (mv["item_id"], int(str(mv["delta"]).strip()))
            for mv in by_kind.get("REVERSAL", []))
        if issue.get("status") == "REVERSED":
            expected_lines_pos = sorted(
                (ln["item_id"], int(str(ln["quantity"]).strip())) for ln in lines)
            if expected_reversal != expected_lines_pos:
                raise BundleError(
                    f"reversed issue {issue_id} is missing matching REVERSAL movements")
        elif expected_reversal:
            raise BundleError(
                f"active issue {issue_id} must not have REVERSAL movements")
        stray = [mv for kind, mvs in by_kind.items()
                 for mv in mvs if kind not in ("ISSUE", "REVERSAL")]
        if stray:
            raise BundleError(
                f"issue {issue_id} links a non-issue movement")
