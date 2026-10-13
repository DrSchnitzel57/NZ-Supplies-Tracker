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

Only `version: 1` is written. A legacy `version: 0` bundle (same format,
all seven tables with current headers but no checksums/counts) is migrated
explicitly: checksums and counts are computed, every validation below still
applies, and the migration is recorded as `migrated_from_version: 0` in the
manifest and the import audit. Anything else is rejected before any write.

Archive bounds are enforced before any table is allocated: the ZIP itself is
capped (12 MB), entries are capped (16 entries, 8 MB each, 32 MB expanded),
directory/absolute/parent entries, unexpected files and encrypted entries
are rejected. Every checksum entry is mandatory, manifest counts must match
row counts exactly, and headers must match the tables below exactly.
Duplicate IDs (every table), duplicate external IDs, duplicate normalized
inventory keys, dangling references, negative live balances, bad types
(non-integer counts, bad booleans/UUIDs/timestamps/dates/emails) and unknown
issue statuses are rejected before any write. Ledger consistency is
cross-checked: each item's Available must equal its summed movement ledger
(for example Available=999 with 10 units of recorded movement fails); each
issue's lines must match its ISSUE movements, reversed issues must carry
matching REVERSAL movements plus timestamp and reason, and non-issue
movement kinds must not link issues. Ordered per-item chains are validated
in deterministic (created_at, id) order: exactly one leading OPENING with
delta == balance, RECEIPT positive / ISSUE negative / REVERSAL positive
deltas, every row satisfying balance == previous + delta with no historical
negative, so aggregate-correct but chain-invalid bundles (for example
OPENING delta 0 with balance 999) are rejected.

## Tables and types

All CSVs are UTF-8, LF newlines, comma-separated with minimal quoting.
First row is the header; headers are lowercase `snake_case` exactly as
below. Timestamps are ISO-8601 with timezone. UUIDs are lowercase strings.
Booleans are `true`/`false`. Integers are plain whole numbers.

- `settings.csv`: `key,value` — site settings (for example `site_notice`,
  `default_low_threshold`). No accounts, passwords or sessions.
- `roster.csv`: `id,external_id,name,email,title_prefix,arrival_status,stake,mission,language,arrival_date,release_date,personal_email,intake_metadata,is_archived,archived_at,version`
  - `title_prefix` is `Elder`/`Sister`; `intake_metadata` is a JSON object of
    preserved source fields the schema does not name.
- `items.csv`: `id,category,name,size,unit,on_hand,low_threshold,is_archived,archived_at,version,source_remaining,source_corrected`
  - `on_hand` is the corrected live nonnegative balance.
  - `source_remaining` preserves the original source value when it was
    negative; `source_corrected` is `true` only for authorized corrections.
- `issues.csv`: `id,missionary_id,missionary_name,missionary_external_id,issued_by_username,issued_at,note,status,reversed_at,reversal_reason,reversed_by_username`
  - `reversed_by_username` is a durable snapshot set at reversal time; it
    survives renames, deletes and re-imports to different accounts without
    exporting accounts/passwords/sessions.
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

CSV cells are exported with a single leading apostrophe (`'`) prepended
when the value already starts with `'` or its left-stripped text starts with
`=`, `+`, `-`, `@`, `|`, `%` (for example `=1+1` becomes `'=1+1`, and
`  =1+1` keeps its spaces). On import exactly one leading `'` is removed
when the remainder starts with `'` or its left-stripped text starts with a
trigger — a lossless round trip that preserves whitespace, apostrophes such
as O'Brien, and control characters via CSV quoting. Staff CSV downloads use
the same encoding, so spreadsheet apps never execute cell text as formulas.
Importers never evaluate formulas: cells still starting with `=`, `+`, `-`
or `@` after decoding (including bundle-quoted `'=…`) are rejected as row
errors.

## Individual spreadsheet imports

Routine roster/inventory imports accept `.csv` and plain `.xlsx` (first
worksheet only, documented). Bounds are enforced before allocation: 2 MB per
file, 2000 rows, 64 columns, 200,000 cells, 4000 characters per cell, 16 MB
expanded XLSX; `.xlsx` magic bytes are checked, macro workbooks
(`vbaProject.bin`) are rejected, and only finite whole counts within
±999,999,999 are accepted (fractional, infinite/NaN and overflow counts are
row errors). Up to two leading title rows are skipped to find the header
row; missing required columns are a clear file-level error. Header names
are case-insensitive with the following aliases:

