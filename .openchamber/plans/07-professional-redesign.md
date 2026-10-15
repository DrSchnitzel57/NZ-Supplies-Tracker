# 07 — Professional redesign

## Steps
- [x] 1. Lock capabilities/design.
- [x] 2. Rebuild shell/controls.
- [x] 3. Replace search/issuing.
- [x] 4. Complete records/panels/batches.
- [x] 5. Polish supporting screens.
- [x] 6. Validate and record evidence.
- [x] 7. Update docs/roadmap 07.

## Goal and context
One app, not a reskin: approved sidebar, global/local search, unified picker, catalogue/summary issuing, panels/pages and batches. Reftab practicality, Church restraint. Baseline: 228 Django/36 Chromium pass after declared PyMuPDF installation. Design/research: docs/ARCHITECTURE.md, section 07.

## Scope and decisions
All app-owned UI/copy; retain Django/SQLite/Compose, roles, schema/ZIP/safety, light/dark/mobile. Tailored local components avoid template bloat. MTC Supplies identity; no unapproved Church assets. No signatures, saved drafts/queues, SMTP, barcodes, rollout/presentation. Approved non-main checkout; small commits, no push.

## Work details
1. docs/REVIEW.md: map capabilities/safe substitutions, no private data. Visual references: neutral surfaces, blue (#006184), licensed Māori-capable type, clear fields, coherent spacing/icons/tables, fewer cards, concise labels.
2. templates/base.html, components/, static/app.css/app.js: sidebar (Overview landing, Missionaries, Inventory, History, Reports), prominent Issue supplies, header finder, Help/account utilities, role-gated Administration. Preserve deep links; complete mobile menu. Shared forms/tables/feedback/combobox/panels with empty/loading/error/expiry states.
3. apps/common/, roster/inventory/issuing views/URLs/templates, static/issuing.js: single recipient input/attached suggestions, explicit ID selection, identity/Change; no filtering select. Global people/item finder; local row search. Bounded term matching, exact-ID/name priority, stable ordering, stale-response rejection, server auth, explicit archives; background lookup never extends expiry. Catalogue rows: category/size/availability/Add. Desktop catalogue/visible summary; mobile stacked. Combine items, quantity/remove, note, named submit. Keep 50-item bound, recipient-change clearing, shortage confirmation, atomic stock/idempotency, open-tab recovery; no autosave/replay/storage.
4. roster/inventory/common apps, templates/JS: profiles/full edits, sorting/grouping, title/arrival filters, correct pagination/totals. Panels for short item edits/stock/counts; pages for long forms/batches. Page fallbacks, list filters/page/scroll, focus return, dirty-close guards; serialize uncertain writes. Batch: max 50, review/validate all, reject normalized duplicates, atomic ledger/audit/maintenance, retained draft/stable-key retry; reject changed-payload reuse, partial saves/truncation.
5. reports/transfers/accounts/operations templates, apps/reports/, guide/static/img/: consistent filters, category/arrival summaries, complete CSV/Unicode A4 PDF, low-stock copy/mail-app, clear import changes/errors/approval. Keep current-net/snapshots, source fields, blank preservation, preview revision/ownership/TTL, permissions/reauth/reset/backup. Human copy, consequential warnings, synthetic illustrated help.
6. tests/, docs/REVIEW.md: preserve regressions despite selector/copy changes. Prove search/selection, sorted/grouped pagination, panel focus/return/dirty/uncertain states, batch rollback/concurrency/retry. Inspect populated 95-person/224-item/long-history screens/PDFs: desktop/mobile, light/dark, zoom/keyboard/contrast/reduced motion/failures. Judge rendered design, not strings/one-record screenshots. Serial suites; no SHIP with blockers.
7. Update AGENTS.md/quick-start/architecture; tick roadmap 07 after acceptance, keeping independent re-review/manual gates separate.

## Risks and assumptions
Protect drafts/identities with request ownership/server guards. No data migration. Staff procedure unknown; infer no new process. Fifty-row batch bound is engineering scope.

## Manual checks
Skip in implementation: user-owned staff trial, source fidelity, host/network/restore and final visual acceptance. Trust deferred.

## Acceptance criteria
1. `.venv/bin/python manage.py test --noinput` passes.
2. `.venv/bin/python -m pytest tests/browser` passes, no skips/internet.
3. `.venv/bin/python -m pytest tests/deployment` passes.
4. `.venv/bin/python manage.py check` passes.
5. `.venv/bin/python manage.py makemigrations --check --dry-run` finds no changes.
6. `docker compose -f compose.test.yaml config --quiet` passes.
7. `docker build -t supplies-tracker:test .` passes.
8. `bash -n scripts/update.sh scripts/restore.sh scripts/daily_backup.sh` passes.
9. `git diff --check` passes.
10. docs/REVIEW.md shows capability/design coverage, populated screen/PDF/error evidence, no blockers and separate manual gates.
