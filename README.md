# MTC Missionary Supplies Tracker — Shared Application (Milestone 01)

Foundation and data invariants for the LAN replacement. `READ ME.txt` remains
the prototype's instructions; this README covers the Django application.

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
```

Local database defaults to `.local/db.sqlite3` (gitignored). Override with
`DJANGO_DB_PATH`. Copy `.env.example` to `.env` for local tweaks; never commit
real secrets.

## Notes

- SQLite uses WAL, immediate write transactions and a 5s busy timeout.
- Sessions expire after 30 minutes of user inactivity; `/poll/` and
  `/api/poll/` never extend the timer.
- All inventory/issuing writes go through centralized services with reasons,
  version checks, idempotency keys and atomic stock verification.
- Host admin recovery: `python manage.py recover_admin --username NAME [--create]`
  (interactive password prompt; never passed as an argument or logged).
- Tests use synthetic fixtures only (`tests/factories.py`). The ignored local
  HTML prototype is obtained separately from the operator on fresh clones.
