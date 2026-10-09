# Roadmap

All milestones are planned, not implemented. The standalone HTML remains the only working app until the replacement is validated. This is a single-host stopgap with one active roster; no Microsoft integrations, multi-intake management, handwritten signatures, offline synchronization or zero-downtime deployment.

- [ ] **01 — Foundation and data invariants:** Django skeleton, accounts, permissions, idle sessions, stable records, ledger, transactional mutations, synthetic tests. [Plan](../.openchamber/plans/01-foundation.md)
- [ ] **02 — Daily staff workflows:** familiar responsive screens, issuing with explicit partial fulfillment, reversals, count adjustments, archiving and live stock refresh. [Plan](../.openchamber/plans/02-workflows.md)
- [ ] **03 — Transfers, reports and administrator safeguards:** standard interchange, ignored local seed conversion, validated imports, exports, account administration and protected empty reset. [Plan](../.openchamber/plans/03-transfers.md)
- [ ] **04 — Deployment and recovery:** Compose/Caddy, private image CI, safe backups/restores/updates, security and concurrent-user verification, operator documentation. [Plan](../.openchamber/plans/04-deployment.md)

Milestones are dependency ordered. Do not advertise intermediate scaffolding as a production replacement. Real deployment, browser trust, private registry permissions and host networking are manual checks. Never push or publish releases without explicit authorization.
