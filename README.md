# MTC Missionary Supplies Tracker — Shared Application (Milestones 01–06)

Foundation, data invariants, daily staff workflows, portable transfers,
reports, administrator safeguards, deployment/recovery and review
remediation for the LAN
replacement, with the finished five-section staff workspace. `READ ME.txt` remains the prototype's instructions; this README
covers the Django application.

## Prerequisites

- Python 3.13
- `uv` (or any pip-compatible installer)

## Install / run / test

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
mkdir -p .local
.venv/bin/python -m playwright install chromium
.venv/bin/python manage.py migrate
.venv/bin/python manage.py check
.venv/bin/python manage.py test --noinput
.venv/bin/python -m pytest tests/browser
.venv/bin/python -m pytest tests/deployment
```

`requirements.prod.txt` is the production-only subset shipped in the image
(no pytest/playwright/native PDF test renderer). Browser and deployment suites are real:
Chromium flows with all internet hosts blocked, and isolated Compose
TLS/backup/restore drills with throwaway volumes. The Playwright browser
needs its binary once per machine:

```sh
.venv/bin/python -m playwright install chromium
```

Without the binary the browser suite fails (it never skips); CI installs it
automatically (see `.github/workflows/tests.yml`).

Run Django and browser suites serially: they share a file-backed test database.
PDF tests extract text/font/geometry and rasterize in a checked subprocess.
Final milestone-06 acceptance: 224 Django, 33 browser (no skips) and 21 deployment
tests pass. See `docs/REVIEW.md` for fresh rendered/export evidence and the
separate pending staff/source/target-host manual gates. No rollout performed.

Local database defaults to `.local/db.sqlite3` (gitignored). Override with
`DJANGO_DB_PATH`. Copy `.env.example` to `.env` for local tweaks; never commit
real secrets.

## Notes

- SQLite uses WAL, immediate write transactions and a 5s busy timeout.
- Sessions expire after 30 minutes of user inactivity; `/poll/`,
  `/api/poll/`, `/api/stock/`, `/api/dashboard/` and `/api/session/` never extend the timer.
  Dashboard/stock numbers refresh about every 15 seconds without touching
  open form drafts; no personal data is stored in browser storage.
- All inventory/issuing writes go through centralized services with reasons,
  version checks, idempotency keys and atomic stock verification. Both the
  main Issue goods screen and missionary profiles enter one workspace, with
  explicit recipients and up to 50 distinct items; repeats consolidate.
- Shortages never partially write: staff review asked-for/available/revised
  quantities and confirm, and the server rechecks at commit. Whole issues are
  undone once with a reason; originals are kept and reports use net issuance.
- Drafts last only in the open tab; reload/close discards them. Same-user
  cross-tab login refreshes security tokens before deliberate retry. Uncertain
  replies retain the original request/key; there is no automatic replay/queue.
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
- Optional blank/missing import cells preserve values in both modes; edit forms
  allow deliberate clearing. Old previews require re-upload. Shared report
  filters apply to screen/complete CSV/embedded-Unicode A4 PDF; dates use
  Pacific/Auckland/current net, while Available stock always means stock now.
- Tests use synthetic fixtures only (`tests/factories.py`). The ignored local
  HTML prototype is obtained separately from the operator on fresh clones.
