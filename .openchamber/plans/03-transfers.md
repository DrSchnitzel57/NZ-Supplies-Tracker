# 03 — Transfers, reports and administrator safeguards

## Goal and context
Deliver portable transfers, initial-data conversion, complete reports and protected administration on milestones 01–02. See `docs/ARCHITECTURE.md`. Source HTML has 95 missionaries, 224 items, no issue history and four negative opening balances; user authorized zeroing those with audit records.

## Scope
In: versioned ZIP/CSV manifest, seed conversion, CSV/XLSX import previews, CSV/PDF reporting, accounts UI, full reset. Out: rewriting Git history, signatures, SMTP, multi-intake reporting and actual host deployment.

## Steps and affected areas
1. Specify `docs/DATA_FORMAT.md`: CSV tables/manifest versions, types, stable references, timestamps/checksums, exclusions and reversible spreadsheet-safe text encoding. Full bundles include archived records, movements, issue snapshots/reversals and operational audits, but no accounts/passwords/sessions. Bundles are migration interchange, not exact database backups.
2. Add `python manage.py seed_bundle --source "MTC Missionary Supplies Tracker.html" --output .local/imports/initial.zip`. Parse embedded JSON without executing HTML. Generate a standard bundle, preserve source-negative values as correction metadata and record four authorized corrections on import; only corrected nonnegative opening balances go live. Output and temporary artifacts must be gitignored; no personal data in logs/tests/images. Validate with synthetic equivalent seeds in automated tests.
3. Implement administrator-only full import/export in `apps/transfers`: bounded archives/uploads, checksums/types/references/versions, empty operational database requirement, atomic commit and source-version guards. Never import on startup/update. Provide host `import_bundle` command using the same pipeline for installation. No implicit history merge/replay.
4. Preserve existing documented header aliases for individual CSV/XLSX imports; reject unsupported/macro formats and formula evaluation. Preview row errors, counts, archival effects and movements. Allow approved valid subsets for add/update; replacements require all rows valid and administrator permission. Match roster external IDs and normalized item keys. Imported Remaining or Stock minus Out becomes reasoned count adjustments; never fabricate issue history. Reject stale previews/negative counts; archive missing records on replacement.
5. Implement roster/inventory CSV/PDF exports and all four report families in `apps/reports`. Include net issuance and accessible original/reversal history, active/archived filters and full bundle migration exports. Bundle assets; low-stock copying and mail-app links remain client actions, not SMTP.
6. Add administrator account UI and system settings. Implement protected reset to empty operational data: recent reauthentication, typed confirmation, exclusive maintenance and verified SQLite backup. Keep accounts and a minimal protected reset marker, no hidden live copy of cleared operational PII. Recovery failures must prevent deletion. Use shared operations/backup services, later scheduled in milestone 04.
7. Add synthetic transfer/report/admin/browser tests and docs; update `AGENTS.md`, tick milestone 03, update architecture if design changed.

## Risks
CSV safety and lossless interchange need round-trip tests. Replacement must not orphan history. Source HTML is local-only; fresh clones need an operator-provided copy. Full reset is intentionally destructive; backup failure must fail closed.

## Manual checks
Run the converter locally against approved HTML; verify 95 roster rows, 224 items, four corrections and no distributions, without exposing personal data in reports. Inspect actual CSV/PDF exports and mail-app behavior.

## Acceptance criteria
1. `python manage.py test tests.transfers tests.seed_conversion` passes schema round trips, source corrections, upload bounds, stale previews and import atomicity.
2. `python manage.py test tests.reports tests.admin_safety` passes permissions, net reporting, resets and backup-failure protection.
3. `python -m pytest tests/browser/test_transfers_reports.py` passes preview/partial import/report flows.
4. `python -m pytest tests/browser/test_admin.py` passes account and protected-reset flows.
5. `git check-ignore .local/imports/initial.zip` prints the ignored path.
6. `python manage.py test` passes the complete Django suite.
7. `git diff --check` passes.
