# 04 — Deployment, recovery and releases

## Goal and context
Package milestones 01–03 as a LAN-only Compose installation using private prebuilt releases, with safe backups and controlled remote updates. See `docs/ARCHITECTURE.md`. Internet is available for release downloads; routine workflows must need only the LAN host.

## Scope
In: Docker/Gunicorn/Caddy, CI, operating commands, recovery, concurrent-user/security validation. Out: certificate trust setup/distribution, pushing/publishing without approval, public exposure, external backups, unattended upgrades and zero-downtime promises.

## Steps and affected areas
1. Add `Dockerfile`, `.dockerignore`, `compose.yaml`, `.env.example`, `deploy/Caddyfile` and `scripts/` using pinned images/dependencies. Selectively copy application assets, excluding prototype HTML, `.local/`, bundles, SQLite files, backups, secrets and real fixtures. Gunicorn owns one local-disk database; Caddy exposes configured LAN HTTPS/IP/port only. Retain app data, backups and CA material across container replacement. No public proxy admin port.
2. Add production settings: allowed hosts/origins, trusted proxy rules, secure cookies, CSRF, secret/config validation, permission-protected admin actions and bounded uploads/login retries. Readiness checks DB/schema state; health output/logs contain no PII/secrets. Provide container tests under `tests/deployment/` and documented pytest dependencies/fixtures.
3. Implement SQLite-safe backup/integrity/restore operations and daily scheduler: 30-day retention, latest successful pre-update recovery backup protected until superseded. Permission-restrict artifacts. Restore under exclusive maintenance with integrity/schema compatibility checks and matching release. Never copy a live WAL database file naively. Corrupt backups/failed checks must leave a usable recovery path and no silent success.
4. Provide `bash scripts/update.sh VERSION`: verify config/release, maintenance/quiescence, verified backup, pinned pull, migrations, restart and readiness. Coordinate other writers and operator runs. On failure keep mutation access closed and report exact recovery steps. `bash scripts/restore.sh BACKUP VERSION` restores a compatible DB/image. Recovery does not discard newer work without explicit confirmation.
5. Add GitHub Actions test/build workflows and gated versioned private GHCR publishing configuration. Synthetic data only. Host uses a separate read-only pull credential. Do not push, trigger a publication or create a registry token during implementation without authorization.
6. Write `docs/OPERATIONS.md` for host prerequisites, firewall/IP/port, secrets, bootstrap admin, bundle import, account recovery, backups, updates, rollback and host-loss limitation. Installation, updates, backup monitoring and host/network recovery belong to the operator; account administration is available in the UI. Create `compose.test.yaml` with synthetic settings, never production data/secrets.
7. Add `tests.deployment_security`, browser offline-network checks and Compose integration tests for container persistence, artifact exclusions, restore/update failures and ten-client stock contention. Run full automated validation. Update `AGENTS.md`, tick milestone 04, and update architecture if design changed.

## Risks
Host-only backups cannot survive disk/host loss. Browser trust warnings remain for untrusted local CA. A self-issued certificate alone does not establish LAN isolation. Image rollback alone cannot undo an incompatible migration. Windows/macOS Docker operation and licensing need operator verification.

## Manual checks
Actual host firewall/subnet reachability, boot startup, fixed address, target-browser PDF/mail behavior; private GHCR authorization and real published release pull after user approval. Restore drill and ten-user workflow review on intended hardware. Certificate trust configuration is deferred, not a release criterion.

## Acceptance criteria
1. `docker compose -f compose.test.yaml config --quiet` passes with synthetic settings.
2. `docker build -t supplies-tracker:test .` succeeds without source personal data or secrets.
3. `python manage.py test` passes all Django tests.
4. `python -m pytest tests/browser` passes all browser tests, including routine operation with external internet blocked.
5. `python -m pytest tests/deployment` passes image exclusions, persisted data, TLS/readiness, backup/restore and update-failure cases using isolated Compose services.
6. `python manage.py test tests.concurrency tests.deployment_security` proves ten-client stock safety and production permission/config safeguards.
7. `bash -n scripts/update.sh scripts/restore.sh` passes.
8. `git diff --check` passes.
