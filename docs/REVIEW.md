# Fresh review — milestone 05 (review remediation and validation)

Date: 2026-10-09. Scope: whole codebase at the milestone-05 worktree.
Method: four independent read-only audits (operations, transfers, accounts/UI,
deployment) plus a fresh reviewer pass over every write/auth/import/recovery
path, followed by fixes with behavioral regressions. All evidence uses
synthetic data; the ignored source HTML and the original review report were
not present and were never accessed. String-presence checks were replaced by
runtime proofs: real Chromium flows (desktop + mobile, internet blocked) and
real isolated Compose TLS/backup/restore drills.

## Finding → fix → evidence

### Operations / recovery (`apps/operations/`, `scripts/`)

| Finding | Fix | Evidence |
|---|---|---|
| Maintenance flag shared: second operator silently shared/cleared windows; nested exit reopened outer work; failed imports closed others' windows | Owned exclusive guard: entry fails for a second owner, same-owner nesting counts depth, exit needs the entry token | `tests.review_regressions.OwnedGuardTests` (6 tests); `maintenance --enter/--exit --token` drill in `tests/deployment/test_backup_restore.py::test_owned_maintenance_token_flow` |
| Crash left writes closed forever with no recovery path (or scripts cleared blindly) | No auto-expiry (fail closed); explicit audited `--force` takeover; `maintenance --check` status with owner/reason/age/stale | `test_crash_recovery_needs_explicit_audited_takeover`; `docs/OPERATIONS.md` |
| Backups written non-atomically under final names; no completion record | tmp + fsync + rename; `<name>.manifest.json` (path/sha/version/schema/completion); stale staging cleaned | `test_backup_records_manifest_and_verifies` |
| Newest corrupt `pre-update-*` superseded verified protection | Protection verifies newest-first; corrupt files skipped | `test_corrupt_newest_never_supersedes_verified_protection` |
| Verification ignored permissions/schema-release | 0600 enforced; required tables incl. throttle/audit; backups from newer code refused | `test_verify_rejects_bad_permissions_and_newer_schema` |
| Restore unlinked open WAL sidecars before copying, no connection close/checkpoint/stage/preservation, migrate-before-restore in script | close_all + WAL checkpoint, pre-restore preservation copy, stage→verify→swap, sidecars dropped only after swap; `restore_db` runs restore→migrate→readiness before reopening | `test_offline_restore_never_unlinks_open_wal`, `test_restore_command_migrates_and_checks_readiness`, Compose drill `test_backup_verify_and_protected_restore_drill` |
| Reset took backup inside the write transaction; only `ValueError` mapped; no newer-work confirmation; `pre-reset-*` unprotected | Backup outside transaction; all backup errors fail closed; `expected_counts` re-confirmation; marker records path/sha/version; pre-reset files are protected recovery backups | `test_reset_marker_records_path_and_version`, `test_reset_rejects_any_backup_error_fail_closed`, `tests/admin_safety.py::test_reset_refuses_stale_confirmation_counts` |
| `restore_db` newer-work check ignored movements/lines/audits/settings | All operational tables counted | `test_restore_refuses_movements_only_newer_work` (audit-only live work still refuses) |
| `update.sh` swallowed maintenance failure (`\|\| true`), double-stamped backup names, `.env` sourcing clobbered the requested pin, readiness probed plain HTTP under `SECURE_SSL_REDIRECT` | Acquisition failure aborts; exact `--dest` file; allowlisted `.env` parsing with pin immunity; readiness with `X-Forwarded-Proto` header | `tests/deployment/test_update_failure.py` (refusals + pin immunity); real Compose update-path readiness in drills |
| `restore.sh` wrong order (migrate before restore), broken mounts, no secret guard, reopened writes without readiness | verify→maintenance→restore→migrate→readiness→reopen; `realpath` mounts; secret placeholder guard | `test_restore_script_refuses_bad_input` + drill paths |
| Health probes 301'd under `SECURE_SSL_REDIRECT` | `SECURE_REDIRECT_EXEMPT` for `/healthz|/readyz`; compose healthcheck sends the proto header | `test_health_endpoints_bypass_https_redirect`; Compose `test_tls_serves_readyz_with_version` |
| **New (found by live probe): Caddy TLS handshake failed for IP clients** — browsers send no SNI for IP literals, Caddy had no fallback cert, so LAN HTTPS was unreachable in production | `default_sni {$APP_HOST}` | Live `curl -k https://127.0.0.1:18443/readyz/` failed before, returns `{"ok": true}` after; `test_tls_serves_readyz_with_version` |
| `compose.test.yaml` published `18443:443` while Caddy listens on `APP_PORT` 8443; app had nested backup volume + stray Caddy PKI mount; Caddy/prod tags unpinned | Fixed mapping `18443:8443`, single `app-data` volume, pinned `python:3.13.7` + `caddy:2.10.2`, prod-only `requirements.prod.txt` | `docker compose -f compose.test.yaml config --quiet`; image build + exclusions test |

