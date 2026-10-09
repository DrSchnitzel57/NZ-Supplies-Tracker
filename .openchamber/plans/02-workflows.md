# 02 — Daily staff workflows

## Goal and context
Build the familiar five-screen staff experience on milestone 01 services, replacing browser-local state rather than reusing prototype mutations. See `docs/ARCHITECTURE.md`. One active roster; no multi-intake workflow.

## Scope
In: responsive dashboard, roster, inventory, issuing, basic on-screen reports, stock refresh and correction flows. Out: transfer files, full reports/export, reset, deployment and signatures.

## Steps and affected areas
1. Build shared Django templates/navigation and local static assets in `templates/` and `static/`. Preserve familiar navigation, search, category/size filters, thresholds and readable narrow-screen layouts. Make Issue goods the primary action. Show current user and sign-out, not a typed issuer field. Use Available, Add stock, Correct stock count and Undo this issue; avoid ledger/movement/reversal jargon. Keep bulk imports/archives secondary and hide administrator-only tools from staff.
2. Wire `apps/roster` and `apps/inventory` views to permission-checked services. Support edits, archive/reactivate, receipts and reasoned physical-count adjustments. Archived records stay available in history but are absent from new-issue selectors. Display balance/net issuance/adjustments with honest labels rather than editable Stock/Out fields.
3. Implement both profile-based and main-screen issuing with one shared service and multi-line drafts. Consolidate duplicate item demands. On shortages, show original/current/revised quantities and require confirmation; revalidate again at commit. No silent partial writes. Keep drafts after validation/connection errors; prevent duplicate clicks with server idempotency.
4. Add whole-issue reversal UI with a reason and history showing original issuer/time and reversal actor/time. Original snapshots remain unchanged; on-screen consumption/per-person summaries use net issuance, with original/reversed details discoverable.
5. Poll read-only dashboard/stock approximately every 15 seconds without rerendering forms, replacing drafts or extending idle sessions. Show connection status and last successful refresh. Never queue offline mutations or store personal data in localStorage. Show a sign-in-expiry warning; sensitive draft handling stays in memory.
6. Add contextual help and clear, specific success/error messages, including shortages and duplicate submissions. Write `docs/QUICK_START.md`: one illustrated page covering sign-in, issuing, adding stock and undoing mistakes, using synthetic screenshots. Link it from the app. Staff need only a browser link/account, never Docker or terminal commands. Add browser tests in `tests/browser/`, pinned Playwright/pytest tooling and documented `python -m playwright install chromium` setup. Use local Django test services and synthetic data, no CDN. Cover desktop/narrow layouts, keyboard forms, plain labels/help, shortages, reversals, polling, expired sessions and error feedback. Add `tests.workflows` for routes/permissions.
7. Update verified commands in `AGENTS.md`, tick milestone 02 in the roadmap, and update architecture if design changed.

## Risks
Old HTML has two separate issue implementations; both new entrypoints must call the same service. Polling cannot steal focus or extend sessions. Staff attribution is not a missionary signature. Archived records still need reversible history.

## Manual checks
Usability acceptance before rollout: a representative first-time, nontechnical staff member signs in and issues goods with synthetic data without coaching. With the one-page guide, they also add stock and undo an incorrect issue without a terminal or technical explanation. Record hesitation/errors and fix confusing UI; review actual touch layouts. These checks are for the user, not automated implementation.

## Acceptance criteria
1. `python manage.py test tests.workflows tests.auth tests.ledger tests.concurrency` passes.
2. `python -m pytest tests/browser/test_workflows.py` passes synthetic desktop/mobile issuing/correction flows, plain-language labels, contextual help and quick-start access.
3. `python -m pytest tests/browser/test_refresh_sessions.py` proves polling preserves drafts, failures are visible and background requests do not keep sessions alive.
4. `python manage.py check` passes.
5. `git diff --check` passes.
