# Supplies tracker architecture

## Status and goal

Milestone 01 (foundation and data invariants) is implemented and tested: Django scaffold, local identity, stable records, ledger with atomic mutations, maintenance guards and synthetic suites (`tests.foundation`, `tests.auth`, `tests.ledger`, `tests.concurrency`). Milestone 02 (daily staff workflows) is implemented and tested: server-rendered dashboard/roster/inventory/issuing/reports screens on the milestone 01 services, one shared issuing service for main-screen and profile entrypoints, shortage review with rechecked confirm, whole-issue undo with kept history, read-only 15-second polling that never extends sessions, one-page quick-start (`docs/QUICK_START.md`) and synthetic suites (`tests.workflows`, `tests/browser/`). Milestone 03 (transfers, reports and administrator safeguards) is implemented and tested: versioned ZIP/CSV interchange (`docs/DATA_FORMAT.md`, `apps/transfers`), ignored local seed conversion (`seed_bundle`), validated spreadsheet previews with stale-token and replacement guards, staff CSV/PDF report families with net issuance (`apps/reports`, bundled PDF rendering), administrator account UI/system settings and protected empty reset with verified backup (`tests.transfers`, `tests.seed_conversion`, `tests.reports`, `tests.admin_safety`, `tests/browser/test_transfers_reports.py`, `test_admin.py`). Milestone 04 remains planned work; the milestone is not a deployable replacement yet. The repository also contains the standalone HTML prototype and `READ ME.txt`; milestones 03–04 remain planned work.

Replace browser-local persistence with a dependable LAN tool for approximately ten simultaneous staff until the Power App arrives. Preserve dashboard, roster, inventory, issuing, low-stock alerts, imports and reports. Use one active roster, not a multi-intake system. The original HTML remains a functional reference and approved initial data source, not production frontend code.

## Runtime and boundaries

- Django 5.2 LTS, Python 3.13, server-rendered templates, small JavaScript enhancements, Gunicorn and SQLite. Pin supported patch versions and dependencies during implementation.
- `config/` owns settings, URLs and deployment entrypoints. `apps/` separates accounts, roster, inventory, issuing, transfers, reports, audit and operations. Templates/static assets are local; `tests/` uses synthetic data only.
- Business services own mutations; views, importers and commands invoke the same services. Database constraints and atomic transactions enforce invariants independently of UI validation.
- One application host, one local-disk SQLite database; no live database on OneDrive/network storage. Configure WAL, bounded busy timeout and immediate write transactions. Do not rely on SQLite row-level `select_for_update`; prove concurrent writes using separate connections/processes.
- Compose runs application and Caddy; static assets and persistent app data are separated from image layers. A fixed LAN IP/port is configured by the operator. Caddy issues local certificates covering the configured address; browsers warn unless devices trust the local CA. Only HTTPS is exposed; admin/proxy management interfaces are not public.
- Firewall/bind configuration restricts access to the approved LAN. No router port forwarding. Host prerequisites, Docker Desktop licensing where relevant and host disk security are operator checks. Certificate trust setup/distribution is out of scope for now; browser warnings remain a known limitation, not a task or acceptance criterion.

## Data and business rules

- Internal immutable IDs connect roster entries, items, issues, issue lines, movements and audit events. Missionary external IDs remain unique strings; item matching normalizes category/item/size. Renames do not rewrite past issue snapshots.
- Items can be archived/reactivated without losing balances or history. Archived records cannot receive new issues; historical reversals remain possible. Account deactivation invalidates active sessions.
- Opening balances, receipts, physical-count adjustments, issues and reversals are recorded movements. Remaining is authoritative from the ledger; Out is net recorded issuance, not arbitrary stock reductions. Display corrections separately so Stock/Out labels do not misrepresent losses or count changes.
- Never commit a negative available balance. Every submission rechecks availability; duplicate submissions have idempotency keys. A full issue either commits all accepted lines or none. On shortage, return an explicit revised proposal; staff confirm it, and the server rechecks again. Do not silently reduce quantities.
- Staff can reverse a complete issue once, with a reason; reversal restores stock, retains original lines and actor, and contributes correctly to net reports. No partial reversal in this first release.
- Forms use record/version checks to prevent stale edits or physical counts silently overwriting newer work. Concurrent writes, reset, imports and updates coordinate through maintenance/write guards.

## Identity and permissions

- Local accounts only. Administrators create users/reset temporary passwords; require a password change before application access. A documented host command recovers an administrator without exposing passwords in logs or command arguments.
- Staff manage routine rosters, items, adjustments, add/update imports, issuing/reversals and reports. Administrators additionally manage users, replacement imports, full bundles, full reset and system settings. Explicit permissions are checked on every server entrypoint.
- Secure cookie sessions, CSRF protection, password validation, bounded login throttling and no secrets in images/logs. Thirty minutes of user inactivity expires a session; background polling does not extend it. Sensitive admin actions require recent password reauthentication.
- Full reset requires typed confirmation, reauthentication, exclusive maintenance, and a verified backup. Empty operational tables only; preserve accounts and a protected reset audit marker. Do not recreate the seed automatically. Previous operational audit content is part of the backed-up data, not retained as a hidden live copy after reset.

## Imports, initialization and migration

