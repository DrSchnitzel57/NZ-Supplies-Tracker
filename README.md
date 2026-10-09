# MTC Missionary Supplies Tracker — Shared Application (Milestones 01–03)

Foundation, data invariants, daily staff workflows, portable transfers,
reports and administrator safeguards for the LAN
replacement. `READ ME.txt` remains the prototype's instructions; this README
covers the Django application.

## Prerequisites

- Python 3.13
- `uv` (or any pip-compatible installer)

## Install / run / test

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py check
.venv/bin/python manage.py test tests.foundation tests.auth
.venv/bin/python manage.py test tests.ledger
.venv/bin/python manage.py test tests.concurrency
.venv/bin/python manage.py test tests.workflows tests.auth tests.ledger tests.concurrency
.venv/bin/python manage.py test tests.transfers tests.seed_conversion
.venv/bin/python manage.py test tests.reports tests.admin_safety
.venv/bin/python -m pytest tests/browser/test_workflows.py
.venv/bin/python -m pytest tests/browser/test_refresh_sessions.py
.venv/bin/python -m pytest tests/browser/test_transfers_reports.py
.venv/bin/python -m pytest tests/browser/test_admin.py
```

Browser tests use local Django test services and synthetic data only (no
CDN). The Playwright smoke test needs a browser binary once per machine:

```sh
.venv/bin/python -m playwright install chromium
```

Without the binary that single test skips; every other browser check still
runs against the same HTML a browser receives.

Local database defaults to `.local/db.sqlite3` (gitignored). Override with
`DJANGO_DB_PATH`. Copy `.env.example` to `.env` for local tweaks; never commit
real secrets.

## Notes

- SQLite uses WAL, immediate write transactions and a 5s busy timeout.
- Sessions expire after 30 minutes of user inactivity; `/poll/`,
  `/api/poll/`, `/api/stock/` and `/api/dashboard/` never extend the timer.
  Dashboard/stock numbers refresh about every 15 seconds without touching
  open form drafts; no personal data is stored in browser storage.
- All inventory/issuing writes go through centralized services with reasons,
  version checks, idempotency keys and atomic stock verification. Both the
  main Issue goods screen and per-profile quick issues call the same service.
- Shortages never partially write: staff review asked-for/available/revised
  quantities and confirm, and the server rechecks at commit. Whole issues are
  undone once with a reason; originals are kept and reports use net issuance.
- Staff UI uses plain language (Available, Add stock, Correct stock count,
  Undo this issue). The one-page guide is at `docs/QUICK_START.md`, also
  linked in the app as Quick-start guide.
- Host admin recovery: `python manage.py recover_admin --username NAME [--create]`
  (interactive password prompt; never passed as an argument or logged).
- Transfers: `python manage.py seed_bundle --source "MTC Missionary Supplies Tracker.html" --output .local/imports/initial.zip`
  converts the local prototype into a versioned bundle (gitignored; counts
  only in logs). Full bundles are admin-only, import only into empty data,
  and are described in `docs/DATA_FORMAT.md`. Host install import:
  `python manage.py import_bundle BUNDLE.zip --username ADMIN`.
- Spreadsheet imports accept `.csv`/plain `.xlsx` with previewed row errors;
  add/update may apply an approved valid subset, replacement needs every row
  valid plus an administrator. Reports offer staff CSV/PDF downloads with net
  issuance; full reset lives in the destructive admin area (recent password
  confirmation + typed RESET + verified backup, accounts and one marker kept).
- Tests use synthetic fixtures only (`tests/factories.py`). The ignored local
  HTML prototype is obtained separately from the operator on fresh clones.
