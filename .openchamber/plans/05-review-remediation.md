# 05 — Review fixes and validation

## Goal and context
Fix all findings at `04b2227`, then find more. 118 Django/19 browser/15 deployment checks pass (one skip); runtime fails. Non-default worktree branch; no rollout/push. Ignored HTML/report do not travel. `P` = `.venv/bin/python`.

## Scope
Fixes below only; no certificate trust, SMTP, signatures, multi-intake/offline sync/redesign. Read architecture/01–04 plans.

## Steps
1. Add source-shaped synthetic `tests.review_regressions`; no PII/string-only behavioral checks.
2. `apps/operations/`: owned exclusive/nested guards, concurrent operators, all-writer/session quiescence/crash recovery. Failed imports retain others' windows; acquisition failure aborts. Offline restore closes connections, never unlinks open WAL; verify checksum/schema/release, stage/preserve recovery, restore then migrate/readiness before reopening. Atomic restricted backups record path/version/schema/completion; corrupt files cannot supersede verified protection. Daily/30-day retention; test real reset backups/failures and newer-work confirmation.
3. `apps/transfers/`: exact revisions checked transactionally (lower-version edits invalidate), explicit none/all/subset; unchecked-all writes nothing. Ownership/TTL/atomic single-use, archived-match reactivation. Snapshot exports; validate schemas/types/checksums/duplicates/references/statuses and ledger/issue/reversal consistency (999 Available/10 ledger fails). Preserve timestamps/actor snapshots without accounts. Explicit format migration, lossless apostrophe/whitespace/control/formula encoding; safe staff CSV. Bound ZIP/XLSX expansion/entries/rows/columns/cells/uploads before allocation; handle malformed formats, reject macros/formulas/fractional/nonfinite/overflow counts. Preview reasons/deltas.
4. Models/forms/converter/transfers: preserve arrival status, Elder/Sister, stake/mission/language, arrival/release dates, personal email/intake metadata; `remain`/`lowThreshold`, original aliases/title rows. Keep four authorized zero corrections. Restore stock/low-stock/person/category/intake reports, roster/inventory CSV/PDF; no export truncation, retain archive filters. Regenerate ignored seed bundle.
5. Accounts/views/JS: shared atomic bounded login/reauth throttling; fresh identity/session/password-bound confirmation for admin account changes/replacements/bundles/settings/reset; safe redirects. Functional audited/guarded notice/default threshold. Poll JSON auth errors, persistent expiry warning/no idle extension. Drafts/retries/duplicate cancellation; searchable person/item/category/size, add/remove rows beyond three, explicit limit/no truncation. Plain labels/hidden admin tools/local synthetic guide screenshots.
6. Docker/Compose/Caddy/scripts: correct TLS ports/static serving; canonical pinned tags, requested VERSION immune to .env overwrite, persisted release/consistent restore containers/safe config parsing. Actual HTTP Host/proxy/schema/version readiness, not config checks or Gunicorn TLS redirects. Safe guards/offline restore, actual backup paths/volume access/executable recovery. Test two versions/persistence/dead server/DB/migrations/update/restore failures.
7. Actual Playwright desktop/mobile/poll/expiry/draft/error/import/export/admin flows, internet blocked. CI installs Chromium; failures cannot skip. Actual isolated Compose TLS/assets/persistence/recovery/version tests; exclude HTML/.local/bundles/backups/secrets from images. Gate publishing on all suites; clean test-owned resources only.
8. Fresh whole-codebase review beyond this list: rerun old/new criteria, trace write/auth/import/recovery/adversarial cases; fix new in-scope blockers with regressions, flag scope changes. Track evidence in `docs/REVIEW.md`; no SHIP with open findings/untested critical paths.
9. Update docs/AGENTS; tick roadmap 05 after acceptance/fresh review; manual gates explicit.

## Manual checks
Private HTML: verify fields/thresholds, 95 roster/224 items/four corrections/no issues. Host networking/boot/registry/restore drill; new staff issue unaided, add stock/undo with guide. No trust setup; backups cannot survive host loss.

## Acceptance criteria
1. `P manage.py test` passes all review regressions.
2. `P manage.py makemigrations --check --dry-run` reports no missing migrations.
3. `P -m pytest tests/browser` passes real flows, no unexpected skips.
4. `P -m pytest tests/deployment` passes real Compose/WAL/update/restore checks.
5. `docker compose -f compose.test.yaml config --quiet` and `docker build -t supplies-tracker:test .` pass; verify image exclusions.
6. `bash -n scripts/update.sh scripts/restore.sh scripts/daily_backup.sh` passes.
7. `git diff --check` passes.
8. `docs/REVIEW.md`: fresh SHIP evidence for all findings; manual gates separate.