- Roster: `external_id` (`externalId`, `extId`, `id`, `missionary id`, `full`
  variants); `name` (`fullName`, `full_name`, `missionary name`);
  `email` (`e-mail`, `email address`); `title_prefix`, `arrival_status`,
  `stake`, `mission`, `language`, `arrival_date`, `release_date`,
  `personal_email`; `reason` (`note`, `comment`).
- Inventory: `category` (`cat`, `group`); `name` (`itemName`, `item_name`,
  `product`, `title`); `size` (`variant`); `remaining` (`remain`, `onHand`,
  `onhand`, `available`, `qty`, `quantity`, `count`); `stock` (`stock`);
  `out` (`givenOut`, `given out`, `issued`, `total out`);
  `low_threshold` (`lowThreshold`, `low`, `reorder level`, `alert level`,
  …); `reason` (`note`, `comment`).

Matching: roster by `external_id`; inventory by normalized
`category/item/size`. An inventory `Remaining` value, or `Stock minus Out`
when both are supplied, becomes a reasoned physical-count movement
(`COUNT_ADJUST`); supplied `Out` never fabricates issue history. Negative
counts, unknown references and formula cells are row errors. Previews show
row errors, counts, archival effects and planned movements with per-row
reasons and deltas (current → counted), and carry an exact revision token
(count plus a hash of every record's id/version, so even a lower-version
edit invalidates confirmation). The revision is re-checked transactionally
while holding the preview lock; changed source data invalidates
confirmation (stale previews are rejected). Previews are single-use and
owned by their creator (administrators may apply anyone's); expired
previews (24 h) are deleted. Add/update imports offer explicit all / none /
subset approval — confirming with nothing checked writes nothing.
Replacement imports require every row valid plus administrator permission
with fresh password confirmation, and archive records missing from the file
instead of deleting them.

Missing columns and blank optional cells preserve existing values in **both**
add/update and replacement modes; new optional values remain empty. Clear a
field explicitly in its Edit details form. New arrival/release dates are saved.
Previews now record uploaded field presence. Legacy previews without that
contract must be re-uploaded; the application cannot reconstruct erased data.

Canonical headers take priority over legacy aliases regardless of column order.
In particular, exported `external_id` is the roster matching identity; the
separate exported internal `id` is not used as its alias when `external_id` exists.
Legacy sheets with only `id` still map it to the external ID.

## Staff reports

Screens, CSV and A4 PDF downloads share validated filters and all matching rows
(screens paginate). Summaries group by stable IDs with current names; history
keeps original snapshots and includes item/size/quantity, notes and undo reasons.
Issued dates use Pacific/Auckland. Current-net summaries exclude every undone
issue, including those undone later; they are not period or as-of accounting.
Available-stock reports always describe stock now and offer no date filter.
Archived records are an explicit filter. Raw roster/inventory CSV web endpoints
default to active records too; full ZIP bundles retain archives unconditionally.
Roster CSV includes all supported fields and source metadata. PDF text embeds
licensed Noto Sans, wraps long cells and repeats summary-table headers.

## Seed conversion

`seed_bundle --source HTML --output ZIP` parses the embedded `SEED` JSON
without executing the HTML and writes a standard v1 bundle. Supported
source shapes: an object with roster-like keys (`missionaries`, `people`,
`roster`) and item-like keys (`items`, `inventory`), each a list; roster
entries accept `external_id/id/extId` + `name/fullName` + `email`, plus
`Elder`/`Sister` name prefixes (split into `title_prefix`), the exact source
keys `type` → title, `arrived` → arrival status, `homeStake` → stake,
`mission` , `tLanguage` → language, `missionArrival`/`missionRelease` →
arrival/release dates, `personalEmail` and any
other fields (preserved verbatim in `intake_metadata`, carried through
bundles/models/forms/import/export); items accept
`category/cat` + `name/item` + `size` + quantity keys (`remaining`,
`remain`, `stock`, `onHand`, `qty`) and optional `out`, plus
`lowThreshold` variants. Unparseable dates are kept as `*_raw` metadata
instead of being silently dropped. Comments inside strings are never stripped and
apostrophes (O'Brien) survive single/double-quote-tolerant parsing.
Source-negative remaining values are preserved in `source_remaining` with
`source_corrected=true`; live `on_hand` is zeroed and authorized
`seed.correction` audit rows are included (four in the approved source —
verified as a manual check). The real HTML is local-only; tests use
synthetic equivalent seeds only.
