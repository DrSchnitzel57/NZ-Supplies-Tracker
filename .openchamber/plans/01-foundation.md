# 01 — Foundation and data invariants

## Goal and context
Create the shared application's tested data/security foundation. Current software is one HTML file with browser-local state and no tests/tooling. The agreed design is in `docs/ARCHITECTURE.md`; approximately ten users share one LAN host. This milestone is not a deployable replacement.

## Scope
In: Django 5.2 LTS/Python 3.13 scaffold, SQLite transactions, local identity, permission services, stable records and ledger. Out: production UI, real personal-data import, bundles, Compose, release publication.

## Steps and affected areas
1. Add `manage.py`, `config/`, pinned dependency declarations, `apps/`, `templates/`, `static/` and synthetic `tests/`. Document local install/run/test commands in `README.md`, retaining `READ ME.txt` as prototype instructions. Extend existing ignore rules for local databases, credentials, backups and generated artifacts. Preserve the ignored local HTML; fresh clones obtain it separately from the operator.
2. Implement accounts/staff/admin permissions, temporary-password changes, deactivation/session invalidation, CSRF, secure production settings, login throttling and 30-minute idle expiry independent of background polling. Add an interactive host admin recovery command; never log credentials.
3. Model roster/items with internal immutable IDs, external missionary IDs as unique strings, archival state, version tokens, issue/line snapshots, movements and audits. Renaming current records cannot rewrite history. Define Remaining/net Out and separate count corrections explicitly.
4. Centralize writes: receipts/count adjustments require reasons; issues atomically check aggregated quantities; insufficient stock returns a proposal without writing; confirmation rechecks availability. Idempotency keys prevent replay. Staff may reverse a whole issue once with a reason, including archived items. Reject negative balances and stale edits/counts.
5. Configure local SQLite WAL, immediate transactions and bounded lock timeout. Add maintenance/write guards reusable by import/reset/update operations. Test real separate-connection concurrent transactions, not row-lock assumptions.
6. Provide `tests.foundation`, `tests.auth`, `tests.ledger`, `tests.concurrency`. Cover permissions, idle polling, session deactivation, renames, rollback, idempotency, negative counts, simultaneous issues, shortages and double reversals using synthetic fixtures only.
7. Update `AGENTS.md` with verified commands, tick milestone 01 in the roadmap, and update architecture decisions if implementation changed the design.

## Risks
SQLite lacks row-level locks; tests must use actual transaction boundaries. Prototype totals/name joins are not the new schema. No real seed data belongs in tests or images. Framework commands below are contracts to implement, not a passing baseline today.

## Manual checks
None; real host/browser checks belong to milestone 04.

## Acceptance criteria
1. `python manage.py check` passes.
2. `python manage.py makemigrations --check --dry-run` reports no missing migrations.
3. `python manage.py test tests.foundation tests.auth` passes identity, permission and schema cases.
4. `python manage.py test tests.ledger` passes mutation/history/idempotency cases.
5. `python manage.py test tests.concurrency` proves competing issues cannot create negative stock, duplicate reversals fail safely, and stale counts cannot overwrite newer movements.
6. `git diff --check` passes.
