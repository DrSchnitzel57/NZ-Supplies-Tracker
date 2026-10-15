# Current status — independent-review follow-up

The independent review of `60d4c9f` returned **FIX** with six findings despite
green baseline suites. All six were reproduced and remediated at `465475b` on
2026-10-13. Fresh acceptance passes: **228 Django, 36 internet-blocked Chromium,
21 real Compose tests**, plus all build/config/migration/syntax/whitespace checks.
Implementation-side automated verdict after remediation: **SHIP**. Independent
re-review remains pending; this does not supersede the reviewer's verdict by
assertion. See the follow-up section below. Manual rollout gates remain pending.

# Historical implementation verdict — milestone 06

**VERDICT: SHIP — automated product acceptance at `138ae8b` (2026-10-12).**
224 Django, 33 internet-blocked Chromium and 21 real Compose tests pass.
Fresh implementation-side review found no remaining product blockers after the
fixes below. This is **not an independent reviewer verdict or rollout approval**.
User-owned staff/source/target-host gates remain pending; no private data was
accessed, no real-data initialization occurred, and nothing was pushed/published.
See the milestone-06 section below for exact acceptance and rendered evidence.

# Historical review — milestone 05 (review remediation and validation)

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

## Findings open at the historical assessment (most severe first)

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

All findings were **open at this assessment** (closed by milestone 06 below). The agreed design/decisions are recorded in
`docs/ARCHITECTURE.md`; `.openchamber/plans/06-product-overhaul.md` covers one
milestone with ordered internal phases. Reproduce failures with committed
synthetic behavioral regressions, fix them, then independently rerun the whole
product acceptance contract. Ignore local audit artifacts as dependencies.

VERDICT: FIX — keep the tested foundation; complete product correctness and
usability before presenting it as finished. Manual staff/source/target-host
gates, certificate trust limitations and host-only backup risk remain explicit.

# Fresh finished-product review — milestone 06

Date: 2026-10-12. Final application/test commit: `138ae8b` on `vivid-walrus`.
Plan: `.openchamber/plans/06-product-overhaul.md`; four ordered phases, one app.
Method: implementation-side final code-path sweep against the plan and P01–P13,
fresh serial reruns of every acceptance command, real internet-blocked Chromium
interaction, visual inspection of generated desktop/light and narrow/dark pages,
and actual PDF extraction/font/geometry/rasterization. This is not an independent
audit or an accessibility certification. All records, images and exports are
synthetic; the ignored private HTML was not used.

## Assessment closure and additional probes

