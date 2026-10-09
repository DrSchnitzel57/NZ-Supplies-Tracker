# Repository context

- The replacement Django foundation (milestone 01) is implemented: `manage.py`, `config/`, `apps/` (accounts, roster, inventory, issuing, audit, operations, common), `templates/`, `static/`, pinned `requirements.txt` (Django 5.2 LTS), and synthetic tests in `tests/` (`foundation`, `auth`, `ledger`, `concurrency`). The standalone HTML prototype remains entirely in the ignored `MTC Missionary Supplies Tracker.html`: embedded `SEED` data, styles, DOM rendering, business logic, and browser persistence. `READ ME.txt` describes this local edition; `README.md` covers the Django app's install/run/test commands.
- Verified milestone-01 commands (run from repo root with the project venv): `uv pip install --python .venv/bin/python -r requirements.txt`; `.venv/bin/python manage.py migrate`; `.venv/bin/python manage.py check` (passes); `.venv/bin/python manage.py makemigrations --check --dry-run` (no missing migrations); `.venv/bin/python manage.py test tests.foundation tests.auth` (passes); `.venv/bin/python manage.py test tests.ledger` (passes); `.venv/bin/python manage.py test tests.concurrency` (passes: competing issues cannot go negative, duplicate reversals fail safely, stale counts lose, same-key concurrent submits write once, maintenance blocks other connections); `git diff --check` (passes). Baseline: 50 tests (26 + 18 + 6), Python 3.13, Django 5.2.18.
- Startup runs `initTheme()`, builds navigation, and calls `go("dashboard")`; `RENDER` contains the five screens: dashboard, missionaries, inventory, issue goods, and reports.
- `loadState()` reads `mtc_intake_stores_v2` from localStorage, falling back to `SEED`. `save()` silently catches storage errors. Hosting or sharing the HTML does not share edits or update the file.
- Fonts, PDF export (jsPDF/AutoTable), and XLSX import load from external CDNs. Do not assume browser caching guarantees offline availability. CSV import/export uses local code; `trySave()` falls back from an optional Claude downloads integration to browser downloads.

## Data and behavioral traps

- `SEED` contains personal roster data, including names, IDs, and emails. Preserve it as the approved initial migration source; do not copy it into documentation, logs, test fixtures, or distributed application images. Its presence in private Git history is not authorization to publish it.
- Distributions currently reference missionaries by name and contain item snapshots rather than durable relationships. Renaming a missionary rewrites matching distribution names; migration must resolve these relationships explicitly.
- Both `dwSubmit()` and the Issue Goods submit handler record distributions and alter stock. Inventory also permits direct edits to stock/out totals. Changing only one path does not enforce an inventory rule globally.
- The prototype permits negative stock after confirmation, and "Issued by" is free text. Neither behavior meets the agreed replacement requirements.
- Reset reloads `SEED`; clear deletes browser-held data. Do not carry these unrestricted destructive controls into the shared application.

## Agreed replacement direction (not implemented)

- Preserve the core roster, inventory, issuing, low-stock, import/export, and reporting workflows; retain familiar navigation rather than pursue a cosmetic redesign.
- One self-hosted modular application with SQLite on the host's local disk, serving roughly 10 simultaneous users. All browsers use the same server; no OneDrive persistence or live database on a network share.
- Local staff accounts with admin provisioning/recovery and 30-minute idle expiry; polling cannot extend sessions. Staff handle daily workflows and reasoned whole-issue reversals; admins additionally control accounts, replacement imports, full bundles and full reset.
- Never allow negative stock. Enforce stock checks and distribution writes atomically on the server, including concurrent requests. Use stable IDs instead of names for relationships.
- The HTML is local-only and gitignored; obtain it separately from the operator on fresh clones. Its original commit was rewritten with user authorization; prior downloads/caches may still exist. Never force-add it. Convert its data into gitignored `.local/imports/initial.zip`; import explicitly into empty operational storage, never on startup/update. The seed has 95 missionaries, 224 items, no distribution history and four negative balances; user approved zeroing those with audit records.
- Docker Compose deployment, LAN-only HTTPS at a fixed address/port, and private versioned prebuilt images. The internet-connected host should pull releases, not build source. Self-signed certificates are acceptable, but browser trust warnings remain unless trust is configured.
- Controlled, remotely manageable updates with pre-migration backups, health checks, and a brief interruption; no unattended `latest` upgrades. Database rollback needs its own recovery strategy.
- Daily host-local backups with 30-day retention and verified pre-update backups; these do not protect against host/disk loss. Full reset requires recent reauthentication, typed confirmation and verified backup; empty operational data but keep accounts and a minimal reset audit marker.

## Planning handoff

- Read `docs/ARCHITECTURE.md`, `docs/ROADMAP.md` and the relevant numbered plan in `.openchamber/plans/` before implementation or review. Four dependency-ordered milestones cover foundation, workflows, transfers, deployment.
- Agreed stack: Django 5.2 LTS/Python 3.13, server templates/small JavaScript, SQLite, Gunicorn and Caddy. Milestone 01 (foundation/data invariants) is implemented with verified commands above; milestones 02-04 plan commands remain future contracts, not runnable or passing baseline commands; update this file with verified commands as further tooling lands.
- One active roster only; no signatures, SMTP or offline sync. Bundle runtime assets. Preserve draft forms during 15-second stock/dashboard polling. Shortages require explicit revised-quantity confirmation and another server stock check.
- Staff-facing UI uses plain language (Available, Add stock, Correct stock count, Undo this issue), prioritizes issuing and links a one-page illustrated quick-start. Hide admin tools; keep operational commands out of staff workflows. Certificate trust setup is deferred, not a deliverable; LAN HTTPS remains required.
- Standard full interchange is a versioned ZIP/CSV manifest; admin-only full import/export, empty-data full import, no accounts/secrets. Routine imports preview changes; approved valid subsets are allowed only for add/update, not replacement. Replacements archive missing records; inventory counts create ledger adjustments, not invented issues.
