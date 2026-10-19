# Repository context

MTC Supplies Tracker: a self-hosted Django 5.2 LTS / Python 3.13 / SQLite (WAL)
app for the New Zealand MTC supplies room, replacing a single-file HTML
prototype. Server-rendered templates plus three small vanilla JS files
(`static/app.js`, `components.js`, `issuing.js`); no build step, no CDN.
Apps: `accounts`, `roster`, `inventory`, `issuing`, `audit`, `operations`,
`transfers` (bundles, seed conversion, spreadsheet imports), `reports`
(screen/CSV/PDF), `common` (finder, sorting, template filters).

Deployment paths: native Windows (`deploy/windows/`, Waitress + Caddy +
Task Scheduler, `mtc` command; docs/INSTALL_WINDOWS.md), Docker Compose
(Gunicorn + Caddy; docs/INSTALL_DOCKER.md, docs/OPERATIONS.md) and a
single-replica Kubernetes example (`deploy/kubernetes/`). All three use
Caddy `tls internal` and serve the local root at `/ca.crt`.

Milestones 01–08 are implemented. Plans: `.openchamber/plans/`. Design and
reasons: `docs/ARCHITECTURE.md`. Earlier verification logs, reviews and the
roadmap: `docs/history/`.

## Working rules

- Do not implement or commit on `main`; work on a user-approved branch or
  worktree. Never push.
- Run suites **serially** (they share the file-backed test DB
  `.local/test_db.sqlite3`): `manage.py test --noinput`, then
  `pytest tests/browser`, then `pytest tests/deployment` (needs Docker).
  Also `manage.py check`, `makemigrations --check --dry-run`, `git diff --check`.
- Fresh checkout: `uv venv .venv --python 3.13`,
  `uv pip install --python .venv/bin/python -r requirements.txt`,
  `mkdir -p .local`, `.venv/bin/python -m playwright install chromium`.
- PDF probes use test-only PyMuPDF in a subprocess (`tests/pdf_probe.py`);
  without it four PDF tests fail locally. Browser tests block all internet.
- Tests and screenshots use synthetic data only. `load_demo_data` makes an
  invented, realistic dataset (refuses non-empty databases) for screenshots
  in `static/img/` and demos.
- Bundle format is versioned (`apps/transfers/bundle.py`, currently v2,
  reads v1). Any roster/item field change needs a version bump, a legacy
  header entry and a compatibility test; seed conversion and the original
  HTML import must keep working.

## Data and behavioural traps

- The original `MTC Missionary Supplies Tracker.html` embeds a `SEED` with
  real missionary names, IDs and emails. It is gitignored and local-only.
  Never commit, force-add, copy into docs/fixtures/logs/images, or ship it.
  Convert it into ignored `.local/imports/initial.zip` and import explicitly
  into empty data, never on startup/update. Real-data screenshots stay
  internal; committed images must come from invented data.
- Source quirks handled by `apps/transfers/seed.py`: `arrived` is a boolean
  (normalised to `arrived`/`expected`), `missionArrival` is the day they leave
  the MTC (stored as `departure_date`), names are `Family, Given` and some are
  ALL CAPS (display tidies them), four items have negative balances (zeroed
  with audit rows), there is no distribution history.
- Never allow negative stock; every stock change goes through the ledger
  services (`StockMovement`) atomically with idempotency keys. Relationships
  use stable IDs, not names. Issues are undone (reversed) with a reason,
  never deleted.
- Polling (15 s) is read-only and never extends the 30-minute idle session.
  Drafts live only in the open tab: no autosave, offline queue or background
  writes. Shortages need explicit revised-quantity confirmation and a second
  server stock check.
- Writes are blocked during owned maintenance windows (updates, restores,
  backups with `--quiesce`); crashed windows stay closed until an audited
  takeover.
- The Django admin site is removed on purpose (it bypassed login throttling);
  administrator tools live under `/accounts/users/`, `/admin-area/` and
  `/transfers/`.

## Product decisions

- Staff-facing language is plain: Issue supplies, Available, Add stock,
  Count / Correct stock count, Undo this issue, Needs reordering. Missionaries
  are shown as staff address them ("Sister Halvorsen", "Elder & Sister X" for
  couples); staff accounts have an optional display name.
- Dates use New Zealand formats (`config/formats/en_NZ`). Sizes sort
  naturally (`apps/common/sorting.py`).
- One active roster; no signatures, SMTP or offline sync. The reorder list
  offers copy and a `mailto:` link only.
- Not an official Church product: a temporary stop gap until an official
  app exists. No paid services or outside accounts.