| Finding | Resolution | Behavioral evidence |
|---|---|---|
| P01 recipient ambiguity | Search never chooses silently; hidden mismatched UUID is cleared. Recipient changes confirm before clearing items/note; summary names the recipient. | Chromium `test_typing_never_reuses_a_hidden_recipient`, `test_recipient_change_confirm_clears_items_and_note` |
| P02 import erasure/dates | Preserve omitted/blank optional values in both modes, save new dates, reject legacy field-presence previews. Excel date cells normalize to ISO. | `ProductSafetyTests`: both-mode blank/missing matrix, new dates, legacy re-upload, real XLSX dates; old transfers/seed round trips retained |
| P03 settings guard/audit | One immediate guarded transaction for settings and audit. Invalid settings retain safe bound input. | `test_settings_audit_failure_rolls_back_settings`, `test_settings_guard_runs_inside_transaction`; maintenance guards/concurrency suite |
| P04 export reauth alias | Both full-bundle export routes require recent password confirmation. | `test_report_bundle_alias_requires_recent_password`; updated permission test exercises successful export only after reauth |
| P05 account reauth 405 | POST-only actions return to Accounts/bundle GET screen; no password retention or automatic replay. | Chromium `test_account_lifecycle_with_fresh_identity` now follows the offered path; bound username/password-non-retention tests |
| P06 cross-tab recovery | No-activity `/api/session/` refreshes CSRF; original-user identity checked before deliberate issue/record submission. Different-user drafts stay unsent. | Chromium same-user CSRF, different-user, record-form recovery; Django no-activity token probe; real expiry/poll tests |
| P07 draft loss | Original issue request survives shortage/back, maintenance, offline, uncertain success and error; safe bound record/count/undo input retained with original stale token/key. | Chromium shortage-back/offline/lost-success-reply/maintenance/leave-reload tests; bound-form/stale-count/undo-reason regressions |
| P08 filter mismatch | One selected report, validated shared row/filter contract, complete CSV/PDF, explicit archive filter; raw web CSV defaults agree. | Django archive/date/current-net tests; Chromium filtered archived screen/CSV/PDF real-download probe |
| P09 PDF clipping | Embedded licensed Noto Sans; wrapping/repeated headings; portrait A4 summaries and labeled landscape A4 wide records. | `FinishedReportTests` plus checked native subprocess: macron/accent extraction, embedded font stream, A4 page/word bounds, long history/metadata, rasterization |
| P10 locale/live display | `en-nz`/`Pacific/Auckland`, local-day counts, uncapped low total, refreshed Home rows/status/net/Available; no invented stock percentage meters. | local-day/22-low-item test; Chromium live stock/count-token/focus and idle timer probes |
| P11 unreachable rows | Paginated lists, profile stock/issue history, full filtered issue history and reports; exports remain complete. | 205-history and 501-roster page probes; old untruncated exports retained |
| P12 incomplete narrow menu | All five staff sections plus help/theme/permitted admin destinations accessible through Menu; Escape restores focus. | real 390px menu/theme tests; narrow/dark full-page render with no document overflow |
| P13 contrast/control inconsistencies | Shared bound fields, explicit focus, current-page/status announcements, touch controls, corrected dark primary colour and checkbox sizing. | computed ≥4.5:1 primary text contrast, keyboard focus/reduced-motion, inspected light/dark rendered screens |

Additional sweep findings were reproduced and fixed before the final rerun:
second-shortage tables now compare the **original** request to the **actual new**
proposal (including dropped zero-stock items); archived-history defaults survive
pagination; malformed original confirmation UUIDs yield retained-input 400s,
not 500s; invalid emails/overlong supported fields are preview row errors, not
surprise atomic-confirm failures. No open product finding remains from this pass.

## Product contract beyond the initial findings

- Main and profile entrypoints share one recipient/catalogue/consolidated-summary
  workspace. 50 distinct items commit atomically; 51 fail without partial writes
  in service and HTTP probes. A real keyboard/source-scale test uses 95 synthetic
  missionaries/224 items and verifies the UI's 50/51 boundary.
- Open-tab drafts only: no local/session storage except the theme preference,
  no server draft, autosave, queue or automatic mutation retry. Leaving warns;
  reload discards. A lost reply **after server success** retains/freezes the
  original payload/key; deliberate retry proves exactly one write.
- Complete roster edits include both dates/contact/context fields and deliberate
  optional clearing. Source metadata remains expandable; inventory keeps stock
  actions, reasoned ledger/provenance and archives separate from Edit details.
- Summary totals use stable IDs/current names; history keeps original snapshots.
  Dates select issuance in NZ time with current-net semantics, excluding every
  undone issue even if undone later. Available stock is always stock now.
- Existing stock contention/negative-balance/idempotency, auth/throttling,
  preview subset/ownership/TTL/revision/replacement, lossless/formula-safe ZIP/CSV,
  reset backup and operational recovery coverage remains green. No migrations or
  bundle-format changes. Operator tools remain isolated from staff navigation.

## Fresh acceptance evidence

Commands run from the checkout with the pinned development dependencies:

