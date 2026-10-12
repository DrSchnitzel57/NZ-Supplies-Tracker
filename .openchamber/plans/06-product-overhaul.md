# 06 — Finished product overhaul

## Goal and context
One finished app on Django/SQLite/Compose. Baseline `548aad3`: 195 Django/17 browser/21 deployment tests pass; product probes return FIX (`docs/REVIEW.md`). Design/reasons: `docs/ARCHITECTURE.md`. One milestone, ordered phases.

## Scope
In: fixes, UI/workflows, reports/imports/admin, help, validation. Out: stack/role changes, durable drafts/queues, partial returns/period accounting, Microsoft/SMTP/signatures/multi-intake, trust setup, rollout/presentation. Synthetic data only. Approved non-main branch, small commits, never push.

## Steps and affected areas
### Phase 1 — Correctness and safety
1. `tests/`, accounts/operations/roster/inventory/transfers/issuing/config: add behavioral regressions; fix stale hidden recipients, import field erasure/lost new dates, invalid counts becoming zero, lost form/shortage drafts, POST-only reauth redirects, stale CSRF after other-tab login and bundle-export reauth bypass. Make settings/audit one guarded transaction. Set `en-nz`/`Pacific/Auckland`; fix local-day counts and incomplete live indicators.
### Phase 2 — Shell and issuing
2. Shared `templates/`, `static/` styles/JS, bound forms. Clean hierarchy/help/errors; keyboard/focus/contrast, light/dark/reduced motion. Desktop-first, complete narrow navigation. Home landing unless safe requested page. Sections: Home, Issue goods, Inventory, Missionaries, Reports; New issue/History tabs, contextual imports, separate admin.
3. One main/profile issuing workspace: explicit missionary, searchable item/category/size catalogue, consolidated list/quantities/named summary. Cap 50 distinct items; no truncation/partial writes. Confirm recipient changes, clear items/note. Keep original drafts through shortages/errors/offline/uncertain replies/same-user login; retain retry idempotency and recheck revised quantities. Warn before leaving; reload/close discards. No autosave/personal browser storage/replay. Polls never alter drafts/focus or renew sessions; stale counts require review.
### Phase 3 — Remaining workflows
4. Roster/inventory: search/pagination; profiles show identity/arrival/mission/language/goods first, other fields under Details; complete edits. Separate Add stock/Correct stock count/Edit details. Keep reasons, stale guards, archives/history/provenance; retain safe input, never passwords.
5. Issuing/reports: history filters missionary/item/date/staff/status; one report with shared screen/CSV/PDF filters, including archives. Summaries group stable IDs/current names; history keeps snapshots. Dates filter issuance using current net (all undone excluded); stock is now, not as-of. Complete/formula-safe exports, compatible ZIPs; wrapped A4 PDFs with embedded licensed Unicode font and tested long text/macrons/page bounds.
6. Transfers/admin: readable field/count/archive previews. Both modes preserve missing/blank optional fields; deliberate clearing in edits. Save new dates; re-upload legacy previews lacking field-presence semantics. Keep subset/ownership/TTL/revision safeguards and wholly valid admin replacement archiving missing records. Repair accounts/reauth; isolate migration/reset with existing confirmation/backup/maintenance. Refresh in-app guide, synthetic images and existing quick-start.
### Phase 4 — Validate the finished product
7. Rerun suites; synthetic 95-person/224-item, >200-history, 50/51-item cases. Internet-blocked keyboard/narrow/contrast/draft/expiry/concurrency/maintenance/filter/export/PDF checks. Replace selectors, preserve coverage. Fresh `docs/REVIEW.md` evidence; no SHIP with open findings.
8. Update `AGENTS.md` evidence/docs and architecture if design changed; tick roadmap 06 after automated acceptance, keeping manual gates explicit.

## Risks
No reconstruction of erased data promised; old previews need re-upload.

## Manual checks
User-owned; skip in implementation: unaided staff issue/add stock/undo; source fidelity; host networking/registry/reboot/restore. Trust deferred; host-local backups cannot survive host loss.

## Acceptance criteria
1. `.venv/bin/python manage.py test --noinput` passes old/new regressions.
2. `.venv/bin/python -m pytest tests/browser` passes, no skips/internet.
3. `.venv/bin/python -m pytest tests/deployment` passes recovery/TLS/exclusions.
4. `.venv/bin/python manage.py check` passes.
5. `.venv/bin/python manage.py makemigrations --check --dry-run` finds no changes.
6. `docker compose -f compose.test.yaml config --quiet` passes.
7. `docker build -t supplies-tracker:test .` passes.
8. `bash -n scripts/update.sh scripts/restore.sh scripts/daily_backup.sh` passes.
9. `git diff --check` passes.
10. `docs/REVIEW.md` shows fresh product SHIP, rendered/export evidence and separate manual gates.