### Transfers / data preservation (`apps/transfers/`, models, `apps/reports/`)

| Finding | Fix | Evidence |
|---|---|---|
| Revision token was count+max-version: lower-version edits invisible; checked outside the transaction | Exact token (count + hash of every id/version); re-checked under `select_for_update` inside the write transaction | `test_lower_version_edit_invalidates_preview` |
| Unchecked-all applied all valid rows | Explicit all/none/subset (`scope` + `has_rows`); empty set writes nothing | `test_unchecked_all_writes_nothing`; Playwright `test_apply_none_writes_nothing` |
| Previews consumable by anyone, reusable, no TTL enforcement | Creator-or-admin ownership, atomic single-use delete, 24 h TTL in service + view, per-user listing | `test_preview_ownership_and_single_use`, `test_staff_never_sees_replace_mode_or_others_previews` |
| Roster archived matches never reactivated | Reactivate-then-update on archived matches | `test_archived_roster_match_reactivates` |
| Export dropped `source_remaining/corrected`; import dropped timestamps/actor names | Model fields + snapsh
...[truncated 7370 chars]
## Reviewer-feedback round (second agent review)

All findings below were accepted and fixed with regressions. No finding
was declined.

- **Update/restore scripts vs owned guard (blocker, accepted):** nested
  `backup_db --quiesce` / `restore_db` under a script-held window were
  refused as foreign owners. Nesting now requires presenting the outer
  token (`--owner` + `--token`, threaded through both scripts); same-owner
  calls without the token are refused. Composition tests:
  `MaintenanceCompositionTests.test_nested_quiesced_backup_joins_outer_window`,
  `test_nested_restore_joins_outer_window`,
  `test_nesting_requires_the_outer_token`.
- **Restore wipes the maintenance row (found while fixing the above,
  accepted):** `restore_db` now re-acquires a fresh window after restoring
  (force-taking over a stale flag carried in the backup, audited), runs
  migrate + readiness, and leaves the window held — printing
  `maintenance-token:` for the caller. Pre-write refusals reopen instead.
  `restore.sh` follows the corrected protocol (verify → stop writers →
  restore → readiness gate → release with the printed token).
- **Reset counts raced the window (blocker, accepted):** `expected_counts`
  validation moved inside the held window; proven by
  `test_reset_counts_checked_inside_the_window` (a row inserted at window
  acquisition aborts the reset with nothing deleted).
- **API JSON for forced-change/deactivated pollers (accepted):**
  `AccountStatusMiddleware` returns JSON 401/403 for `/api/*`
  (`ApiAccountStatusTests`).
- **Title-row numbering dead code (accepted):** preview row numbers now use
  `first_row=start + 2` (`TitleRowNumberingTests`).
- **URI helper robustness (accepted):** read-only connects, no stray-file
  checkpoint creation, no double URI prefixing, collision-free preservation
  names.
- **Stock N+1 (accepted):** one annotated query for stock/inventory exports
  (`StockExportQueryTests` with query-count bounds).
- **`maintenance_mode` masking nit (accepted):** release errors no longer
  mask the block's own exception
  (`test_release_failure_never_masks_the_blocks_own_error`).

---

# Remediation review — independent FIX of e8b3c76 (2026-10-09, worktree)

Scope: the seven findings carried in the task prompt (seed data loss,
unpinned release/misreported probes, uninitialized restore verify, held-guard
recovery, export snapshot, ledger chains, reversal attribution) plus the
deployment string-check coverage gap. The ignored
`.local/review/independent-review-e8b3c76.md` and the private HTML were not
present and were never accessed; all fixtures are synthetic source-shaped
data. Prior SHIP claims in this file are treated as unverified; evidence
below is from fresh runs on this branch.

## Finding → fix → evidence (all behavioral, no string checks)

1. Seed migration data loss (`remain` ignored, `intake_metadata` dropped,
   `arrived/homeStake/missionArrival/missionRelease/tLanguage/type` lost):
   `_norm_item` now selects `remain` (case-insensitive, Stock-minus-Out
   aware); `_norm_person` maps all six original keys to agreed fields;
   `seed_to_bundle_rows` writes `intake_metadata` JSON; unparseable dates are
   kept as `*_raw` metadata. Evidence:
   `SeedFidelityTests` (6 tests: remain→7, all six keys, Stock/Out/threshold
   variants, four negative corrections, seed→bundle→import→export round trip,
   date-raw preservation).