| Criterion | Result | Command and key output |
|---|---|---|
| 1. Django regressions | PASS | `.venv/bin/python manage.py test --noinput` — 224 tests, OK (153.045s) |
| 2. Real browser workflows | PASS | `.venv/bin/python -m pytest tests/browser` — 33 passed, no skips, all internet hosts blocked (111.79s) |
| 3. Real deployment/recovery | PASS | `.venv/bin/python -m pytest tests/deployment` — 21 passed; isolated image/TLS/persistence/backup/restore/update drills |
| 4. Django configuration | PASS | `.venv/bin/python manage.py check` — no issues |
| 5. Migration drift | PASS | `.venv/bin/python manage.py makemigrations --check --dry-run` — No changes detected |
| 6. Compose configuration | PASS | `docker compose -f compose.test.yaml config --quiet` — exit 0 |
| 7. Production image | PASS | `docker build -t supplies-tracker:test .` — successful image export/tag; production-only dependencies, bundled assets |
| 8. Operator shell syntax | PASS | `bash -n scripts/update.sh scripts/restore.sh scripts/daily_backup.sh` — exit 0 |
| 9. Whitespace | PASS | `git diff --check` — exit 0 |
| 10. Fresh product evidence | PASS | This section: fresh SHIP, observed rendered/export evidence, distinct manual gates |

Local test warnings are non-blocking: the checkout has no collected `staticfiles/`
(the production build collects them); pinned ReportLab emits a Python 3.13
deprecation warning. An overlapping early test run dropped the shared test DB;
it was rejected as evidence and the final Django/browser suites ran serially.
An in-process native PDF teardown crash was fixed by subprocess isolation with
checked exit status, not by suppressing errors; actual renderer failures still
fail acceptance. Logs are ignored `.local/{django,browser,deployment,build}-final.log`.

## Observed rendered/export evidence

The final browser run regenerated these synthetic illustrated help images;
they were visually inspected and refreshed in the tracked `static/img/` files:

- [Sign-in](../static/img/01-sign-in.png) and [Home](../static/img/02-dashboard.png).
- [Single issuing workspace](../static/img/03-issue-goods.png): full named summary,
  catalogue, quantities and one explicit submit; no old hidden-recipient combo.
- [Stock profile](../static/img/04-stock-detail.png): separate Add stock/count forms,
  reason fields, immutable history and version-conflict guidance.
- [Selected report](../static/img/05-reports.png): relevant filters and one CSV/PDF
  pair; corrected checkbox sizing and complete matching-row count.

Reproduce additional ignored artifacts using the two final suites above:
`tests/browser/test_rendered_product.py` produces
`.local/review/narrow-dark-issue.png` (390px, keyboard-focus/menu, no page overflow).
`tests/test_product_reports.py` produces `stock-unicode.pdf`, `stock-unicode.png`,
`long-history.pdf`, `long-roster.pdf` in `.local/review/`; the rendered stock page
was inspected for wrapping/readable columns. The downloaded
`filtered-per-person.pdf` also proves filters and embedded-font content through
a real Chromium download. PNG signatures and `%PDF` headers alone are not proof.
Noto font origin/license/SHA-256 are tracked in `static/fonts/README.md`/`OFL.txt`.

## Manual gates — user-owned, not performed

1. First-time staff unaided issue/add-stock/undo trial, using the in-app guide;
   observe and address hesitation/errors before accepting usability in practice.
2. Compare the approved private source and migration output for full fidelity,
   including raw dates/metadata and four audited negative-source corrections.
   No previously erased data reconstruction is promised; old previews need re-upload.
3. Target-host LAN/firewall/fixed-port/private-registry access, reboot/persistence
   and backup/restore drill on the actual deployment host. No real-data import
   or rollout was performed or authorized by these automated results.

Certificate trust setup remains deferred; LAN HTTPS is tested but clients may
warn until trust is configured. Host-local backups do **not** protect against
host/disk loss. No SMTP, signatures, durable drafts/queue, multi-intake, period
accounting, presentation or new stack/roles were added.

**VERDICT: SHIP for milestone-06 automated product acceptance.** Manual gates
remain separate and pending; this implementation-side review is not a claim
of independent approval or production rollout.

# Independent-review follow-up — 2026-10-13

Reviewed baseline: `60d4c9f`; application fixes/tests: `465475b`.
All six findings are accepted; none declined. New synthetic tests were run
against the baseline first and failed on the reported behaviors, then passed
after remediation. No private source/data was accessed, no real-data import or
rollout occurred, and nothing was pushed. This is remediation evidence for a
fresh independent re-review, not a replacement independent verdict.

