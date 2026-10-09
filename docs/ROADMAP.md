# Roadmap

Milestones 01–02 are implemented and tested (not a deployable replacement). The standalone HTML remains the only working staff app until the replacement is validated. This is a single-host stopgap with one active roster; no Microsoft integrations, multi-intake management, handwritten signatures, offline synchronization or zero-downtime deployment.

- [x] **01 — Foundation and data invariants:** Django skeleton, accounts, permissions, idle sessions, stable records, ledger, transactional mutations, synthetic tests. [Plan](../.openchamber/plans/01-foundation.md)
- [x] **02 — Daily staff workflows:** familiar responsive screens, plain-language guidance, one-page quick-start, first-time-user usability checks, issuing with explicit partial fulfillment, reversals, count adjustments, archiving and live stock refresh. [Plan](../.openchamber/plans/02-workflows.md)
- [ ] **03 — Transfers, reports and administrator safeguards:** standard interchange, ignored local seed conversion, validated imports, exports, account administration and protected empty reset. [Plan](../.openchamber/plans/03-transfers.md)
- [ ] **04 — Deployment and recovery:** Compose/Caddy, private image CI, safe backups/restores/updates, security and concurrent-user verification, operator documentation. [Plan](../.openchamber/plans/04-deployment.md)

Milestones are dependency ordered. Do not advertise intermediate scaffolding as a production replacement. Staff usability, real deployment, private registry permissions and host networking are manual checks. Certificate trust setup is out of scope for now; HTTPS remains required and browser warnings remain possible. Never push or publish releases without explicit authorization.