2. Unpinned release + 400 probes: `compose.yaml` image is now
   `${IMAGE_BASE:-${IMAGE:-repo}}:${VERSION}` (tag always from VERSION);
   `.env.example` uses tagless `IMAGE_BASE`; scripts refuse tagged bases,
   verify serving `APP_VERSION`, and atomically persist `VERSION` on success;
   `settings.ALLOWED_HOSTS` always appends loopback/service names (no
   wildcard) so `Host: 127.0.0.1:8000` probes return 200 with LAN-only config.
   Evidence: `ProductionProbeTests` (loopback readyz + real `compose config`
   JSON for 1.2.3/1.2.4/legacy IMAGE); deployment
   `test_production_image_tag_matches_requested_version` (real config JSON,
   two releases) and `test_update_script_persists_release_pin` (executes the
   real persist function).
3. Uninitialized restore verify: new `verify_backup` management command
   (initialized Django) replaces bare `python -c`; `restore.sh` calls it.
   Evidence: `RestoreTakeoverTests.test_verify_backup_command_initializes_django`
   and deployment `test_restore_script_uses_initialized_verify` (real container).
4. Held-guard recovery: `restore_db --force-takeover` (audited, writes stay
   closed) plus re-recorded post-restore takeover audit that survives the file
   swap; scripts accept `--takeover`, stop writers, keep writes closed through
   offline WAL restore/preserved copy/readiness, and print owner/token plus
   explicit takeover recovery steps. Evidence:
   `RestoreTakeoverTests.test_failed_update_window_needs_takeover_then_recovers`
   (refusal without takeover, audited recovery with it) and container drills
   `test_protected_restore_drill_restores_data` (real restore with newer-work
   guard) + `test_failed_update_rollback_in_stack` (held update → refusal →
   audited takeover → readiness → reopen).
5. Export snapshot: all tables read inside one transaction in deterministic
   order with an in-memory ledger gate and bounded retry, so concurrent
   writers yield old-or-new consistent bundles that always pass `parse_bundle`.
   Evidence: `ExportSnapshotTests` (threaded writer+reader, 5 exports all parse).
6. Ledger chains: deterministic `(created_at, id)` per-item validation —
   exactly one leading OPENING with delta==balance, directional deltas,
   running `balance == previous + delta`, no historical negatives, reversals
   linked to REVERSED issues. Aggregate-correct/chain-invalid fixtures
   rejected. Evidence: `LedgerChainTests` (4 tests: OPENING 0/999, ISSUE
   direction, RECEIPT direction + missing OPENING, running 999 with matching sums).
7. Reversal attribution: durable `Issue.reversed_by_username` (migration +
   backfill + service snapshot + save fallback), export prefers snapshot,
   import preserves it without accounts, reports/templates fall back to it.
   Evidence: `ReversalSnapshotTests` (different-destination round trip,
   rename/delete preservation).

Coverage gap: `test_update_failure.py` string/index assertions replaced by
real `compose config` JSON, real persist-function execution, tagged-base
refusals and container `verify_backup`; `test_backup_restore.py` now really
restores (`test_protected_restore_drill_restores_data`) and proves
failed-update takeover (`test_failed_update_rollback_in_stack`). No critical
test is skipped.

## Fresh whole-system sweep (this worktree)

- Re-traced write/auth/import/recovery paths: row-import revisions still
  checked under lock; reset counts inside window; backup manifest/retention
  verified-only; guards nest only with token; force takeovers audited.
- One new in-scope issue found and fixed here: the pre-restore takeover audit
  was lost by the file swap, leaving same-owner retries unaudited. Fixed by
  re-recording `--force-takeover` after the swap (survives restore).
- Laptop-trial find (post-SHIP, fixed with regression): production
  `compose.yaml` forwarded host port to container **443** while the
  Caddyfile serves `{$APP_PORT}` (8443), so TLS died after the TCP open on
  any real `compose.yaml` stack. `compose.test.yaml` had the correct `:8443`
  mapping, and every deployment test resolved only the test file — the
  production mapping was never executed or asserted. Fixed the mapping to
  `${APP_PORT}:${APP_PORT}` and added
  `test_production_caddy_mapping_matches_listen_port`, which resolves the
  production file at two ports (a hardcoded `:443` fails the non-default
  one). Lesson: deployment tests must resolve the production compose file,
  not just the test double.