| Finding | Change and reason | Regression |
|---|---|---|
| 1. Roster CSV duplicate identity | Canonical headers take priority over aliases independent of column order. Legacy `id` still works only when `external_id` is absent; exported internal UUID no longer becomes the external identity. | `ReviewFollowupTests.test_roster_csv_roundtrip_preserves_identity_in_both_modes` applies real web CSV exports in add/update and replacement; zero creates/archives, stable IDs/issue relationship retained. Header-order/legacy fallback matrix also passes. |
| 2. Cross-form uncertain receipt lost | Serialize safe-form writes through definitive completion. A competing form cannot POST or replace main while another form has an in-flight/uncertain request. Original payload/key remain frozen and retryable; abandoning uncertainty warns before leaving. | Chromium `test_uncertain_receipt_blocks_other_form_and_retries_original_once`: server commits receipt, reply is lost, count form is blocked without POST, original reason/key survive, retry leaves exactly one receipt/stock increment. |
| 3. Stale logout CSRF after recovery | Refresh all page form CSRF fields, including the untouched shell, after same-user session verification; shared helper also serves issuing. | Extended `test_record_form_cross_tab_security_recovery_retains_nonpassword_fields` completes recovered edit, signs out from the original tab and proves `/api/session/` is 401. |
| 4. POST-only URLs on errors | Update browser URL only after a successful redirect. Rejected action POSTs retain the current GET screen URL. | Parameterized Chromium stock-action errors preserve detail URL, follow stock-history pagination and reload with HTTP 200 for both Add stock and Correct stock count. |
| 5. Password silently trimmed | `AdminUserForm.password` uses `strip=False`: hashing/authentication receives the exact password provided. Password widgets still never repopulate values. | Account-creation test with leading/trailing spaces proves exact password authenticates, trimmed password does not. |
| 6. Edited external ID unsearchable | Search current missionary external IDs as well as current names and immutable snapshots. | Edit-before-search test covers report screen/CSV and issue history with both current and old external IDs; snapshot remains unchanged. |

The cross-form tradeoff is deliberate: one safe-form write owns page recovery
until its outcome is known. A second write is blocked rather than letting a
whole-page response destroy the first request's memory. No persistent draft,
queue or automatic mutation retry was added.

## Fresh follow-up acceptance

| Criterion | Result | Exact command / evidence |
|---|---|---|
| 1. Django tests | PASS | `.venv/bin/python manage.py test --noinput` — 228 tests, OK (155.076s) |
| 2. Browser tests | PASS | `.venv/bin/python -m pytest tests/browser` — 36 passed, no skips, internet blocked (135.32s) |
| 3. Deployment tests | PASS | `.venv/bin/python -m pytest tests/deployment` — 21 passed (91.45s) |
| 4. Configuration | PASS | `.venv/bin/python manage.py check` — no issues |
| 5. Migrations | PASS | `.venv/bin/python manage.py makemigrations --check --dry-run` — no changes |
| 6. Compose | PASS | `docker compose -f compose.test.yaml config --quiet` — exit 0 |
| 7. Image | PASS | `docker build -t supplies-tracker:test .` — successful export/tag |
| 8. Shell syntax | PASS | `bash -n scripts/update.sh scripts/restore.sh scripts/daily_backup.sh` — exit 0 |
| 9. Whitespace | PASS | `git diff --check` — clean |
| 10. Product evidence | PASS, implementation-side | Existing rendered/export probes reran; this section records fresh automated SHIP after remediation and keeps manual gates/independent re-review separate. |

Logs: ignored `.local/followup-{django,browser,deployment,build}-final.log`;
before/after targeted regressions: `.local/review-followup-{before,after}.log`
and `.local/browser-followup-{before,after}.log`. Django/browser suites ran
serially. Existing rendered UI and Unicode/long-PDF probes reran successfully;
artifacts regenerate under `.local/review/`. Non-blocking local-test warnings
remain the absent collected-static directory and ReportLab deprecation.

**Pending:** independent re-review and all user-owned staff/source/actual-host
manual gates described above. Certificate trust remains deferred and host-local
backups still do not protect against host/disk loss.
# Milestone 07 — professional redesign implementation evidence

This section is implementation-side evidence, not independent approval. Synthetic
data only; private source fidelity, staff trial, target host/restore/network and
final visual acceptance remain user-owned manual gates. No rollout is authorized.