- Standard full-data interchange is a versioned ZIP: manifest plus CSV tables for settings, roster, items, issues/lines, movements and operational audit. Specify types, encoding, timestamps, checksums, ID references, exclusions and reversible spreadsheet-safety encoding. Exclude accounts, credentials and sessions. Database backups, not bundles, provide exact recovery.
- A local converter extracts `SEED` without executing the HTML and writes `.local/imports/initial.zip`, which will be gitignored. Original seed: 95 roster entries, 224 items, no distributions, four negative balances. Preserve their original values as correction metadata; initialize corrected balances at zero with four audit records, never negative live balances.
- The original HTML is now local-only and gitignored; the user authorized removing it from branch history. Obtain it separately from the operator on fresh clones. Prior downloads and cached GitHub objects may still exist; never force-add the file. Exclude HTML, `.local/`, databases, bundles, backups and secrets from image build contexts; CI/tests use synthetic seeds only.
- Full-bundle import/export is administrator-only; import only into empty operational data under a write guard. Initialization runs explicitly once, never on startup/update. Reject unsupported versions, bad checksums/references, duplicate IDs and invalid balances before writing.
- Individual roster/inventory CSV and XLSX imports retain documented existing header aliases. Bound uploads/archive expansion, ignore unsupported/macro formats, and never evaluate formulas. Preview row errors and planned changes. Add/update imports may apply an explicitly approved valid subset; replacement imports require a wholly valid file.
- Roster matches by external ID; inventory by normalized category/item/size. Existing inventory counts become reasoned physical-count movements using Remaining or Stock minus Out; supplied Out never fabricates distributions. Replacement archives missing records rather than deleting them. Previews carry revision tokens; changed source data invalidates confirmation.
- Individual CSV/PDF reports remain available to staff. Complete bundles include archived records and original/reversed history. Reports distinguish net issuance from original records and filter active versus archived data explicitly.

## UI and operations

- Preserve familiar responsive navigation and core screens. Authenticated staff identity replaces typed Issued by. No handwritten signature collection or offline-browser synchronization.
- Optimize for nontechnical staff: Issue goods is the primary action; use Available, Add stock, Correct stock count and Undo this issue instead of internal accounting jargon. Keep imports/archives secondary, hide administrator-only tools, provide contextual help and specific outcome messages. Preserve familiarity, not every prototype layout detail.
- Provide a linked, one-page illustrated `docs/QUICK_START.md` using synthetic examples. Manual usability acceptance before rollout: a first-time representative staff member issues goods without coaching, then adds stock and corrects an issue with the guide. Observe hesitation/errors and address confusing UI rather than assume it is intuitive.
- Separate staff from operator complexity: staff use a browser link/account, administrators manage accounts through the UI, and the operator owns installation, updates, backup monitoring and host/network recovery. Full reset lives in a clearly marked destructive admin area, never ordinary troubleshooting.
- Poll read-only dashboard/stock values approximately every 15 seconds, without disturbing drafts or extending sessions. Clearly show connection/save failures and preserve drafts in memory; do not silently queue offline mutations or persist personal data in localStorage.
- Bundle fonts and dependencies. Routine work needs only the LAN host. Low-stock alerts can be copied or opened using `mailto:`; no SMTP service. Browser PDF/clipboard/mail behavior needs client checks.
- Daily SQLite-safe backups on the host, 30-day daily retention; preserve the latest successful pre-update recovery backup until superseded. Verify integrity/checksums and document restore. A host/disk failure can destroy both live data and these backups.
- Versioned private GHCR images built/tested by GitHub Actions. The host has a separate least-privilege registry download credential, not a developer's general token. No source checkout/build required there.
- Operator-triggered updates: validate selected version/config, enter maintenance, quiesce writes, verify backup, pull pinned images, migrate, restart and check readiness. Fail closed rather than continue with half-upgraded data. Rollback may require matching image plus database restore; no unattended latest updates or zero-downtime promise.

## Decisions and why

- **Modular monolith over patching the HTML or microservices:** preserves workflows while replacing tangled persistence and centralizing inventory correctness, without service sprawl.
- **Django/templates over React plus a separate API:** built-in identity/migrations and fewer independently maintained components suit this temporary single-host tool.
- **SQLite over PostgreSQL:** adequate for ten users and one writer-owning server, simpler recovery/operations; revisit if multi-server or sustained contention is needed.
- **Local accounts over Microsoft/SMTP dependencies:** no paid Microsoft services are available; administrators handle provisioning/recovery.
- **Ledger, stable IDs and retained history over overwritten totals/name joins:** support concurrent correctness, corrections and the eventual migration.
- **Explicit partial fulfillment over silent reduction or blanket rejection:** staff can finish available work but must confirm the revised list.
- **Single roster and staff attribution over multi-intake/signature expansion:** preserve the requested scope without adding unused features.
- **ZIP/CSV manifest over opaque database interchange:** portable and inspectable for Power App migration while representing complete relationships; backups remain the recovery source.
- **Compose/private prebuilt images over on-host builds or OS-specific installers:** consistent deployment and controlled remote updates.
- **LAN HTTPS/self-issued certificates over public hosting:** matches available infrastructure; trust warnings and host controls remain explicit operational limitations.
- **Guided everyday workflows over exposing technical controls:** nontechnical staff need plain language, progressive disclosure and a short guide; operational complexity stays with the operator. Certificate trust configuration is deferred by user request, while HTTPS remains required.
- **Immediate transactions plus bounded retry of transient SQLite locks (milestone 01):** WAL and `BEGIN IMMEDIATE` serialize writers, but contended writers can still see transient `database is/table locked` errors; write services retry the whole atomic block a bounded number of times so the loser re-reads fresh state and raises the proper domain error (shortage, stale version, already-reversed) instead of a raw database error.
- **Host-only backups over external backup infrastructure for now:** accepted initial constraint, not protection against host loss.

## Planning references

See `docs/ROADMAP.md` and `.openchamber/plans/01-foundation.md` through `04-deployment.md`. Commands in plans are future implementation contracts, not commands that currently exist or have passed. No app implementation or registry publication is authorized by these planning documents alone.
