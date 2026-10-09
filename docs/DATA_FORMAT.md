# Data interchange format (bundle v1)

Standard full-data interchange is a versioned ZIP archive. Bundles are
**migration interchange, not exact database backups**: they carry complete
operational relationships for review and migration. Database backups remain
the exact recovery source.

## Bundle layout (version 1)

```
manifest.json
settings.csv
roster.csv
items.csv
issues.csv
issue_lines.csv
movements.csv
audits.csv
```

`manifest.json`:

```json
{
  "format": "mtc-bundle",
  "version": 1,
  "created_at": "2026-10-09T00:00:00+00:00",
  "source": "export:username",
  "counts": {"roster": 1, "items": 1, "issues": 1},
  "checksums": {"roster.csv": "sha256-hex", "...": "..."},
  "notes": "optional free text"
}
```

Only `version: 1` is accepted. Unknown versions are rejected before any
write. Every CSV listed in `checksums` must be present and match its
SHA-256 hex digest. Duplicate IDs, dangling references and negative live
balances are rejected before any write.

## Tables and types

All CSVs are UTF-8, LF newlines, comma-separated with minimal quoting.
First row is the header; headers are lowercase `snake_case` exactly as
below. Timestamps are ISO-8601 with timezone. UUIDs are lowercase strings.
Booleans are `true`/`false`. Integers are plain whole numbers.

- `settings.csv`: `key,value` — site settings (for example `site_notice`,
  `default_low_threshold`). No accounts, passwords or sessions.
- `roster.csv`: `id,external_id,name,email,is_archived,archived_at,version`
- `items.csv`: `id,category,name,size,unit,on_hand,low_threshold,is_archived,archived_at,version,source_remaining,source_corrected`
  - `on_hand` is the corrected live nonnegative balance.
  - `source_remaining` preserves the original source value when it was
    negative; `source_corrected` is `true` only for authorized corrections.
- `issues.csv`: `id,missionary_id,missionary_name,missionary_external_id,issued_by_username,issued_at,note,status,reversed_at,reversal_reason,reversed_by_username`
- `issue_lines.csv`: `id,issue_id,item_id,quantity,item_category,item_name,item_size`
- `movements.csv`: `id,item_id,kind,delta,balance_after,reason,issue_id,created_by_username,created_at`
  - `kind` is one of `OPENING,RECEIPT,COUNT_ADJUST,ISSUE,REVERSAL`.
- `audits.csv`: `id,actor_username,action,target_type,target_id,detail_json,created_at`

Full bundles include archived records, movements, issue snapshots,
reversals and operational audits. They exclude accounts, password hashes,
sessions and login throttling state.

## Stable references

- Roster rows are matched by `external_id` (unique operator-visible string).
- Items are matched by normalized `category/name/size` key (trimmed,
  single-spaced, lowercased). Internal UUIDs connect issues, lines,
  movements and audits; renames never rewrite snapshots.
- Issues reference `missionary_id`; lines reference `issue_id` + `item_id`;
  movements optionally reference `issue_id`.

## Spreadsheet-safe text encoding

CSV cells beginning with `=`, `+`, `-`, `@`, `|`, `%`, or containing a
formula-like leading `=` after whitespace, are exported with a single
leading apostrophe (`'`) prepended (for example `=1+1` becomes `'=1+1`).
On import one leading `'` is removed when the next character is one of the
trigger characters above (reversible). Importers never evaluate formulas:
cells still starting with `=` after decoding are rejected as row errors.

## Individual spreadsheet imports

Routine roster/inventory imports accept `.csv` and plain `.xlsx` (first
worksheet, first row headers, no macro workbooks: `.xlsm/.xls/.xlsb` are
rejected). Uploads are bounded (2 MB per file, 2000 rows, worksheet text
capped); archives are never expanded here. Header names are
case-insensitive with the following aliases:

- Roster: `external_id` (`id`, `missionary id`, `ext id`, `external id`);
  `name` (`full name`, `missionary`, `missionary name`); `email`
  (`e-mail`, `email address`); `reason` (`note`).
- Inventory: `category` (`cat`, `group`); `name` (`item`, `item name`,
  `product`); `size` (`variant`); `remaining` (`on hand`,
  `on_hand`, `available`, `qty`, `quantity`, `count`); `stock` (`stock`);
  `out` (`given out`, `issued`, `total out`); `low_threshold` (`low`,
  `low stock`, `threshold`, `reorder`, `minimum`, `alert level`);
  `reason` (`note`, `comment`).

Matching: roster by `external_id`; inventory by normalized
`category/item/size`. An inventory `Remaining` value, or `Stock minus Out`
when both are supplied, becomes a reasoned physical-count movement
(`COUNT_ADJUST`); supplied `Out` never fabricates issue history. Negative
counts, unknown references and formula cells are row errors. Previews show
row errors, counts, archival effects and planned movements, and carry a
revision token; changed source data invalidates confirmation (stale
previews are rejected). Add/update imports may apply an explicitly approved
valid subset; replacement imports require every row valid plus
administrator permission, and archive records missing from the file instead
of deleting them.

## Seed conversion

`seed_bundle --source HTML --output ZIP` parses the embedded `SEED` JSON
without executing the HTML and writes a standard v1 bundle. Supported
source shapes: an object with roster-like keys (`missionaries`, `people`,
`roster`) and item-like keys (`items`, `inventory`), each a list; roster
entries accept `external_id/id/extId` + `name/fullName` + `email`; items
accept `category/cat` + `name/item` + `size` + quantity keys (`remaining`,
`stock`, `onHand`, `qty`) and optional `out`. Source-negative remaining
values are preserved in `source_remaining` with `source_corrected=true`;
live `on_hand` is zeroed and four authorized `seed.correction` audit rows
are included. The real HTML is local-only; tests use synthetic equivalent
seeds only.