- No other new blockers found in scope. Out of scope (deferred): certificate
  trust setup, SMTP, signatures, multi-intake/offline sync, zero-downtime.

## Automated evidence (this branch, serial runs)

- `/tmp/opencode/workvenv/bin/python manage.py test --noinput`: 195 tests OK
  (178 prior + 17 new).
- `/tmp/opencode/workvenv/bin/python manage.py makemigrations --check --dry-run`:
  No changes detected. `manage.py check` passes.
- `/tmp/opencode/workvenv/bin/python -m pytest tests/browser`: 17 passed.
- `/tmp/opencode/workvenv/bin/python -m pytest tests/deployment`: 20 passed
  (21 after the production Caddy-mapping regression below).
- `docker compose -f compose.test.yaml config --quiet` passes;
  `docker build -t supplies-tracker:test .` succeeds (exclusions covered).
- `bash -n scripts/update.sh scripts/restore.sh scripts/daily_backup.sh` passes;
  `git diff --check` passes.

## Verdict and gates

VERDICT: SHIP (automated evidence only) — recorded 2026-10-12 after an
independent review of `19376ee`. The reviewer reran every acceptance
command serially on main and behaviorally reprobed all seven findings:

- `.venv/bin/python manage.py test --noinput`: 195 tests, OK.
- `.venv/bin/python manage.py makemigrations --check --dry-run`: no changes.
- `.venv/bin/python -m pytest tests/browser`: 17 passed, no skips.
- `.venv/bin/python -m pytest tests/deployment`: 21 passed (20 + the
  production Caddy-mapping regression).
- `docker compose -f compose.test.yaml config --quiet`, image build,
  `bash -n` on all three scripts, and `git diff --check`: all pass.
- Seed `remain`/metadata mapping, pinned `IMAGE_BASE:VERSION` with matching
  `APP_VERSION`, initialized `verify_backup`, `--takeover` recovery,
  snapshot-consistent exports, ordered ledger chains, and reversal snapshots
  all reprobed with synthetic fixtures and confirmed fixed.

Rollout still requires the user-owned manual gates: target-host
networking/registry/reboot/restore drill, nontechnical staff trial, and the
source manual gate (95/224/four corrections against the separately supplied
private HTML, never force-added). The earlier "no push performed" statement
below is superseded: the operator pushed this branch to the private remote;
no public publish, real-data initialization, or live deploy has occurred.

---

# Product assessment — before milestone 06 implementation

Baseline: `548aad3`. This assessment supports the agreed single product-overhaul
milestone, not implementation or rollout approval. Prior automated SHIP evidence
above remains historical; it does not cover the new probes or proposed redesign.
Only synthetic records were used, in a separate ignored audit database. The
private HTML/roster was not inspected or copied. No application code changed.

## Verified baseline

- `.venv/bin/python manage.py test --noinput`: 195 passed; system check clean.
- `.venv/bin/python -m pytest tests/browser`: 17 passed, no skips.
- `.venv/bin/python -m pytest tests/deployment`: 21 passed, including an actual
  `docker build -t supplies-tracker:test .`, TLS, persistence and recovery.
- `.venv/bin/python manage.py makemigrations --check --dry-run`: no changes.
- `.venv/bin/python manage.py check`: no issues.
- `docker compose -f compose.test.yaml config --quiet`: passed.
- `bash -n scripts/update.sh scripts/restore.sh scripts/daily_backup.sh`: passed.
- `git diff --check`: passed; checkout clean before planning-document changes.

These results establish a regression baseline, not proof of finished usability.
New failure probes are outside the old suites; new tests must cover the actual
interaction and content rather than only DOM hooks or file headers.

## Open findings (most severe first)

