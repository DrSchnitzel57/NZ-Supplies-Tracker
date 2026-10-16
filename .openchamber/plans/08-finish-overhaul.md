# 08 — Finishing overhaul

## Steps
- [x] 1. Use the app with a full-size roster; list what breaks or confuses.
- [x] 2. Fix data fidelity (departure date, arrival values, import aliases, bundle v2).
- [x] 3. Names as staff say them; staff display names.
- [x] 4. NZ date formats, natural size order, sizes in summaries, readable stock history.
- [x] 5. Remove the Django admin site.
- [x] 6. New visual system and redesigned Overview, Issue supplies, Inventory, Missionaries, History, Reports and supporting screens.
- [x] 7. `load_demo_data` command; regenerate synthetic screenshots.
- [x] 8. Tidy docs (README, install guides, quick start, history folder, AGENTS).
- [x] 9. Regressions (`tests/finish_overhaul.py`), full serial suites.

## Goal and context
Make the app something to hand to the MTC as a finished product: correct with
the real source data, quick for the daily task (issuing), and clear at a
glance (Overview). Not an official Church product; temporary until an
official app replaces it.

## Scope and decisions (agreed with the user)
- Native Windows install is the recommended path; Docker and Kubernetes stay.
- Certificate: leave Caddy's local CA; offer the free `/ca.crt` download and
  Group Policy steps. No paid certificates or outside accounts.
- Schema changes only where they improve the experience and keep the
  original-HTML import working: `Missionary.departure_date`,
  `User.display_name`. Bundle v2, still reads v1.
- Missionaries shown as "Elder/Sister + surname".
- Redesign the key screens; keep Django/SQLite/templates and every safety
  contract (ledger, idempotency, maintenance, previews, uncertain-write recovery).
- Deliver as a new local branch; do not push.

## Validation
- Django suite, browser suite (internet blocked) and deployment suite run
  serially; `makemigrations --check`, `git diff --check`.
- Real-data checks only on a local, ignored database; committed screenshots
  and fixtures are invented.
- PDF probes need PyMuPDF; Windows scripts are syntax-checked on Linux pwsh
  and still need a run on a real Windows PC.