## Capability and safe-substitution map

| Capability | 07 destination / safety retained |
|---|---|
| Roster, all source fields, archives | Missionaries, profile and full edit; stable IDs and versions |
| Inventory, categories/sizes, alerts | Grouped/sorted Inventory; thresholds, ledger and current availability |
| Multi-item creation | Reviewed 1–50 row batch, atomic opening movements/audit and fingerprinted retry |
| Receipt / physical count / item edit | Short panels with canonical page fallback; reasons, versions and page-owned uncertain requests |
| Issue / profile shortcut | One recipient combobox, catalogue rows and named summary; 50 items and explicit ID selection |
| Negative-stock prototype override | Replaced by explicit shortage review plus atomic server recheck, never negative stock |
| Free-text staff / destructive prototype reset | Authenticated actor; protected admin reset with reauth, confirmation and verified backup |
| History / correction | Direct History, filters/pagination, immutable snapshots and reasoned whole-issue undo |
| Local/global search | Shared bounded all-term matching, exact identity priority, active-only background lookup without idle renewal |
| Reports / low-stock follow-up | Nine families, complete CSV/Unicode A4 PDF, current-net semantics, copy/mail-app without SMTP |
| CSV/XLSX / full interchange | Contextual previews, blank preservation, ownership/TTL/revision, explicit valid-subset approval; admin-only lossless ZIP |
| Accounts/settings/auth/help | Shared controls; roles, reauth/expiry/recovery and synthetic illustrated guide |
| Browser persistence | No personal storage, queue or autosave; drafts and retry payloads stay in open-tab memory |

Design: compact 216px sidebar, Overview landing, direct History, restrained blue
`#006184`, bundled OFL Noto Sans, locally authored SVG icons, neutral tables and
short contextual copy. No Church assets, remote UI dependencies or schema change.
Panels intentionally reuse full bound pages in same-origin dialogs, retaining the
tested write/recovery path rather than implementing a competing client mutation.

## Behavioural and rendered evidence

`tests/test_professional_redesign.py` covers Unicode/cross-field matching,
exact-ID priority, bounded active-only lookup, no idle renewal, expiry, sorted
pagination and blank filters, safe list-return URLs, complete low-stock messages,
review-before-save, 50/51 bounds, duplicates including archives, rollback after
the first row, maintenance, changed-payload/other-actor retries and competing
batches. These extend, not replace, the stock/auth/import/ledger regressions.

`tests/browser/test_professional_redesign.py` uses real Chromium with internet
hosts blocked. It checks delayed out-of-order finder replies, explicit keyboard
identity selection, grouped global results and failures, actual clipboard/mail
content, panel dirty cancellation/focus, vertical and horizontal list return,
same-user panel recovery and parent-shell CSRF repair, blocked competing tasks
during an uncertain save, deferred-script readiness before exposing a panel,
safe cancellation of frame-blocked expired-session loads, quiet signed-out
screens without false draft-expiry warnings, and deliberate batch/receipt retries after the server
commits but its reply is lost. Existing selector-based tests now exercise the
new controls; the old uncertain-write and action-URL regressions still assert
no competing POST, exactly one movement, retained original key/input, and usable
parent GET pagination after a rejected panel action.

The populated rendering test creates **95 synthetic people, 224 items and 210
issues**, including low/out-of-stock cases, long labels and Māori macrons. It
captures Overview, roster, grouped Inventory, the fifth history page, category
reports and issuing at **1440/1280/390px, light and dark**. It checks page overflow,
200% CSS zoom with actual non-overlapping stacked catalogue/summary geometry,
and reduced motion. This is a CSS-zoom stress test, not a claim of automated
browser-zoom or accessibility certification. Existing tests also exercise real
keyboard focus, narrow navigation and primary-button contrast ≥4.5:1.