| ID | Finding and evidence | Source |
|---|---|---|
| P01 | Recipient ambiguity: type one missionary name, then another. Chromium retained the first UUID while hiding its selected option. Selection/search disagreement can issue to the wrong person. | `templates/issuing/issue.html:86–104` |
| P02 | Roster loss: a routine ID/name-only import cleared existing email, mission and language. A new import with a valid arrival date stored `None`. Reproduced through the real preview/confirm services inside rolled-back synthetic transactions. | `apps/transfers/row_imports.py:275–284`, `apps/transfers/services.py:429–471`, `apps/roster/services.py:27–41` |
| P03 | Settings maintenance race: its write check is outside the mutation transaction. A deterministic boundary probe acquired maintenance immediately after the check; settings were written while maintenance was active (the probe transaction was then rolled back). Settings/audit are also separate autocommit writes in normal use. | `apps/operations/views.py:128–140` |
| P04 | Sensitive-export protection inconsistent: a freshly authenticated administrator without recent password confirmation gets a redirect from `/transfers/bundle/export/`, but a ZIP/200 from `/reports/bundle/export/`. This is a fresh-password-policy bypass, not a staff/anonymous privilege escalation. | `apps/reports/views.py:44–50`, `apps/transfers/views.py:46–52` |
| P05 | Broken ordinary reauth flow: submit Create account without fresh confirmation, enter the correct password on the offered page, and the browser follows a GET to the POST-only creation endpoint, ending at HTTP 405. Existing browser coverage manually chooses a different return URL instead. | `apps/accounts/reauth.py:79–83`, `apps/operations/views.py:169`, `tests/browser/test_admin.py:29–38` |
| P06 | Another-tab sign-in advice is unsafe for an existing draft: sign out/in through a second tab, then submit the original form. Chromium received 403 for its stale CSRF token. Current guide/banner recommends that recovery without repairing tokens or checking submission identity. | `templates/base.html:66`, `templates/guide.html:14`, `static/app.js` |
| P07 | Shortage cancellation drops the draft: request more stock than available, then choose Back without issuing. Chromium found recipient, item, quantity and note blank. The copy promises the original request is retained. Other inventory/roster error paths redirect or rebuild from stored records, discarding entered fields. | `templates/issuing/shortage.html:28`, `apps/issuing/views.py:139`, `apps/inventory/views.py`, `apps/roster/views.py` |
| P08 | Screen/download filters disagree: selecting archived records on Reports leaves family download links without that flag; raw roster/inventory CSV endpoints separately default to including archives. Browser link inspection and view inspection confirm the mismatch. | `templates/reports/home.html:13–28`, `apps/reports/views.py:20–35` |
| P09 | Valid long descriptions break printed reports: a synthetic supported-length item name produced a PDF whose table extended beyond both page edges, hiding most columns. Rendered PDF inspected; current browser tests only assert the `%PDF` signature. | `apps/reports/services.py:241–301`, `tests/browser/test_transfers_reports.py:81–98` |
| P10 | Locale/live presentation unreliable: no NZ locale/timezone is configured; runtime settings report `America/Chicago`/`en-us`. Today queries use a UTC date. Refresh updates some values but not issues-today, dashboard rows or corresponding stock badges/meters; initial dashboard low count uses a capped list. | `config/settings.py`, `apps/operations/views.py:29–44,102–104`, `static/app.js:32–63`, `templates/dashboard.html`, `templates/inventory/list.html` |
| P11 | Older records are not browsable: issue history silently stops at 200 without search/pagination; other lists/detail histories also use fixed caps. This is source inspection, not a synthetic >200-record execution in this assessment. | `apps/issuing/views.py:302–309`, roster/inventory views |
| P12 | Narrow-screen navigation incomplete: below 900px the sidebar disappears and the replacement exposes only five links, with no complete menu for imports, help, administration or theme. Browser navigation inspection confirms the omission. | `static/app.css:233–245`, `templates/base.html:88–94` |
| P13 | Contrast/control inconsistency: computed dark-theme primary-button colours have a 1.86:1 text contrast ratio. Inputs without an explicit type do not match the `input[type=text]` styling; issue quantities/notes visibly use browser-default controls. Active links lack `aria-current`; connection changes lack an appropriate announcement mechanism. This is targeted inspection, not a full accessibility certification. | `static/app.css:35–42,181,187`, `templates/issuing/issue.html`, `templates/base.html`, `static/app.js` |

## Product-design gaps and agreed response

- Reports currently combine about 17 download choices and eight summaries on one
  long page. Select one report with relevant shared filters instead.
- Stored roster context is absent from the profile/edit UI. Show useful task
  context first, expandable details and complete supported editing.
- Summary queries group by historical descriptions and can fragment a stable
  record after renaming. The user chose stable-ID/current-name summaries while
  preserving exact original issue/history snapshots.
- Repeated technical explanations and arbitrary stock percentages obscure the
  next task. Replace them with consistent controls, concise contextual help,
  real status and clear recovery states; keep operator tools out of daily work.

All findings are **open** here. The agreed design/decisions are recorded in
`docs/ARCHITECTURE.md`; `.openchamber/plans/06-product-overhaul.md` covers one
milestone with ordered internal phases. Reproduce failures with committed
synthetic behavioral regressions, fix them, then independently rerun the whole
product acceptance contract. Ignore local audit artifacts as dependencies.

VERDICT: FIX — keep the tested foundation; complete product correctness and
usability before presenting it as finished. Manual staff/source/target-host
gates, certificate trust limitations and host-only backup risk remain explicit.
