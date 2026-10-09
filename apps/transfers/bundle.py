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

ROSTER_FIELDS = ["id", "external_id", "name", "email", "is_archived", "archived_at", "version"]
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

_SAFE_TRIGGERS = ("=", "+", "-", "@", "|", "%")


def safe_encode(value) -> str:
    text = "" if value is None else str(value)
    stripped = text.lstrip()
    if stripped.startswith(_SAFE_TRIGGERS):
        return "'" + text
    return text


def safe_decode(value) -> str:
    text = "" if value is None else str(value)
    if len(text) >= 2 and text[0] == "'" and text[1] in _SAFE_TRIGGERS:
        return text[1:]
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
    """Validate structure, checksums, versions, references. Returns tables."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BundleError("not a valid ZIP archive") from exc
    try:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError) as exc:
        raise BundleError("manifest.json is missing or invalid") from exc
    if manifest.get("format") != BUNDLE_FORMAT:
        raise BundleError("unknown bundle format")
    if manifest.get("version") != BUNDLE_VERSION:
        raise BundleError(f"unsupported bundle version {manifest.get('version')!r}")
    checksums = manifest.get("checksums") or {}
    tables: dict[str, list[dict]] = {}
    for name in TABLE_FILES:
        try:
            raw = zf.read(name)
        except KeyError as exc:
            raise BundleError(f"missing table {name}") from exc
        expected = checksums.get(name)
        if expected and _sha(raw) != expected:
            raise BundleError(f"checksum mismatch in {name}")
        text = raw.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        tables[name] = [{k: safe_decode(v) for k, v in (row or {}).items()} for row in reader]
    _check_references(tables)
    return {"manifest": manifest, "tables": tables}


def _check_references(tables: dict) -> None:
    roster_ids = {r.get("id") for r in tables["roster.csv"]}
    item_ids = {r.get("id") for r in tables["items.csv"]}
    issue_ids = {r.get("id") for r in tables["issues.csv"]}
    for key in ("roster_ids", "item_ids", "issue_ids"):
        pass
    if "" in roster_ids or "" in item_ids:
        raise BundleError("blank record IDs are not allowed")
    if len(roster_ids) != len(tables["roster.csv"]):
        raise BundleError("duplicate roster IDs")
    if len(item_ids) != len(tables["items.csv"]):
        raise BundleError("duplicate item IDs")
    if len(issue_ids) != len(tables["issues.csv"]):
        raise BundleError("duplicate issue IDs")
    ext_ids = [r.get("external_id") for r in tables["roster.csv"]]
    if len(set(ext_ids)) != len(ext_ids):
        raise BundleError("duplicate roster external IDs")
    for row in tables["items.csv"]:
        try:
            on_hand = int(row.get("on_hand") or 0)
        except ValueError as exc:
            raise BundleError(f"invalid on_hand for item {row.get('id')}") from exc
        if on_hand < 0:
            raise BundleError(f"negative live balance for item {row.get('id')}")
    for row in tables["issues.csv"]:
        if row.get("missionary_id") not in roster_ids:
            raise BundleError(f"issue {row.get('id')} references unknown missionary")
    for row in tables["issue_lines.csv"]:
        if row.get("issue_id") not in issue_ids:
            raise BundleError(f"issue line {row.get('id')} references unknown issue")
        if row.get("item_id") not in item_ids:
            raise BundleError(f"issue line {row.get('id')} references unknown item")
        try:
            if int(row.get("quantity") or 0) <= 0:
                raise BundleError(f"invalid quantity on line {row.get('id')}")
        except ValueError as exc:
            raise BundleError(f"invalid quantity on line {row.get('id')}") from exc
    for row in tables["movements.csv"]:
        if row.get("item_id") not in item_ids:
            raise BundleError(f"movement {row.get('id')} references unknown item")
        if row.get("issue_id") and row.get("issue_id") not in issue_ids:
            raise BundleError(f"movement {row.get('id')} references unknown issue")
        if row.get("kind") not in {"OPENING", "RECEIPT", "COUNT_ADJUST", "ISSUE", "REVERSAL"}:
            raise BundleError(f"unknown movement kind {row.get('kind')!r}")
        try:
            if int(row.get("balance_after") or 0) < 0:
                raise BundleError(f"negative balance in movement {row.get('id')}")
        except ValueError as exc:
            raise BundleError(f"invalid balance in movement {row.get('id')}") from exc