Implementation inspection of the resulting screens found and fixed oversized
filter checkboxes, unnecessarily stretched empty attention panels, zoom overlap
and full-page screenshot sticky-header offsets. A repeated mobile run also
exposed a panel-readiness race: task forms now remain hidden until deferred
recovery and parent-completion handlers are ready. A controlled delayed-script
probe and three mobile repetitions cover it. Signed-out pages no longer poll
operational APIs or show the resulting false draft-expiry warning. Batch fieldsets
now use full-width three-column fields on desktop and a single column on mobile,
with explicit compact-action geometry checks; this fixes a stretched Remove row
control caught in the actual error capture. Narrow screens keep failure/expiry
warnings visible; a connection failure also shows the last-refresh indicator.
Healthy connection chatter stays quiet.
Final inspection checks long
labels wrapping, aligned availability, visible recipient identity/summary and
short-task error/retry messages; it is not the user's final visual acceptance.
The in-app guide's four committed images in `static/img/` are refreshed from
synthetic real-browser flows, including the actual Add stock panel.

Reproduce the populated matrix and failure captures with
`.venv/bin/python -m pytest tests/browser/test_professional_redesign.py`.
Generated evidence is intentionally ignored at `.local/review/07/` (not an
implementation dependency): `{width}-{theme}-{screen}.png`, `200-percent-zoom.png`,
`search-error.png`, `panel-{dirty,uncertain}.png`, `batch-{duplicate,uncertain}.png`
and `issue-{shortage,offline,expired}.png`. Stock (224 rows), roster (95 people)
and history (210 issues) exports are rendered by the checked subprocess
`tests/pdf_probe.py`: every page has embedded Noto Sans, Māori text, A4 geometry
and word boxes within 25pt of the page edges. Actual first-page raster images
are retained alongside the PDFs; summary PDFs retain portrait A4 and wide records
use labeled landscape A4 blocks. Existing suites cover all nine report families,
complete formula-safe CSV, source preservation, import approval and current-net
versus historical-snapshot semantics.

## Execution status

Application code is committed at `4613c43`, with the synthetic guide/trial update
at `65c4435`. **Automated acceptance: PASS.** Final checks ran serially: 241
Django tests, 53 internet-blocked Chromium cases (no skips) and 21 real isolated
Compose/TLS/recovery tests pass. No unresolved implementation blockers were
found in these checks or the rendered inspection described above. This is not
independent review or rollout approval. An earlier overlapped Django/deployment invocation was invalid: pytest-django initializes
the same file-backed test database even for the container suite. Its lock/missing
table errors are runner contamination, not acceptance evidence. All three suites
must be serial; `AGENTS.md` now states this explicitly. The initial 120-second
test timeout was also insufficient; full suites use a longer runner timeout.

| Acceptance command / behaviour | Result |
|---|---|
| `.venv/bin/python manage.py test --noinput` | PASS — 241 tests |
| `.venv/bin/python -m pytest tests/browser` | PASS — 53 real Chromium cases, no skips; internet hosts blocked |
| `.venv/bin/python -m pytest tests/deployment` | PASS — 21 real isolated Compose/TLS/recovery tests |
| `.venv/bin/python manage.py check` | PASS — no issues |
| `.venv/bin/python manage.py makemigrations --check --dry-run` | PASS — no changes detected |
| `docker compose -f compose.test.yaml config --quiet` | PASS — exit 0 |
| `docker build -t supplies-tracker:test .` | PASS — final application/assets built |
| `bash -n scripts/update.sh scripts/restore.sh scripts/daily_backup.sh` | PASS — exit 0 |
| `git diff --check` | PASS — exit 0 |
| Capability/design and populated render/PDF/error coverage | PASS — recorded above; no unresolved implementation blockers; manual gates separate |

Non-blocking warnings remain the absent development `staticfiles/` directory
(the image collects static assets) and ReportLab's Python 3.13 deprecation warning.

## Separate re-review and manual gates — not performed

- Independent review of milestone 07, including the preserved regression intent.
- Representative staff trial without coaching, and the user's final visual acceptance.
- Private-source fidelity against the operator's original data; no private HTML or real data was used here.
- Target-host LAN/TLS/networking, versioned image download, reboot/update and recovery/restore checks.

Certificate trust setup remains deferred. Host-local backups do not protect
against host/disk loss. Automated acceptance does not authorize publishing,
real-data initialization or rollout.
