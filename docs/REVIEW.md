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
- No other new blockers found in scope. Out of scope (deferred): certificate
  trust setup, SMTP, signatures, multi-intake/offline sync, zero-downtime.

## Automated evidence (this branch, serial runs)

- `/tmp/opencode/workvenv/bin/python manage.py test --noinput`: 195 tests OK
  (178 prior + 17 new).
- `/tmp/opencode/workvenv/bin/python manage.py makemigrations --check --dry-run`:
  No changes detected. `manage.py check` passes.
- `/tmp/opencode/workvenv/bin/python -m pytest tests/browser`: 17 passed.
- `/tmp/opencode/workvenv/bin/python -m pytest tests/deployment`: 20 passed.
- `docker compose -f compose.test.yaml config --quiet` passes;
  `docker build -t supplies-tracker:test .` succeeds (exclusions covered).
- `bash -n scripts/update.sh scripts/restore.sh scripts/daily_backup.sh` passes;
  `git diff --check` passes.

## Verdict and gates

VERDICT: HOLD — all seven findings remediated with behavioral regressions and
real container proof above, but rollout still requires (a) an independent
review of this branch and (b) the user-owned manual gates: target-host
networking/registry/reboot/restore drill, nontechnical staff trial, and the
source manual gate (95/224/four corrections against the separately supplied
private HTML, never force-added). No real-data initialization, push, publish,
or deploy was performed from this worktree.
